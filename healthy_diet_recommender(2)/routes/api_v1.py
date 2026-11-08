# routes/api_v1.py
"""
API v1 — 微信小程序专用接口（纯 JSON + JWT 认证）

所有端点均返回 JSON，使用 @jwt_login_required 认证。
与原有 Web 蓝图并存，不影响现有 HTML 模板页面。
"""
from flask import Blueprint, request, jsonify, current_app, g
from utils.database import db
from utils.auth import jwt_login_required
from utils.file_handler import save_uploaded_file, allowed_file_health_report, allowed_file_fridge_image
from utils.yolo_detector import detect_ingredients
from utils.rag import generate_recipe_with_rag
from utils.llm_client import LLMClient
from utils.risk_guard import check_health_risk, warn_summary, DISCLAIMER
from utils.report_parser import build_report_context
from utils.report_ocr import extract_report_text
from utils.recipe_options import normalize_all
from utils.meal_plan_prompt import build_meal_plan_prompt, estimate_max_tokens
from utils.metric_store import save_metrics_for_report, latest_metrics, metric_trend
from utils.tts import synthesize_speech
from models.user import User
from models.recipe import Recipe, Favorite
from models.community import (
    CommunityPost, LIKE, FAVORITE,
    toggle_interaction, delete_post, list_posts, list_posts_by_user,
    related_posts, find_share,
)
from models.health_report import HealthReport
from models.fridge_image import FridgeImage
from models.ingredient import Ingredient
from utils.food_lookup import lookup_food

api_v1_bp = Blueprint("api_v1", __name__)


@api_v1_bp.route('/food-guide', methods=['GET'])
@jwt_login_required
def food_guide_catalog():
    from utils.food_guide import catalog, CATEGORIES, FILTERS, TIPS, SOURCES, NOTICE
    try:
        foods = catalog(request.args.get('q', ''), request.args.get('category', 'all'),
                        request.args.get('choice', 'all'))
    except ValueError as exc:
        return fail(str(exc))
    return ok({'foods': foods, 'total': len(foods), 'categories': CATEGORIES,
               'filters': FILTERS, 'tips': TIPS, 'sources': SOURCES, 'notice': NOTICE})


@api_v1_bp.route('/food-guide/portion', methods=['GET'])
@jwt_login_required
def food_guide_portion():
    from utils.food_guide import portion_result
    try:
        return ok(portion_result(request.args.get('food_id'), request.args.get('amount'), request.args.get('label_carb')))
    except LookupError as exc:
        return fail(str(exc), status=404)
    except ValueError as exc:
        return fail(str(exc))


@api_v1_bp.route('/food-guide/lookup', methods=['POST'])
@jwt_login_required
def food_guide_lookup():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return fail('请提交食品名称')
    try:
        result = lookup_food(payload.get('q'), g.user_id)
    except ValueError as exc:
        return fail(str(exc))
    if result['status'] == 'rate_limited':
        return fail(result['message'], status=429)
    return ok(result)


@api_v1_bp.route('/food-guide/label-portion', methods=['GET'])
@jwt_login_required
def food_guide_label_portion():
    from utils.food_guide import label_portion
    try:
        return ok(label_portion(request.args.get('name'), request.args.get('amount'),
                                request.args.get('label_carb'), request.args.get('unit', 'g')))
    except ValueError as exc:
        return fail(str(exc))


# ──────────────────────────────────────────────
# 辅助：统一成功响应
# ──────────────────────────────────────────────
def ok(data=None, message="ok"):
    return jsonify({"code": 0, "message": message, "data": data})


def fail(message="error", code=1, status=400):
    return jsonify({"code": code, "message": message}), status


# ──────────────────────────────────────────────
# 辅助：食谱 / 分享帖的 JSON 形状
# ──────────────────────────────────────────────
# 控糖档位（低/中/高升糖）为什么**不写进 Recipe.to_dict()**：
#   算档位要扫 154 味食材表，属业务判断。放进 model 就等于任何一次
#   to_dict() 都背上这个开销（社区列表一次 dump 十条，没必要每条都扫）；
#   而且 models 层一旦 import utils.diabetes_advice，依赖方向就反了。
# 所以放在接口层：客户端拿到的是算好的 tier，不用自己再维护一份食材表。
def _gi_summary(ingredients_text):
    """一道菜的控糖摘要 → {tier, tier_label, high_names, consult}。

    认不出任何食材时 tier='unknown'（**不是**默认判成低升糖），
    文案由 utils.diabetes_advice.label_of_tier 统一给，这里不自己写中文。
    """
    try:
        from utils.diabetes_advice import build_advice
        advice = build_advice(ingredients_text or "")
    except Exception as exc:                       # noqa: BLE001 —— 摘要失败不能拖垮列表
        current_app.logger.warning("控糖摘要计算失败: %s", exc)
        return {"tier": "unknown", "tier_label": "未收录",
                "high_names": [], "consult": False}

    return {
        "tier": advice.get("tier"),
        "tier_label": advice.get("tier_label"),
        # 只给"哪几味让它判高了"的名单，够前端做高亮；完整依据在网页「为什么这样判」里
        "high_names": advice.get("high_names") or [],
        "consult": bool(advice.get("consult")),
    }


