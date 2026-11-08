# test_health_metrics_page.py
"""
「健康报告」页（上传 + 看指标，已合并）自测 —— 造临时数据库，不碰真实 app.db

背景（2026-09-15 改动）
    原先「上传报告」和「健康指标」是两个入口、两个页面。现在只有一个页面：
    `/api/upload/upload_health_report`（导航里叫「健康报告」），上传区在上、指标区在下。
    旧的 `/health/metrics` 保留为 302 跳转，免得历史链接和 `_risk_alert` 的默认按钮 404。

本文件负责
    1. 合并页 **GET** 时的渲染：卡片、判档标签、趋势折线、切换指标、非法 key 回退、
       无数据空状态、免责声明 —— 也就是"不进上传流程、直接打开就能看到的那些"。
    2. 旧地址 `/health/metrics` 的行为：未登录跳登录页；登录后 302 且**保留 ?key=**。
    3. 用项目真实的 app（`app.py`）确认路由已注册。

    「上传之后就地出指标」由 `test_upload_metrics_inline.py` 覆盖（那条链要 mock OCR/PDF）。

为什么分两段
    临时库能随便造多点数据（验证"折线"必须 ≥2 个点），且完全不写真实数据；
    真实 app 那一段只做只读校验，证明 `create_app()` 的接线没问题。

用法
    python test_health_metrics_page.py
"""
import os
import sys
import tempfile
from datetime import datetime

from flask import Flask

from utils.database import db
from utils.metric_store import save_metrics_for_text
from models.user import User                     # noqa: F401
from models.health_report import HealthReport     # noqa: F401
from models.health_metric import HealthMetric

# 与 app.py::create_app() 保持一致的蓝图清单
from routes.user_routes import user_bp
from routes.upload_routes import upload_bp
from routes.recipe_routes import recipe_bp
from routes.tts_routes import tts_bp
from routes.voice_routes import voice_bp
from routes.wechat_routes import wechat_bp
from routes.api_v1 import api_v1_bp
from routes.health_routes import health_bp
from routes.nutrition_routes import nutrition_bp
from routes.community_routes import community_bp
from routes.checkin_routes import checkin_bp   # R5 每日打卡（「健康报告」页上的那张卡片）

# 合并后的「健康报告」页地址
PAGE = '/api/upload/upload_health_report'
# 旧地址（现在只做跳转）
OLD_PAGE = '/health/metrics'


def build_isolated_app(db_path):
    """搭一个指向临时库的完整应用（模板 / 静态目录都用项目自己的）。

    注意：蓝图清单必须与 `app.py::create_app()` 同步。
    `base.html` 的导航里有 `url_for(...)`，少注册一个蓝图 → 渲染任何继承了 base 的页面都会 BuildError。
    新增页面蓝图时，这里要跟着加一行。
    """
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path.replace('\\', '/')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.secret_key = 'test-secret-key'
    db.init_app(app)

    app.register_blueprint(user_bp, url_prefix='/api/user')
    app.register_blueprint(upload_bp, url_prefix='/api/upload')
    app.register_blueprint(recipe_bp, url_prefix='/api/recipes')
    app.register_blueprint(tts_bp, url_prefix='/api/tts')
    app.register_blueprint(voice_bp, url_prefix='/api/voice')
    app.register_blueprint(wechat_bp, url_prefix='/api/wechat')
    app.register_blueprint(api_v1_bp, url_prefix='/api/v1')
    app.register_blueprint(health_bp, url_prefix='/health')
    app.register_blueprint(nutrition_bp, url_prefix='/nutrition')
    app.register_blueprint(community_bp, url_prefix='/api/community')
    # R5：upload_health.html 里的打卡卡片要 url_for('checkin.save')，漏注册就是 BuildError
    app.register_blueprint(checkin_bp, url_prefix='/api/checkin')

    # base.html 里 url_for('dashboard') 用到，真实项目里定义在 create_app 内
    @app.route('/dashboard')
    def dashboard():
        return 'dashboard'

    with app.app_context():
        db.create_all()
    return app


# 三次就诊记录：血糖逐步升高，最后一次到危险水平
_SEED = [
    (101, datetime(2026, 7, 1, 8, 0),
     "身高 172cm 体重 78kg 血压 145/92 mmHg 空腹血糖 8.9 mmol/L 总胆固醇 6.4 mmol/L"),
    (102, datetime(2026, 8, 10, 8, 0),
     "空腹血糖 7.6 mmol/L 总胆固醇 6.1 mmol/L"),
    (103, datetime(2026, 9, 1, 8, 0),
     "空腹血糖 17.2 mmol/L 总胆固醇 5.9 mmol/L"),
]


