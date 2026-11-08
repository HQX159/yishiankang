# test_risk_alert_page.py
"""
「建议就医」提示自测 —— 造临时数据库，不碰真实 app.db

做什么
    1. 造一位**报告含危值**的用户（空腹血糖 17.2），分别打网页版与小程序版的生成入口，
       确认：返回的不是食谱，而是「建议就医」提示；提示里有具体超标项、下一步建议、免责声明。
    2. 确认**正常报告 / 没有报告**的用户**不会**看到这张卡片（宁可漏报不可误报那一侧的回归）。
    3. 确认两处 JSON 接口的错误形状一致（code 1001 + data.risk），前端才能共用一套处理。

为什么不去真打生成接口
    "未被拦截"的分支会真的调大模型，本测试要离线可跑、且不能烧额度，
    因此那一侧改为直接渲染模板 + 断言风险判定结果。

用法
    python test_risk_alert_page.py
"""
import os
import sys
import tempfile
from datetime import datetime

from test_health_metrics_page import build_isolated_app

from utils.database import db
from utils.auth import generate_jwt_token
from utils.risk_guard import check_health_risk
from models.health_report import HealthReport
from models.health_metric import HealthMetric      # noqa: F401  建表需要
from models.recipe import Recipe                   # noqa: F401
from models.fridge_image import FridgeImage        # noqa: F401
from models.ingredient import Ingredient           # noqa: F401

