# test_api_checkin.py
"""
血糖记录 JSON 接口自测（`/api/v1/health/checkin`）—— 临时库，不碰真实 app.db

为什么单独一套
    网页那条路是普通表单 POST + PRG，已经有 test_r5_checkin.py 覆盖；
    这里测的是**小程序 / 鸿蒙**那条路：JSON 进、JSON 出、Bearer token 认证。
    两条路共用 utils/checkin.py 的校验与落库，所以断言的重点是
    "同一份规则从 JSON 进来也成立"，而不是把校验规则再写一遍。

覆盖什么
    1. 不带 token → 401
    2. 今天没填过：done=false，三个时点齐全，value/last_value 都是 null
    3. POST 只填一个 → 存进去、done=true、值回读一致
    4. 同一天再 POST 同一项 → updated=1、库里仍然只有 1 行（不冒出第二个点）
    5. 三个时点一次提交 → 3 行，key 与网页端完全一致
    6. 非法值（非数字 / 超范围 / 空提交）→ 400 + 中文原因，且库里不动
    7. {"values": {...}} 这种多包一层的形状也认
    8. 表里没定义的 key（如 foo）→ 静默忽略，不整单失败；体重是正式字段（2026-10-22 晚加回）
    9. 用户隔离：别人看不到
   10. 报告写进来的行（report_id 非空）一行不动
   11. last_value / last_date：昨天填过 7.2，今天页面能拿到"上次 7.2 · 昨天"

用法
    python test_api_checkin.py
"""
import os
import sys
import shutil
import tempfile
from datetime import datetime, timedelta

TMPDIR = tempfile.mkdtemp(prefix='api_checkin_')
DB_PATH = os.path.join(TMPDIR, 't.db').replace('\\', '/')
# 必须在 import config 之前设：config.py 在 import 时就把 DATABASE_URL 读成类属性
os.environ['DATABASE_URL'] = 'sqlite:///' + DB_PATH

from flask import Flask                                    # noqa: E402
from config import config as app_config                    # noqa: E402
from utils.database import db                              # noqa: E402
from utils.auth import generate_jwt_token                  # noqa: E402
from utils.biz_time import utc_now, to_biz                 # noqa: E402
from routes.api_v1 import api_v1_bp                        # noqa: E402
from models.user import User                               # noqa: E402
from models.health_report import HealthReport              # noqa: E402
from models.health_metric import HealthMetric              # noqa: E402

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


def json_of(resp):
    try:
        return resp.get_json() or {}
    except Exception:                                       # noqa: BLE001
        return {}


def field_of(payload, key):
    for f in (payload.get('data') or {}).get('fields') or []:
        if f.get('key') == key:
            return f
    return {}


