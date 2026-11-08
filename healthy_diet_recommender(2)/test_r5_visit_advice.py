# test_r5_visit_advice.py
"""R5 第 3 项「就诊沟通清单」页面级自测 —— 造临时数据库，不碰真实 app.db

这条功能的口径是**做就诊沟通辅助、不做用药推荐**（用户 2026-10-20 拍板）。
所以本文件除了"卡片能不能出来"，还专门盯着几条**红线**：

    1. 页面里不出现具体药名、不出现剂量写法、不出现"建议服用 / 你应该吃"；
    2. 免责声明必须与内容同屏 —— 也就是渲染出来在最后一个 </details> **之后**，
       而不是被折进 <details> 里（与"血糖偏高提示不许折叠"同一条规矩）；
    3. 指标正常时整块不出现（不能变成每天挂着的一张吓人卡片）。

用法
    "C:/Users/123/AppData/Local/Programs/Python/Python314/python.exe" test_r5_visit_advice.py
"""
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta

from flask import Flask

from utils.database import db
from models.user import User                      # noqa: F401
from models.health_report import HealthReport      # noqa: F401
from models.health_metric import HealthMetric
from utils.visit_advice import build_visit_advice, VISIT_DISCLAIMER

# 与 app.py::create_app() 保持一致的蓝图清单（少了任何一个 → base.html 的 url_for 直接 BuildError）
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
from routes.checkin_routes import checkin_bp

PAGE = '/api/upload/upload_health_report'

# 明确不许出现在页面上的词语（药名 + 剂量写法 + 越界措辞）
FORBIDDEN = (
    '二甲双胍', '格华止', '拜糖平', '阿卡波糖', '格列美脲', '格列齐特', '格列吡嗪',
    '西格列汀', '达格列净', '恩格列净', '利拉鲁肽', '司美格鲁肽', '吡格列酮',
    '罗格列酮', '瑞格列奈', '那格列奈', '甘精胰岛素',
    'mg', '毫克', '每日两次', '每日三次', '每次一片', '建议服用', '你应该吃',
)


def build_isolated_app(db_path):
    """指向临时库的完整应用；仪表盘用真实模板 + 一份最小 stats。"""
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
    app.register_blueprint(checkin_bp, url_prefix='/api/checkin')

    @app.route('/dashboard')
    def dashboard():
        """真实项目里这段在 create_app 内；这里只保留与本功能相关的两个上下文变量，
        其余给空值 —— 目的是验证 dashboard/index.html 真的把清单卡片 include 进来了。"""
        from flask import render_template, session
        from utils.checkin import build_card_context
        user_id = session.get('user_id', 1)
        stats = {
            'recipe_count': 0, 'favorite_count': 0, 'report_count': 0,
            'latest_health': None, 'latest_fridge': None,
            'latest_ingredients': [], 'ingredient_count': 0,
            'recent_recipes': [], 'recent_advices': {},
            'my_share_count': 0, 'community_latest': [], 'liked_post_ids': set(),
            'checkin': build_card_context(user_id, action_url='/api/checkin/save',
                                          next_url='/dashboard'),
            'visit': build_visit_advice(user_id),
        }
        return render_template('dashboard/index.html', stats=stats)

    with app.app_context():
        db.create_all()
    return app


def _add_metric(user_id, key, label, value, unit, when, report_id=None):
    db.session.add(HealthMetric(user_id=user_id, report_id=report_id, metric_key=key,
                                label=label, value=value, unit=unit, measured_at=when))


def _details_spans(html):
    """返回 [(start, end)]：所有 <details>…</details> 的位置区间。"""
    spans = []
    for m in re.finditer(r'<details\b', html):
        end = html.find('</details>', m.start())
        if end > 0:
            spans.append((m.start(), end + len('</details>')))
    return spans


def _inside_details(html, needle):
    idx = html.find(needle)
    if idx < 0:
        return None
    return any(start <= idx <= end for start, end in _details_spans(html))


def _visit_card_html(html):
    """截出 .visit-card 那一段（到 </section> 为止）；找不到返回空串。"""
    start = html.find('class="visit-card')
    if start < 0:
        return ''
    end = html.find('</section>', start)
    return html[start:end] if end > 0 else html[start:]


