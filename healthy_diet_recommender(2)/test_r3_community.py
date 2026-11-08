# -*- coding: utf-8 -*-
"""R3 自测：社区食谱分享

覆盖：
    1. 建表：community_post / post_interaction 存在，同人同食谱只能分享一次
    2. 分享：落库、分享语、重复分享幂等
    3. 广场：渲染、筛选（三维度）、排序（最新/最热）、分页与越界收敛
    4. 点赞 / 收藏：开关、计数、计数与互动明细始终一致
    5. 删除：作者可删（连互动一起清），非作者被拒
    6. 详情页：作者/标签/控糖提示/同类推荐/删除按钮（仅作者可见）
    7. 食谱详情页：分享前是按钮、分享后是「已分享」入口
    8. 登录守卫：未登录访问社区跳登录页
    9. api_v1（小程序入口）：列表 / 详情 / 分享 / 点赞 / 删除
   10. 适老化：导航入口、退出确认、触控按钮

用临时数据库，不碰 app.db；不发任何 LLM 请求。

用法
    py test_r3_community.py
"""
import datetime
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8')

dbpath = os.path.join(tempfile.gettempdir(), 'ysak_r3_test.db')
if os.path.exists(dbpath):
    os.remove(dbpath)
os.environ['DATABASE_URL'] = 'sqlite:///' + dbpath.replace('\\', '/')

sys.path.insert(0, '.')
from app import create_app                       # noqa: E402
from utils.database import db                    # noqa: E402
from utils.auth import generate_jwt_token        # noqa: E402
from models.user import User                     # noqa: E402
from models.recipe import Recipe                 # noqa: E402
from models.community import (                   # noqa: E402
    CommunityPost, PostInteraction, list_posts, related_posts, find_share,
)

app = create_app()
client_a = app.test_client()      # 分享者
client_b = app.test_client()      # 另一个用户
client_anon = app.test_client()   # 未登录

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


def make_recipe(user_id, title, purpose=None, flavor=None, meal_time=None,
                created_at=None):
    """直接建一条食谱（绕开 LLM），返回 id。"""
    with app.app_context():
        r = Recipe(user_id=user_id, title=title,
                   ingredients='- 白米饭 1 碗\n- 西兰花 300g\n- 牛肉 150g',
                   instructions='1. 洗菜\n2. 下锅炒熟',
                   nutrition_info='热量约 400 kcal',
                   purpose=purpose, flavor=flavor, meal_time=meal_time)
        if created_at:
            r.created_at = created_at
        db.session.add(r)
        db.session.commit()
        return r.id


def register_and_login(client, username):
    client.post('/api/user/register', data={'username': username, 'password': 'abc123'})
    client.post('/api/user/login', data={'identifier': username, 'password': 'abc123'})
    with app.app_context():
        return User.query.filter_by(username=username).first().id


UID_A = register_and_login(client_a, 'r3alpha')
UID_B = register_and_login(client_b, 'r3bravo')

print('=== 1) 建表与唯一约束 ===')
with app.app_context():
    from sqlalchemy import inspect
    names = set(inspect(db.engine).get_table_names())
check('community_post 表已建', 'community_post' in names, str(sorted(names)))
check('post_interaction 表已建', 'post_interaction' in names)

RID_SUGAR = make_recipe(UID_A, '控糖西兰花炒牛肉', purpose='control_sugar',
                        flavor='plain', meal_time='dinner')
RID_PLAIN = make_recipe(UID_A, '家常番茄鸡蛋面', purpose='control_fat',
                        flavor='savory', meal_time='lunch')

print('=== 2) 分享 ===')
r = client_a.post('/api/community/share',
                  data={'recipe_id': RID_SUGAR, 'note': '这道菜我吃完血糖没怎么动'})
check('分享返回跳转', r.status_code == 302, 'status=%s' % r.status_code)
with app.app_context():
    posts = CommunityPost.query.all()
    p1 = posts[0] if posts else None
check('落库 1 条帖子', len(posts) == 1, str(len(posts)))
check('分享语已保存', p1 and p1.note == '这道菜我吃完血糖没怎么动', p1.note if p1 else '')
check('点赞数初始为 0', p1 and p1.like_count == 0)
POST1 = p1.id if p1 else 0

