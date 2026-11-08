# test_biz_time_flow.py
"""北京时间边界 —— 线上故障的回归测试（2026-10-24）

线上真实故障
    容器 `TZ` 为空 -> `datetime.now()` 返回 UTC，而"今天"也按 UTC 算。
    于是**北京时间早上 7:00 记的空腹血糖**（= UTC 前一天 23:00）会被归到前一天：
    用户 8:00 再打开仪表盘，卡片显示"今天还没记录"、输入框变空，以为数据丢了。
    清晨空腹血糖正好是这个产品最典型的场景。

    更麻烦的是本地开发机是 UTC+8，两边 `datetime.now()` 一致，
    所以**老代码在本地怎么测都是绿的**。这个文件就是专门盯这件事的：
    所有时刻都写成"北京 xx 点"，再显式换算成 UTC 去调函数，不依赖机器时区。

用法
    python test_biz_time_flow.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

TMPDIR = tempfile.mkdtemp(prefix='biz_time_')
DB_PATH = os.path.join(TMPDIR, 't.db').replace('\\', '/')
os.environ['DATABASE_URL'] = 'sqlite:///' + DB_PATH

from app import create_app                                    # noqa: E402
from utils.database import db                                 # noqa: E402
from models.user import User                                  # noqa: E402
from utils.biz_time import (                                  # noqa: E402
    biz_day_bounds, biz_now, to_biz, to_utc, utc_now,
)
from utils.checkin import (                                   # noqa: E402
    build_api_context, build_card_context, parse_checkin, save_checkin,
)
from utils.metric_store import save_metrics_for_text          # noqa: E402
from utils.visit_advice import collect_records                # noqa: E402

passed = total = 0


def check(name, cond, detail=''):
    global passed, total
    total += 1
    if cond:
        passed += 1
        print('[PASS] %s' % name)
    else:
        print('[FAIL] %s   %s' % (name, detail))


def at(bj_day, bj_hour, bj_minute=0):
    """写"北京时间"、返回库里存的 UTC。测试里只用这一个入口造时间。"""
    return to_utc(datetime(2026, 10, bj_day, bj_hour, bj_minute))


def main():
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        u = User(username='tz_alice')
        u.set_password('pw123456')
        db.session.add(u)
        db.session.commit()
        uid = u.id

        print('=' * 78)
        print('北京时间边界 · 回归自测')
        print('=' * 78)

        # ---------------------------------------------------------------
        # 1. 核心场景：北京 10-25 早上 7:00 记一次空腹血糖
        # ---------------------------------------------------------------
        morning = at(25, 7, 0)                     # 用户以为的"今天早上 7 点"
        check('北京 07:00 换算成 UTC 是前一天 23:00',
              morning == datetime(2026, 10, 24, 23, 0), str(morning))

        save_checkin(uid, parse_checkin({'fasting_glucose': '7.2'})[0], when=morning)

        # 8:00 打开仪表盘（旧代码在这里翻车：按 UTC 已经跨日了）
        ctx_0800 = build_card_context(uid, '/api/checkin/save', when=at(25, 8, 0))
        check('★ 8:00 再看，卡片仍显示"今天已记录"',
              ctx_0800['done'] is True and ctx_0800['filled_count'] == 1,
              str((ctx_0800['done'], ctx_0800['filled_count'])))
        check('★ 8:00 再看，输入框回填 7.2',
              ctx_0800['fields'][0]['value'] == '7.2',
              str(ctx_0800['fields'][0]['value']))

        # 同一天晚上仍然是"今天"
        ctx_2300 = build_card_context(uid, '/x', when=at(25, 23, 0))
        check('同日 23:00 仍算今天', ctx_2300['done'] is True, str(ctx_2300['done']))

        # 次日凌晨 0:30 才该翻页
        ctx_next = build_card_context(uid, '/x', when=at(26, 0, 30))
        check('次日 00:30 才算新的一天', ctx_next['done'] is False, str(ctx_next['done']))

        # 而且昨天的记录要出现在"上次"里（北京时间 10-25）
        check('次日看到的"上次"日期是北京的 10-25',
              '10-25' in ctx_next['fields'][0]['last_text'],
              str(ctx_next['fields'][0]['last_text']))
        check('次日"上次"用的是"上次"而不是"报告"',
              ctx_next['fields'][0]['last_text'].startswith('上次'),
              str(ctx_next['fields'][0]['last_text']))

        # ---------------------------------------------------------------
        # 2. 清除后重来：验证"报告行"（UTC 存的）与"手填行"（也 UTC）同口径
        # ---------------------------------------------------------------
        with app.app_context():
            db.session.execute(db.text('delete from health_metric'))
            db.session.commit()

        # 报告是"北京 10-20 08:00 上传"的 -> 库里 UTC 10-20 00:00
        save_metrics_for_text(uid, '空腹血糖 6.4 mmol/L', report_id=901,
                              measured_at=at(20, 8, 0))
        ctx_report = build_card_context(uid, '/x', when=at(25, 9, 0))
        check('报告值的日期按北京时间显示（10-20）',
              '10-20' in ctx_report['fields'][0]['last_text'],
              str(ctx_report['fields'][0]['last_text']))

        # ---------------------------------------------------------------
        # 3. 接口返回的 date 必须是"北京今天"，不是服务器日期
        # ---------------------------------------------------------------
        api_ctx = build_api_context(uid)
        check('接口 date = 北京今天',
              api_ctx['date'] == biz_now().strftime('%Y-%m-%d'),
              '%s vs %s' % (api_ctx['date'], biz_now().strftime('%Y-%m-%d')))
        # 手工构造一个"UTC 还在昨天、北京已是今天"的时刻，验证 date 跟北京走
        utc_late = datetime(2026, 10, 24, 23, 30)      # 北京 10-25 07:30
        api_late = build_api_context(uid, when=utc_late)
        check('★ UTC 23:30 时接口 date 是北京的次日',
              api_late['date'] == '2026-10-25', api_late['date'])

        # ---------------------------------------------------------------
        # 4. visit_advice 的"近 14 天"按北京日切窗
        # ---------------------------------------------------------------
        with app.app_context():
            db.session.execute(db.text('delete from health_metric'))
            db.session.commit()

        # 北京 10-25 07:00 的记录（UTC 10-24 23:00）
        save_checkin(uid, parse_checkin({'fasting_glucose': '8.4'})[0], when=at(25, 7, 0))
        rec = collect_records(uid, days=14, now=at(25, 9, 0))
        check('近 14 天能数到今早这条', rec['count'] == 1, str(rec['count']))
        check('这条记录的日期显示成 10-25',
              rec['rows'] and rec['rows'][0]['date'] == '10-25',
              str(rec['rows']))
        check('今早 8.4 被判为偏高', rec['high_count'] == 1, str(rec['high_count']))

        # 窗口边界：北京 10-12 00:00 的记录在内，10-11 23:59 的不在
        with app.app_context():
            db.session.execute(db.text('delete from health_metric'))
            db.session.commit()
        save_checkin(uid, parse_checkin({'fasting_glucose': '6.9'})[0], when=at(12, 0, 0))
        rec_edge = collect_records(uid, days=14, now=at(25, 9, 0))
        check('窗口起点（北京 10-12 00:00）包含在内', rec_edge['count'] == 1,
              str(rec_edge['count']))

        # ---------------------------------------------------------------
        # 5. 真实 HTTP 链路：POST 之后 GET 卡片，值必须在
        #    （用真实 now，验的是"存储与读取用同一套时基"）
        # ---------------------------------------------------------------
        with app.app_context():
            db.session.execute(db.text('delete from health_metric'))
            db.session.commit()

        client.post('/api/user/login',
                    data={'identifier': 'tz_alice', 'password': 'pw123456'})
        resp = client.post('/api/checkin/save',
                           data={'fasting_glucose': '5.8', 'postprandial_glucose': '7.1'},
                           follow_redirects=True)
        page = resp.get_data(as_text=True)
        check('POST 之后页面上出现"今天已记录"', '今天已记录' in page,
              '页面里没有"今天已记录"')
        check('POST 之后输入框回填 5.8', 'value="5.8"' in page, '找不到 value="5.8"')

        # ---------------------------------------------------------------
        # 6. 存进库的必须仍是 UTC（不破坏历史数据口径）
        # ---------------------------------------------------------------
        with app.app_context():
            from models.health_metric import HealthMetric
            row = HealthMetric.query.filter_by(user_id=uid, metric_key='fasting_glucose') \
                .order_by(HealthMetric.id.desc()).first()
            gap = abs((row.measured_at - utc_now()).total_seconds())
            check('落库时间戳是 UTC（与 utc_now 相差 < 60s）', gap < 60,
                  '相差 %.0f 秒' % gap)

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
