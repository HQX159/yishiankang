# routes/recipe_routes.py
from flask import Blueprint, request, jsonify, session, current_app, render_template, redirect, \
    url_for, flash
from models.recipe import Recipe, Favorite
from models.fridge_image import FridgeImage
from models.health_report import HealthReport
from models.ingredient import Ingredient
from utils.database import db
from utils.auth import login_guard
from utils.rag import generate_recipe_with_rag
from utils.llm_client import LLMClient
from utils.risk_guard import check_health_risk, warn_summary, DISCLAIMER
from utils.report_parser import build_report_context
from utils.diabetes_advice import build_advice, build_method_page
from models.community import find_share
from utils.recipe_options import (
    DEFAULTS, normalize_all, infer_purpose, options_for_template,
)
from utils.meal_plan_prompt import build_meal_plan_prompt, estimate_max_tokens
# 详情页要把「**备料**」这类 markdown 洗掉，并把营养信息拆成可扫读的结构
from utils.recipe_text import split_steps, build_nutrition_block
import json
import re

recipe_bp = Blueprint('recipe', __name__)


def advice_map(recipes):
    """给一批食谱算控糖提示（列表 / 收藏 / 仪表盘的小徽标用）。

    返回 {recipe_id: advice}。列表页只用到 tier / tier_label；
    详情页直接用 build_advice() 的完整结果。
    """
    return {r.id: build_advice(r.ingredients, r.servings) for r in recipes}


def generate_form_context(preset=None):
    """生成页上下文：三个维度的选项 + 表单默认值（R4）。

    「饮食目的」的默认值会读用户**最新健康报告**推断：
    报告里有糖代谢异常迹象（血糖/糖化超标，或明确写了糖尿病）就默认选中「控糖」，
    模板会据此说明一句"已根据您的健康报告默认选择控糖"。用户随时能改成别的。

    Args:
        preset: 出错重渲染时把用户已经选好的三维度传回来，别让选择白填。
    """
    user_id = session.get('user_id')
    latest = None
    if user_id:
        latest = HealthReport.query.filter_by(user_id=user_id) \
            .order_by(HealthReport.uploaded_at.desc()).first()

    inferred = infer_purpose(latest.content_text if latest else "")
    ctx = options_for_template()
    ctx['preset'] = preset or {
        'purpose': inferred or DEFAULTS['purpose'],
        'flavor': DEFAULTS['flavor'],
        'meal_time': DEFAULTS['meal_time'],
    }
    # 只有"默认来自报告推断"时才在页面上说明；用户自己选过就不再废话
    ctx['purpose_inferred'] = bool(inferred) and not preset
    return ctx


def check_user_logged_in():
    """未登录守卫：页面跳登录页、接口回 401 JSON（判据统一在 utils.auth）。

    别在这里再手写 `jsonify({'message': '未登录'})` —— 这个蓝图下的
    `/recipes`、`/favorites`、`/meal_plan`、`/generate_recipe` 都是**页面**，
    一律回 JSON 的话，session 过期后用户点导航会看到一屏裸 JSON。
    """
    return login_guard()