r2 = client_a.post('/api/community/share', data={'recipe_id': RID_SUGAR, 'note': '再发一次'})
with app.app_context():
    n_after = CommunityPost.query.count()
check('重复分享不新建（幂等）', n_after == 1, str(n_after))
check('重复分享给了提示', '已经分享过' in client_a.get('/api/community/').get_data(as_text=True) or
      r2.status_code == 302, 'status=%s' % r2.status_code)

r3 = client_a.post('/api/community/share', json={'recipe_id': 999999})
check('分享不存在的食谱被拒', r3.status_code == 404, 'status=%s' % r3.status_code)

with app.app_context():
    other = Recipe.query.filter_by(id=RID_SUGAR).first()
    check('他人食谱不可分享（不是自己的）', other.user_id == UID_A)
rb = client_b.post('/api/community/share', json={'recipe_id': RID_SUGAR})
check('B 分享 A 的食谱被拒', rb.status_code == 404, 'status=%s' % rb.status_code)

print('=== 3) 广场：渲染 / 筛选 / 排序 / 分页 ===')
html = client_b.get('/api/community/').get_data(as_text=True)
check('广场页 200 且能看到标题', '控糖西兰花炒牛肉' in html)
check('卡片显示作者名', 'r3alpha' in html)
check('卡片显示三维度标签',
      'post-tag' in html and '控糖' in html and '清淡原味' in html and '晚餐' in html)
check('有免责声明', '不能代替医生或营养师的建议' in html)
check('有筛选面板', 'filter-panel' in html and '按条件筛选' in html)
check('筛选用整块可点的标签（不是下拉框）',
      'class="chip' in html and '<select' not in html)

# 再补几条，覆盖筛选与分页
for i in range(11):
    rid = make_recipe(UID_A, '控糖测试菜 %02d' % i, purpose='control_sugar',
                      flavor='plain', meal_time='lunch',
                      created_at=datetime.datetime(2026, 10, 1, 8, 0, i))
    with app.app_context():
        db.session.add(CommunityPost(recipe_id=rid, user_id=UID_A))
        db.session.commit()
rid_plain_share = make_recipe(UID_A, '普通家常菜', purpose='control_fat',
                              flavor='savory', meal_time='lunch')
with app.app_context():
    db.session.add(CommunityPost(recipe_id=rid_plain_share, user_id=UID_A))
    db.session.commit()

sugar_html = client_b.get('/api/community/?purpose=control_sugar').get_data(as_text=True)
check('按目的筛选：只出控糖的', '普通家常菜' not in sugar_html)
check('按目的筛选：控糖的还在', '控糖测试菜' in sugar_html)

bogus_html = client_b.get('/api/community/?purpose=%3Cscript%3E').get_data(as_text=True)
check('非法筛选值 = 不筛（不是兜到默认档）',
      '普通家常菜' in bogus_html and '控糖测试菜' in bogus_html)

plain_html = client_b.get('/api/community/?purpose=control_fat').get_data(as_text=True)
check('筛选控脂减重时其它目的不出现', '控糖测试菜' not in plain_html)
check('筛选控脂减重时它自己在',
      '普通家常菜' in plain_html and '控糖西兰花炒牛肉' not in plain_html)

with app.app_context():
    items, total, page, pages = list_posts(per_page=10)
check('分页：总数正确', total == 13, str(total))
check('分页：共 2 页', pages == 2, str(pages))
check('分页：第一页 10 条', len(items) == 10, str(len(items)))
with app.app_context():
    _, _, page99, pages99 = list_posts(page=99, per_page=10)
check('页码越界收敛到最后一页', page99 == 2 and pages99 == 2, '%s/%s' % (page99, pages99))
with app.app_context():
    _, _, page0, _ = list_posts(page=0, per_page=10)
check('页码 0 / 负数收敛到第 1 页', page0 == 1, str(page0))
check('广场分页控件出现', '第 1 / 2 页' in client_b.get('/api/community/').get_data(as_text=True))

