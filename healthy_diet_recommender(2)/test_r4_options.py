# -*- coding: utf-8 -*-
"""R4 自测：食谱三维度（饮食目的 / 口味 / 用餐时间）

覆盖：
    1. 生成页三组选项都渲染出来，默认值符合方案 §3.2
    2. 「目的」默认值按健康报告推断（血糖超标 / 写明糖尿病 → 控糖），并给出说明
    3. 提交时三维度正确落库（recipe.purpose / flavor / meal_time）
    4. 不传 / 传中文 / 传非法值 都能收敛，不会让生成失败
    5. 标签回显：详情页、列表页
    6. 改造前的老食谱（三列为空）不显示标签行
    7. 控糖会真的改变提示词（注入糖尿病知识 + 硬性食材约束）
    8. api_v1（小程序入口）同样接受三维度

LLM 调用全程 mock，不发真实请求。用临时数据库，不碰 app.db。

用法
    py test_r4_options.py
"""
import os
import sys
import tempfile
from unittest.mock import patch

sys.stdout.reconfigure(encoding='utf-8')

dbpath = os.path.join(tempfile.gettempdir(), 'ysak_r4_test.db')
if os.path.exists(dbpath):
    os.remove(dbpath)
os.environ['DATABASE_URL'] = 'sqlite:///' + dbpath.replace('\\', '/')

sys.path.insert(0, '.')
from app import create_app                     # noqa: E402
from utils.database import db                  # noqa: E402
from utils.auth import generate_jwt_token      # noqa: E402
from models.user import User                   # noqa: E402
from models.recipe import Recipe               # noqa: E402
from models.health_report import HealthReport  # noqa: E402

app = create_app()
client = app.test_client()

FAKE_RECIPE = """**菜品名称**：清炒西兰花鸡胸

**所需食材**：
- 西兰花 300g
- 鸡胸肉 150g
- 橄榄油 少许

**烹饪步骤**：
1. 西兰花掰小朵，焯水 1 分钟
2. 鸡胸肉切片，少油下锅翻炒

**营养信息**：热量约 320 kcal | 蛋白质 35 g | 碳水 18 g | 脂肪 12 g

**健康提示**：控糖友好，主食减半
"""

GOOD_REPORT = "体检报告\n身高 170cm 体重 78kg\n空腹血糖 5.2 mmol/L\n总胆固醇 4.1 mmol/L\n"
SUGAR_REPORT = "体检报告\n身高 170cm 体重 78kg\n空腹血糖 8.9 mmol/L\n糖化血红蛋白 7.8 %\n"

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


def add_report(text):
    with app.app_context():
        db.session.add(HealthReport(user_id=UID, filename='r.pdf', filepath='x',
                                    content_text=text))
        db.session.commit()


def clear_reports():
    with app.app_context():
        HealthReport.query.delete()
        db.session.commit()


def newest_recipe():
    with app.app_context():
        return Recipe.query.filter_by(user_id=UID).order_by(Recipe.id.desc()).first()


def generate(**form):
    data = {'query': '来个菜'}
    data.update(form)
    with patch('routes.recipe_routes.generate_recipe_with_rag', return_value=FAKE_RECIPE):
        return client.post('/api/recipes/generate_recipe', data=data)


# ── 准备账号 ────────────────────────────────────────────────
client.post('/api/user/register', data={'username': 'r4user', 'password': 'abc123'})
client.post('/api/user/login', data={'identifier': 'r4user', 'password': 'abc123'})
with app.app_context():
    UID = User.query.filter_by(username='r4user').first().id

print('=== 1) 生成页：三组选项与默认值（无报告）===')
r = client.get('/api/recipes/generate_recipe')
html = r.get_data(as_text=True)
check('返回 200', r.status_code == 200, 'status=%s' % r.status_code)
check('有饮食目的', '🎯 饮食目的' in html)
check('有口味偏好', '🍲 口味偏好' in html)
check('有用餐时间', '🕐 用餐时间' in html)
check('目的 4 个选项齐全（已收敛）',
      all(k in html for k in ['control_sugar', 'control_fat', 'control_bp', 'gentle']))
check('已下架的 增肌/普通养生 不再出现',
      'value="muscle"' not in html and 'value="balanced"' not in html)