@recipe_bp.route('/generate_recipe', methods=['GET', 'POST']) # Add GET
def generate_recipe():
    if request.method == 'GET':
        # Render the generate form page（带 R4 三维度选项与默认值）
        return render_template('recipe/generate.html', **generate_form_context())

    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    # Handle form data or JSON data
    user_query = None
    if request.is_json: # Check if request is JSON
        data = request.get_json()
        user_query = data.get('query', '根据我的情况生成健康食谱')
    else: # Handle form data
        user_query = request.form.get('query', '根据我的情况生成健康食谱')

    # R4 三维度：没传 -> 默认档；传了非法值 -> normalize 收敛。
    # 生成流程不能因为一个脏参数挂掉，所以这一步不做校验、只做兜底。
    tags = normalize_all(request.get_json() if request.is_json else request.form)

    if not user_query:
        if request.is_json:
             return jsonify({'message': '请提供食谱要求'}), 400
        else:
            flash('请提供食谱要求', 'error')
            return render_template('recipe/generate.html',
                                   **generate_form_context(preset=tags))

    # 获取用户最新的健康报告
    latest_health_report = HealthReport.query.filter_by(user_id=user_id) \
        .order_by(HealthReport.uploaded_at.desc()).first()

    # 获取用户最新的冰箱食材
    latest_fridge_image = FridgeImage.query.filter_by(user_id=user_id) \
        .order_by(FridgeImage.uploaded_at.desc()).first()

    # 构建健康报告文本：优先用结构化指标摘要，抽不到指标则退回原文
    health_report_text = build_report_context(
        latest_health_report.content_text if latest_health_report else ""
    )

    # ── 安全闸门 ──────────────────────────────────────────────
    # 用报告**全文**（不截断）做风险扫描；指标达危险水平时不生成食谱，改提示就医。
    risk = check_health_risk(
        latest_health_report.content_text if latest_health_report else ""
    )
    if risk["blocked"]:
        if request.is_json:
            # 错误形状与 routes/api_v1.py 保持一致：code 1001 + data.risk，供前端跳「建议就医」页。
            # 顶层额外保留 risk 字段，兼容可能已存在的旧调用方。
            return jsonify({
                'code': 1001,
                'message': risk['message'],
                'risk': risk,
                'data': {'risk': risk},
            }), 409
        # 网页版由模板内的「建议就医」卡片展示，不再 flash（否则同一段长文案会说两遍）
        return render_template('recipe/generate.html', risk=risk,
                               **generate_form_context(preset=tags))
    # ─────────────────────────────────────────────────────────

    # 构建食材文本 - 修正这里
    ingredients_text = ""
    if latest_fridge_image:
        # Query ingredients associated with the latest fridge image
        fridge_ingredients = Ingredient.query.filter_by(fridge_image_id=latest_fridge_image.id).all()
        if fridge_ingredients:
            ingredients = [ing.name for ing in fridge_ingredients]
            ingredients_text = ", ".join(ingredients)

    # 使用RAG生成食谱
    try:
        recipe_content = generate_recipe_with_rag(
            health_report_text,
            ingredients_text,
            user_query,
            purpose=tags['purpose'],
            flavor=tags['flavor'],
            meal_time=tags['meal_time'],
        )

        # 解析 LLM 生成的食谱内容 — 按段落标题拆分
        title, ingredients, instructions, nutrition_info, health_tip = _parse_recipe_content(recipe_content)

        # 偏高指标提示 + 免责声明，随食谱一并保存
        warn_text = warn_summary(risk)
        if warn_text:
            nutrition_info = (nutrition_info or "") + "\n\n【请留意】" + warn_text
        nutrition_info = (nutrition_info or "") + "\n\n———\n" + DISCLAIMER

        # 保存生成的食谱到数据库（含 R4 三维度标签）
        recipe = Recipe(
            user_id=user_id,
            title=title,
            ingredients=ingredients,
            instructions=instructions,
            nutrition_info=nutrition_info,
            purpose=tags['purpose'],
            flavor=tags['flavor'],
            meal_time=tags['meal_time'],
        )

        db.session.add(recipe)
        db.session.commit()

        # 记下这次的需求，供详情页「换一道」复用（只留最近一条，不占 session 体积）
        session['last_gen_recipe_id'] = recipe.id
        session['last_gen_query'] = user_query

        if request.is_json: # If original request was JSON (e.g., from frontend app)
            return jsonify({
                'message': '食谱生成成功',
                'recipe': recipe.to_dict()
            }), 201
        else: # If request was from HTML form
            flash('食谱生成成功', 'success')
            return redirect(url_for('recipe.get_recipes')) # Redirect to recipes list after success

    except Exception as e:
        current_app.logger.error(f"生成食谱失败: {str(e)}")
        if request.is_json:
            return jsonify({'message': f'生成食谱失败: {str(e)}'}), 500
        else:
            flash(f'生成食谱失败: {str(e)}', 'error')
            return render_template('recipe/generate.html',
                                   **generate_form_context(preset=tags)) # Return to form on error