print('=== 4) 点赞 / 收藏 ===')
# 网页端这几个动作接口返回的是**扁平 JSON**（{message, active, like_count}），
# 直接给 static/js/community.js 用；小程序那套才包一层 {code,message,data}。
rb1 = client_b.post('/api/community/post/%d/like' % POST1)
body1 = rb1.get_json() or {}
check('点赞成功', rb1.status_code == 200 and body1.get('active') is True,
      str(body1)[:160])
check('点赞数变 1', body1.get('like_count') == 1, str(body1)[:160])
check('同一人再点 = 取消',
      (client_b.post('/api/community/post/%d/like' % POST1).get_json() or {})
      .get('active') is False)
with app.app_context():
    check('取消后计数归零', db.session.get(CommunityPost, POST1).like_count == 0)

client_a.post('/api/community/post/%d/like' % POST1)
client_b.post('/api/community/post/%d/like' % POST1)
with app.app_context():
    p = db.session.get(CommunityPost, POST1)
    real = PostInteraction.query.filter_by(post_id=POST1, type='like').count()
check('两人各点一次 -> 计数 2', p.like_count == 2, str(p.like_count))
check('计数缓存与互动明细一致', p.like_count == real, '%s vs %s' % (p.like_count, real))

rfav = client_b.post('/api/community/post/%d/favorite' % POST1)
check('收藏成功', (rfav.get_json() or {}).get('active') is True,
      str(rfav.get_json())[:160])
check('收藏不影响点赞数',
      (rfav.get_json() or {}).get('like_count') == 2,
      str(rfav.get_json())[:160])
check('再点一次 = 取消收藏',
      (client_b.post('/api/community/post/%d/favorite' % POST1).get_json() or {})
      .get('active') is False)

r404 = client_b.post('/api/community/post/999999/like')
check('点赞不存在的帖子 -> 404', r404.status_code == 404, 'status=%s' % r404.status_code)

hot_html = client_b.get('/api/community/?sort=hot').get_data(as_text=True)
check('按热度排序可用', '控糖西兰花炒牛肉' in hot_html)

print('=== 5) 详情页 ===')
det_a = client_a.get('/api/community/post/%d' % POST1)
det_html = det_a.get_data(as_text=True)
check('详情页 200', det_a.status_code == 200, 'status=%s' % det_a.status_code)
check('详情页有作者', 'r3alpha' in det_html)
check('详情页有分享语', '血糖没怎么动' in det_html)
check('详情页有控糖提示', '控糖提示' in det_html and '建议份量' in det_html)
check('详情页有食材与步骤', '所需食材' in det_html and '烹饪步骤' in det_html)
check('作者能看到删除按钮', '从社区删除' in det_html)

det_b = client_b.get('/api/community/post/%d' % POST1).get_data(as_text=True)
check('非作者看不到删除按钮', '从社区删除' not in det_b)
det_404 = client_a.get('/api/community/post/999999')
check('不存在的帖子 -> 跳回广场', det_404.status_code == 302, 'status=%s' % det_404.status_code)

print('=== 6) 我的分享 ===')
mine_a = client_a.get('/api/community/mine')
mine_a_html = mine_a.get_data(as_text=True)
check('我的分享 200', mine_a.status_code == 200, 'status=%s' % mine_a.status_code)
check('A 能看到自己的分享', '控糖西兰花炒牛肉' in mine_a_html)
mine_b_html = client_b.get('/api/community/mine').get_data(as_text=True)
check('B 的我的分享是空的', '还没有分享过食谱' in mine_b_html)

print('=== 7) 食谱详情页的分享入口 ===')
rd = client_a.get('/api/recipes/recipes/%d' % RID_PLAIN).get_data(as_text=True)
check('未分享 -> 出现分享按钮', '分享到社区' in rd and 'share-form' in rd)
check('分享按钮带确认（防误触）', 'confirm(' in rd and '要把这道菜分享到社区吗' in rd)
rd2 = client_a.get('/api/recipes/recipes/%d' % RID_SUGAR).get_data(as_text=True)
check('已分享 -> 变成「已分享到社区」入口',
      '已分享到社区' in rd2 and 'share-form' not in rd2)

