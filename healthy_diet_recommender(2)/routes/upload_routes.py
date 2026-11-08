# routes/upload_routes.py
from flask import Blueprint, request, jsonify, session, current_app, render_template, redirect, \
    url_for  # Add redirect here
from html import escape
from utils.file_handler import save_uploaded_file, allowed_file_health_report, allowed_file_fridge_image
from utils.yolo_detector import detect_ingredients
from models.health_report import HealthReport
from models.fridge_image import FridgeImage
from models.ingredient import Ingredient
from utils.database import db
from utils.auth import login_guard
from utils.report_parser import (detect_metric_names, label_of,
                                 CORE_DIABETES_KEYS, METABOLIC_KEYS)
from utils.report_ocr import extract_report_text
from utils.checkin import build_card_context
import io

upload_bp = Blueprint('upload', __name__)


def check_user_logged_in():
    """未登录守卫：页面跳登录页、接口回 401 JSON（判据统一在 utils.auth）。

    本蓝图两个入口都是**页面**（`/upload_fridge_image`、`/upload_health_report`，
    浏览器表单提交），session 过期时必须跳登录页，不能把裸 JSON 甩给用户。
    """
    return login_guard()


def build_page_metrics(user_id, selected_key=None):
    """组装本页指标区的展示数据。

    失败只记日志、返回 None —— 指标画不出来是小事，不能让"上传报告"这个主流程挂掉。
    """
    try:
        from utils.metric_view import build_metrics_context
        return build_metrics_context(user_id, selected_key=selected_key)
    except Exception as exc:                       # noqa: BLE001
        print(f"[metric_view] 用户 {user_id} 指标展示组装失败：{exc}")
        return None


def latest_report_source(user_id):
    """取该用户**最近上传的那份报告**，连同"系统到底读到了什么"一起交给模板。

    为什么要单独给这个
        指标区为空时，用户没法分辨是哪种情况：
          ① 还没传过报告；
          ② 传了，但这份报告里本来就没有可抽的数字（胃镜 / 影像 / 病理那种叙述型报告）；
          ③ 传的文件根本没读到文字（扫描件、图片型 PDF、解析报错）。
        以前只有"刚上传的那一次"才说第 ② 种，用户一刷新或从别处点回来，
        页面又变回"还没有可展示的指标 / 上传第一份体检报告后…"，看着就像上传功能坏了。
        把最近那份报告**系统实际读到的文字**摆出来，用户自己一眼就能判断是哪种 ——
        这比任何文案都管用。

    返回 None 表示"这个用户还没传过任何报告"。
    失败只记日志、返回 None —— 原文是辅助信息，绝不能连累上传主流程。
    """
    if not user_id:
        return None
    try:
        report = (HealthReport.query
                  .filter(HealthReport.user_id == user_id)
                  .order_by(HealthReport.id.desc())
                  .first())
    except Exception as exc:                       # noqa: BLE001
        print(f"[report_source] 用户 {user_id} 取最近报告失败：{exc}")
        return None
    if report is None:
        return None

    text = (report.content_text or '').strip()
    # 上传时如果 PDF / 图片没解析成功，会往 content_text 里写 "[无法解析PDF内容]" 这类占位符。
    # 它看着有字，但不是报告原文 —— 单独归到"没读到文字"，别摆在原文框里冒充正文。
    failed = text.startswith('[') and text.endswith(']') and len(text) < 40

    if failed:
        note = '上传时没能解析这份文件（PDF 读取失败，或图片识别不可用）。'
        shown, shown_len = '', 0
    elif not text:
        note = '这份文件里没有提取到任何文字 —— 可能是扫描件或图片型 PDF（没有文字层）。'
        shown, shown_len = '', 0
    else:
        note = ''
        shown, shown_len = text, len(text)

    # 空态文案在这里拼好，而不是在模板里用 ~ 拼 ——
    # Jinja 的 `~` 会把参与拼接的普通字符串整体转义（只要其中一项是 `| e` 的 Markup），
    # 结果文案里的 <br>/<b> 会变成字面量。所以带用户数据的文案统一在 Python 里组装，
    # 模板只做 `{% set %}` 引用、输出时按 HTML 处理。
    #
    # 2026-10-14：不再只说"没读出指标"，而是按**报告里到底提没提到这些指标**分三种说法 ——
    # 光说"没读出"，用户分不清是自己传错了、还是系统坏了。判据是 detect_metric_names()：
    # 只看指标名，不做任何医学判断。
    # ⚠️ 措辞红线：一份胃镜报告**证明不了**血糖正常，所以这里说"看不出血糖情况"，
    #    绝不说"您没有糖尿病"。要不要下医学结论，由医师定。
    safe_name = escape(report.filename or "")
    mentioned = detect_metric_names(text) if text else []
    core_hit = [k for k in mentioned if k in CORE_DIABETES_KEYS]
    other_hit = [k for k in mentioned if k in METABOLIC_KEYS and k not in CORE_DIABETES_KEYS]

    if failed or not text:
        # ① 根本没读到文字 —— 与报告类型无关，是文件 / 解析的问题
        empty_text = (
            f'最近上传的《{safe_name}》里没有读到任何文字，因此看不出任何指标。<br>'
            '如果是拍照或扫描件，建议换医院导出的电子版 PDF 再传一次 —— '
            '<b>读不出时我们不会猜</b>。'
        )
    elif core_hit:
        # ② 血糖 / 糖化写了却没读出来 —— 是系统没认出来，得坦白说，别让用户以为报告里没有
        names = '、'.join(label_of(k) for k in core_hit[:4])
        empty_text = (
            f'最近上传的《{safe_name}》里提到了{names}，但系统没能读出具体数值。<br>'
            '多半是这份报告的排版比较特殊（比如表格被打散）—— '
            '<b>读不出时我们不会猜</b>，下面是系统实际读到的内容，你可以核对。'
        )
    else:
        # ③ 没有血糖 / 糖化这类指标 —— 胃镜、影像、病理报告都落在这里，
        #    只提到血脂血压的也落在这里。这种情况必须明确说"看不出血糖情况"，
        #    并给出出路（传血糖 / 生化报告单），否则用户会以为系统坏了。
        extra = ''
        if other_hit:
            extra = f'（报告里出现的是{"、".join(label_of(k) for k in other_hit[:4])}）'
        empty_text = (
            f'最近上传的《{safe_name}》里没有血糖、糖化血红蛋白这类糖尿病相关指标{extra}。<br>'
            '这多半是报告类型不同 —— 胃镜、影像、病理这类报告本身就不含血糖数据，'
            '<b>就这份报告而言，看不出血糖 / 糖尿病方面的情况</b>。<br>'
            '想了解糖尿病风险，请上传血糖或生化检验报告单。'
        )

    return {
        'filename': report.filename or '',
        # ⚠️ uploaded_at 存的是 UTC（models 里 default=datetime.utcnow），
        #    这里与 dashboard 的显示口径保持一致、不做时区换算，免得同一个时间两处不一样。
        'uploaded_text': (report.uploaded_at.strftime('%Y-%m-%d %H:%M')
                          if report.uploaded_at else ''),
        'text': shown,
        'note': note,
        'text_len': shown_len,
        'empty_text': empty_text,
    }


