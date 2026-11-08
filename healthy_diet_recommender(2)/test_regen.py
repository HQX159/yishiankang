# -*- coding: utf-8 -*-
"""自测：「换一道」—— 沿用原食谱三维度重新生成一份不同的

覆盖：
    1. 详情页有「换一道」按钮，带正确的接口地址
    2. 未登录 → 401，并给出去登录页的 redirect_url
    3. 别人的食谱 → 404（不能越权换别人的）
    4. 正常换一道 → 200，新食谱入库、属于自己、三维度继承原食谱
    5. 原食谱原封不动（不删不改）—— 用户可以对比后再决定留哪个
    6. 提示词里明确要求「不要做成刚才那道」（否则同参数容易换汤不换药）
    7. 健康报告触发风险拦截 → 409 + code 1001，且**不落库**
       （换一道不能变成绕过「建议就医」的后门）
    8. 改造前的老食谱（三维度为空）→ 兜到默认档，不崩
    9. session 里记的原始需求会被复用；对不上时退回通用话术

LLM 全程 mock。临时库，不碰 app.db。

用法
    py test_regen.py
"""
import os
import sys
import tempfile
from unittest.mock import patch

sys.stdout.reconfigure(encoding='utf-8')

dbpath = os.path.join(tempfile.gettempdir(), 'ysak_regen_test.db')
if os.path.exists(dbpath):
    os.remove(dbpath)
os.environ['DATABASE_URL'] = 'sqlite:///' + dbpath.replace('\\', '/')

sys.path.insert(0, '.')
from app import create_app                     # noqa: E402
from utils.database import db                  # noqa: E402
from models.user import User                   # noqa: E402
from models.recipe import Recipe               # noqa: E402
from models.health_report import HealthReport  # noqa: E402

app = create_app()
client = app.test_client()

FAKE_RECIPE = """**菜品名称**：凉拌鸡丝黄瓜

**所需食材**：
- 黄瓜 200g
- 鸡胸肉 120g
- 蒜末 少许

**烹饪步骤**：
1. 鸡胸肉煮熟撕成丝
2. 黄瓜切丝，与鸡丝拌匀

**营养信息**：热量约 240 kcal | 蛋白质 28 g | 碳水 8 g | 脂肪 9 g

**健康提示**：低碳水，适合控糖加餐
"""

GOOD_REPORT = "体检报告\n身高 170cm 体重 78kg\n空腹血糖 5.2 mmol/L\n"
DANGER_REPORT = "体检报告\n空腹血糖 22.5 mmol/L\n出现酮症\n"

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


def make_recipe(**kw):
    with app.app_context():
        r = Recipe(user_id=kw.pop('user_id', UID), title='清炒西兰花鸡胸',
                   ingredients='西兰花 300g\n鸡胸肉 150g',
                   instructions='1. 焯水\n2. 翻炒',
                   nutrition_info='热量约 320 kcal', **kw)
        db.session.add(r)
        db.session.commit()
        return r.id


def recipe_by_id(rid):
    with app.app_context():
        return db.session.get(Recipe, rid)


def count_recipes(uid=None):
    with app.app_context():
        q = Recipe.query
        if uid is not None:
            q = q.filter_by(user_id=uid)
        return q.count()


def regen(rid, **kw):
    """调换一道，同时抓走 LLM 收到的 prompt 以便断言。"""
    with patch('routes.recipe_routes.generate_recipe_with_rag',
               return_value=FAKE_RECIPE) as m:
        r = client.post('/api/recipes/recipes/%d/regenerate' % rid, **kw)
        return r, m


# ── 准备两个账号 ────────────────────────────────────────────
client.post('/api/user/register', data={'username': 'regen_a', 'password': 'abc123'})
client.post('/api/user/login', data={'identifier': 'regen_a', 'password': 'abc123'})
with app.app_context():
    UID = User.query.filter_by(username='regen_a').first().id

other = app.test_client()
other.post('/api/user/register', data={'username': 'regen_b', 'password': 'abc123'})
other.post('/api/user/login', data={'identifier': 'regen_b', 'password': 'abc123'})
with app.app_context():
    UID_B = User.query.filter_by(username='regen_b').first().id

# ── 1) 详情页按钮 ──────────────────────────────────────────
print('=== 1) 详情页的「换一道」入口 ===')
rid = make_recipe(purpose='control_sugar', flavor='plain', meal_time='lunch')
html = client.get('/api/recipes/recipes/%d' % rid).get_data(as_text=True)
check('详情页有换一道按钮', '换一道' in html)
check('按钮带正确的接口地址',
      '/api/recipes/recipes/%d/regenerate' % rid in html)
check('按钮有说明（tooltip）', 'title="用同样的饮食目的' in html)

# ── 2) 未登录 ──────────────────────────────────────────────
print('=== 2) 未登录 ===')
anon = app.test_client()
r = anon.post('/api/recipes/recipes/%d/regenerate' % rid)
check('未登录返回 401', r.status_code == 401, 'status=%s' % r.status_code)
check('未登录给出去登录页', '/api/user/login' in (r.get_json() or {}).get('redirect_url', ''))