check('口味 6 个选项齐全',
      all(k in html for k in ['plain', 'savory', 'sour_spicy',
                              'sweet', 'sauce', 'any']))
check('用餐时间 5 个选项齐全',
      all(k in html for k in ['breakfast', 'lunch', 'dinner', 'snack', 'night']))
check('默认目的=控糖选中（红线兜底）',
      html.split('value="control_sugar"')[1][:60].count('checked') == 1)
check('默认口味=不挑选中', html.split('value="any"')[1][:60].count('checked') == 1)
check('默认用餐时间=午餐选中', html.split('value="lunch"')[1][:60].count('checked') == 1)
check('没有报告时不显示推断说明', '已根据您的健康报告' not in html)
check('单选是整块可点的卡片', 'class="choice"' in html and 'choice-body' in html)

print('=== 2) 按健康报告推断「目的」默认值 ===')
add_report(SUGAR_REPORT)
html_sugar = client.get('/api/recipes/generate_recipe').get_data(as_text=True)
check('血糖超标 -> 默认选中控糖',
      html_sugar.split('value="control_sugar"')[1][:60].count('checked') == 1)
check('血糖超标 -> 页面上说明一句', '已根据您的健康报告' in html_sugar)
check('血糖超标 -> 其它目的没被选中',
      html_sugar.split('value="control_fat"')[1][:60].count('checked') == 0)

clear_reports()
add_report(GOOD_REPORT)
html_good = client.get('/api/recipes/generate_recipe').get_data(as_text=True)
check('指标正常 -> 不推断，但仍默认控糖（兜底不等于推断）',
      html_good.split('value="control_sugar"')[1][:60].count('checked') == 1)
check('指标正常 -> 不出现推断说明', '已根据您的健康报告' not in html_good)
clear_reports()

print('=== 3) 三维度正确落库 ===')
generate(purpose='control_sugar', flavor='plain', meal_time='dinner')
rec = newest_recipe()
check('purpose 落库', rec.purpose == 'control_sugar', rec.purpose)
check('flavor 落库', rec.flavor == 'plain', rec.flavor)
check('meal_time 落库', rec.meal_time == 'dinner', rec.meal_time)
check('中文标签可用', rec.purpose_label == '控糖' and rec.flavor_label == '清淡原味')

print('=== 4) 不传 / 中文 / 非法值都能收敛 ===')
generate()
rec = newest_recipe()
check('不传三维度 -> 落默认档',
      (rec.purpose, rec.flavor, rec.meal_time) == ('control_sugar', 'any', 'lunch'),
      (rec.purpose, rec.flavor, rec.meal_time))

generate(purpose='控糖', flavor='酸辣', meal_time='夜宵')
rec = newest_recipe()
check('传中文标签也能收敛',
      (rec.purpose, rec.flavor, rec.meal_time) == ('control_sugar', 'sour_spicy', 'night'),
      (rec.purpose, rec.flavor, rec.meal_time))

r = generate(purpose='<script>alert(1)</script>', flavor='!!!', meal_time='zzz')
rec = newest_recipe()
check('非法值回落到默认档而不报错',
      r.status_code in (200, 302) and
      (rec.purpose, rec.flavor, rec.meal_time) == ('control_sugar', 'any', 'lunch'),
      'status=%s tags=%s' % (r.status_code, (rec.purpose, rec.flavor, rec.meal_time)))
check('非法值不会原样写进库', '<script>' not in (rec.purpose or ''))

print('=== 5) 标签回显 ===')
detail = client.get('/api/recipes/recipes/%d' % rec.id).get_data(as_text=True)
check('详情页有标签行', 'tag-row' in detail)
check('详情页显示目的标签', '控糖' in detail)
listhtml = client.get('/api/recipes/recipes').get_data(as_text=True)
check('列表页有标签行', 'tag-row' in listhtml)
check('列表页显示用餐时间标签', '午餐' in listhtml)
rfav = client.get('/api/recipes/favorites')
check('收藏页正常渲染（含新标签片段也不报错）', rfav.status_code == 200,
      'status=%s' % rfav.status_code)

print('=== 6) 老食谱（三列为空）不显示标签 ===')
with app.app_context():
    old = Recipe(user_id=UID, title='改造前的老食谱',
                 ingredients='- 白米饭 1 碗', instructions='1. 煮', nutrition_info='')
    db.session.add(old)
    db.session.commit()
    old_id, old_has = old.id, old.has_tags
