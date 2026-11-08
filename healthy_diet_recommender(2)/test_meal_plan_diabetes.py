# -*- coding: utf-8 -*-
"""自测：控糖膳食计划（A1 糖尿病专项改造）

改造前的问题（这条先记下来，否则以后有人会把它改回去）：
    多日膳食计划还是通用版 —— 只排三餐、不要求碳水克数、开口就是「健康膳食计划」。
    同一个系统里，单道食谱详情页讲升糖档位讲得很细，一转身做多日计划又退回通用营养餐。

覆盖：
    一、提示词层（utils/meal_plan_prompt）
        1 五餐齐全 / 2 要求碳水克数 / 3 全天碳水区间 / 4 禁白粥等 / 5 要主食不极端
        6 蛋白质与蔬菜 / 7 偏好·报告·食材·风险代入 / 8 空值兜底
        9 不再含旧通用话术 / 10 max_tokens 随天数增长、30 天不超上限
    二、解析层（parse_meal_plan）
        11 「第 1 天」带空格能认（原实现认不出 → 整份计划解析成空）
        12 「## 第 2 天」「Day 3」也能认
        13 「上午加餐 / 下午加餐」各成一条（原实现整行丢弃）
        14 碳水抽取：中文括号 / 冒号 / 克 / 反向写法
        15 描述里不再残留碳水标注（页面上有徽章，不能重复）
        16 全天碳水合计
        17 乱序时按五餐顺序排
        18 同一天重复的早餐只留第一条
        19 不合理碳水（350g）丢弃
        20 老格式（三餐、无碳水）仍能解析，只是没有 carb
        21 空文本返回 None
    三、两个入口都要接
        22 Web 端页面出现「控糖膳食计划」「上午加餐」「全天碳水约」
        23 Web 端传给模型的提示词是控糖版
        24 api_v1 返回的 JSON 里有 carb / carb_total
        25 风险拦截时 Web 仍提示就医且**不调模型**
        26 风险拦截时 api_v1 返回 409 + code 1001 且**不调模型**

LLM 全程 mock。临时库，不碰 app.db。

用法
    py test_meal_plan_diabetes.py
"""
import os
import re
import sys
import tempfile
from unittest.mock import patch

sys.stdout.reconfigure(encoding='utf-8')

dbpath = os.path.join(tempfile.gettempdir(), 'ysak_mealplan_test.db')
if os.path.exists(dbpath):
    os.remove(dbpath)
os.environ['DATABASE_URL'] = 'sqlite:///' + dbpath.replace('\\', '/')

sys.path.insert(0, '.')
from app import create_app                     # noqa: E402
from utils.database import db                  # noqa: E402
from utils.meal_plan_prompt import (           # noqa: E402
    MEAL_SLOTS, build_meal_plan_prompt, estimate_max_tokens)
from routes.recipe_routes import parse_meal_plan  # noqa: E402
from models.health_report import HealthReport  # noqa: E402
from models.user import User                   # noqa: E402

app = create_app()
client = app.test_client()

passed = 0
failed = 0


def check(name, cond, extra=''):
    global passed, failed
    if cond:
        passed += 1
        print('  PASS  %s' % name)
    else:
        failed += 1
        print('  FAIL  %s   %s' % (name, extra))


# ── 假的模型输出（故意混入真实模型常见的小毛病）────────────────
FAKE_PLAN = """第 1 天：
早餐：燕麦牛奶粥+水煮蛋 - 燕麦 40g 煮粥，配一个鸡蛋（碳水约 38g）
上午加餐：无糖酸奶 100g + 半个苹果（碳水约 14g）
午餐：杂粮饭+清蒸鲈鱼+西兰花 - 杂粮饭小半碗（碳水约 52g）
下午加餐：原味坚果 10g（碳水约 5g）
晚餐：荞麦面+番茄鸡蛋+凉拌菠菜 - 荞麦面 60g（碳水约 45g）

## 第 2 天：
早餐：杂粮馒头半个+豆浆 - 无糖豆浆一杯（碳水约 35g）
晚餐：玉米+清炒虾仁+时蔬 - 玉米一根（碳水约 42g）
午餐：藜麦饭+香菇滑鸡+青菜 - 藜麦饭小半碗（碳水约 50g）
早餐：重复写了一遍早餐（碳水约 99g）

**总体控糖建议：**
1. 早餐不要只喝白粥，蛋白质要跟上。
2. 水果放在加餐吃，每次一个拳头大。
"""

GOOD_REPORT = '体检报告\n身高 170cm 体重 78kg\n空腹血糖 6.8 mmol/L\n糖化血红蛋白 6.5%\n'
DANGER_REPORT = '体检报告\n空腹血糖 22.5 mmol/L\n出现酮症\n'

# ══════════════════════════════════════════════════════════
print('=== 一、提示词层 ===')
# ══════════════════════════════════════════════════════════
p = build_meal_plan_prompt(7, preference='不吃海鲜', health_text='空腹血糖 6.8 mmol/L 偏高',
                           ingredients_text='燕麦, 鸡蛋, 西兰花',
                           risk_note='\n【必须提醒】空腹血糖偏高')