@upload_bp.route('/upload_health_report', methods=['GET', 'POST']) # Add GET
def upload_health_report():
    """「健康报告」页 —— 上传与看指标是**同一个页面**（2026-09-15 合并）。

    以前上传完会 redirect 到仪表盘，指标在另一个 /health/metrics 页看，
    结果是"上传"和"看指标"两个入口、两个页面。现在只有这一个页面：
    上传区在上，指标区在下，上传成功原地多出提示，不跳走。

    2026-10-12 补充：指标区为空时不再只说"还没上传过"。
    见 latest_report_source() —— 读不出数字时，页面要说明**是哪份报告读不出**，
    并把系统实际读到的原文摆出来，否则用户会以为上传功能坏了。
    """
    from flask import flash
    from utils.risk_guard import check_health_risk, warn_summary, DISCLAIMER
    from utils.visit_advice import build_visit_advice

    def render_page(risk=None):
        """本页唯一的渲染出口。所有分支（含各种出错）都走它，
        免得某个分支漏传 metrics，页面就少一截。"""
        user_id = session.get('user_id')
        return render_template('upload/upload_health.html',
                               metrics=build_page_metrics(user_id,
                                                          selected_key=request.args.get('key')),
                               # R5 每日记录：本页也放一张（用户来"看指标"时顺手就能补今天的数据）。
                               # 与仪表盘共用 templates/health/_checkin_card.html，不另写一份。
                               checkin=build_card_context(
                                   user_id,
                                   action_url=url_for('checkin.save'),
                                   next_url=request.full_path.rstrip('?'),
                               ) if user_id else None,
                               # 指标区为空时，页面靠它说清"是没传过，还是这份读不出数字"，
                               # 并把系统读到的原文摆出来给用户核对（GET 刷新后也一样）。
                               latest_report=latest_report_source(user_id),
                               # R5 第 3 项 就诊沟通清单：偏高 / 连续偏高才会 show，
                               # 判档与阈值全部走 utils.visit_advice（与拦截逻辑共用一份）。
                               visit=build_visit_advice(user_id) if user_id else None,
                               risk=risk,
                               disclaimer=DISCLAIMER)

    if request.method == 'GET':
        # GET 也要把指标读出来：合并成一页之后，历史指标只有这里能看。
        # ?key=xxx 决定趋势图画哪个指标（切换标签就指回本路由）。
        return render_page()

    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    if 'file' not in request.files:
        flash('没有文件部分', 'error')
        return render_page()
    file = request.files['file']
    if file.filename == '':
        flash('没有选择文件', 'error')
        return render_page()
    if not (file and allowed_file_health_report(file.filename)):
        flash('文件类型不被允许', 'error')
        return render_page()

    filepath, filename = save_uploaded_file(file, current_app.config['UPLOAD_FOLDER_HEALTH_REPORTS'])
    if not filepath:
        flash('文件保存失败', 'error')
        return render_page()

    content_text, _method = extract_report_text(filepath, filename)
    # 解析块统一交给了 utils/report_ocr（PDF 文字层 → 扫描件走视觉模型 → 图片走视觉模型）。
    # 只有"文件打开就炸"这类硬失败才需要占位符；"读不出文字"请保持空串 ——
    # latest_report_source() 对空串和占位符说的是两回事（前者=没有文字层，后者=解析报错）。
    if _method == 'none' and filename.lower().endswith('.pdf'):
        content_text = "[无法解析PDF内容]"

    health_report = HealthReport(
        user_id=user_id,
        filename=filename,
        filepath=filepath,
        content_text=content_text
    )
    db.session.add(health_report)
    db.session.commit()

    # 指标落库：报告上传成功后，顺手把里面的数字抽出来存进 health_metric
    # 安全兜底：落库失败只记日志，不让用户刚上传的报告白传
    try:
        from utils.metric_store import save_metrics_for_report
        result = save_metrics_for_report(health_report)
        if result.get('error'):
            print(f"[metric_store] 报告 {health_report.id} 指标落库失败：{result['error']}")
        else:
            print(f"[metric_store] 报告 {health_report.id} 落库 {result['saved']} 项指标")
    except Exception as exc:                       # noqa: BLE001
        print(f"[metric_store] 报告 {health_report.id} 指标落库异常：{exc}")

    # 风险扫描：报告一落地就告知，别等用户点"生成食谱"才发现。
    # 用**全文**扫描（不是喂模型用的截断文本），否则危值可能落在截断之外。
    risk = check_health_risk(content_text, context="upload")

    flash('健康报告上传成功', 'success')
    if risk['level'] == 'warn':
        flash('⚠️ ' + warn_summary(risk), 'warning')

    # 同样是本页渲染（不 redirect）：报告已经传上去了，用户此刻最想看"这份报告读出了什么数字"。
    # 一跳走，成功提示和刚算出来的指标就都看不到了。
    # 注意 risk 只在 blocked 时交给模板 —— 偏高走 flash 提示，不在页面上再摆一张卡片。
    return render_page(risk=risk if risk['blocked'] else None)