def _recipe_payload(recipe):
    """食谱 JSON = to_dict() + 控糖摘要。"""
    if recipe is None:
        return None
    data = dict(recipe.to_dict())
    data["gi"] = _gi_summary(data.get("ingredients"))
    return data


def _post_payload(post, viewer_id):
    """社区分享帖 JSON —— 内嵌的 recipe 也补上控糖摘要（列表页要画徽标）。"""
    data = dict(post.to_dict(viewer_id))
    if isinstance(data.get("recipe"), dict):
        data["recipe"]["gi"] = _gi_summary(data["recipe"].get("ingredients"))
    return data


# ══════════════════════════════════════════════
# 用户 / 认证
# ══════════════════════════════════════════════

@api_v1_bp.route("/user/login", methods=["POST"])
def user_login():
    """用户名密码登录（兼容非微信场景）"""
    data = request.get_json() or {}
    identifier = data.get("identifier") or data.get("username")
    password = data.get("password")
    if not identifier or not password:
        return fail("请输入用户名和密码")

    user = User.query.filter(
        (User.username == identifier) | (User.email == identifier)
    ).first()
    if not user or not user.check_password(password):
        return fail("用户名或密码错误")

    from utils.auth import generate_jwt_token
    token = generate_jwt_token(user.id)
    return ok({"token": token, "user": user.to_dict()})


