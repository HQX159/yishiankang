# test_r5_checkin.py
"""R5 每日血糖记录 —— 页面级自测（造临时库，不碰真实 app.db）

覆盖什么
    1. 未登录访问仪表盘 → 跳登录页
    2. 仪表盘上有血糖记录卡片：三个时点（空腹 / 餐后1小时 / 餐后2小时）+ 保存按钮
       —— 并且**没有体重框**（2026-10-22 拿掉，低频指标走报告上传）
    3. 只填一个值能存；回到页面自动回填、"今天已记录"
    4. 同一天再填 → 改那条，不新增第二个点
    5. 填错（非数字 / 超范围 / 一个都不填）→ 提示，且库里不动
    6. ★核心★ 先记录 7.8，再传一份含 6.5 的报告
         · 报告日期在上周 → 仪表盘仍显示 7.8
         · 报告"今天才上传"（同一时间戳）→ 也仍显示 7.8
         · 报告那条 6.5 必须还在库里，一行都没少
    7. 「健康报告」页有同一张卡片，指标卡上标出「记录 / 报告」
    8. next 参数不能把人带到站外
    9. 用户之间看不到彼此的记录

为什么用真实 create_app()（而不是手搭蓝图）
    卡片在模板里依赖 url_for('checkin.save')，仪表盘视图也依赖它。
    手搭蓝图就必须把 app.py 的接线再抄一遍，抄漏一个就是"测试通过、线上 500"。
    真实 create_app() + 临时 DATABASE_URL，测的就是线上跑的那套接线。

用法
    python test_r5_checkin.py
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMPDIR = tempfile.mkdtemp(prefix='checkin_page_')
DB_PATH = os.path.join(TMPDIR, 't.db').replace('\\', '/')
# 必须在 import app 之前设置：config.py 在 import 时就把 DATABASE_URL 读成类属性了
os.environ['DATABASE_URL'] = 'sqlite:///' + DB_PATH

from app import create_app                        # noqa: E402 —— 顺序不能动
from utils.database import db                     # noqa: E402
from models.user import User                      # noqa: E402
from models.health_metric import HealthMetric     # noqa: E402
from utils.metric_store import latest_metrics, save_metrics_for_text  # noqa: E402

passed = 0
total = 0


def check(name, cond, detail=''):
    global passed, total
    total += 1
    if cond:
        passed += 1
        print('[PASS] %s' % name)
    else:
        print('[FAIL] %s   %s' % (name, detail))


def html_of(resp):
    return resp.get_data(as_text=True)


def main():
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        alice = User(username='ck_alice')
        alice.set_password('pw123456')
        bob = User(username='ck_bob')
        bob.set_password('pw123456')
        db.session.add_all([alice, bob])
        db.session.commit()
        alice_id, bob_id = alice.id, bob.id

    print('=' * 78)
    print('R5 每日血糖记录 · 页面自测')
    print('=' * 78)

    # --- 1. 未登录 ---------------------------------------------------------
    resp = client.get('/dashboard')
    check('未登录访问仪表盘跳登录页',
          resp.status_code == 302 and 'login' in resp.headers.get('Location', ''),
          '%s %s' % (resp.status_code, resp.headers.get('Location')))

    # 登录（表单字段是 identifier，不是 username）
    resp = client.post('/api/user/login',
                       data={'identifier': 'ck_alice', 'password': 'pw123456'},
                       follow_redirects=True)
    page = html_of(resp)
    check('登录后落在仪表盘', resp.status_code == 200 and '健康仪表盘' in page,
          str(resp.status_code))

    # --- 2. 卡片在不在 -----------------------------------------------------
    check('仪表盘上有血糖记录卡片', '今日血糖记录' in page)
    for key, label in (('fasting_glucose', '空腹血糖值'),
                       ('postprandial_1h_glucose', '餐后 1 小时血糖'),
                       ('postprandial_glucose', '餐后 2 小时血糖')):
        check('卡片里有「%s」输入框' % label, 'name="%s"' % key in page)
        check('卡片上显示「%s」' % label, label in page)
    # 2026-10-22 晚：体重加回来了，但**单独一行**，不占血糖三项的网格。
    check('卡片里有体重输入框', 'name="weight"' in page)
    check('体重是独立一行（不混进血糖三格）', 'checkin-weight-row' in page)
    check('卡片有保存按钮', 'checkin-submit' in page)
    check('还没填过时不显示"已记录"', '今天已记录' not in page)

    # --- 3. 只填一个也能存 -------------------------------------------------
    resp = client.post('/api/checkin/save',
                       data={'fasting_glucose': '7.8',
                             'postprandial_1h_glucose': '',
                             'postprandial_glucose': '',
                             'next': '/dashboard'},
                       follow_redirects=True)
    page = html_of(resp)
    check('保存后提示已记录', '已记录' in page and '7.8' in page, page[:200])
    check('输入框回填 7.8', 'value="7.8"' in page)
    check('状态变成今天已记录', '今天已记录' in page)

    with app.app_context():
        rows = HealthMetric.query.filter_by(user_id=alice_id, report_id=None).all()
        check('库里只写了 1 行', len(rows) == 1, str(len(rows)))
        check('写入的是空腹血糖 7.8',
              rows and rows[0].metric_key == 'fasting_glucose' and rows[0].value == 7.8,
              str([(r.metric_key, r.value) for r in rows]))

    # --- 4. 同一天再填 = 改那条 -------------------------------------------
    resp = client.post('/api/checkin/save',
                       data={'fasting_glucose': '8.1', 'next': '/dashboard'},
                       follow_redirects=True)
    page = html_of(resp)
    with app.app_context():
        rows = HealthMetric.query.filter_by(user_id=alice_id, report_id=None).all()
        check('同日再填不新增第二个点', len(rows) == 1, str(len(rows)))
        check('同日再填改成了 8.1', rows and rows[0].value == 8.1, str(rows[0].value if rows else None))
    check('页面上回填的是 8.1', 'value="8.1"' in page)

    # --- 4b. 三个时点一次填完（2026-10-22 起的卡片形状）-------------------
    resp = client.post('/api/checkin/save',
                       data={'fasting_glucose': '8.1',
                             'postprandial_1h_glucose': '10.6',
                             'postprandial_glucose': '9.3',
                             'next': '/dashboard'},
                       follow_redirects=True)
    page = html_of(resp)
    with app.app_context():
        rows = HealthMetric.query.filter_by(user_id=alice_id, report_id=None).all()
        by_key = {r.metric_key: r.value for r in rows}
        check('三个时点落成三行', len(rows) == 3, str(sorted(by_key)))
        check('餐后1小时落库为 10.6', by_key.get('postprandial_1h_glucose') == 10.6, str(by_key))
        check('餐后2小时落库为 9.3', by_key.get('postprandial_glucose') == 9.3, str(by_key))
        check('餐后1小时的中文名与报告解析一致',
              HealthMetric.query.filter_by(metric_key='postprandial_1h_glucose').first().label
              == '餐后1小时血糖', '')
    check('三个值都回填到输入框',
          'value="8.1"' in page and 'value="10.6"' in page and 'value="9.3"' in page,
          page[:200])
    check('保存提示里带上了三个值',
          '10.6' in page and '9.3' in page, page[:300])

    # --- 5. 填错的处理 -----------------------------------------------------
    resp = client.post('/api/checkin/save',
                       data={'fasting_glucose': 'abc', 'next': '/dashboard'},
                       follow_redirects=True)
    page = html_of(resp)
    check('非数字有明确提示', '不是数字' in page, page[:300])
    with app.app_context():
        row = HealthMetric.query.filter_by(user_id=alice_id, report_id=None,
                                           metric_key='fasting_glucose').first()
        check('非数字没写进库（还是 8.1）', row and row.value == 8.1,
              str(row.value if row else None))

    resp = client.post('/api/checkin/save',
                       data={'fasting_glucose': '999', 'next': '/dashboard'},
                       follow_redirects=True)
    check('超范围有明确提示', '超出合理范围' in html_of(resp))

    resp = client.post('/api/checkin/save',
                       data={'fasting_glucose': '', 'postprandial_1h_glucose': '',
                             'postprandial_glucose': '',
                             'next': '/dashboard'},
                       follow_redirects=True)
    check('一个都不填会提示', '还没有填数值' in html_of(resp))

    # --- 6. 核心：报告不能把记录顶掉 ---------------------------------------
    now = datetime.now()
    with app.app_context():
        # (a) 上周的报告（用户今天才上传，measured_at 传的就是"今天"以外的旧日期）
        save_metrics_for_text(alice_id, '空腹血糖 6.5 mmol/L', report_id=555,
                              measured_at=now - timedelta(days=7))
        # (b) 报告时间戳与记录同一天、且更晚（最容易顶掉记录的情况）
        save_metrics_for_text(alice_id, '空腹血糖 6.4 mmol/L', report_id=556,
                              measured_at=now + timedelta(minutes=5))

        latest = {c['key']: c for c in latest_metrics(alice_id)}
        check('★ 仪表盘仍显示记录的 8.1（旧报告顶不掉）',
              latest['fasting_glucose']['value'] == 8.1,
              str(latest['fasting_glucose']))
        check('★ 同一天更晚的报告也顶不掉记录',
              latest['fasting_glucose']['value'] == 8.1,
              str(latest['fasting_glucose']))
        check('报告的两行都还在库里（一行没少）',
              HealthMetric.query.filter(HealthMetric.report_id.in_((555, 556))).count() == 2,
              str(HealthMetric.query.filter(HealthMetric.report_id.in_((555, 556))).count()))
        check('报告里的 6.5 / 6.4 原值未被修改',
              {r.value for r in HealthMetric.query.filter(
                  HealthMetric.report_id.in_((555, 556))).all()} == {6.5, 6.4},
              str([r.value for r in HealthMetric.query.filter(
                  HealthMetric.report_id.in_((555, 556))).all()]))

    # --- 7. 「健康报告」页也认这张卡片 + 标来源 ---------------------------
    # 需要一张"只有报告才有"的指标卡（血脂），否则最新值全是记录，页面上一张报告卡都看不见
    with app.app_context():
        save_metrics_for_text(alice_id, '总胆固醇 6.2 mmol/L', report_id=557,
                              measured_at=now - timedelta(days=3))
    resp = client.get('/api/upload/upload_health_report')
    page = html_of(resp)
    check('健康报告页 200', resp.status_code == 200, str(resp.status_code))
    check('健康报告页也有记录卡片', '今日血糖记录' in page)
    check('指标卡上标了「记录」来源', 'metric-source is-checkin' in page and '>记录<' in page,
          page[:200])
    check('指标卡上标了「报告」来源', 'metric-source is-report' in page and '>报告<' in page)

    # --- 8. next 不能跳站外 -----------------------------------------------
    resp = client.post('/api/checkin/save',
                       data={'fasting_glucose': '7.0', 'next': '//evil.com/steal'})
    check('next 指站外时回落到仪表盘',
          resp.status_code == 302 and resp.headers.get('Location', '').endswith('/dashboard'),
          resp.headers.get('Location'))
    resp = client.post('/api/checkin/save',
                       data={'fasting_glucose': '7.0', 'next': '/api/upload/upload_health_report'})
    check('next 指站内页面时正常回落原页',
          resp.status_code == 302 and 'upload_health_report' in resp.headers.get('Location', ''),
          resp.headers.get('Location'))

    # --- 9. 用户隔离 -------------------------------------------------------
    other = app.test_client()
    other.post('/api/user/login',
               data={'identifier': 'ck_bob', 'password': 'pw123456'},
               follow_redirects=True)
    bob_page = html_of(other.get('/dashboard'))
    check('别的用户看不到我的记录（输入框是空的）',
          'value="7.0"' not in bob_page and 'value="8.1"' not in bob_page,
          '页面上出现了别人的值')

    print('-' * 78)
    print('通过 %d/%d' % (passed, total))
    return passed == total


if __name__ == '__main__':
    ok = False
    try:
        ok = main()
    finally:
        try:
            os.remove(DB_PATH)
            os.rmdir(TMPDIR)
        except OSError:
            pass
    sys.exit(0 if ok else 1)