@recipe_bp.route('/recipes')
def get_recipes():
    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    recipes = Recipe.query.filter_by(user_id=user_id) \
        .order_by(Recipe.created_at.desc()).all()

    return render_template('recipe/list.html', recipes=recipes,
                           advices=advice_map(recipes)) # Render a list page


@recipe_bp.route('/recipes/<int:recipe_id>')
def get_recipe(recipe_id):
    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    recipe = Recipe.query.filter_by(id=recipe_id, user_id=user_id).first()
    if not recipe:
        from flask import abort
        abort(404) # Or render an error page

    return render_template('recipe/detail.html', recipe=recipe,
                           advice=build_advice(recipe.ingredients, recipe.servings),
                           # 步骤：洗掉模型写的 `**备料**` 和行首编号
                           steps=split_steps(recipe.instructions),
                           # 营养：拆成「四个数字 + 一句话 + 可折叠细节」
                           nut=build_nutrition_block(recipe.nutrition_info),
                           # R3：已分享过就把按钮换成「已分享到社区」入口
                           share_post=find_share(user_id, recipe_id)) # Render a detail page


# ── 「升糖标签是怎么来的」（R5 第 4 项 E）────────────────────────
@recipe_bp.route('/gi-method')
def gi_method():
    """依据的完整版：数据来源、分档界线、与国标不一致的条目及理由。

    故意**不要求登录**：这一页是给"凭什么信你"这个问题准备的
    （老师、评委、想自己核对的糖友）。要先注册才能看依据，等于把可信度
    锁在门后。页面里没有任何用户数据 —— 只有食材表和统计数字。
    """
    return render_template('recipe/gi_method.html', m=build_method_page())


# ── 「换一道」 ─────────────────────────────────────────────────
# 生成食谱一次不满意是常态，改造前只能退回生成页把三个选项重填一遍。
# 这里直接用原食谱存着的三维度 + 用户最新报告/食材再生成一份。
DEFAULT_QUERY = '根据我的情况生成健康食谱'


def _latest_ingredients_text(user_id):
    """取用户最近一次冰箱图里认出的食材，拼成逗号串（没有就空串）。"""
    latest = FridgeImage.query.filter_by(user_id=user_id) \
        .order_by(FridgeImage.uploaded_at.desc()).first()
    if not latest:
        return ''
    names = [ing.name for ing in
             Ingredient.query.filter_by(fridge_image_id=latest.id).all()]
    return ', '.join(names)


def _regen_query(recipe):
    """拼「换一道」的用户需求。

    必须显式告诉模型「不要做成刚才那道」—— 否则同样的食材 + 同样的三维度，
    模型很容易换汤不换药给出高度相似的结果。

    用户当初手写的需求走 session（只在同一次浏览会话内有效）：
    食谱表里没有存 query，为了不为此改表结构，先用 session 兜住最常见的情况
    （刚生成完就点换一道）。存不住了就退回通用话术，不影响功能可用。
    """
    base = ''
    if session.get('last_gen_recipe_id') == recipe.id:
        base = session.get('last_gen_query') or ''
    avoid = '不要做成「%s」' % (recipe.title or '刚才那道菜')
    tail = '换一种做法或搭配，食材可以有重合但菜式要明显不同。'
    if base and base != DEFAULT_QUERY:
        return '%s。再来一道不一样的：%s，%s' % (base, avoid, tail)
    return '再推荐一道不一样的菜：%s，%s' % (avoid, tail)