@api_v1_bp.route("/user/register", methods=["POST"])
def user_register():
    """用户名密码注册（邮箱选填）"""
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return fail("请填写用户名和密码")
    if len(password) < 6:
        return fail("密码至少 6 位")

    if User.query.filter_by(username=username).first():
        return fail("用户名已存在")
    if email:
        if User.query.filter_by(email=email).first():
            return fail("邮箱已被注册")
    else:
        # 邮箱选填：未填则写占位值（老库 email 是 NOT NULL + UNIQUE）
        email = "u%s@local" % username

    user = User(username=username, email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    from utils.auth import generate_jwt_token
    token = generate_jwt_token(user.id)
    return ok({"token": token, "user": user.to_dict()}, message="注册成功")


@api_v1_bp.route("/user/profile", methods=["GET"])
@jwt_login_required
def user_profile():
    user = User.query.get(g.user_id)
    if not user:
        return fail("用户不存在", status=404)
    return ok({"user": user.to_dict()})


@api_v1_bp.route("/user/change_password", methods=["POST"])
@jwt_login_required
def user_change_password():
    """修改密码（JWT 认证）"""
    data = request.get_json(silent=True) or {}
    old_password = data.get("old_password")
    new_password = data.get("new_password")
    if not old_password or not new_password:
        return fail("请填写原密码和新密码")
    if len(new_password) < 6:
        return fail("新密码至少6位")

    user = User.query.get(g.user_id)
    if not user:
        return fail("用户不存在", status=404)
    if not user.check_password(old_password):
        return fail("原密码错误")

    user.set_password(new_password)
    db.session.commit()
    return ok(message="密码修改成功")


# ══════════════════════════════════════════════
# 仪表盘
# ══════════════════════════════════════════════

@api_v1_bp.route("/dashboard", methods=["GET"])
@jwt_login_required
def dashboard():
    user_id = g.user_id
    stats = {
        "recipe_count": Recipe.query.filter_by(user_id=user_id).count(),
        "favorite_count": Favorite.query.filter_by(user_id=user_id).count(),
        "report_count": HealthReport.query.filter_by(user_id=user_id).count(),
    }

    latest_health = HealthReport.query.filter_by(user_id=user_id) \
        .order_by(HealthReport.uploaded_at.desc()).first()
    latest_fridge = FridgeImage.query.filter_by(user_id=user_id) \
        .order_by(FridgeImage.uploaded_at.desc()).first()

    latest_health_data = None
    if latest_health:
        latest_health_data = {
            "id": latest_health.id,
            "filename": latest_health.filename,
            "content_text": (latest_health.content_text or "")[:500],
            "uploaded_at": latest_health.uploaded_at.isoformat() if latest_health.uploaded_at else None,
        }

    latest_ingredients = []
    if latest_fridge:
        ingredients = Ingredient.query.filter_by(fridge_image_id=latest_fridge.id).all()
        latest_ingredients = [
            {"name": ing.name, "confidence": ing.confidence} for ing in ingredients
        ]
        stats["ingredient_count"] = len(latest_ingredients)
    else:
        stats["ingredient_count"] = 0

    recent_recipes = Recipe.query.filter_by(user_id=user_id) \
        .order_by(Recipe.created_at.desc()).limit(3).all()
    recent = [
        {"id": r.id, "title": r.title, "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in recent_recipes
    ]

    return ok({
        "stats": stats,
        "latest_health": latest_health_data,
        "latest_ingredients": latest_ingredients,
        "recent_recipes": recent,
    })


# ══════════════════════════════════════════════
# 健康报告上传
# ══════════════════════════════════════════════

@api_v1_bp.route("/upload/health_report", methods=["POST"])
@jwt_login_required
def upload_health_report():
    if "file" not in request.files:
        return fail("没有文件部分")
    file = request.files["file"]
    if not file or file.filename == "":
        return fail("没有选择文件")
    if not allowed_file_health_report(file.filename):
        return fail("不支持的文件格式，请上传 PDF/PNG/JPG")

    folder = current_app.config["UPLOAD_FOLDER_HEALTH_REPORTS"]
    filepath, filename = save_uploaded_file(file, folder)
    if not filepath:
        return fail("文件保存失败", status=500)

    # 解析：PDF 文字层 / 扫描件内嵌图 / 图片视觉转录，统一由 utils/report_ocr 编排。
    # 图片以前只走 pytesseract（本机没装 tesseract → 必抛错），现在优先视觉大模型。
    content_text, _method = extract_report_text(filepath, filename)
    if _method == "none" and filename.lower().endswith(".pdf"):
        content_text = "[无法解析PDF内容]"

    report = HealthReport(
        user_id=g.user_id, filename=filename, filepath=filepath, content_text=content_text
    )
    db.session.add(report)
    db.session.commit()

    # 指标落库：抽出来的数字存进 health_metric，供趋势 / 评分使用
    # 安全兜底：落库失败只记日志，不影响上传本身
    saved = 0
    try:
        result = save_metrics_for_report(report)
        saved = result.get("saved", 0)
        if result.get("error"):
            print(f"[metric_store] 报告 {report.id} 指标落库失败：{result['error']}")
    except Exception as exc:                              # noqa: BLE001
        print(f"[metric_store] 报告 {report.id} 指标落库异常：{exc}")

    # 风险扫描：报告一上传完就告知，别等用户点"生成食谱"才发现。
    # 用**全文**扫描（不是喂模型用的 content_text[:500]）。
    # 注意这条路**只提示、不阻断** —— 报告已经存好了，上传本身是成功的，
    # 所以仍返回 code 0，风险信息挂在 data.risk 里由前端决定怎么展示。
    risk = check_health_risk(content_text, context="upload")

    return ok({
        "report": {
            "id": report.id,
            "filename": report.filename,
            "content_text": content_text[:1000],
            "uploaded_at": report.uploaded_at.isoformat() if report.uploaded_at else None,
        },
        "metrics_saved": saved,
        "risk": risk,
    }, message="健康报告上传成功")


# ══════════════════════════════════════════════
# 健康指标（结构化解析结果）
# ══════════════════════════════════════════════

@api_v1_bp.route("/health/metrics", methods=["GET"])
@jwt_login_required
def health_metrics():
    """
    当前用户各项指标的**最新值**。

    返回 data.metrics：[{key,label,unit,value,measured_at,report_id,points}]
      - points = 该指标一共有几个历史点，> 1 就可以画趋势
    没有任何指标数据时 metrics 为空数组（不是报错）。
    """
    metrics = latest_metrics(g.user_id)
    return ok({
        "metrics": metrics,
        "count": len(metrics),
    })


@api_v1_bp.route("/health/metrics/trend", methods=["GET"])
@jwt_login_required
def health_metric_trend():
    """
    单个指标的时间序列，供前端画趋势图。

    查询参数：
        key   必填，指标键，如 fasting_glucose（见 /health/metrics 返回的 key）
        limit 选填，最多返回几个点，默认 30，最大 200
    """
    key = (request.args.get("key") or "").strip()
    if not key:
        return fail("缺少 key 参数，例如 key=fasting_glucose")

    try:
        limit = int(request.args.get("limit", 30))
    except (TypeError, ValueError):
        limit = 30
    limit = max(1, min(limit, 200))

    return ok(metric_trend(g.user_id, key, limit=limit))


# ══════════════════════════════════════════════
# 血糖记录（每日手工录入）
# ══════════════════════════════════════════════
# 网页端是普通表单 POST + PRG（routes/checkin_routes.py），这里是同一份数据的
# JSON 通道，给小程序 / 鸿蒙用。校验、落库、回填一律走 utils/checkin.py，
# **不在这里再写一套规则** —— 否则"网页能存 7.2、手机说不是数字"这种不一致迟早出现。

@api_v1_bp.route("/health/checkin", methods=["GET"])
@jwt_login_required
def get_health_checkin():
    """今天的血糖记录（三个时点 + 体重，以及各自的上次值）。

    返回 data：
        date / done / filled_count / weight_filled / fields / weight
        fields 每项：
            key / label / ui_label / unit / lo / hi / step
            value      今天填的值，null 表示这一项今天还没填
            last_value 上一次的值（不含今天）；**自己没填过时退回上传报告里的值**
            last_date  上一次的日期 YYYY-MM-DD
            last_source 'manual' = 自己填的 / 'report' = 来自上传的报告（快照，可能较旧）
                        —— 客户端在 report 时请把"上次"改成"报告"，别让用户
                        以为是自己昨天量的
    weight     同结构的单个对象（体重不挤进 fields，客户端另起一行渲染）
    done=true 表示**血糖**今天填过至少一项；只填了体重时 done=false，
    weight_filled 才是判断体重有没有填。
    """
    from utils.checkin import build_api_context
    return ok(build_api_context(g.user_id))


@api_v1_bp.route("/health/checkin", methods=["POST"])
@jwt_login_required
def save_health_checkin():
    """保存今天的血糖记录。

    请求体两种形状都收（客户端照着顺手的那种写即可）：
        {"fasting_glucose": "7.2", "postprandial_glucose": "8.4"}
        {"values": {"fasting_glucose": "7.2"}}

    · 只填一个也能存；空串 / null = 没填，不算错
    · 同一天同一时点再提交 = 改自己那条，不新增第二个点
    · 提交的键不在三个时点里 → 忽略（不报错，免得客户端多传一个字段就整单失败）
    · 校验失败**不落库**，返回 code=1 + HTTP 400，message 是可直接展示的中文
    """
    from utils.checkin import parse_checkin, save_checkin, build_api_context

    data = request.get_json(silent=True) or {}
    values = data.get("values") if isinstance(data.get("values"), dict) else data

    items, errors = parse_checkin(values)
    if errors:
        return fail("；".join(msg for _key, msg in errors))
    if not items:
        return fail("还没有填数值")

    result = save_checkin(g.user_id, items)
    if result.get("error"):
        current_app.logger.error("血糖记录保存失败: %s", result["error"])
        return fail("保存失败，请稍后再试", status=500)

    payload = build_api_context(g.user_id)
    payload["saved"] = result["saved"]
    payload["updated"] = result["updated"]      # 其中几条是覆盖今天已有的
    return ok(payload, message="已记录")


# ══════════════════════════════════════════════
# 冰箱食材上传 + YOLO 识别
# ══════════════════════════════════════════════

@api_v1_bp.route("/upload/fridge_image", methods=["POST"])
@jwt_login_required
def upload_fridge_image():
    if "file" not in request.files:
        return fail("没有文件部分")
    file = request.files["file"]
    if not file or file.filename == "":
        return fail("没有选择文件")
    if not allowed_file_fridge_image(file.filename):
        return fail("不支持的文件格式，请上传 PNG/JPG")

    folder = current_app.config["UPLOAD_FOLDER_FRIDGE_IMAGES"]
    filepath, filename = save_uploaded_file(file, folder)
    if not filepath:
        return fail("文件保存失败", status=500)

    # YOLOv8 食材检测
    detections = detect_ingredients(filepath)

    fridge_image = FridgeImage(user_id=g.user_id, filename=filename, filepath=filepath)
    db.session.add(fridge_image)
    db.session.flush()

    for det in detections:
        ingredient = Ingredient(
            fridge_image_id=fridge_image.id,
            name=det["name"],
            confidence=det["confidence"],
        )
        db.session.add(ingredient)
    db.session.commit()

    # 0 项时不能说"识别成功"（2026-10-23 改）：大模型限流或图太糊都会返回空，
    # 客户端若直接把 message 弹给用户，"成功 + 空列表"会让人以为系统坏了。
    # code 仍是 0 —— 照片确实存下了，只是没认出东西，不算请求失败。
    items = Ingredient.query.filter_by(fridge_image_id=fridge_image.id).all()
    return ok({
        "image": {
            "id": fridge_image.id,
            "filename": fridge_image.filename,
            "uploaded_at": fridge_image.uploaded_at.isoformat() if fridge_image.uploaded_at else None,
        },
        "ingredients": [
            {"id": ing.id, "name": ing.name, "confidence": ing.confidence}
            for ing in items
        ],
    }, message="食材识别成功" if items else "照片已上传，但这次没认出食材")


# ══════════════════════════════════════════════
# 食谱
# ══════════════════════════════════════════════

@api_v1_bp.route("/recipes/generate", methods=["POST"])
@jwt_login_required
def generate_recipe():
    data = request.get_json() or {}
    user_query = data.get("query", "根据我的情况生成健康食谱")
    # R4 三维度：小程序可不传（走默认档），也可传中文标签，非法值自动收敛
    tags = normalize_all(data)

    latest_health = HealthReport.query.filter_by(user_id=g.user_id) \
        .order_by(HealthReport.uploaded_at.desc()).first()
    latest_fridge = FridgeImage.query.filter_by(user_id=g.user_id) \
        .order_by(FridgeImage.uploaded_at.desc()).first()

    # 优先用结构化指标摘要，抽不到指标则退回原文
    health_text = build_report_context(latest_health.content_text if latest_health else "")
    ingredients_text = ""
    if latest_fridge:
        ings = Ingredient.query.filter_by(fridge_image_id=latest_fridge.id).all()
        ingredients_text = ", ".join(ing.name for ing in ings)

    # 安全闸门：用报告全文扫描，指标达危险水平时不出食谱，改提示就医
    risk = check_health_risk(latest_health.content_text if latest_health else "")
    if risk["blocked"]:
        return jsonify({
            "code": 1001,
            "message": risk["message"],
            "data": {"risk": risk},
        }), 409

    try:
        recipe_content = generate_recipe_with_rag(
            health_text, ingredients_text, user_query,
            purpose=tags["purpose"],
            flavor=tags["flavor"],
            meal_time=tags["meal_time"],
        )
        from routes.recipe_routes import _parse_recipe_content
        title, ingredients, instructions, nutrition_info, _ = _parse_recipe_content(recipe_content)

        # 偏高指标提示 + 免责声明，随食谱一并保存
        warn_text = warn_summary(risk)
        if warn_text:
            nutrition_info = (nutrition_info or "") + "\n\n【请留意】" + warn_text
        nutrition_info = (nutrition_info or "") + "\n\n———\n" + DISCLAIMER

        recipe = Recipe(
            user_id=g.user_id,
            title=title,
            ingredients=ingredients,
            instructions=instructions,
            nutrition_info=nutrition_info,
            purpose=tags["purpose"],
            flavor=tags["flavor"],
            meal_time=tags["meal_time"],
        )
        db.session.add(recipe)
        db.session.commit()

        return ok({"recipe": _recipe_payload(recipe), "risk": risk}, message="食谱生成成功")
    except Exception as e:
        current_app.logger.error(f"生成食谱失败: {e}")
        return fail(f"生成食谱失败: {str(e)}", status=500)


@api_v1_bp.route("/recipes", methods=["GET"])
@jwt_login_required
def list_recipes():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    per_page = min(per_page, 50)

    pagination = Recipe.query.filter_by(user_id=g.user_id) \
        .order_by(Recipe.created_at.desc()) \
        .paginate(page=page, per_page=per_page, error_out=False)

    return ok({
        "recipes": [_recipe_payload(r) for r in pagination.items],
        "total": pagination.total,
        "page": pagination.page,
        "per_page": pagination.per_page,
        "pages": pagination.pages,
    })


@api_v1_bp.route("/recipes/<int:recipe_id>", methods=["GET"])
@jwt_login_required
def get_recipe(recipe_id):
    recipe = Recipe.query.filter_by(id=recipe_id, user_id=g.user_id).first()
    if not recipe:
        return fail("食谱不存在", status=404)

    is_favorited = Favorite.query.filter_by(
        user_id=g.user_id, recipe_id=recipe_id
    ).first() is not None

    return ok({"recipe": _recipe_payload(recipe), "is_favorited": is_favorited})


@api_v1_bp.route("/recipes/<int:recipe_id>/regenerate", methods=["POST"])
@jwt_login_required
def regenerate_recipe(recipe_id):
    """换一道：沿原食谱的「用途 / 口味 / 餐次」重新生成一份明显不同的。

    与网页「换一道」（routes/recipe_routes.py::regenerate_recipe）共用同一套底层
    （utils/rag 生成 + 同一个解析函数），区别只在认证方式与返回形状：
    网页返回扁平 JSON 直接喂 fetch，这里返回 {code,message,data}。

    · 不是自己生成的食谱 → 404（不透露别人的食谱是否存在）
    · 报告达危险水平 → code 1001 + 409，带 data.risk（与生成接口同一契约，
      客户端收到 1001 应跳「建议就医」页，不要弹 toast）
    · **换一道不能变成绕过「建议就医」的后门** —— 所以安全闸门照走一遍

    请求体（都可选）：
        {"query": "想吃清淡点的"}   覆盖"再推荐一道"的默认话术
    """
    from routes.recipe_routes import (
        _parse_recipe_content, _regen_query, _latest_ingredients_text,
    )

    recipe = Recipe.query.filter_by(id=recipe_id, user_id=g.user_id).first()
    if not recipe:
        return fail("食谱不存在", status=404)

    latest_report = HealthReport.query.filter_by(user_id=g.user_id) \
        .order_by(HealthReport.uploaded_at.desc()).first()
    report_text = latest_report.content_text if latest_report else ""

    risk = check_health_risk(report_text)
    if risk["blocked"]:
        return jsonify({
            "code": 1001,
            "message": risk["message"],
            "data": {"risk": risk},
        }), 409

    tags = normalize_all({
        "purpose": recipe.purpose,
        "flavor": recipe.flavor,
        "meal_time": recipe.meal_time,
    })

    data = request.get_json(silent=True) or {}
    user_query = (data.get("query") or "").strip()
    if not user_query:
        # 换一道的意图必须显式告诉模型，否则同样的食材 + 同样的三维度会换汤不换药
        user_query = _regen_query(recipe)

    try:
        content = generate_recipe_with_rag(
            build_report_context(report_text),
            _latest_ingredients_text(g.user_id),
            user_query,
            purpose=tags["purpose"],
            flavor=tags["flavor"],
            meal_time=tags["meal_time"],
        )
        title, ingredients, instructions, nutrition_info, _tip = \
            _parse_recipe_content(content)

        warn_text = warn_summary(risk)
        if warn_text:
            nutrition_info = (nutrition_info or "") + "\n\n【请留意】" + warn_text
        nutrition_info = (nutrition_info or "") + "\n\n———\n" + DISCLAIMER

        new_recipe = Recipe(
            user_id=g.user_id,
            title=title,
            ingredients=ingredients,
            instructions=instructions,
            nutrition_info=nutrition_info,
            purpose=tags["purpose"],
            flavor=tags["flavor"],
            meal_time=tags["meal_time"],
        )
        db.session.add(new_recipe)
        db.session.commit()
    except Exception as exc:                       # noqa: BLE001
        db.session.rollback()
        current_app.logger.error("换一道失败（原 #%s）：%s", recipe.id, exc)
        return fail("换一道失败，请稍后再试", status=500)

    current_app.logger.info("换一道（API）：原 #%s -> 新 #%s", recipe.id, new_recipe.id)
    return ok({
        "recipe": _recipe_payload(new_recipe),
        "from_recipe_id": recipe.id,               # 客户端可据此把旧的那份标记为"已换掉"
        "risk": risk,
    }, message="已换一道")


# ══════════════════════════════════════════════
# 收藏
# ══════════════════════════════════════════════

@api_v1_bp.route("/recipes/<int:recipe_id>/favorite", methods=["POST"])
@jwt_login_required
def add_favorite(recipe_id):
    recipe = Recipe.query.get(recipe_id)
    if not recipe:
        return fail("食谱不存在", status=404)

    existing = Favorite.query.filter_by(user_id=g.user_id, recipe_id=recipe_id).first()
    if existing:
        return fail("已经收藏过该食谱")

    fav = Favorite(user_id=g.user_id, recipe_id=recipe_id)
    db.session.add(fav)
    db.session.commit()
    return ok({"is_favorited": True}, message="收藏成功")


@api_v1_bp.route("/recipes/<int:recipe_id>/favorite", methods=["DELETE"])
@jwt_login_required
def remove_favorite(recipe_id):
    fav = Favorite.query.filter_by(user_id=g.user_id, recipe_id=recipe_id).first()
    if not fav:
        return fail("未收藏该食谱")
    db.session.delete(fav)
    db.session.commit()
    return ok({"is_favorited": False}, message="取消收藏成功")


@api_v1_bp.route("/recipes/favorites", methods=["GET"])
@jwt_login_required
def list_favorites():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    per_page = min(per_page, 50)

    fav_query = db.session.query(Favorite, Recipe) \
        .join(Recipe) \
        .filter(Favorite.user_id == g.user_id) \
        .order_by(Favorite.created_at.desc())

    total = fav_query.count()
    items = fav_query.offset((page - 1) * per_page).limit(per_page).all()
    recipes = [recipe for _, recipe in items]

    return ok({
        "recipes": [_recipe_payload(r) for r in recipes],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


# ══════════════════════════════════════════════
# 膳食计划
# ══════════════════════════════════════════════

@api_v1_bp.route("/recipes/meal_plan", methods=["POST"])
@jwt_login_required
def meal_plan():
    data = request.get_json() or {}
    days = int(data.get("days", 7))
    preference = data.get("preference", "")

    latest_health = HealthReport.query.filter_by(user_id=g.user_id) \
        .order_by(HealthReport.uploaded_at.desc()).first()
    latest_fridge = FridgeImage.query.filter_by(user_id=g.user_id) \
        .order_by(FridgeImage.uploaded_at.desc()).first()

    # 优先用结构化指标摘要，抽不到指标则退回原文
    health_text = build_report_context(latest_health.content_text if latest_health else "")
    ingredients_text = ""
    if latest_fridge:
        ings = Ingredient.query.filter_by(fridge_image_id=latest_fridge.id).all()
        ingredients_text = ", ".join(ing.name for ing in ings)

    # 安全闸门：报告指标达危险水平时不出膳食计划，改提示就医
    risk = check_health_risk(latest_health.content_text if latest_health else "")
    if risk["blocked"]:
        return jsonify({
            "code": 1001,
            "message": risk["message"],
            "data": {"risk": risk},
        }), 409

    # 偏高指标要求模型在建议里明确提醒
    warn_text = warn_summary(risk)
    risk_note = ""
    if warn_text:
        risk_note = (
            f"\n【必须提醒】该报告存在以下偏高指标，"
            f"请在最后的总体健康建议中明确提醒用户：{warn_text}"
        )

    # 控糖五餐制提示词与 Web 端共用一份（utils/meal_plan_prompt.py）
    meal_prompt = build_meal_plan_prompt(
        days,
        preference=preference,
        health_text=health_text,
        ingredients_text=ingredients_text,
        risk_note=risk_note,
    )

    try:
        llm = LLMClient()
        # 原来写死 2000：超过 7 天的计划会被截断
        meal_plan_text = llm.generate_text(
            meal_prompt, temperature=0.7, max_tokens=estimate_max_tokens(days))
        from routes.recipe_routes import parse_meal_plan
        plan_data = parse_meal_plan(meal_plan_text, days)
        return ok({"meal_plan": plan_data, "risk": risk}, message="膳食计划生成成功")
    except Exception as e:
        current_app.logger.error(f"生成膳食计划失败: {e}")
        return fail(f"生成膳食计划失败: {str(e)}", status=500)


# ══════════════════════════════════════════════
# TTS 语音合成
# ══════════════════════════════════════════════

@api_v1_bp.route("/tts/text_to_speech", methods=["POST"])
@jwt_login_required
def text_to_speech():
    data = request.get_json() or {}
    text = data.get("text", "")
    if not text:
        return fail("请提供需要转换的文本")

    audio_path = synthesize_speech(text)
    if not audio_path:
        return fail("语音合成失败", status=500)

    audio_url = "/" + audio_path
    return ok({"audio_url": audio_url}, message="语音合成成功")


# ══════════════════════════════════════════════
# 语音指令（ASR → 食谱生成）
# ══════════════════════════════════════════════

@api_v1_bp.route("/voice/voice_command", methods=["POST"])
@jwt_login_required
def voice_command():
    if "audio" not in request.files:
        return fail("没有音频文件")

    audio_file = request.files["audio"]
    if not audio_file or audio_file.filename == "":
        return fail("没有选择音频文件")

    import os
    audio_dir = os.path.join("static", "uploads", "audio")
    os.makedirs(audio_dir, exist_ok=True)
    file_path = os.path.join(audio_dir, audio_file.filename)
    audio_file.save(file_path)

    llm = LLMClient()
    user_query = llm.transcribe_audio(file_path)
    if not user_query:
        return fail("语音转文字失败", status=500)

    latest_health = HealthReport.query.filter_by(user_id=g.user_id) \
        .order_by(HealthReport.uploaded_at.desc()).first()
    latest_fridge = FridgeImage.query.filter_by(user_id=g.user_id) \
        .order_by(FridgeImage.uploaded_at.desc()).first()

    # 优先用结构化指标摘要，抽不到指标则退回原文
    health_text = build_report_context(latest_health.content_text if latest_health else "")
    ingredients_text = ""
    if latest_fridge:
        ings = Ingredient.query.filter_by(fridge_image_id=latest_fridge.id).all()
        ingredients_text = ", ".join(ing.name for ing in ings)

    try:
        recipe_content = generate_recipe_with_rag(health_text, ingredients_text, user_query)
        from routes.recipe_routes import _parse_recipe_content
        title, ingredients, instructions, nutrition_info, _ = _parse_recipe_content(recipe_content)

        recipe = Recipe(
            user_id=g.user_id, title=title, ingredients=ingredients,
            instructions=instructions, nutrition_info=nutrition_info,
        )
        db.session.add(recipe)
        db.session.commit()

        return ok({
            "recipe": _recipe_payload(recipe),
            "transcribed_text": user_query,
        }, message="语音指令处理成功")
    except Exception as e:
        current_app.logger.error(f"语音处理失败: {e}")
        return fail(f"语音处理失败: {str(e)}", status=500)


# ══════════════════════════════════════════════
# 社区食谱分享（R3）
# ══════════════════════════════════════════════
# 与网页入口 routes/community_routes.py 共用 models/community.py 里的同一套逻辑，
# 保证小程序和网页看到的社区内容、点赞数完全一致。

def _community_filters():
    """筛选参数：认不出就当作没筛（与网页端一致，不用 normalize 兜默认档）。"""
    from utils.recipe_options import OPTIONS
    out = {}
    for kind in ("purpose", "flavor", "meal_time"):
        raw = (request.args.get(kind) or "").strip()
        keys = {k for k, *_ in OPTIONS[kind]}
        out[kind] = raw if raw in keys else None
    return out


@api_v1_bp.route("/community/posts", methods=["GET"])
@jwt_login_required
def community_posts():
    """社区广场：支持三维度筛选 + 最新/最热排序 + 分页。"""
    f = _community_filters()
    sort = request.args.get("sort") if request.args.get("sort") in ("new", "hot") else "new"
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 10, type=int), 50)

    posts, total, page, pages = list_posts(
        purpose=f["purpose"], flavor=f["flavor"], meal_time=f["meal_time"],
        sort=sort, page=page, per_page=per_page)

    return ok({
        "posts": [_post_payload(p, g.user_id) for p in posts],
        "total": total, "page": page, "pages": pages,
        "per_page": per_page, "sort": sort, "filters": f,
    })


@api_v1_bp.route("/community/posts/<int:post_id>", methods=["GET"])
@jwt_login_required
def community_post_detail(post_id):
    post = db.session.get(CommunityPost, post_id)
    if not post:
        return fail("这条分享不存在或已被删除", status=404)
    return ok({
        "post": _post_payload(post, g.user_id),
        "related": [_post_payload(p, g.user_id) for p in related_posts(post)],
    })


@api_v1_bp.route("/community/mine", methods=["GET"])
@jwt_login_required
def community_mine():
    posts = list_posts_by_user(g.user_id)
    return ok({"posts": [_post_payload(p, g.user_id) for p in posts], "total": len(posts)})


@api_v1_bp.route("/community/share", methods=["POST"])
@jwt_login_required
def community_share():
    """把一条食谱分享到社区。同一人同一条食谱只分享一次（重复调用返回原帖）。"""
    data = request.get_json() or {}
    try:
        recipe_id = int(data.get("recipe_id"))
    except (TypeError, ValueError):
        return fail("缺少食谱编号")

    recipe = db.session.get(Recipe, recipe_id)
    if not recipe or recipe.user_id != g.user_id:
        return fail("食谱不存在", status=404)

    existed = find_share(g.user_id, recipe_id)
    if existed:
        return ok({"post_id": existed.id, "post": _post_payload(existed, g.user_id)},
                  message="这条食谱你已经分享过了")

    note = (data.get("note") or "").strip()[:200]
    post = CommunityPost(recipe_id=recipe_id, user_id=g.user_id, note=note or None)
    db.session.add(post)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        existed = find_share(g.user_id, recipe_id)
        if not existed:
            return fail("分享失败，请稍后再试", status=500)
        return ok({"post_id": existed.id, "post": _post_payload(existed, g.user_id)},
                  message="这条食谱你已经分享过了")

    return ok({"post_id": post.id, "post": _post_payload(post, g.user_id)},
              message="已分享到社区")


def _community_toggle(post_id, kind):
    post = db.session.get(CommunityPost, post_id)
    if not post:
        return fail("这条分享不存在或已被删除", status=404)
    active = toggle_interaction(post, g.user_id, kind)
    db.session.commit()
    return ok({"active": active, "like_count": post.like_count},
              message=("已取消" if not active else ("点赞成功" if kind == LIKE else "收藏成功")))


@api_v1_bp.route("/community/posts/<int:post_id>/like", methods=["POST"])
@jwt_login_required
def community_like(post_id):
    return _community_toggle(post_id, LIKE)


@api_v1_bp.route("/community/posts/<int:post_id>/favorite", methods=["POST"])
@jwt_login_required
def community_favorite(post_id):
    return _community_toggle(post_id, FAVORITE)


@api_v1_bp.route("/community/posts/<int:post_id>", methods=["DELETE"])
@jwt_login_required
def community_delete(post_id):
    post = db.session.get(CommunityPost, post_id)
    if not post:
        return ok(message="这条分享已经不在社区里了")
    if post.user_id != g.user_id:
        return fail("只能删除自己分享的食谱", status=403)
    delete_post(post)
    db.session.commit()
    return ok(message="已从社区删除")