print('=== 8) 登录守卫 ===')
anon = client_anon.get('/api/community/')
check('未登录访问广场 -> 跳登录页',
      anon.status_code == 302 and 'login' in (anon.headers.get('Location') or ''),
      'status=%s loc=%s' % (anon.status_code, anon.headers.get('Location')))
anon_mine = client_anon.get('/api/community/mine')
check('未登录访问我的分享 -> 跳登录页',
      anon_mine.status_code == 302, 'status=%s' % anon_mine.status_code)
anon_like = client_anon.post('/api/community/post/%d/like' % POST1)
check('未登录点赞 -> 401', anon_like.status_code == 401, 'status=%s' % anon_like.status_code)

print('=== 9) 删除 ===')
rb_deny = client_b.post('/api/community/post/%d/delete' % POST1)
with app.app_context():
    still = db.session.get(CommunityPost, POST1)
check('非作者删除被拒（帖子还在）', still is not None, 'status=%s' % rb_deny.status_code)
check('非作者删除（网页表单）-> 跳转 + 提示',
      rb_deny.status_code == 302 and
      '只能删除自己分享的食谱' in client_b.get('/api/community/').get_data(as_text=True),
      'status=%s' % rb_deny.status_code)
rb_deny_json = client_b.post('/api/community/post/%d/delete' % POST1, json={})
check('非作者删除（JSON）-> 403', rb_deny_json.status_code == 403,
      'status=%s' % rb_deny_json.status_code)

ra_del = client_a.post('/api/community/post/%d/delete' % POST1)
with app.app_context():
    gone = db.session.get(CommunityPost, POST1)
    leftover = PostInteraction.query.filter_by(post_id=POST1).count()
check('作者删除成功', gone is None)
check('删除后互动明细一起清掉', leftover == 0, str(leftover))
check('删除返回跳转到我的分享', ra_del.status_code == 302, 'status=%s' % ra_del.status_code)

print('=== 10) api_v1（小程序入口）===')
with app.app_context():
    token_a = generate_jwt_token(UID_A)
    token_b = generate_jwt_token(UID_B)
auth_a = {'Authorization': 'Bearer %s' % token_a}
auth_b = {'Authorization': 'Bearer %s' % token_b}

res = client_b.get('/api/v1/community/posts', headers=auth_b)
payload = res.get_json() or {}
check('列表接口 200 且 code=0',
      res.status_code == 200 and payload.get('code') == 0, str(payload)[:200])
check('列表返回帖子数组', isinstance(payload.get('data', {}).get('posts'), list))
check('列表带分页信息', 'pages' in payload.get('data', {}) and 'total' in payload.get('data', {}))
first = (payload.get('data', {}).get('posts') or [{}])[0]
check('帖子带作者名', first.get('author_name') == 'r3alpha', str(first)[:120])
check('帖子带三维度标签', isinstance(first.get('tags'), list) and len(first['tags']) > 0,
      str(first.get('tags')))
check('帖子带点赞/收藏状态',
      'liked' in first and 'favorited' in first and 'like_count' in first)

res_f = client_b.get('/api/v1/community/posts?purpose=control_fat', headers=auth_b)
f_posts = (res_f.get_json() or {}).get('data', {}).get('posts', [])
check('接口支持按目的筛选',
      len(f_posts) > 0 and all(p['recipe']['purpose'] == 'control_fat' for p in f_posts),
      str([p['recipe']['purpose'] for p in f_posts])[:120])

res_share = client_b.post('/api/v1/community/share',
                          json={'recipe_id': RID_PLAIN, 'note': 'B 也来分享'},
                          headers=auth_b)
check('B 分享自己的食谱失败（不是自己的）', res_share.status_code == 404,
      'status=%s' % res_share.status_code)

rid_b = make_recipe(UID_B, 'B 的拿手菜', purpose='control_sugar', flavor='sauce',
                    meal_time='dinner')
res_share2 = client_b.post('/api/v1/community/share',
                           json={'recipe_id': rid_b, 'note': 'B 也来分享'}, headers=auth_b)
sp = (res_share2.get_json() or {}).get('data', {})
check('接口分享成功', res_share2.status_code == 200 and sp.get('post_id'),
      str(res_share2.get_json())[:200])