def main():
    passed = total = 0

    def check(name, cond, detail=''):
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print(f'[PASS] {name}')
        else:
            print(f'[FAIL] {name}   {detail}')

    print('=' * 78)
    print('R5 第 3 项 就诊沟通清单 · 页面自测')
    print('=' * 78)

    tmpdir = tempfile.mkdtemp(prefix='visit_page_test_')
    db_path = os.path.join(tmpdir, 't.db')
    app = build_isolated_app(db_path)

    today = datetime.now()
    day = lambda n: (today - timedelta(days=n)).replace(hour=7, minute=30, second=0, microsecond=0)

    try:
        with app.app_context():
            # 用户 1：最近一次空腹 17.8（critical）+ 近几天打卡
            _add_metric(1, 'fasting_glucose', '空腹血糖', 17.8, 'mmol/L', day(0))
            for i in (1, 2, 3):
                _add_metric(1, 'fasting_glucose', '空腹血糖', 8.6, 'mmol/L', day(i))
            _add_metric(1, 'postprandial_glucose', '餐后血糖', 13.4, 'mmol/L', day(1))

            # 用户 2：只有一次 7.8（warn）
            _add_metric(2, 'fasting_glucose', '空腹血糖', 7.8, 'mmol/L', day(0))

            # 用户 3：全部正常
            _add_metric(3, 'fasting_glucose', '空腹血糖', 5.4, 'mmol/L', day(0))
            _add_metric(3, 'systolic_bp', '收缩压', 122, 'mmHg', day(0))

            # 用户 4：最新 6.1 正常，但前 3 天连续偏高 → 靠"连续偏高"才该出现
            for i in (1, 2, 3):
                _add_metric(4, 'fasting_glucose', '空腹血糖', 8.4, 'mmol/L', day(i))
            _add_metric(4, 'fasting_glucose', '空腹血糖', 6.1, 'mmol/L', day(0))
            db.session.commit()

            # 用户 1 有一份报告（准备清单里会提醒带报告）
            db.session.add(HealthReport(user_id=1, filename='体检.png', filepath='x',
                                        content_text='空腹血糖 17.8 mmol/L',
                                        uploaded_at=day(0)))
            db.session.commit()

            print(f'临时库已造：{HealthMetric.query.count()} 行指标 / '
                  f'{HealthReport.query.count()} 份报告')
        print('-' * 78)

        client = app.test_client()

        # ══════════ 1. 危值：红卡 + 三件套全在 ══════════
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get(PAGE)
        html = resp.get_data(as_text=True)
        check('危值用户页面 200', resp.status_code == 200, str(resp.status_code))
        check('出现就诊清单卡片', 'class="visit-card is-critical"' in html,
              '未找到 visit-card is-critical')
        check('卡片标题说明需就医', '这个数值需要尽快就医' in html)
        check('① 就诊准备清单在', '去之前准备这些' in html)
        check('② 三个问题在', '可以照这样问医生' in html)
        check('③ 药物类别科普在', '医生可能会提到的药物类别' in html)
        check('列出用户自己的数值', '17.8' in html)
        check('给出需就医旗标', 'visit-finding-flag' in html and '需就医' in html)
        check('免责声明在页面里', '不是处方' in html and '请勿自行购药' in html)
        check('急救口径在页面里', '拨打 120' in html)

        # 红线 1：全文不得出现药名 / 剂量
        # 只扫"这张卡"的范围：整页里的 `img` 之类会把子串 `mg` 带进来（假阳性），
        # 本功能管的是卡片上的文字，不该被别的标签误伤。
        card_html = _visit_card_html(html)
        hit = [w for w in FORBIDDEN if w in card_html]
        check('卡片里不出现具体药名/剂量/越界措辞', not hit, str(hit))

        # 红线 2：免责声明不能被折进 <details>
        inside = _inside_details(html, '不是处方')
        check('免责声明不在任何 <details> 里（同屏可见）', inside is False, str(inside))
        # 各块标题有 ①②③，方便用户对上"三件套"
        check('三块内容带序号', '①' in html and '②' in html and '③' in html)

        # 逐条记录（自己填的数据进清单）
        check('列出逐条血糖记录', 'visit-record-list' in html)
        # 2026-10-22：「血糖打卡」改叫「血糖记录」，来源标签同步改成「记录」
        check('记录标出「记录」来源', '>记录<' in html)
        check('记录标出偏高', 'visit-record-flag' in html)

        # ══════════ 2. visit_no_title：已有红卡时不重复标题 ══════════
        # GET 打开本页不会重新扫风险（risk 只在 POST 上传后传），所以这里直接渲染片段，
        # 验证「上传后危值 + 红卡」那条分支的措辞不重复。
        from flask import render_template
        with app.test_request_context('/'):
            with_title = render_template('health/_visit_card.html',
                                         visit=build_visit_advice(1))
            no_title = render_template('health/_visit_card.html',
                                       visit=build_visit_advice(1), visit_no_title=True)
        check('默认渲染带标题', 'class="visit-head"' in with_title, '缺标题行')
        check('visit_no_title 时不画标题行', 'class="visit-head"' not in no_title,
              'visit-head 仍然出现')
        check('不画标题时正文与免责声明仍在',
              '去之前准备这些' in no_title and '不是处方' in no_title)
        check('不画标题时也不重复"尽快就医"那句标题',
              '这个数值需要尽快就医' not in no_title)
        # 红卡本身（.risk-alert）由 _risk_alert.html 负责，这里只确认两个片段互不干扰
        with app.test_request_context('/'):
            alert_html = render_template(
                'health/_risk_alert.html',
                risk={'level': 'critical', 'message': 'm', 'findings': [],
                      'disclaimer': 'd', 'blocked': True})
        check('红卡片段仍可单独渲染', 'class="risk-alert"' in alert_html)

        # ══════════ 3. 偏高：黄卡，且没有红卡 ══════════
        with client.session_transaction() as sess:
            sess['user_id'] = 2
        html2 = client.get(PAGE).get_data(as_text=True)
        check('偏高用户出黄卡', 'class="visit-card is-warn"' in html2, '未找到 is-warn')
        check('偏高用户没有红卡', 'class="risk-alert"' not in html2)
        check('偏高时标题建议复查', '建议近期复查' in html2)
        check('偏高时仍给三个问题', '可以照这样问医生' in html2)
        check('偏高时也带免责声明', '不是处方' in html2)

        # ══════════ 4. 正常：整块不出现 ══════════
        with client.session_transaction() as sess:
            sess['user_id'] = 3
        html3 = client.get(PAGE).get_data(as_text=True)
        check('指标正常时不出现清单卡片', 'visit-card' not in html3, '正常用户不该看到卡片')
        check('正常时也不出现免责声明块', 'visit-disclaimer' not in html3)

        # ══════════ 5. 最新一次正常、但连续 3 天偏高 → 仍要提示 ══════════
        with client.session_transaction() as sess:
            sess['user_id'] = 4
        html4 = client.get(PAGE).get_data(as_text=True)
        check('连续偏高触发卡片', 'class="visit-card is-warn"' in html4, '未触发')
        check('标题写明连续偏高', '连续偏高' in html4 or '最近 3 天' in html4)
        check('findings 为空时卡片仍成立（没有误标某天）',
              'visit-finding-value' not in html4, '不该有偏高指标胶囊')

        # ══════════ 6. 仪表盘也挂了这张卡 ══════════
        with client.session_transaction() as sess:
            sess['user_id'] = 2
        dash = client.get('/dashboard')
        dash_html = dash.get_data(as_text=True)
        check('仪表盘 200', dash.status_code == 200, str(dash.status_code))
        check('仪表盘上有就诊清单', 'class="visit-card is-warn"' in dash_html, '未渲染')
        check('仪表盘上仍保留每日打卡卡', 'checkin-card' in dash_html)
        check('清单卡排在打卡卡之前（就医优先）',
              dash_html.find('visit-card') < dash_html.find('checkin-card'),
              f"visit={dash_html.find('visit-card')} checkin={dash_html.find('checkin-card')}")

        # ══════════ 7. 接线检查：app.py 真的传了 visit ══════════
        root = os.path.dirname(os.path.abspath(__file__))
        app_src = open(os.path.join(root, 'app.py'), encoding='utf-8').read()
        check('app.py 引入 build_visit_advice', 'build_visit_advice' in app_src)
        check('仪表盘上下文里有 visit', "stats['visit'] = build_visit_advice" in app_src)

        upload_src = open(os.path.join(root, 'routes', 'upload_routes.py'), encoding='utf-8').read()
        check('健康报告页传了 visit', 'visit=build_visit_advice' in upload_src)

        # ══════════ 8. 样式在共用样式表里（模板内不写样式） ══════════
        css = open(os.path.join(root, 'static', 'css', 'style.css'), encoding='utf-8').read()
        for cls in ('.visit-card', '.visit-findings', '.visit-prep', '.visit-questions',
                    '.visit-class', '.visit-disclaimer'):
            check(f'style.css 有 {cls}', cls in css, '缺样式')

        # ══════════ 9. 纯逻辑层：不是"只靠模板兜" ══════════
        with app.app_context():
            v = build_visit_advice(1)
            check('visit_advice 判为 critical', v['level'] == 'critical', v['level'])
            check('免责声明就是常量本身', v['disclaimer'] == VISIT_DISCLAIMER)
            check('问题恰好 3 个', len(v['questions']) == 3, str(v['questions']))
            check('准备清单至少 4 条', len(v['prep']) >= 4, str(len(v['prep'])))
            normal = build_visit_advice(3)
            check('正常用户 show=False', normal['show'] is False, str(normal['level']))
    finally:
        try:
            os.remove(db_path)
            os.rmdir(tmpdir)
        except OSError:
            pass

    print('-' * 78)
    print(f'通过 {passed}/{total}')
    return passed == total


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