def main():
    passed = 0
    total = 0

    def check(name, condition, detail=''):
        nonlocal passed, total
        total += 1
        if condition:
            passed += 1
            print(f'[PASS] {name}')
        else:
            print(f'[FAIL] {name}   {detail}')

    print('=' * 78)
    print('「健康报告」页（合并后）自测')
    print('=' * 78)

    tmpdir = tempfile.mkdtemp(prefix='health_page_test_')
    db_path = os.path.join(tmpdir, 't.db')
    app = build_isolated_app(db_path)

    try:
        with app.app_context():
            for report_id, when, text in _SEED:
                result = save_metrics_for_text(1, text, report_id=report_id, measured_at=when)
                assert result['error'] is None, result
            print(f'临时库已造：{HealthMetric.query.count()} 行指标（3 份报告）')
        print('-' * 78)

        client = app.test_client()

        # ══════════ 1. 未登录 ══════════
        resp = client.get(OLD_PAGE)
        check('旧地址未登录跳转登录页', resp.status_code == 302, str(resp.status_code))

        # ══════════ 2. 合并页：GET 就能看到指标（不用先上传） ══════════
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get(PAGE)
        html = resp.get_data(as_text=True)
        check('合并页返回 200', resp.status_code == 200, str(resp.status_code))
        check('标题是「健康报告」（不再是「健康指标」）', '🩺 健康报告' in html)
        check('上传区与指标区在同一页', 'id="selectBtn"' in html and 'class="metric-grid"' in html)
        check('两个区各有小标题', '上传新报告' in html and '我的健康指标' in html)
        check('列出指标卡片', '空腹血糖' in html and '总胆固醇' in html and '收缩压' in html)
        check('最新值正确（血糖 17.2）', 'metric-value">17.2' in html, '未找到 17.2')
        check('显示测量日期', '09-01' in html)

        # ══════════ 3. 一行概览（"分析"的部分：只数数，不下医学结论） ══════════
        check('出现指标概览', '指标概览' in html)
        check('概览含总项数', '项指标' in html)
        check('概览点出有几项需就医', '项需就医' in html)
        check('判不出级别的算"未判定口径"，不算正常', '未判定口径' in html)

        # ══════════ 4. 判档标签 ══════════
        check('血糖危值标红「需就医」', '需就医' in html)
        check('卡片带 critical 样式', 'is-critical' in html)

        # ══════════ 5. 趋势图 ══════════
        check('渲染出 SVG 折线图', '<svg' in html and '<polyline' in html)
        check('折线有 3 个点', html.count('<circle') >= 3, str(html.count('<circle')))
        check('显示数据点数量', '共 3 个数据点' in html)
        check('显示与上次对比', '较上次' in html and '↑' in html)

        # ══════════ 6. 切换指标：标签必须指回本页，不能跳去别的页面 ══════════
        check('切换标签指向本页（不再指 /health/metrics）',
              f'href="{PAGE}?key=' in html, '切换标签的 href 不是本页')
        resp = client.get(PAGE + '?key=height')
        html2 = resp.get_data(as_text=True)
        check('切换指标返回 200', resp.status_code == 200, str(resp.status_code))
        check('切换后标题变成身高', '身高 趋势' in html2, '标题未变')
        check('单点数据给出提示', '当前只有 1 个数据点' in html2)
        check('单点不画折线', '<polyline' not in html2)

        # 总胆固醇有 3 个点 → 应当画折线
        resp = client.get(PAGE + '?key=total_cholesterol')
        html2b = resp.get_data(as_text=True)
        check('三点指标仍画折线', '<polyline' in html2b and '共 3 个数据点' in html2b)

        # ══════════ 7. 非法 key 回退到默认 ══════════
        resp = client.get(PAGE + '?key=不存在的指标')
        html3 = resp.get_data(as_text=True)
        check('非法 key 回退默认且不报错',
              resp.status_code == 200 and '空腹血糖 趋势' in html3, str(resp.status_code))

        # ══════════ 8. 旧地址：登录后跳到合并页，且保留 ?key= ══════════
        resp = client.get(OLD_PAGE)
        check('旧地址 302 跳到合并页', resp.status_code == 302, str(resp.status_code))
        check('跳转目标就是合并页',
              PAGE in (resp.headers.get('Location') or ''), resp.headers.get('Location') or '')
        resp = client.get(OLD_PAGE + '?key=total_cholesterol')
        loc = resp.headers.get('Location') or ''
        check('跳转保留 ?key=（否则切换指标会丢）',
              'key=total_cholesterol' in loc, loc)

        # ══════════ 9. 没有任何指标的用户 ══════════
        with client.session_transaction() as sess:
            sess['user_id'] = 2
        resp = client.get(PAGE)
        html4 = resp.get_data(as_text=True)
        check('无数据用户返回 200', resp.status_code == 200, str(resp.status_code))
        check('无数据时说明"还没有可展示的指标"', '还没有可展示的指标' in html4)
        check('无数据时不画空卡片', 'class="metric-grid"' not in html4)
        check('无数据时上传表单仍在（这是唯一的入口）', 'id="selectBtn"' in html4)
        check('页面带免责声明', '不能替代医师' in html4)

        # ══════════ 10. 导航只剩一个入口 ══════════
        check('导航里叫「健康报告」', '>健康报告</a>' in html4)
        check('导航里不再有独立的「健康指标」入口', '>健康指标</a>' not in html4)

    finally:
        try:
            os.remove(db_path)
            os.rmdir(tmpdir)
        except OSError:
            pass

    # ══════════ 11. 真实 app 接线校验（只读） ══════════
    print('-' * 78)
    try:
        from app import app as real_app
        rules = {r.rule for r in real_app.url_map.iter_rules()}
        check('真实 app 已注册 /health/metrics（保留为跳转）', '/health/metrics' in rules,
              str(sorted(rules))[:200])
        check('真实 app 已注册合并页', PAGE in rules)

        real_client = real_app.test_client()
        resp = real_client.get(OLD_PAGE)
        check('真实 app 未登录也跳转', resp.status_code == 302, str(resp.status_code))

        with real_client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = real_client.get(PAGE)
        real_html = resp.get_data(as_text=True)
        check('真实数据下合并页能渲染', resp.status_code == 200, str(resp.status_code))
        check('真实数据下出现指标区（卡片或空状态二选一）',
              'class="metric-grid"' in real_html or '还没有可展示的指标' in real_html)
    except Exception as exc:                                   # noqa: BLE001
        check('真实 app 校验（导入失败）', False, f'{type(exc).__name__}: {exc}')

    print('-' * 78)
    print(f'通过 {passed}/{total}')
    return passed == total


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