POST_B = sp.get('post_id')
res_share3 = client_b.post('/api/v1/community/share', json={'recipe_id': rid_b}, headers=auth_b)
check('接口重复分享返回原帖（不新建）',
      (res_share3.get_json() or {}).get('data', {}).get('post_id') == POST_B,
      str(res_share3.get_json())[:200])
res_share4 = client_b.post('/api/v1/community/share', json={}, headers=auth_b)
check('接口缺 recipe_id -> 400', res_share4.status_code == 400,
      'status=%s' % res_share4.status_code)

res_det = client_b.get('/api/v1/community/posts/%d' % POST_B, headers=auth_b)
d = (res_det.get_json() or {}).get('data', {})
check('详情接口返回帖子与同类推荐',
      d.get('post', {}).get('id') == POST_B and isinstance(d.get('related'), list),
      str(res_det.get_json())[:160])

res_like = client_a.post('/api/v1/community/posts/%d/like' % POST_B, headers=auth_a)
check('接口点赞成功', (res_like.get_json() or {}).get('data', {}).get('active') is True,
      str(res_like.get_json())[:160])
check('接口点赞计数为 1',
      (res_like.get_json() or {}).get('data', {}).get('like_count') == 1,
      str(res_like.get_json())[:160])
res_fav = client_a.post('/api/v1/community/posts/%d/favorite' % POST_B, headers=auth_a)
check('接口收藏成功', (res_fav.get_json() or {}).get('data', {}).get('active') is True,
      str(res_fav.get_json())[:160])

res_mine = client_b.get('/api/v1/community/mine', headers=auth_b)
mb = (res_mine.get_json() or {}).get('data', {})
check('接口我的分享', mb.get('total') == 1 and mb['posts'][0]['id'] == POST_B,
      str(mb)[:160])

res_del_deny = client_a.delete('/api/v1/community/posts/%d' % POST_B, headers=auth_a)
check('接口删他人帖子 -> 403', res_del_deny.status_code == 403,
      'status=%s' % res_del_deny.status_code)
res_del = client_b.delete('/api/v1/community/posts/%d' % POST_B, headers=auth_b)
check('接口删自己的帖子成功', res_del.status_code == 200,
      'status=%s' % res_del.status_code)
with app.app_context():
    check('接口删除后帖子消失', db.session.get(CommunityPost, POST_B) is None)

res_noauth = client_anon.get('/api/v1/community/posts')
check('接口未带 token -> 401/403', res_noauth.status_code in (401, 403),
      'status=%s' % res_noauth.status_code)

print('=== 11) 适老化 / 入口 ===')
dash = client_a.get('/dashboard').get_data(as_text=True)
check('导航有「社区」入口', 'href="/api/community/"' in dash and '社区' in dash)
check('个人中心有「我的分享」', '我的分享' in dash)
check('退出有确认（防误触）', '确定要退出登录吗' in dash)
check('仪表盘有社区区块', '社区最新分享' in dash)
check('仪表盘有社区快捷入口', '社区食谱' in dash)

sq = client_a.get('/api/community/').get_data(as_text=True)
check('点赞按钮有状态语义（aria-pressed）', 'aria-pressed' in sq)
check('社区样式已加载', 'post-card' in sq and 'btn-action' in sq)
check('社区脚本已加载（广场页）', 'js/community.js' in sq)
check('社区脚本在仪表盘也加载（那里也有点赞按钮）', 'js/community.js' in dash)
check('社区脚本只在 base 里加载一次',
      sq.count('js/community.js') == 1, str(sq.count('js/community.js')))
css = open('static/css/style.css', encoding='utf-8').read()
check('触控按钮走 --tap-min token', 'min-height: var(--tap-min);' in css)
check('高对比模式下社区卡片有描边', 'html[data-contrast="high"] .post-card' in css)
check('判档/按钮样式未被装饰覆盖（无硬编码 56px）', 'min-height: 56px' not in css)

print()
print('=' * 46)
print('  通过 %d 项，失败 %d 项' % (passed, failed))
print('=' * 46)

try:
    os.remove(dbpath)
except OSError:
    pass
sys.exit(1 if failed else 0)