for _k, label in MEAL_SLOTS:
    check('提示词含「%s」' % label, '%s：' % label in p)
check('点明 2 型糖尿病', '2 型糖尿病' in p)
check('要求每餐标碳水克数', '碳水约 XXg' in p)
check('给出全天碳水区间', '130~180g' in p)
check('点名避开白粥', '白粥' in p)
check('要求主食要吃（不走向极端）', '主食要有' in p)
check('要求蛋白质', '蛋白质' in p)
check('要求绿叶蔬菜', '绿叶蔬菜' in p)
check('偏好代入', '不吃海鲜' in p)
check('报告摘要代入', '空腹血糖 6.8' in p)
check('食材代入', '西兰花' in p)
check('风险提醒代入', '【必须提醒】' in p)
check('不再出现旧话术「健康膳食计划」', '健康膳食计划' not in p)
check('不再用「营养均衡」当兜底', '营养均衡' not in p)

p2 = build_meal_plan_prompt(1)
check('空偏好兜底', '无特殊偏好' in p2)
check('空报告兜底', '暂无健康报告' in p2)
check('空食材兜底', '未检测到食材' in p2)

check('3 天 token 够用（>1000）', estimate_max_tokens(3) > 1000, estimate_max_tokens(3))
check('30 天不超上限', estimate_max_tokens(30) <= 8000)
check('token 随天数递增', estimate_max_tokens(14) > estimate_max_tokens(3))

# ══════════════════════════════════════════════════════════
print('=== 二、解析层 ===')
# ══════════════════════════════════════════════════════════
plan = parse_meal_plan(FAKE_PLAN, 2)
days = plan['days']
check('解析出 2 天', len(days) == 2, '实际 %d' % len(days))
check('「第 1 天」带空格能认出', days and days[0]['day'] == 1)
check('「## 第 2 天」能认出', len(days) > 1 and days[1]['day'] == 2)

d1 = days[0]
types = [m['type'] for m in d1['meals']]
check('第 1 天五餐齐全', len(d1['meals']) == 5, types)
check('上午加餐单独成条（原实现会丢）', 'snack_am' in types, types)
check('下午加餐单独成条（原实现会丢）', 'snack_pm' in types, types)
check('早餐碳水抽到 38', d1['meals'][0]['carb'] == 38, d1['meals'][0])
check('上午加餐碳水抽到 14', [m for m in d1['meals'] if m['type'] == 'snack_am'][0]['carb'] == 14)
check('描述里不残留碳水标注',
      all('碳水' not in (m['description'] or '') for m in d1['meals']),
      [m['description'] for m in d1['meals']])
check('早餐名与描述已拆开', d1['meals'][0]['name'].startswith('燕麦牛奶粥'))
check('全天碳水合计 = 154', d1['carb_total'] == 154, d1['carb_total'])

d2 = days[1]
t2 = [m['type'] for m in d2['meals']]
check('乱序输入按五餐顺序排', t2 == ['breakfast', 'lunch', 'dinner'], t2)
check('重复的早餐只留第一条', len([x for x in t2 if x == 'breakfast']) == 1, t2)
check('重复那条没被算进合计（35+50+42=127）', d2['carb_total'] == 127, d2['carb_total'])

check('建议段落去掉 markdown 星号', '**' not in plan['advice'], plan['advice'][:30])
check('建议内容抓到了', '水果放在加餐吃' in plan['advice'])
check('建议不重复模板标题「总体控糖建议」',
      not plan['advice'].startswith('总体控糖建议'), plan['advice'][:20])

# 各种碳水写法
for text, want in [
    ('早餐：小米粥+蛋 - 描述（碳水约 40g）', 40),
    ('早餐：小米粥+蛋 - 描述（碳水：40 克）', 40),
    ('早餐：小米粥+蛋 - 描述 约40g碳水', 40),
    ('早餐：小米粥+蛋 - 描述（碳水约 40.5g）', 40.5),
]:
    got = parse_meal_plan('第 1 天：\n' + text, 1)['days'][0]['meals'][0]['carb']
    check('碳水写法可解析：%s' % text.split('描述')[-1].strip('） '), got == want, got)

bad = parse_meal_plan('第 1 天：\n早餐：怪东西 - 描述（碳水约 350g）', 1)
check('离谱碳水 350g 丢弃', bad['days'][0]['meals'][0]['carb'] is None)
check('全部丢掉时合计为 None', bad['days'][0]['carb_total'] is None)

# 老格式兼容（改造前生成的三餐计划）
legacy = parse_meal_plan('第1天：\n早餐：白粥 - 清淡\n午餐：米饭 - 顶饱\n晚餐：面条 - 好消化\n', 1)
check('老格式三天餐仍能解析', len(legacy['days'][0]['meals']) == 3, legacy['days'][0])
check('老格式没有 carb 字段值', legacy['days'][0]['meals'][0]['carb'] is None)
check('老格式合计为 None（不伪造数字）', legacy['days'][0]['carb_total'] is None)

