# routes/health_routes.py
"""
「健康指标」—— 2026-09-15 起已**并入**「健康报告」页，本蓝图只做兼容跳转。

为什么合并
    原先「上传报告」和「健康指标」是两个入口、两个页面，用户上传完还要再点一次才看得到数字。
    现在只有一个页面：`/api/upload/upload_health_report`（导航里叫「健康报告」），
    上传区在上、指标区在下，上传成功原地展示。

为什么还留着这个路由，而不是直接删掉
    它仍被三处引用，删了就会 404：
      - 历史链接 / 书签
      - `templates/health/_risk_alert.html` 的默认按钮（generate / meal_plan 页在用）
      - `templates/health/_metrics_panel.html` 的指标切换标签（调用方传 mp_endpoint 覆盖，
        没传时的默认值仍指向这里）
    所以保留路径、只把内容改成 302 跳到合并后的页面，**并保留 ?key=**，
    否则"点某个指标看趋势"的链接会丢掉选中的指标。

`templates/health/metrics.html`（旧的独立页模板）已随合并**删除**——
路由已改成跳转，没有任何代码再渲染它；指标面板本身抽在 `templates/health/_metrics_panel.html`，
由「健康报告」页 include，功能没有丢。
"""
from flask import Blueprint, redirect, request, url_for

from utils.auth import login_required

health_bp = Blueprint('health', __name__)


@health_bp.route('/metrics', methods=['GET'])
@login_required
def metrics_page():
    """兼容旧地址：跳到合并后的「健康报告」页，保留选中的指标。"""
    # request.args 是 MultiDict，** 展开可直接还原 ?key=xxx 等参数
    target = url_for('upload.upload_health_report', **request.args)
    return redirect(target + ('#metric-trend' if request.args.get('key') else ''), code=302)