def _fridge_detect_message(detections):
    """冰箱图识别完给用户的提示 → (文案, 类别)。

    2026-10-23 之前这里是**无条件** flash('…检测成功', 'success')：
    识别出 0 项（大模型限流 / 图太糊 / 没装 YOLO 兜底）也一样说"成功"，
    用户看到的是"检测成功，共 0 项"，只会以为系统坏了。
    现在按结果分开说，并且 0 项时告诉他下一步该干什么。
    """
    items = list(detections or [])

    if not items:
        # ⚠️ 别承诺"可以手动添加"—— 项目里没有新增/编辑食材的入口（2026-10-23 核过），
        #    说了用户会去找那个按钮。
        return ('照片已上传，但这次没认出食材。'
                '试试离近一点、光线亮一点，或者拍整个冰箱内部再传一张。', 'warning')

    names = [str(d.get('name', '')).strip() for d in items if isinstance(d, dict)]
    names = [n for n in names if n]
    shown = '、'.join(names[:5])
    more = '' if len(names) <= 5 else ' 等 %d 项' % len(names)
    return ('已认出 %d 种食材：%s%s' % (len(names) or len(items), shown, more), 'success')


@upload_bp.route('/upload_fridge_image', methods=['GET', 'POST']) # Add GET
def upload_fridge_image():
    if request.method == 'GET':
        # Render the upload form page
        return render_template('upload/upload_fridge.html')

    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    if 'file' not in request.files:
        from flask import flash
        flash('没有文件部分', 'error')
        return render_template('upload/upload_fridge.html')
    file = request.files['file']
    if file.filename == '':
        from flask import flash
        flash('没有选择文件', 'error')
        return render_template('upload/upload_fridge.html')
    if file and allowed_file_fridge_image(file.filename):
        filepath, filename = save_uploaded_file(file, current_app.config['UPLOAD_FOLDER_FRIDGE_IMAGES'])
        if filepath:
            detections = detect_ingredients(filepath)

            fridge_image = FridgeImage(
                user_id=user_id,
                filename=filename,
                filepath=filepath
            )
            db.session.add(fridge_image)
            db.session.flush()

            for det in detections:
                ingredient = Ingredient(
                    fridge_image_id=fridge_image.id,
                    name=det['name'],
                    confidence=det['confidence']
                )
                db.session.add(ingredient)

            db.session.commit()

            from flask import flash
            message, category = _fridge_detect_message(detections)
            flash(message, category)
            return redirect(url_for('dashboard')) # Now redirect should work
        else:
            from flask import flash
            flash('文件保存失败', 'error')
            return render_template('upload/upload_fridge.html')
    else:
        from flask import flash
        flash('文件类型不被允许', 'error')
        return render_template('upload/upload_fridge.html')