# test_api_recipe_extras.py
"""
食谱 JSON 接口的补强自测 —— 控糖档位字段 + 「换一道」接口

为什么单独一套
    这两件事都是**为小程序 / 鸿蒙补的**（网页端早就有控糖徽标和换一道按钮），
    所以要在 JSON 这一侧证明"客户端拿到的东西不比网页少"。

覆盖什么
    1. 食谱列表 / 详情都带 gi（tier / tier_label / high_names / consult）
    2. gi 的档位来自 utils.diabetes_advice，**接口没有另算一套**
       （断言与直接调 build_advice 的结果一致）
    3. 认不出食材时是 unknown + 「未收录」，不是默认判成低升糖
    4. 收藏列表也带 gi
    5. 社区分享帖里内嵌的 recipe 也带 gi
    6. 换一道：别人的食谱 / 不存在的 id → 404
    7. 换一道：报告达危险水平 → code 1001 + 409（带 data.risk），
       **不生成新食谱**（这是"不能绕过建议就医"的核心断言）
    8. 换一道：正常 → 200，新食谱沿用原食谱的三维度，返回 from_recipe_id
    9. 换一道：客户端传 query 时覆盖默认话术
   10. 换一道：不传 token → 401

为什么不真调大模型
    用 monkeypatch 把 generate_recipe_with_rag 换掉（记录入参、返回固定文本），
    测的是路由逻辑本身。真调模型既慢又花钱，还会让自测结果随机。

用法
    python test_api_recipe_extras.py
"""
import os
import shutil
import sys
import tempfile

TMPDIR = tempfile.mkdtemp(prefix='api_recipe_extras_')
DB_PATH = os.path.join(TMPDIR, 't.db').replace('\\', '/')
# 必须在 import config 之前设：config.py 在 import 时就把 DATABASE_URL 读成类属性
os.environ['DATABASE_URL'] = 'sqlite:///' + DB_PATH

from flask import Flask                                    # noqa: E402
from config import config as app_config                    # noqa: E402
from utils.database import db                              # noqa: E402
from utils.auth import generate_jwt_token                  # noqa: E402
from utils.diabetes_advice import build_advice             # noqa: E402
from utils.recipe_options import normalize_all             # noqa: E402
import routes.api_v1 as api_v1                             # noqa: E402
from models.user import User                                # noqa: E402
from models.health_report import HealthReport               # noqa: E402
from models.recipe import Recipe, Favorite                  # noqa: E402
from models.community import CommunityPost                  # noqa: E402

passed = 0
total = 0

FAKE_RECIPE_TEXT = (
    "**菜品名称**：清炒时蔬\n"
    "**所需食材**：西兰花 200g、胡萝卜 100g、植物油 5ml\n"
    "**烹饪步骤**：1. 西兰花切小朵焯水；2. 热锅少油快炒 3 分钟。\n"
    "**营养信息**：热量约 120 kcal，碳水约 12 g\n"
)


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


