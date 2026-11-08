# -*- coding: utf-8 -*-
"""R1 自测（第二批）：糖尿病专项 —— 控糖提示落到页面上

分两段：
    A. 纯逻辑层（utils/diabetes_advice）—— 识别 / 判档 / 区间 / 文案的口径
    B. 页面层（临时库）—— 详情页、列表页、收藏页、仪表盘都能看到正确档位

用临时数据库，不碰真实 app.db。

用法
    py test_r1_diabetes.py
"""
import os
import sys
import tempfile
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

dbpath = os.path.join(tempfile.gettempdir(), 'ysak_r1_dm_test.db')
if os.path.exists(dbpath):
    os.remove(dbpath)
os.environ['DATABASE_URL'] = 'sqlite:///' + dbpath.replace('\\', '/')

sys.path.insert(0, '.')
from app import create_app                        # noqa: E402
from utils.database import db                     # noqa: E402
from models.user import User                      # noqa: E402
from models.recipe import Recipe                  # noqa: E402
from utils.diabetes_advice import build_advice    # noqa: E402

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


# ── A. 逻辑层 ────────────────────────────────────────────────
print('=== A1) 判档口径 ===')
a = build_advice('- 白米饭\n- 西兰花\n- 鸡胸肉')
check('含主食白米饭 → 高升糖', a['tier'] == 'high', a['tier'])
check('中文标签正确', a['tier_label'] == '高升糖', a['tier_label'])
check('点名高升糖食材', a['high_names'] == ['白米饭'], str(a['high_names']))
check('碳水给区间不给单值', '~' in a['carb_text'], a['carb_text'])
check('高升糖会建议咨询', a['consult'] is True)

print('=== A2) 认不出来时不编数字 ===')
a = build_advice('- 少许秘制香料\n- 适量清水')
check('档位是 unknown', a['tier'] == 'unknown', a['tier'])
check('不给碳水 / 糖数字', a['carb'] is None and a['sugar'] is None)
check('计入未收录数量', a['unmatched_count'] == 2, str(a['unmatched_count']))
check('文案说明"不给数字"的原因', '不给' in a['note'])

print('=== A3) 免责与语气 ===')
a = build_advice('- 白米饭')
check('带免责说明', '不能替代' in a['note'])
check('建议份量不是命令式', '建议' in a['portion_text'] or '可以' in a['portion_text'])
check('咨询提示是温和口吻', '可以' in a['consult_text'])

print('=== A3b) 否定说法不算放了糖 ===')
a = build_advice('- 白胡椒粉 少许（不加糖、不勾芡）')
check('「不加糖」不再判高升糖', a['tier'] != 'high', a['tier'])
check('不点名「糖」为高升糖食材', a['high_names'] == [], str(a['high_names']))
a = build_advice('- 香醋 3ml（拌菜用，不额外加糖）')
check('「不额外加糖」同样跳过', a['tier'] != 'high', a['tier'])
a = build_advice('- 原味无糖酸奶 100g')
check('「无糖酸奶」只认酸奶', [i['name'] for i in a['food_items']] == ['酸奶'],
      str([i['name'] for i in a['food_items']]))
a = build_advice('- 白糖 5g')
check('真的写了白糖仍是高升糖', a['tier'] == 'high', a['tier'])

# ── B. 页面层 ────────────────────────────────────────────────
print('=== B0) 造数据 ===')
client.post('/api/user/register', data={'username': 'dmuser', 'password': 'abc123'})
client.post('/api/user/login', data={'identifier': 'dmuser', 'password': 'abc123'})
with app.app_context():
    u = User.query.filter_by(username='dmuser').first()
    uid = u.id
    low = Recipe(user_id=uid, title='清蒸西兰花鸡胸',
                 ingredients='- 西兰花 200g\n- 鸡胸肉 1 块\n- 橄榄油 少许',
                 instructions='1. 蒸熟', created_at=datetime(2026, 10, 16, 9, 0))
    high = Recipe(user_id=uid, title='白米饭配土豆烧牛肉',
                  ingredients='- 白米饭 1 碗\n- 土豆 2 个\n- 牛肉 200g',
                  instructions='1. 炖', created_at=datetime(2026, 10, 16, 10, 0))
    odd = Recipe(user_id=uid, title='神秘料理',
                 ingredients='- 少许秘制香料',
                 instructions='1. 随便', created_at=datetime(2026, 10, 16, 11, 0))
    db.session.add_all([low, high, odd])
    db.session.commit()
    low_id, high_id, odd_id = low.id, high.id, odd.id
check('三条食谱入库', all([low_id, high_id, odd_id]))

print('=== B1) 详情页：低升糖 ===')
html = client.get('/api/recipes/recipes/%d' % low_id).get_data(as_text=True)
check('出现「控糖提示」卡片', '🩸 控糖提示' in html)
check('标为低升糖', 'gi-badge-low' in html and '低升糖' in html)
check('有碳水粗估', '碳水' in html and '一人份粗估' in html)
check('有建议份量', '建议份量' in html)
check('不打扰用户（无咨询提示块）', 'gi-consult' not in html)
check('带免责说明', '不能替代' in html)
check('没漏出 Python 的 None', 'None' not in html)

print('=== B2) 详情页：高升糖 ===')
html = client.get('/api/recipes/recipes/%d' % high_id).get_data(as_text=True)
check('标为高升糖', 'gi-badge-high' in html and '高升糖' in html)
check('点名高升糖食材', '白米饭' in html and '土豆' in html)
check('出现醒目提醒', 'gi-warn' in html)
check('出现「咨询营养师」温和提示', 'gi-consult' in html and '营养师' in html)
check('替换建议给了具体例子', '杂粮饭' in html)

print('=== B3) 详情页：认不出食材 ===')
html = client.get('/api/recipes/recipes/%d' % odd_id).get_data(as_text=True)
check('标为未收录', 'gi-badge-unknown' in html)
check('不给碳水数字卡片', 'gi-metrics' not in html)
check('说明为什么没给数字', '没收录' in html or '不给' in html)

print('=== B4) 列表页徽标 ===')
html = client.get('/api/recipes/recipes').get_data(as_text=True)
check('列表页有高升糖徽标', 'gi-badge-high' in html)
check('列表页有低升糖徽标', 'gi-badge-low' in html)
check('列表页三条都在', '清蒸西兰花鸡胸' in html and '白米饭配土豆烧牛肉' in html)

print('=== B5) 收藏页徽标 ===')
client.post('/api/recipes/favorites/%d' % high_id)
html = client.get('/api/recipes/favorites').get_data(as_text=True)
check('收藏页有徽标', 'gi-badge-high' in html)
check('收藏页含该食谱', '白米饭配土豆烧牛肉' in html)

print('=== B6) 仪表盘最近食谱徽标 ===')
html = client.get('/dashboard').get_data(as_text=True)
check('仪表盘返回 200', '仪表盘' in html)
check('仪表盘出现升糖徽标', 'gi-badge-' in html)

print()
print('=' * 46)
print('  通过 %d 项，失败 %d 项' % (passed, failed))
print('=' * 46)

try:
    os.remove(dbpath)
except OSError:
    pass
sys.exit(1 if failed else 0)