# ── 3) 越权 ────────────────────────────────────────────────
print('=== 3) 换别人的食谱 ===')
before_b = count_recipes(UID_B)
r, _m = regen(rid)
check('本账号换自己的 → 不是 404', r.status_code != 404)
r2 = other.post('/api/recipes/recipes/%d/regenerate' % rid)
check('别人换我的 → 404', r2.status_code == 404, 'status=%s' % r2.status_code)
check('越权时没有替别人新建食谱', count_recipes(UID_B) == before_b,
      'B=%s before=%s' % (count_recipes(UID_B), before_b))

# ── 4) 正常换一道 ──────────────────────────────────────────
print('=== 4) 正常换一道 ===')
add_report(GOOD_REPORT)
before = count_recipes(UID)
r, m = regen(rid)
data = r.get_json() or {}
check('返回 200', r.status_code == 200, 'status=%s body=%s' % (r.status_code, data))
check('返回新食谱 id', data.get('recipe_id') and data['recipe_id'] != rid, data)
check('返回跳转地址', data.get('redirect_url', '').endswith('/%s' % data.get('recipe_id')))
check('食谱总数 +1', count_recipes(UID) == before + 1)

new = recipe_by_id(data['recipe_id'])
check('新食谱属于自己', new.user_id == UID)
check('新食谱标题来自模型', new.title == '凉拌鸡丝黄瓜', new.title)
check('继承了目的', new.purpose == 'control_sugar', new.purpose)
check('继承了口味', new.flavor == 'plain', new.flavor)
check('继承了用餐时间', new.meal_time == 'lunch', new.meal_time)
check('营养信息带了免责声明', '———' in (new.nutrition_info or ''))

# ── 5) 原食谱不动 ──────────────────────────────────────────
print('=== 5) 原食谱保留 ===')
old = recipe_by_id(rid)
check('原食谱还在', old is not None)
check('原食谱标题没被改', old.title == '清炒西兰花鸡胸', old.title)
check('原食谱内容没被改', old.instructions == '1. 焯水\n2. 翻炒', old.instructions)

# ── 6) 提示词要求「不要重复」────────────────────────────────
print('=== 6) 提示词约束 ===')
prompt = m.call_args
args = prompt[0] if prompt else ()
kwargs = prompt[1] if len(prompt) > 1 else {}
query_sent = args[2] if len(args) > 2 else kwargs.get('user_query', '')
check('提示词点名了原菜名', '清炒西兰花鸡胸' in query_sent, query_sent[:120])
check('提示词要求换做法/搭配', '换一种做法或搭配' in query_sent, query_sent[:120])
check('三维度照原样传下去',
      kwargs.get('purpose') == 'control_sugar' and
      kwargs.get('flavor') == 'plain' and
      kwargs.get('meal_time') == 'lunch', kwargs)

# ── 7) 风险拦截 ────────────────────────────────────────────
print('=== 7) 健康风险拦截 ===')
clear_reports()
add_report(DANGER_REPORT)
before = count_recipes(UID)
r, m = regen(rid)
data = r.get_json() or {}
check('风险时返回 409', r.status_code == 409, 'status=%s' % r.status_code)
check('带 code 1001', data.get('code') == 1001, data)
check('给出去生成页（看就医提示）',
      '/generate_recipe' in data.get('redirect_url', ''), data)
check('风险时**没有**调用模型', not m.called)
check('风险时没有落库', count_recipes(UID) == before)

# ── 8) 老食谱兜底 ─────────────────────────────────────────
print('=== 8) 改造前的老食谱（三维度为空）===')
clear_reports()
add_report(GOOD_REPORT)
old_id = make_recipe()          # 三列全 None
r, m = regen(old_id)
data = r.get_json() or {}
check('老食谱也能换一道', r.status_code == 200, 'status=%s %s' % (r.status_code, data))
new = recipe_by_id(data.get('recipe_id') or 0)
check('空目的兜到控糖（红线一侧）', new.purpose == 'control_sugar', new.purpose)
check('空口味兜到不挑', new.flavor == 'any', new.flavor)
check('空用餐时间兜到午餐', new.meal_time == 'lunch', new.meal_time)

# ── 9) session 里的原始需求 ────────────────────────────────
print('=== 9) 复用用户当初手写的需求 ===')
with client.session_transaction() as sess:
    sess['last_gen_recipe_id'] = old_id
    sess['last_gen_query'] = '想吃鱼，清淡点'
r, m = regen(old_id)
args = m.call_args[0] if m.call_args else ()
query_sent = args[2] if len(args) > 2 else ''
check('复用了原始需求', '想吃鱼' in query_sent, query_sent[:120])
check('同时仍然要求不重复', '换一种做法或搭配' in query_sent, query_sent[:120])

with client.session_transaction() as sess:
    sess['last_gen_recipe_id'] = 99999      # 对不上号
    sess['last_gen_query'] = '想吃鱼，清淡点'
r, m = regen(old_id)
args = m.call_args[0] if m.call_args else ()
query_sent = args[2] if len(args) > 2 else ''
check('对不上号时不乱用别人的需求', '想吃鱼' not in query_sent, query_sent[:120])

# ── 汇总 ──────────────────────────────────────────────────
print()
print('=' * 52)
print('通过 %d 项，失败 %d 项' % (passed, failed))
print('=' * 52)
sys.exit(0 if failed == 0 else 1)