def main():
    # 最小应用：只挂 api_v1，不 import app.py（避免加载 YOLO 权重，脱机也能跑）
    app = Flask(__name__)
    app.config.from_object(app_config['default'])
    app.config['TESTING'] = True
    db.init_app(app)
    app.register_blueprint(api_v1_bp, url_prefix='/api/v1')

    with app.app_context():
        db.create_all()
        alice = User(username='api_ck_alice')
        alice.set_password('pw123456')
        bob = User(username='api_ck_bob')
        bob.set_password('pw123456')
        db.session.add_all([alice, bob])
        db.session.commit()
        alice_id, bob_id = alice.id, bob.id
        token = generate_jwt_token(alice_id)
        token_bob = generate_jwt_token(bob_id)

    client = app.test_client()
    auth = {'Authorization': 'Bearer ' + token}
    auth_bob = {'Authorization': 'Bearer ' + token_bob}

    print('=' * 78)
    print('血糖记录 JSON 接口自测')
    print('=' * 78)

    # --- 1. 未登录 --------------------------------------------------------
    resp = client.get('/api/v1/health/checkin')
    check('不带 token 返回 401', resp.status_code == 401, str(resp.status_code))

    resp = client.post('/api/v1/health/checkin', json={'fasting_glucose': '7.2'})
    check('不带 token 不能写入', resp.status_code == 401, str(resp.status_code))

    # --- 2. 初始状态 ------------------------------------------------------
    resp = client.get('/api/v1/health/checkin', headers=auth)
    body = json_of(resp)
    data = body.get('data') or {}
    check('GET 返回 code=0', resp.status_code == 200 and body.get('code') == 0,
          str((resp.status_code, body.get('code'))))
    check('今天没填过时 done=false', data.get('done') is False, str(data.get('done')))
    check('三个时点齐全',
          [f.get('key') for f in data.get('fields') or []]
          == ['fasting_glucose', 'postprandial_1h_glucose', 'postprandial_glucose'],
          str([f.get('key') for f in data.get('fields') or []]))
    f0 = field_of(body, 'fasting_glucose')
    check('没填时 value / last_value 都是 null',
          f0.get('value') is None and f0.get('last_value') is None, str(f0))
    check('字段带上范围与单位（客户端可做前端校验）',
          f0.get('lo') == 1.0 and f0.get('hi') == 40.0 and f0.get('unit') == 'mmol/L', str(f0))

    # --- 3. 只填一个也能存 -------------------------------------------------
    resp = client.post('/api/v1/health/checkin', json={'fasting_glucose': '7.8'},
                       headers=auth)
    body = json_of(resp)
    check('只填一个能存（200 / code=0）',
          resp.status_code == 200 and body.get('code') == 0,
          str((resp.status_code, body.get('code'))))
    check('返回 saved=1 / updated=0',
          (body.get('data') or {}).get('saved') == 1
          and (body.get('data') or {}).get('updated') == 0, str(body.get('data')))
    check('存完 done=true', (body.get('data') or {}).get('done') is True)
    check('回读并回填 7.8', field_of(body, 'fasting_glucose').get('value') == 7.8,
          str(field_of(body, 'fasting_glucose')))

    # --- 4. 同一天重复提交 → 覆盖自己那条 ---------------------------------
    resp = client.post('/api/v1/health/checkin', json={'fasting_glucose': '8.1'},
                       headers=auth)
    body = json_of(resp)
    check('同日重复提交是更新（updated=1）',
          (body.get('data') or {}).get('updated') == 1, str(body.get('data')))
    with app.app_context():
        rows = HealthMetric.query.filter_by(user_id=alice_id,
                                            metric_key='fasting_glucose',
                                            report_id=None).count()
    check('同日重复提交不新增行（仍然 1 行）', rows == 1, str(rows))
    check('值被改成 8.1', field_of(body, 'fasting_glucose').get('value') == 8.1,
          str(field_of(body, 'fasting_glucose')))

    # --- 5. 三个时点一次填完 ----------------------------------------------
    resp = client.post('/api/v1/health/checkin',
                       json={'fasting_glucose': '6.8',
                             'postprandial_1h_glucose': '10.2',
                             'postprandial_glucose': '8.4'},
                       headers=auth)
    body = json_of(resp)
    check('三个时点一次提交 saved=3',
          (body.get('data') or {}).get('saved') == 3, str(body.get('data')))
    with app.app_context():
        keys = {r.metric_key for r in HealthMetric.query.filter_by(
            user_id=alice_id, report_id=None).all()}
    check('落库的 key 与网页端完全一致',
          keys == {'fasting_glucose', 'postprandial_1h_glucose', 'postprandial_glucose'},
          str(keys))
    check('餐后1小时写的是 10.2',
          field_of(body, 'postprandial_1h_glucose').get('value') == 10.2,
          str(field_of(body, 'postprandial_1h_glucose')))

    # --- 6. 非法输入 ------------------------------------------------------
    resp = client.post('/api/v1/health/checkin', json={'fasting_glucose': 'abc'},
                       headers=auth)
    body = json_of(resp)
    check('非数字 → 400 且 code=1',
          resp.status_code == 400 and body.get('code') == 1,
          str((resp.status_code, body.get('code'))))
    check('非数字的原因是可以直接展示的中文',
          '不是数字' in (body.get('message') or ''), str(body.get('message')))

    resp = client.post('/api/v1/health/checkin', json={'fasting_glucose': '999'},
                       headers=auth)
    body = json_of(resp)
    check('超范围 → 400 且说明范围',
          resp.status_code == 400 and '超出合理范围' in (body.get('message') or ''),
          str((resp.status_code, body.get('message'))))

    with app.app_context():
        still = HealthMetric.query.filter_by(user_id=alice_id, metric_key='fasting_glucose',
                                             report_id=None).first().value
    check('非法提交没有改掉已有的 6.8', still == 6.8, str(still))

    resp = client.post('/api/v1/health/checkin', json={}, headers=auth)
    body = json_of(resp)
    check('一个都不填 → 400 并提示', resp.status_code == 400
          and '还没有填数值' in (body.get('message') or ''),
          str((resp.status_code, body.get('message'))))

    resp = client.post('/api/v1/health/checkin', json={'fasting_glucose': ''}, headers=auth)
    body = json_of(resp)
    check('空串当作没填（同样 400 提示，而不是写一个 0）',
          resp.status_code == 400, str(resp.status_code))

    # --- 7. 多包一层 values 的形状也认 ------------------------------------
    resp = client.post('/api/v1/health/checkin',
                       json={'values': {'postprandial_glucose': '7.9'}}, headers=auth)
    body = json_of(resp)
    check('{"values": {...}} 形状也收',
          resp.status_code == 200 and field_of(body, 'postprandial_glucose').get('value') == 7.9,
          str((resp.status_code, field_of(body, 'postprandial_glucose'))))

    # --- 8. 表里没有的 key 静默忽略；体重已于 2026-10-22 晚加回 -----------
    resp = client.post('/api/v1/health/checkin',
                       json={'fasting_glucose': '7.0', 'weight': '65', 'foo': 'bar'},
                       headers=auth)
    body = json_of(resp)
    check('多余字段不导致整单失败',
          resp.status_code == 200 and (body.get('data') or {}).get('saved') == 2,
          str((resp.status_code, body.get('data'))))
    with app.app_context():
        weight_rows = HealthMetric.query.filter_by(user_id=alice_id,
                                                   metric_key='weight').count()
        foo_rows = HealthMetric.query.filter_by(user_id=alice_id,
                                                metric_key='foo').count()
    check('体重正常写入（曾是"不该落库"，当晚改回来了）', weight_rows == 1, str(weight_rows))
    check('没定义的 foo 被忽略', foo_rows == 0, str(foo_rows))
    check('接口把体重单列返回',
          ((body.get('data') or {}).get('weight') or {}).get('value') == 65.0,
          str((body.get('data') or {}).get('weight')))

    # --- 9. 用户隔离 ------------------------------------------------------
    resp = client.get('/api/v1/health/checkin', headers=auth_bob)
    body = json_of(resp)
    check('另一个用户看不到别人的记录',
          (body.get('data') or {}).get('done') is False, str(body.get('data')))

    # --- 10. 报告行一行不动 -----------------------------------------------
    with app.app_context():
        alice_report = HealthReport(user_id=alice_id, filename='r.txt',
                                    filepath='uploads/r.txt',
                                    content_text='空腹血糖 6.5 mmol/L')
        db.session.add(alice_report)
        db.session.commit()
        report_id = alice_report.id
        db.session.add(HealthMetric(user_id=alice_id, report_id=report_id,
                                    metric_key='fasting_glucose', label='空腹血糖',
                                    value=6.5, unit='mmol/L',
                                    measured_at=datetime.now() - timedelta(days=3)))
        db.session.commit()

    client.post('/api/v1/health/checkin', json={'fasting_glucose': '7.4'}, headers=auth)
    with app.app_context():
        report_row = HealthMetric.query.filter_by(report_id=report_id).first()
        check('报告里那条 6.5 还在、值没被改',
              report_row is not None and report_row.value == 6.5,
              str(report_row.value if report_row else None))
        manual = HealthMetric.query.filter_by(user_id=alice_id, metric_key='fasting_glucose',
                                              report_id=None).count()
    check('手工记录与报告行各自独立（手工仍只有 1 行）', manual == 1, str(manual))

    # --- 11. last_value / last_date ---------------------------------------
    with app.app_context():
        yesterday = utc_now() - timedelta(days=1)
        db.session.add(HealthMetric(user_id=bob_id, report_id=None,
                                    metric_key='fasting_glucose', label='空腹血糖',
                                    value=9.1, unit='mmol/L', measured_at=yesterday))
        db.session.commit()

    resp = client.get('/api/v1/health/checkin', headers=auth_bob)
    body = json_of(resp)
    fb = field_of(body, 'fasting_glucose')
    check('没有今天的记录时 last_value 给出上一次的值',
          fb.get('value') is None and fb.get('last_value') == 9.1, str(fb))
    check('last_date 是 YYYY-MM-DD',
          (fb.get('last_date') or '') == to_biz(yesterday).strftime('%Y-%m-%d'), str(fb.get('last_date')))

    print('-' * 78)
    print('通过 %d/%d' % (passed, total))
    return passed == total


if __name__ == '__main__':
    ok_all = False
    try:
        ok_all = main()
    finally:
        # Windows 上先断开引擎再删，否则 PermissionError: [WinError 32]
        try:
            with db.engine.dispose():
                pass
        except Exception:                                   # noqa: BLE001
            pass
        shutil.rmtree(TMPDIR, ignore_errors=True)
    sys.exit(0 if ok_all else 1)