check('has_tags 为假', old_has is False)
old_detail = client.get('/api/recipes/recipes/%d' % old_id).get_data(as_text=True)
check('老食谱详情页不出现标签行', 'tag-row' not in old_detail)
check('老食谱 to_dict 的 label 是空串',
      '改造前的老食谱' in old_detail and '"purpose_label": ""' not in old_detail)

print('=== 6b) 已下架 key 的老数据仍能正常显示 ===')
with app.app_context():
    legacy = Recipe(user_id=UID, title='增肌测试老食谱',
                    ingredients='- 鸡胸肉 200g', instructions='1. 煎',
                    nutrition_info='', purpose='muscle', flavor='plain',
                    meal_time='lunch')
    db.session.add(legacy)
    db.session.commit()
    legacy_id = legacy.id
legacy_detail = client.get('/api/recipes/recipes/%d' % legacy_id).get_data(as_text=True)
check('下架 key 不显示成占位符', '🎯 —' not in legacy_detail)
check('下架 key 仍显示原来的中文名', '增肌' in legacy_detail)

print('=== 7) 控糖真的改变提示词 ===')
from utils.recipe_options import build_prompt_clause  # noqa: E402
from utils.rag import build_enhanced_prompt           # noqa: E402
p_sugar = build_enhanced_prompt('空腹血糖 8.9 mmol/L', '西兰花', '来个菜',
                                purpose='control_sugar', flavor='plain', meal_time='lunch')
p_plain = build_enhanced_prompt('空腹血糖 5.2 mmol/L', '西兰花', '来个菜',
                                purpose='gentle', flavor='plain', meal_time='lunch')
check('提示词含三维度要求段', '## 本次生成要求' in p_sugar)
check('控糖时注入糖尿病知识', '【糖尿病】' in p_sugar)
check('控糖时有硬性食材约束', '控糖硬性要求' in p_sugar and '白米饭' in p_sugar)
check('非控糖目的不带控糖硬性约束', '控糖硬性要求' not in p_plain)
check('不传目的时兜底到控糖（红线）',
      '控糖硬性要求' in build_enhanced_prompt('空腹血糖 5.2 mmol/L', '西兰花', '来个菜'))
check('要求段排在用户需求前面',
      p_sugar.find('## 本次生成要求') < p_sugar.find('## 用户需求'))
check('口味会进提示词', '清淡原味' in build_prompt_clause(flavor='plain'))
check('用餐时间会进提示词', '晚餐' in build_prompt_clause(meal_time='dinner'))

print('=== 8) api_v1（小程序入口）===')
with app.app_context():
    token = generate_jwt_token(UID)
auth = {'Authorization': 'Bearer %s' % token}
with patch('routes.api_v1.generate_recipe_with_rag', return_value=FAKE_RECIPE):
    resp = client.post('/api/v1/recipes/generate',
                       json={'query': '来个菜', 'purpose': 'control_sugar',
                             'flavor': 'savory', 'meal_time': 'breakfast'},
                       headers=auth)
payload = resp.get_json() or {}
check('接口 200/201', resp.status_code in (200, 201), 'status=%s' % resp.status_code)
check('响应里带标签字段',
      payload.get('data', {}).get('recipe', {}).get('purpose') == 'control_sugar',
      str(payload)[:200])
check('响应里带中文标签',
      payload.get('data', {}).get('recipe', {}).get('purpose_label') == '控糖')
with patch('routes.api_v1.generate_recipe_with_rag', return_value=FAKE_RECIPE):
    resp2 = client.post('/api/v1/recipes/generate', json={'query': '来个菜'}, headers=auth)
p2 = (resp2.get_json() or {}).get('data', {}).get('recipe', {})
check('小程序不传三维度也照样生成（走默认档）',
      resp2.status_code in (200, 201) and p2.get('purpose') == 'control_sugar', str(p2)[:160])

print()
print('=' * 46)
print('  通过 %d 项，失败 %d 项' % (passed, failed))
print('=' * 46)

try:
    os.remove(dbpath)
except OSError:
    pass
sys.exit(1 if failed else 0)