def main():
    app = Flask(__name__)
    app.config.from_object(app_config['default'])
    app.config['TESTING'] = True
    db.init_app(app)
    app.register_blueprint(api_v1.api_v1_bp, url_prefix='/api/v1')

    with app.app_context():
        db.create_all()
        alice = User(username='rx_alice')
        alice.set_password('pw123456')
        bob = User(username='rx_bob')
        bob.set_password('pw123456')
        db.session.add_all([alice, bob])
        db.session.commit()
        alice_id, bob_id = alice.id, bob.id

        high = Recipe(user_id=alice_id, title='白米饭配馒头',
                      ingredients='白米饭 200g、馒头 1 个',
                      instructions='1. 蒸熟。', nutrition_info='碳水较高',
                      purpose='控糖', flavor='清淡', meal_time='午餐')
        low = Recipe(user_id=alice_id, title='清炒西兰花',
                     ingredients='西兰花 200g、胡萝卜 100g',
                     instructions='1. 快炒。', nutrition_info='清淡',
                     purpose='控糖', flavor='清淡', meal_time='午餐')
        odd = Recipe(user_id=alice_id, title='神秘野菜',
                     ingredients='月亮菜 100g',
                     instructions='1. 炒。', nutrition_info='—',
                     purpose='控糖', flavor='清淡', meal_time='午餐')
        bob_recipe = Recipe(user_id=bob_id, title='别人的菜',
                            ingredients='西兰花 100g', instructions='1. 炒。',
                            nutrition_info='—')
        db.session.add_all([high, low, odd, bob_recipe])
        db.session.commit()
        high_id, low_id, odd_id, bob_recipe_id = high.id, low.id, odd.id, bob_recipe.id

        # 收藏一条，验证收藏列表也带 gi
        db.session.add(Favorite(user_id=alice_id, recipe_id=low_id))
        # 分享一条到社区，验证社区帖里内嵌的 recipe 也带 gi
        db.session.add(CommunityPost(user_id=alice_id, recipe_id=high_id, note='挺香'))
        db.session.commit()

        token = generate_jwt_token(alice_id)
        token_bob = generate_jwt_token(bob_id)

    client = app.test_client()
    auth = {'Authorization': 'Bearer ' + token}
    auth_bob = {'Authorization': 'Bearer ' + token_bob}

    print('=' * 78)
    print('食谱接口补强自测（控糖档位 / 换一道）')
    print('=' * 78)

    # 期望值直接现算，不靠手记食材表 —— 否则食材表一改测试就假红/假绿
    expect_high = build_advice('白米饭 200g、馒头 1 个')
    expect_odd = build_advice('月亮菜 100g')

    # --- 1. 列表带 gi -----------------------------------------------------
    resp = client.get('/api/v1/recipes', headers=auth)
    body = json_of(resp)
    recipes = (body.get('data') or {}).get('recipes') or []
    check('食谱列表返回 3 条', len(recipes) == 3, str(len(recipes)))
    by_id = {r['id']: r for r in recipes}
    check('列表里每条都有 gi 字段',
          all('gi' in r for r in recipes), str([r.get('id') for r in recipes]))
    check('高升糖那道判成 high',
          (by_id.get(high_id) or {}).get('gi', {}).get('tier') == 'high',
          str((by_id.get(high_id) or {}).get('gi')))
    check('tier_label 是给人看的中文',
          (by_id.get(high_id) or {}).get('gi', {}).get('tier_label') == expect_high['tier_label'],
          str((by_id.get(high_id) or {}).get('gi')))
    check('高升糖时点名了具体食材',
          bool((by_id.get(high_id) or {}).get('gi', {}).get('high_names')),
          str((by_id.get(high_id) or {}).get('gi')))
    check('consult 与 diabetes_advice 口径一致',
          (by_id.get(high_id) or {}).get('gi', {}).get('consult') == expect_high['consult'],
          str((by_id.get(high_id) or {}).get('gi', {}).get('consult')))

    # --- 2. 认不出 → unknown（不是默认低升糖）-----------------------------
    gi_odd = (by_id.get(odd_id) or {}).get('gi') or {}
    check('食材全认不出时 tier=unknown', gi_odd.get('tier') == 'unknown', str(gi_odd))
    check('unknown 的标签是「未收录」', gi_odd.get('tier_label') == '未收录', str(gi_odd))
    check('unknown 与 build_advice 一致', gi_odd.get('tier') == expect_odd['tier'],
          str((gi_odd, expect_odd['tier'])))

    # --- 3. 详情带 gi -----------------------------------------------------
    resp = client.get('/api/v1/recipes/%d' % low_id, headers=auth)
    body = json_of(resp)
    gi_low = (body.get('data') or {}).get('recipe', {}).get('gi') or {}
    check('详情页食谱带 gi', 'tier' in gi_low and gi_low.get('tier') != 'unknown', str(gi_low))
    check('详情页 gi 与 build_advice 一致',
          gi_low.get('tier') == build_advice('西兰花 200g、胡萝卜 100g')['tier'], str(gi_low))

    # --- 4. 收藏列表带 gi -------------------------------------------------
    resp = client.get('/api/v1/recipes/favorites', headers=auth)
    favs = (json_of(resp).get('data') or {}).get('recipes') or []
    check('收藏列表带 gi', favs and 'gi' in favs[0], str(favs[:1]))

    # --- 5. 社区帖里内嵌的 recipe 带 gi -----------------------------------
    resp = client.get('/api/v1/community/posts', headers=auth)
    posts = (json_of(resp).get('data') or {}).get('posts') or []
    check('社区帖返回 1 条', len(posts) == 1, str(len(posts)))
    inner = (posts[0].get('recipe') if posts else None) or {}
    check('社区帖内嵌食谱带 gi', inner.get('gi', {}).get('tier') == 'high', str(inner.get('gi')))

    # --- 6. 换一道：鉴权与归属 --------------------------------------------
    resp = client.post('/api/v1/recipes/%d/regenerate' % high_id, json={})
    check('换一道不带 token → 401', resp.status_code == 401, str(resp.status_code))

    resp = client.post('/api/v1/recipes/%d/regenerate' % high_id, json={}, headers=auth_bob)
    check('换别人的食谱 → 404（不透露存在性）', resp.status_code == 404, str(resp.status_code))

    resp = client.post('/api/v1/recipes/999999/regenerate', json={}, headers=auth)
    check('换不存在的食谱 → 404', resp.status_code == 404, str(resp.status_code))

    # --- 7. 换一道：危险报告必须被拦住 ------------------------------------
    with app.app_context():
        danger = HealthReport(user_id=alice_id, filename='d.txt',
                              filepath='uploads/d.txt',
                              content_text='空腹血糖 25.6 mmol/L')
        db.session.add(danger)
        db.session.commit()
        before = Recipe.query.filter_by(user_id=alice_id).count()

    resp = client.post('/api/v1/recipes/%d/regenerate' % high_id, json={}, headers=auth)
    body = json_of(resp)
    check('危险报告下换一道 → 409', resp.status_code == 409, str(resp.status_code))
    check('错误码是 1001（客户端据此跳就医页）', body.get('code') == 1001, str(body.get('code')))
    check('带上了风险详情 data.risk', bool((body.get('data') or {}).get('risk')),
          str((body.get('data') or {}).keys()))
    with app.app_context():
        after = Recipe.query.filter_by(user_id=alice_id).count()
    check('被拦住时没有生成新食谱（行数不变）', after == before, str((before, after)))

    # --- 8. 换一道：正常路径（假生成，不调大模型）-------------------------
    calls = []

    def fake_generate(health_text, ingredients_text, user_query, **kwargs):
        calls.append({'query': user_query, 'kwargs': kwargs})
        return FAKE_RECIPE_TEXT

    original = api_v1.generate_recipe_with_rag
    api_v1.generate_recipe_with_rag = fake_generate
    try:
        with app.app_context():                     # 删掉危险报告，回到正常状态
            HealthReport.query.filter_by(user_id=alice_id).delete()
            db.session.commit()

        resp = client.post('/api/v1/recipes/%d/regenerate' % high_id, json={}, headers=auth)
        body = json_of(resp)
        check('正常换一道 → 200 / code=0',
              resp.status_code == 200 and body.get('code') == 0,
              str((resp.status_code, body.get('code'))))
        new_recipe = (body.get('data') or {}).get('recipe') or {}
        check('返回了新食谱且 id 与原食谱不同',
              new_recipe.get('id') and new_recipe['id'] != high_id,
              str(new_recipe.get('id')))
        check('新食谱带 gi（客户端不用再算一遍）', 'gi' in new_recipe, str(new_recipe.get('gi')))
        check('返回 from_recipe_id 指向原食谱',
              (body.get('data') or {}).get('from_recipe_id') == high_id,
              str((body.get('data') or {}).get('from_recipe_id')))
        check('默认话术里包含「不要做成<原标题>」',
              calls and '不要做成' in calls[-1]['query'], str(calls[-1]['query'] if calls else None))

        # 三维度沿用原食谱（落库存英文 key，中文只在展示层映射 —— 见 utils/recipe_options.py）
        with app.app_context():
            saved = db.session.get(Recipe, new_recipe['id'])
            got = (saved.purpose, saved.flavor, saved.meal_time)
            expected = tuple(normalize_all({'purpose': '控糖', 'flavor': '清淡',
                                            'meal_time': '午餐'})[k]
                             for k in ('purpose', 'flavor', 'meal_time'))
            check('新食谱沿用原食谱的三维度', got == expected, str((got, expected)))
            check('新食谱标题来自解析结果', saved.title == '清炒时蔬', saved.title)

        # 客户端传 query 时覆盖默认话术
        calls.clear()
        resp = client.post('/api/v1/recipes/%d/regenerate' % high_id,
                           json={'query': '想吃清淡少油的'}, headers=auth)
        check('自定义 query 生效',
              resp.status_code == 200 and calls and calls[-1]['query'] == '想吃清淡少油的',
              str(calls[-1]['query'] if calls else None))
    finally:
        api_v1.generate_recipe_with_rag = original

    # --- 9. 生成失败不乱写库 ----------------------------------------------
    def boom(*args, **kwargs):
        raise RuntimeError('模拟模型超时')

    original = api_v1.generate_recipe_with_rag
    api_v1.generate_recipe_with_rag = boom
    try:
        with app.app_context():
            before = Recipe.query.filter_by(user_id=alice_id).count()
        resp = client.post('/api/v1/recipes/%d/regenerate' % high_id, json={}, headers=auth)
        check('生成异常 → 500 且 code=1',
              resp.status_code == 500 and json_of(resp).get('code') == 1,
              str((resp.status_code, json_of(resp).get('code'))))
        with app.app_context():
            after = Recipe.query.filter_by(user_id=alice_id).count()
        check('失败时没有留下半截食谱', after == before, str((before, after)))
    finally:
        api_v1.generate_recipe_with_rag = original

    print('-' * 78)
    print('通过 %d/%d' % (passed, total))
    return passed == total


if __name__ == '__main__':
    ok_all = False
    try:
        ok_all = main()
    finally:
        try:
            with db.engine.dispose():
                pass
        except Exception:                                   # noqa: BLE001
            pass
        shutil.rmtree(TMPDIR, ignore_errors=True)
    sys.exit(0 if ok_all else 1)