# 路径与详情页同构：详情页是 /api/recipes/recipes/<id>（见 get_recipe），
# 所以这里带 /recipes 前缀（蓝图前缀已有一个 /api/recipes，别删掉这个）
@recipe_bp.route('/recipes/<int:recipe_id>/regenerate', methods=['POST'])
def regenerate_recipe(recipe_id):
    """换一道：沿用原食谱的三维度，重新生成一份不同的。

    网页动作接口，返回扁平 JSON（与社区点赞/收藏一致，直接喂前端 fetch）。
    """
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'message': '未登录', 'redirect_url': url_for('user.login')}), 401

    recipe = Recipe.query.filter_by(id=recipe_id, user_id=user_id).first()
    if not recipe:
        return jsonify({'message': '找不到这道食谱'}), 404

    # 安全闸门照走 —— 换一道不能变成绕过「建议就医」的后门
    latest_report = HealthReport.query.filter_by(user_id=user_id) \
        .order_by(HealthReport.uploaded_at.desc()).first()
    report_text = latest_report.content_text if latest_report else ''
    risk = check_health_risk(report_text)
    if risk['blocked']:
        return jsonify({
            'code': 1001,
            'message': risk['message'],
            'redirect_url': url_for('recipe.generate_recipe'),
        }), 409

    health_report_text = build_report_context(report_text)
    ingredients_text = _latest_ingredients_text(user_id)

    # 老食谱的 purpose 可能是空（改造前生成的），normalize_all 会兜到默认档
    tags = normalize_all({
        'purpose': recipe.purpose,
        'flavor': recipe.flavor,
        'meal_time': recipe.meal_time,
    })

    try:
        recipe_content = generate_recipe_with_rag(
            health_report_text,
            ingredients_text,
            _regen_query(recipe),
            purpose=tags['purpose'],
            flavor=tags['flavor'],
            meal_time=tags['meal_time'],
        )
        title, ingredients, instructions, nutrition_info, _tip = \
            _parse_recipe_content(recipe_content)

        warn_text = warn_summary(risk)
        if warn_text:
            nutrition_info = (nutrition_info or '') + '\n\n【请留意】' + warn_text
        nutrition_info = (nutrition_info or '') + '\n\n———\n' + DISCLAIMER

        new_recipe = Recipe(
            user_id=user_id,
            title=title,
            ingredients=ingredients,
            instructions=instructions,
            nutrition_info=nutrition_info,
            purpose=tags['purpose'],
            flavor=tags['flavor'],
            meal_time=tags['meal_time'],
        )
        db.session.add(new_recipe)
        db.session.commit()

        current_app.logger.info('换一道：原 #%s(%s) -> 新 #%s(%s)',
                                recipe.id, recipe.title, new_recipe.id, new_recipe.title)
        return jsonify({
            'message': '已换一道',
            'recipe_id': new_recipe.id,
            'title': new_recipe.title,
            'redirect_url': url_for('recipe.get_recipe', recipe_id=new_recipe.id),
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('换一道失败（原 #%s）：%s', recipe.id, str(e))
        return jsonify({'message': '换一道失败：%s' % str(e)}), 500


@recipe_bp.route('/favorites', methods=['GET'])
def get_favorites():
    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    # Query favorites and join with recipes to get full recipe data
    favorites = db.session.query(Favorite, Recipe).join(Recipe).filter(Favorite.user_id == user_id).all()
    recipes = [fav_recipe_tuple[1] for fav_recipe_tuple in favorites] # fav_recipe_tuple[1] is the Recipe object

    return render_template('recipe/favorites.html', recipes=recipes,
                           advices=advice_map(recipes)) # Render a favorites list page


@recipe_bp.route('/favorites/<int:recipe_id>', methods=['POST', 'DELETE'])
def toggle_favorite(recipe_id):
    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    # 检查食谱是否存在
    recipe = Recipe.query.get(recipe_id)
    if not recipe:
        return jsonify({'message': '食谱不存在'}), 404

    # 检查是否已经收藏
    favorite = Favorite.query.filter_by(user_id=user_id, recipe_id=recipe_id).first()

    if request.method == 'POST':
        if favorite:
            return jsonify({'message': '已经收藏过该食谱'}), 400

        favorite = Favorite(user_id=user_id, recipe_id=recipe_id)
        db.session.add(favorite)
        db.session.commit()
        return jsonify({'message': '收藏成功'}), 201

    else:  # DELETE
        if not favorite:
            return jsonify({'message': '未收藏该食谱'}), 400

        db.session.delete(favorite)
        db.session.commit()
        return jsonify({'message': '取消收藏成功'}), 200


@recipe_bp.route('/<int:recipe_id>', methods=['GET'])
def get_recipe_api(recipe_id):
    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    recipe = Recipe.query.filter_by(id=recipe_id, user_id=user_id).first()
    if not recipe:
        return jsonify({'message': '食谱不存在'}), 404

    return jsonify(recipe.to_dict())


@recipe_bp.route('/meal_plan', methods=['GET', 'POST'])
def meal_plan():
    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    meal_plan_data = None

    if request.method == 'POST':
        days = int(request.form.get('days', '7'))
        preference = request.form.get('preference', '')

        # 获取用户最新的健康报告
        latest_health_report = HealthReport.query.filter_by(user_id=user_id) \
            .order_by(HealthReport.uploaded_at.desc()).first()

        # 获取用户最新的冰箱食材
        latest_fridge_image = FridgeImage.query.filter_by(user_id=user_id) \
            .order_by(FridgeImage.uploaded_at.desc()).first()

        # 构建上下文：优先用结构化指标摘要，抽不到指标则退回原文
        health_report_text = build_report_context(
            latest_health_report.content_text if latest_health_report else ""
        )

        # 安全闸门：报告指标达危险水平时不出膳食计划，改提示就医
        risk = check_health_risk(
            latest_health_report.content_text if latest_health_report else ""
        )
        if risk["blocked"]:
            # 提示文案由模板内的「建议就医」卡片承载，不用 flash 重复一遍
            return render_template('recipe/meal_plan.html', meal_plan=None, risk=risk)

        ingredients_text = ""
        if latest_fridge_image:
            fridge_ingredients = Ingredient.query.filter_by(
                fridge_image_id=latest_fridge_image.id
            ).all()
            if fridge_ingredients:
                ingredients_list = [ing.name for ing in fridge_ingredients]
                ingredients_text = ", ".join(ingredients_list)

        # 构建膳食计划生成提示（偏高指标要求模型在建议里明确提醒）
        warn_text = warn_summary(risk)
        risk_note = ""
        if warn_text:
            risk_note = (
                f"\n【必须提醒】该报告存在以下偏高指标，"
                f"请在最后的总体健康建议中明确提醒用户：{warn_text}\n"
            )

        # 控糖五餐制提示词统一在 utils/meal_plan_prompt.py（两个入口共用一份，
        # 别在这里再抄一遍 —— 复制出来的那份迟早会和另一份说法不一致）
        meal_prompt = build_meal_plan_prompt(
            days,
            preference=preference,
            health_text=health_report_text,
            ingredients_text=ingredients_text,
            risk_note=risk_note,
        )

        try:
            llm_client = LLMClient()
            # 原来写死 2000：30 天的计划写一半就断，用户看到的是残缺计划
            meal_plan_text = llm_client.generate_text(
                meal_prompt, temperature=0.7, max_tokens=estimate_max_tokens(days))

            # 解析膳食计划
            meal_plan_data = parse_meal_plan(meal_plan_text, days)
        except Exception as e:
            current_app.logger.error(f"生成膳食计划失败: {str(e)}")
            flash(f'生成膳食计划失败: {str(e)}', 'error')

    return render_template('recipe/meal_plan.html', meal_plan=meal_plan_data,
                           disclaimer=DISCLAIMER)


def _parse_recipe_content(text):
    """
    解析 LLM 生成的食谱文本，提取结构化字段。

    支持的段落标题：
    - **菜品名称** / 菜名 / 标题
    - **所需食材** / 食材 / 材料
    - **烹饪步骤** / 步骤 / 做法
    - **营养信息** / 营养 / 营养成分
    - **健康提示** / 提示 / 建议

    Returns:
        (title, ingredients, instructions, nutrition_info, health_tip)
    """
    import re

    title = "智能推荐食谱"
    ingredients = ""
    instructions = ""
    nutrition_info = ""
    health_tip = ""

    lines = text.split('\n')

    # 匹配段落标题 + 同行内容：**菜品名称**：番茄炒蛋
    section_pattern = re.compile(
        r'^(?:\*{1,2}|#{1,3})?\s*'
        r'(菜品名称|菜名|食谱名称|标题|'
        r'所需食材|食材|材料|用料|配料|'
        r'烹饪步骤|步骤|做法|制作方法|烹饪方法|'
        r'营养信息|营养成分|营养价值|营养|'
        r'健康提示|温馨提示|提示|建议|小贴士|健康建议)'
        r'\s*(?:\*{1,2})?\s*'
        r'(?:\：|:)?\s*'
        r'(.*)$',
        re.IGNORECASE
    )

    current_section = None
    section_content = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if section_content:
                section_content.append('')
            continue

        match = section_pattern.match(stripped)
        if match:
            # 保存上一节
            content = '\n'.join(section_content).strip()
            if current_section == 'title':
                title = content or title
            elif current_section == 'ingredients':
                ingredients = content
            elif current_section == 'instructions':
                instructions = content
            elif current_section == 'nutrition':
                nutrition_info = content
            elif current_section == 'health_tip':
                health_tip = content

            # 确定新节
            keyword = match.group(1)
            if any(kw in keyword for kw in ['菜名', '名称', '标题']):
                current_section = 'title'
            elif any(kw in keyword for kw in ['食材', '材料', '用料', '配料']):
                current_section = 'ingredients'
            elif any(kw in keyword for kw in ['步骤', '做法', '制作', '烹饪方法']):
                current_section = 'instructions'
            elif any(kw in keyword for kw in ['营养']):
                current_section = 'nutrition'
            elif any(kw in keyword for kw in ['提示', '建议', '小贴士']):
                current_section = 'health_tip'

            section_content = []

            # 同行剩余文本作为该节第一行内容
            inline_text = match.group(2).strip()
            if inline_text:
                section_content.append(inline_text)
            continue

        # 如果还没遇到任何标题，第一行作为标题
        if current_section is None:
            candidate = stripped.strip('*#：: ')
            if candidate:
                title = candidate[:50]
                current_section = 'title'
            continue

        section_content.append(stripped)

    # 保存最后一节
    content = '\n'.join(section_content).strip()
    if content and current_section:
        if current_section == 'title':
            if not title or title == '智能推荐食谱':
                title = content[:50]
        elif current_section == 'ingredients':
            ingredients = content
        elif current_section == 'instructions':
            instructions = content
        elif current_section == 'nutrition':
            nutrition_info = content
        elif current_section == 'health_tip':
            health_tip = content

    # 如果 health_tip 有内容但 nutrition_info 为空，可能是 LLM 把营养信息放到了 health_tip
    if health_tip and not nutrition_info:
        nutrition_info = health_tip
        health_tip = ""

    # 拼合营养信息 + 健康提示
    if health_tip:
        nutrition_info = (nutrition_info + '\n\n💡 健康提示：' + health_tip).strip()

    return title, ingredients, instructions, nutrition_info, health_tip


# ── 膳食计划解析（控糖五餐制） ─────────────────────────────────
# 餐次关键词 → (type, 标准中文名)。
# ⚠️ **长键必须排在短键前面**，且必须包含「上午加餐 / 下午加餐」：
#    改造前只认 {早餐, 午餐, 晚餐, 加餐} 且用 startswith 判断，
#    模型写「上午加餐：」时不以「加餐」开头 → **整行被丢掉**，
#    五餐制下会静默少两餐，页面上看起来"只有三餐"，极难发现。
_MEAL_KEYS = [
    ('上午加餐', 'snack_am', '上午加餐'),
    ('下午加餐', 'snack_pm', '下午加餐'),
    ('早餐', 'breakfast', '早餐'),
    ('早饭', 'breakfast', '早餐'),
    ('午餐', 'lunch', '午餐'),
    ('午饭', 'lunch', '午餐'),
    ('晚餐', 'dinner', '晚餐'),
    ('晚饭', 'dinner', '晚餐'),
    ('加餐', 'snack', '加餐'),
]
_MEAL_ORDER = ['breakfast', 'snack_am', 'lunch', 'snack_pm', 'dinner', 'snack']

# 日标题：模型实际会写「第1天」「第 1 天」「第1 天」「Day 1」「## 第 1 天：」
# 原实现用 f'第{i}天' 精确匹配，"第 1 天"（带空格）**一天都认不出来**，
# 整份计划会被解析成空 → 页面显示"生成失败"。这里统一用正则。
_DAY_RE = re.compile(r'(?:第\s*(\d{1,2})\s*天|\b(?:Day|DAY|day)\s*(\d{1,2})\b)')

# 碳水标注：「（碳水约 45g）」「碳水：45 克」「约45g碳水」
_CARB_RE = re.compile(r'碳水[^0-9]{0,6}(\d{1,3}(?:\.\d+)?)\s*(?:g|G|克)?')
_CARB_RE_ALT = re.compile(r'(\d{1,3}(?:\.\d+)?)\s*(?:g|G|克)\s*[^。；，,]{0,3}碳水')
# 把括号里的碳水标注整段剥掉（页面上用碳水徽章显示，避免描述里再重复一遍）
_CARB_BRACKET_RE = re.compile(r'[（(][^（()）]{0,4}碳水[^（()）]{0,24}[)）]')


def _clean_meal_line(line):
    """去掉 markdown 装饰与全角空格（模型爱写 **早餐**：或 - 早餐：）。"""
    return line.strip().lstrip('#*-+•· \t\u3000').strip()


def _extract_carb(text):
    """从一行里抽出碳水克数，并把这处标注从文本里剥掉。

    返回 (carb 或 None, 清理后的文本)。
    **抽不到就返回 None，绝不猜** —— 数字错了比没有数字更糟。
    """
    s = text or ''
    m = _CARB_RE.search(s) or _CARB_RE_ALT.search(s)
    if not m:
        return None, s.strip()
    try:
        val = float(m.group(1))
    except (TypeError, ValueError):
        return None, s.strip()
    if val <= 0 or val > 300:      # 明显不合理（串行 / 胡说）直接丢
        return None, s.strip()
    cleaned = _CARB_BRACKET_RE.sub('', s)
    if cleaned == s:
        cleaned = re.sub(r'碳水[^0-9]{0,6}\d{1,3}(?:\.\d+)?\s*(?:g|G|克)?', '', s)
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned).strip(' -，,；;、')
    return (int(val) if val == int(val) else val), cleaned


def _match_meal(line):
    """判断这一行是不是某一餐。返回 (type, type_cn, 正文) 或 None。"""
    for key, en, cn in _MEAL_KEYS:
        # 允许行首是「上午加餐」或「**上午加餐**：」这类已清理过的形式
        if not line.startswith(key):
            continue
        rest = line[len(key):].lstrip('*_` \u3000')
        if not rest or rest[0] not in '：:':
            continue
        body = rest[1:].strip()
        return en, cn, body
    return None


def parse_meal_plan(text, days=7):
    """把模型生成的控糖膳食计划文本解析成结构化数据。

    返回：
        {'days': [{'day': 1, 'meals': [...], 'carb_total': 152}, ...],
         'advice': '...'}

    每餐字段：type / type_cn / name / description / carb（碳水克数，可能为 None）

    兼容性：老版本（三餐、不带碳水）生成的文本仍能解析 —— 只是没有 carb 与 carb_total，
    模板那边用 `if meal.carb` 判断，不会显示空徽章。
    """
    if not text:
        return None

    plan = {'days': [], 'advice': ''}

    current_day_data = None
    in_advice = False

    for raw in text.split('\n'):
        line = _clean_meal_line(raw)
        if not line:
            continue

        # ---- 日标题 ----
        dm = _DAY_RE.search(line)
        if dm and not in_advice:
            n = dm.group(1) or dm.group(2)
            try:
                day_no = int(n)
            except (TypeError, ValueError):
                day_no = None
            if day_no:
                if current_day_data:
                    plan['days'].append(current_day_data)
                current_day_data = {'day': day_no, 'meals': [], 'carb_total': None}
                in_advice = False
                continue

        # ---- 某一餐 ----
        if current_day_data is not None and not in_advice:
            hit = _match_meal(line)
            if hit:
                en, cn, body = hit
                carb, body = _extract_carb(body)
                name, desc = body, ''
                if ' - ' in body:
                    name, desc = [x.strip() for x in body.split(' - ', 1)]
                elif '——' in body:
                    name, desc = [x.strip() for x in body.split('——', 1)]
                elif '：' in body or ':' in body:
                    sep = '：' if '：' in body else ':'
                    name, desc = [x.strip() for x in body.split(sep, 1)]

                # 同一天里同一餐只留第一条：模型偶尔把早餐写两遍，
                # 都渲染出来会让页面出现两行"早餐"，看起来像崩了。
                if any(m['type'] == en for m in current_day_data['meals']):
                    continue

                current_day_data['meals'].append({
                    'type': en,
                    'type_cn': cn,
                    'name': name,
                    'description': desc,
                    'carb': carb,
                })
                continue

        # ---- 总体建议 ----
        if '建议' in line or '注意' in line or 'advice' in line.lower():
            in_advice = True
            plan['advice'] += line + '\n'
            continue

        if in_advice:
            plan['advice'] += line + '\n'

    if current_day_data:
        plan['days'].append(current_day_data)

    # 收尾：按五餐顺序排（模型偶尔把加餐写在晚餐后面）、算全天碳水合计
    order = {k: i for i, k in enumerate(_MEAL_ORDER)}
    for day in plan['days']:
        day['meals'].sort(key=lambda m: order.get(m['type'], 99))
        carbs = [m['carb'] for m in day['meals'] if m.get('carb')]
        day['carb_total'] = int(sum(carbs)) if carbs else None

    # 建议段落去掉 markdown 装饰：模型爱写「**总体控糖建议：**」，
    # 模板是当纯文本渲染的（| safe 只用于换行），星号会原样显示出来。
    advice = re.sub(r'\*\*|__', '', plan['advice']).strip()
    # 去掉和模板标题重复的第一行「总体控糖建议：」——
    # 卡片上已经有一个 emoji 标题了，正文再来一行一模一样的小标题很蠢。
    # 判定条件收紧（短、含"建议"、不含数字），避免把真正的第一条建议误删。
    adv_lines = [ln for ln in advice.split('\n') if ln.strip()]
    if adv_lines and len(adv_lines[0].strip()) <= 20 \
            and '建议' in adv_lines[0] and not re.search(r'\d', adv_lines[0]):
        adv_lines = adv_lines[1:]
    plan['advice'] = '\n'.join(ln.strip() for ln in adv_lines).strip()
    return plan
