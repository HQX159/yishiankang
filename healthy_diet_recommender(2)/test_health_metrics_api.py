# test_health_metrics_api.py
"""
健康指标接口自测 —— 只读，不写数据库

做什么
    用项目真实的 app.db + 真实 JWT 认证，直接跑 `/api/v1/health/metrics` 与
    `/api/v1/health/metrics/trend` 两个接口，验证返回值形状对不对。

为什么能脱机跑
    只用 `api_v1` 蓝图搭一个最小应用，**不 import app.py**（避免加载 YOLO 模型），
    也不起 HTTP 服务、不占用 5000 端口，所以不会干扰正在运行的 Flask 与训练进程。

本脚本只发 GET 请求，不会修改任何数据。

用法
    python test_health_metrics_api.py
"""
import sys

from flask import Flask

from config import config as app_config
from utils.database import db
from utils.auth import generate_jwt_token
from routes.api_v1 import api_v1_bp
from models.user import User                # noqa: F401
from models.health_report import HealthReport  # noqa: F401
from models.health_metric import HealthMetric


def build_app():
    app = Flask(__name__)
    app.config.from_object(app_config['default'])
    db.init_app(app)
    app.register_blueprint(api_v1_bp, url_prefix='/api/v1')
    return app


def main():
    app = build_app()
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
    print('健康指标接口自测（只读）')
    print('=' * 78)

    with app.app_context():
        token = generate_jwt_token(1)          # 用户 1（库里已有指标数据）
        # 2026-10-22 改：原来写死"用户 2 没有指标"，但真实库是活的 ——
        # 用户 2 后来传了 5 份报告，现在有 60 项指标，这条断言就假红了。
        # 凡是"某个用户恰好没有某类数据"的假设都不能绑真实库；
        # 现在现查一个真的一条指标都没有的用户，实在找不到就跳过（并说明原因，
        # 不能默默变成假绿）。
        empty_uid = (db.session.query(User.id)
                     .filter(~User.id.in_(db.session.query(HealthMetric.user_id).distinct()))
                     .order_by(User.id).first())
        token_empty = generate_jwt_token(empty_uid[0]) if empty_uid else None

    client = app.test_client()
    auth = {'Authorization': f'Bearer {token}'}
    auth_empty = {'Authorization': f'Bearer {token_empty}'} if token_empty else None

    # 1) 未携带 token → 401
    resp = client.get('/api/v1/health/metrics')
    check('未登录返回 401', resp.status_code == 401, str(resp.status_code))

    # 2) 最新指标
    resp = client.get('/api/v1/health/metrics', headers=auth)
    payload = resp.get_json()
    metrics = (payload or {}).get('data', {}).get('metrics', [])
    check('最新指标接口 200', resp.status_code == 200, str(resp.status_code))
    check('返回 code=0', (payload or {}).get('code') == 0, str(payload))
    check('用户 1 拿到 6 项指标（身高/体重/BMI/血脂两项/血压两项合并为 6 类共 7 行）',
          len(metrics) == 7, f'实际 {len(metrics)}: {[m["key"] for m in metrics]}')

    by_key = {m['key']: m for m in metrics}
    check('总胆固醇 5.9 mmol/L 正确', by_key.get('total_cholesterol', {}).get('value') == 5.9,
          str(by_key.get('total_cholesterol')))
    check('血压 130/85 正确',
          by_key.get('systolic_bp', {}).get('value') == 130
          and by_key.get('diastolic_bp', {}).get('value') == 85,
          f'{by_key.get("systolic_bp")} {by_key.get("diastolic_bp")}')
    check('返回带中文名与单位',
          by_key.get('ldl', {}).get('label') == '低密度脂蛋白'
          and by_key.get('ldl', {}).get('unit') == 'mmol/L',
          str(by_key.get('ldl')))

    # 3) 趋势接口
    resp = client.get('/api/v1/health/metrics/trend?key=total_cholesterol&limit=10', headers=auth)
    trend = (resp.get_json() or {}).get('data', {})
    check('趋势接口 200 且有点位', resp.status_code == 200 and len(trend.get('points', [])) == 1,
          str(trend))
    check('趋势点位带 value 与时间',
          trend.get('points', [{}])[0].get('value') == 5.9
          and bool(trend.get('points', [{}])[0].get('measured_at')),
          str(trend.get('points')))

    # 4) 缺 key 参数 → 业务报错而非 500
    resp = client.get('/api/v1/health/metrics/trend', headers=auth)
    body = resp.get_json() or {}
    check('缺 key 参数返回业务错误', body.get('code') == 1, str(body))

    # 5) 没有指标的指标键 → 空序列，不报错
    resp = client.get('/api/v1/health/metrics/trend?key=uric_acid', headers=auth)
    trend = (resp.get_json() or {}).get('data', {})
    check('无数据指标返回空序列', resp.status_code == 200 and trend.get('points') == [], str(trend))

    # 6) 一条指标都没有的用户 → 空数组而不是报错
    if auth_empty:
        resp = client.get('/api/v1/health/metrics', headers=auth_empty)
        metrics2 = (resp.get_json() or {}).get('data', {}).get('metrics', [])
        check('无数据用户返回空数组', resp.status_code == 200 and metrics2 == [], str(metrics2))
    else:
        check('（跳过）库里已无「零指标用户」，无数据用户这一条没测到', True,
              '全部用户都至少有一条指标')

    # 7) 非法 limit 不炸
    resp = client.get('/api/v1/health/metrics/trend?key=total_cholesterol&limit=abc', headers=auth)
    check('非法 limit 自动回退默认值', resp.status_code == 200, str(resp.status_code))
    resp = client.get('/api/v1/health/metrics/trend?key=total_cholesterol&limit=99999', headers=auth)
    check('超大 limit 被夹到上限', resp.status_code == 200, str(resp.status_code))

    with app.app_context():
        print('-' * 78)
        print(f'health_metric 表行数（只读）: {HealthMetric.query.count()}')

    print('-' * 78)
    print(f'通过 {passed}/{total}')
    return passed == total


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