# 危值报告：血糖 17.2 + 血压 185/115 → 必然 blocked
_CRITICAL_TEXT = (
    "身高 170 cm 体重 78 kg 空腹血糖 17.2 mmol/L 血压 185/115 mmHg 总胆固醇 6.4 mmol/L"
)
# 正常报告：全部在安全范围 → 不应被拦
_NORMAL_TEXT = "身高 170 cm 体重 70 kg 空腹血糖 5.4 mmol/L 血压 118/76 mmHg 总胆固醇 4.2 mmol/L"


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
    print('「建议就医」提示自测')
    print('=' * 78)

    tmpdir = tempfile.mkdtemp(prefix='risk_alert_test_')
    db_path = os.path.join(tmpdir, 'r.db')
    app = build_isolated_app(db_path)

    try:
        with app.app_context():
            db.session.add(HealthReport(
                user_id=1, filename='critical.png', filepath='/tmp/critical.png',
                content_text=_CRITICAL_TEXT, uploaded_at=datetime(2026, 9, 1, 8, 0),
            ))
            db.session.add(HealthReport(
                user_id=2, filename='normal.png', filepath='/tmp/normal.png',
                content_text=_NORMAL_TEXT, uploaded_at=datetime(2026, 9, 1, 8, 0),
            ))
            db.session.commit()
            print('临时库已造：用户1=危值报告，用户2=正常报告，用户3=无报告')
        print('-' * 78)

        client = app.test_client()

        # ────────── 一、网页版 · 生成食谱（用户1，危值） ──────────
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.post('/api/recipes/generate_recipe', data={'query': '随便来个菜'})
        html = resp.get_data(as_text=True)

        check('危值报告返回 200（不是错误页）', resp.status_code == 200, str(resp.status_code))
        check('出现「建议尽快就医」标题', '建议尽快就医' in html)
        check('标明本次未生成食谱', '本次未生成食谱' in html)
        check('显示后端给出的一句话说明', '没有生成食谱' in html)
        check('逐条列出超标项', 'risk-alert-item' in html)
        check('血糖危值原文可见', '17.2' in html)
        check('血压危象原文可见', '185' in html and '115' in html)
        check('超标项打「需就医」标签', '需就医' in html)
        check('给出下一步建议（就诊科室）', '内分泌科' in html and '心血管内科' in html)
        check('提醒不要自行改药', '不要自行加量或停药' in html)
        check('附免责声明', '不能替代医师' in html)
        check('提供「上传新的健康报告」入口',
              'upload/health_report' in html or '上传新的健康报告' in html)
        check('提供「查看健康指标」入口', '查看我的健康指标' in html)
        check('生成表单已收起（避免反复点必然失败的按钮）',
              'id="generateForm"' not in html)
        check('未出现「开始生成个性化食谱」按钮', '开始生成个性化食谱' not in html)
        check('不再重复 flash 同一段长文案', 'flash-error' not in html)

        # ────────── 二、网页版 · 膳食计划（用户1，危值） ──────────
        resp = client.post('/api/recipes/meal_plan', data={'days': '7', 'preference': ''})
        html_meal = resp.get_data(as_text=True)
        check('膳食计划同样被拦并返回 200', resp.status_code == 200, str(resp.status_code))
        check('膳食计划页出现就医卡片', '建议尽快就医' in html_meal)
        check('膳食计划表单已收起', 'meal-plan-form-card' not in html_meal)
        check('未出现「生成膳食计划」按钮', '生成膳食计划</button>' not in html_meal)
        check('不再重复 flash', 'flash-error' not in html_meal)

        # ────────── 三、网页版 · JSON 请求（形状统一） ──────────
        resp = client.post('/api/recipes/generate_recipe',
                           json={'query': '随便来个菜'})
        payload = resp.get_json() or {}
        check('JSON 请求返回 409', resp.status_code == 409, str(resp.status_code))
        check('code = 1001', payload.get('code') == 1001, str(payload.get('code')))
        check('带 data.risk（与小程序接口同形状）',
              bool(payload.get('data', {}).get('risk')), str(list(payload.keys())))
        check('data.risk.blocked = true',
              payload.get('data', {}).get('risk', {}).get('blocked') is True)
        check('data.risk 含 findings',
              len(payload.get('data', {}).get('risk', {}).get('findings', [])) >= 2)
        check('顶层保留 risk（向后兼容）', bool(payload.get('risk')))

        # ────────── 四、小程序接口（api_v1） ──────────
        with app.app_context():
            token = generate_jwt_token(1)
        resp = client.post('/api/v1/recipes/generate',
                           json={'query': '随便来个菜'},
                           headers={'Authorization': f'Bearer {token}'})
        payload = resp.get_json() or {}
        check('小程序接口同样 409', resp.status_code == 409, str(resp.status_code))
        check('小程序接口 code = 1001', payload.get('code') == 1001, str(payload.get('code')))
        check('小程序接口带 data.risk',
              bool(payload.get('data', {}).get('risk', {}).get('findings')),
              str(payload)[:200])

        resp = client.post('/api/v1/recipes/meal_plan',
                           json={'days': 3, 'preference': ''},
                           headers={'Authorization': f'Bearer {token}'})
        payload = resp.get_json() or {}
        check('小程序膳食计划同样被拦',
              resp.status_code == 409 and payload.get('code') == 1001,
              f"{resp.status_code} {str(payload)[:120]}")

        # ────────── 五、未被拦截的那一侧（不能误报） ──────────
        # GET 生成页（无任何风险）→ 表单在，卡片不在
        resp = client.get('/api/recipes/generate_recipe')
        html_plain = resp.get_data(as_text=True)
        check('无风险时显示生成表单', 'id="generateForm"' in html_plain)
        check('无风险时没有就医卡片', '建议尽快就医' not in html_plain)

        # 正常报告 → 风险判定不拦，卡片不出现
        normal_risk = check_health_risk(_NORMAL_TEXT)
        check('正常报告不被拦截', normal_risk['blocked'] is False and normal_risk['level'] == 'ok',
              str(normal_risk['level']))
        check('正常报告 findings 为空', normal_risk['findings'] == [])

        # 正常报告用户（用户2）：不带 risk 渲染 → 仍然是表单
        with client.session_transaction() as sess:
            sess['user_id'] = 2
        resp = client.get('/api/recipes/meal_plan')
        html_meal_plain = resp.get_data(as_text=True)
        check('膳食计划未被拦时显示表单', 'meal-plan-form-card' in html_meal_plain)
        check('膳食计划未被拦时无卡片', '建议尽快就医' not in html_meal_plain)

        # 无报告用户（用户3）：空文本 → 不该出现卡片
        empty_risk = check_health_risk('')
        check('无报告用户不被拦截', empty_risk['blocked'] is False)

    finally:
        try:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
        except Exception:
            pass
        try:
            os.remove(db_path)
            os.rmdir(tmpdir)
        except OSError:
            pass

    # ────────── 六、真实 app 的模板能被渲染（只读） ──────────
    print('-' * 78)
    try:
        from app import app as real_app
        with real_app.test_request_context('/'):
            from flask import render_template
            rendered = render_template(
                'recipe/generate.html',
                risk={
                    'level': 'critical', 'blocked': True,
                    'message': '测试文案',
                    'findings': [{'level': 'critical', 'name': '空腹血糖', 'value': 17.2,
                                  'unit': 'mmol/L', 'message': '空腹血糖 17.2 mmol/L，已达危险水平，需尽快就医'}],
                    'disclaimer': '不能替代医师的诊断与处方。',
                },
            )
        check('真实 app 的模板可渲染就医卡片', '建议尽快就医' in rendered and '17.2' in rendered)
        check('渲染出的卡片含免责声明', '不能替代医师' in rendered)
    except Exception as exc:                                   # noqa: BLE001
        check('真实 app 模板渲染', False, f'{type(exc).__name__}: {exc}')

    print('-' * 78)
    print(f'通过 {passed}/{total}')
    return passed == total


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