check('空文本返回 None', parse_meal_plan('', 7) is None)
check('None 返回 None', parse_meal_plan(None, 7) is None)

day1_only = parse_meal_plan('Day 1:\nBreakfast：x', 3)
check('「Day 1」也能认出天数', bool(day1_only['days']), day1_only)

# ══════════════════════════════════════════════════════════
print('=== 三、两个入口都接 ===')
# ══════════════════════════════════════════════════════════
client.post('/api/user/register', data={'username': 'meal_a', 'password': 'abc123'})
client.post('/api/user/login', data={'identifier': 'meal_a', 'password': 'abc123'})
with app.app_context():
    UID = User.query.filter_by(username='meal_a').first().id


def add_report(text):
    with app.app_context():
        HealthReport.query.filter_by(user_id=UID).delete()
        db.session.add(HealthReport(user_id=UID, filename='r.pdf', filepath='x',
                                    content_text=text))
        db.session.commit()


add_report(GOOD_REPORT)

with patch('routes.recipe_routes.LLMClient') as MockLLM:
    MockLLM.return_value.generate_text.return_value = FAKE_PLAN
    r = client.post('/api/recipes/meal_plan', data={'days': '2', 'preference': '不吃海鲜'})
    html = r.get_data(as_text=True)
    sent = MockLLM.return_value.generate_text.call_args[0][0]

check('Web 端页面 200', r.status_code == 200, r.status_code)
check('页面标题改成「控糖膳食计划」', '控糖膳食计划' in html)
check('页面出现上午加餐', '上午加餐' in html)
check('页面出现下午加餐', '下午加餐' in html)
check('每天显示全天碳水合计', '全天碳水约' in html)
check('每餐显示碳水徽章', 'meal-carb' in html and '碳水 38g' in html, '')
check('加餐卡片有独立样式', 'meal-card-snack' in html)
check('页面有免责说明', '不能替代医师' in html)
check('Web 传给模型的是控糖提示词', '2 型糖尿病' in sent)
check('Web 提示词要求碳水克数', '碳水约 XXg' in sent)
check('Web 提示词带上了用户偏好', '不吃海鲜' in sent)
check('Web 提示词带上了报告指标', '空腹血糖' in sent)

# ---- api_v1 ----
tok = client.post('/api/v1/user/login',
                 json={'identifier': 'meal_a', 'password': 'abc123'})
login_data = tok.get_json() or {}
token = ((login_data.get('data') or {}).get('token')
         or (login_data.get('data') or {}).get('access_token'))
check('拿到 JWT（小程序/鸿蒙入口）', bool(token), login_data)

with patch('routes.api_v1.LLMClient') as MockLLM2:
    MockLLM2.return_value.generate_text.return_value = FAKE_PLAN
    r2 = client.post('/api/v1/recipes/meal_plan',
                     json={'days': 2, 'preference': '不吃海鲜'},
                     headers={'Authorization': 'Bearer %s' % token} if token else {})
    body = r2.get_json() or {}
    sent2 = MockLLM2.return_value.generate_text.call_args[0][0]

check('api_v1 返回 200', r2.status_code == 200, r2.status_code)
check('api_v1 code=0', body.get('code') == 0, body.get('code'))
mp = ((body.get('data') or {}).get('meal_plan') or {})
check('api_v1 返回了两天', len(mp.get('days') or []) == 2, mp.get('days'))
first_meals = (mp.get('days') or [{}])[0].get('meals') or []
check('api_v1 每餐带 carb 字段', any(m.get('carb') for m in first_meals), first_meals)
check('api_v1 每天带 carb_total', (mp.get('days') or [{}])[0].get('carb_total') == 154,
      (mp.get('days') or [{}])[0].get('carb_total'))
check('api_v1 用的是同一份控糖提示词', '2 型糖尿病' in sent2 and '上午加餐' in sent2)

# ---- 风险拦截：两个入口都不能调模型 ----
add_report(DANGER_REPORT)

with patch('routes.recipe_routes.LLMClient') as MockLLM3:
    r3 = client.post('/api/recipes/meal_plan', data={'days': '2'})
    html3 = r3.get_data(as_text=True)
check('危险指标时 Web 端不调模型', not MockLLM3.return_value.generate_text.called)
check('危险指标时 Web 端提示就医', '就医' in html3 or '急诊' in html3, html3[:200])

with patch('routes.api_v1.LLMClient') as MockLLM4:
    r4 = client.post('/api/v1/recipes/meal_plan', json={'days': 2},
                     headers={'Authorization': 'Bearer %s' % token} if token else {})
    body4 = r4.get_json() or {}
check('危险指标时 api_v1 返回 409', r4.status_code == 409, r4.status_code)
check('危险指标时 api_v1 code=1001', body4.get('code') == 1001, body4.get('code'))
check('危险指标时 api_v1 不调模型', not MockLLM4.return_value.generate_text.called)

print()
print('=' * 52)
print('通过 %d 项，失败 %d 项' % (passed, failed))
print('=' * 52)
sys.exit(0 if failed == 0 else 1)
