# -*- coding: utf-8 -*-
"""未登录守卫自测：**页面必须跳登录页，接口必须回 401 JSON**。

为什么值得单独测
    本项目所有页面路由都挂在 `/api/` 下（`/api/recipes/recipes`、`/api/user/settings`、
    `/api/recipes/meal_plan` …）。旧代码用 `request.path.startswith('/api/')` 判断
    "这是接口"，于是把整片页面判成接口 —— session 过期后用户点导航，看到的是
    一屏白底黑字的 `{"message":"未登录"}`，而不是登录页。

    这个 bug **只有带浏览器 Accept 头才复现**：Flask 测试客户端默认不带 Accept，
    所以以前 35 套测试全绿也发现不了。本文件就是专门带 Accept 头把它按住。

跑法
    "C:/Users/123/AppData/Local/Programs/Python/Python314/python.exe" test_login_guard.py
"""
import os
import tempfile

_tmp_db = os.path.join(tempfile.mkdtemp(), 'test_login_guard.db')
os.environ['DATABASE_URL'] = 'sqlite:///' + _tmp_db

from app import app                                    # noqa: E402

app.config['TESTING'] = True
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + _tmp_db
app.config['WTF_CSRF_ENABLED'] = False

from utils.database import db                          # noqa: E402
from utils.auth import wants_json_response             # noqa: E402
from models.user import User                           # noqa: E402

# 浏览器真实发的 Accept（Chrome / Edge）
BROWSER = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
              'image/avif,image/webp,image/apng,*/*;q=0.8',
}

results = []


def check(name, cond, detail=''):
    results.append((name, cond, detail))
    print(('PASS' if cond else 'FAIL'), name, detail)


# ── 被测路由清单 ────────────────────────────────────────────────
# 页面：浏览器点导航/提交表单会走到这里，未登录必须 302 到登录页
PAGE_ROUTES = [
    '/dashboard',
    '/health/metrics',
    '/nutrition/intake',
    '/api/recipes/recipes',
    '/api/recipes/favorites',
    '/api/recipes/meal_plan',
    '/api/user/profile',
    '/api/user/settings',
    '/api/user/change_password',
    '/api/community/',
    '/api/community/mine',
]

# ⚠️ 已知例外（**不是守卫的 bug，是这三个 GET 分支先 render、守卫只在 POST 之后**）：
#    GET 时根本走不到 check_user_logged_in，所以匿名也能看到表单页；提交时才拦。
#    要不要收紧成"必须先登录"是产品决定（见 STATUS/未完成任务清单），本次不动。
ANON_OK_PAGES = [
    '/api/recipes/generate_recipe',
    '/api/upload/upload_fridge_image',
    '/api/upload/upload_health_report',
]

# 接口：小程序/鸿蒙/脚本调，未登录必须 401 JSON
JSON_ROUTES = [
    ('GET', '/api/recipes/1'),
    ('POST', '/api/recipes/favorites/1'),
    ('POST', '/api/recipes/recipes/1/regenerate'),
    ('POST', '/api/tts/text_to_speech'),
    ('POST', '/api/voice/voice_command'),
    ('POST', '/api/checkin/save'),
]

with app.app_context():
    db.drop_all()
    db.create_all()
    u = User(username='guardtest', email='guard@test.com')
    u.set_password('guardpass1')
    db.session.add(u)
    db.session.commit()
    uid = u.id


client = app.test_client()


def call(method, path, headers=None, **kw):
    fn = client.get if method == 'GET' else client.post
    return fn(path, headers=headers or {}, **kw)


def is_login_redirect(resp):
    loc = resp.headers.get('Location', '')
    return resp.status_code == 302 and '/login' in loc


def has_raw_json(resp):
    """页面位置上出现裸 JSON 就是那个 bug 的症状。"""
    body = resp.get_data(as_text=True)
    return resp.status_code == 401 and '未登录' in body and '<html' not in body.lower()


# ══════════════════ 1. 判据本身 ══════════════════
print('=== 1) wants_json_response 判据 ===')


def ctx(path, headers):
    with app.test_request_context(path, method='GET', headers=headers):
        return wants_json_response()


check('浏览器 Accept -> 不是 JSON', ctx('/api/recipes/recipes', BROWSER) is False)
check('fetch 的 Accept: */* -> JSON', ctx('/api/recipes/recipes', {'Accept': '*/*'}) is True)
check('application/json -> JSON',
      ctx('/api/recipes/recipes', {'Accept': 'application/json'}) is True)
check('X-Requested-With 优先于 Accept',
      ctx('/api/recipes/recipes', dict(BROWSER, **{'X-Requested-With': 'XMLHttpRequest'})) is True)
check('不在 /api/ 下一律按页面', ctx('/health/metrics', {}) is False)

# ══════════════════ 2. 页面：未登录必须跳登录页 ══════════════════
print('\n=== 2) 未登录 + 浏览器 Accept -> 302 登录页 ===')
for path in PAGE_ROUTES:
    r = call('GET', path, headers=BROWSER)
    check('页面 %-38s 跳登录页' % path, is_login_redirect(r),
          'status=%s loc=%s' % (r.status_code, r.headers.get('Location', '-')))
    check('页面 %-38s 不是裸 JSON' % path, not has_raw_json(r), 'status=%s' % r.status_code)

# 跟随重定向后应该是登录页（真能看到登录表单）
r = call('GET', '/api/recipes/recipes', headers=BROWSER, follow_redirects=True)
html = r.get_data(as_text=True)
check('跟随重定向落在登录页', r.status_code == 200 and ('identifier' in html or '登录' in html),
      'status=%s len=%d' % (r.status_code, len(html)))

# 三个表单页的 GET 分支先 render、守卫在 POST 之后 —— 记下现状，别当成守卫失效
print('\n--- 已知例外：表单页 GET 允许匿名（守卫只在 POST）---')
for path in ANON_OK_PAGES:
    r = call('GET', path, headers=BROWSER)
    check('表单页 %-38s 匿名可看(200)' % path, r.status_code == 200,
          'status=%s（若要收紧到必须登录，改路由顺序即可）' % r.status_code)

# ══════════════════ 3. 页面上的表单 POST 也要跳登录页 ══════════════════
print('\n=== 3) 表单 POST（session 过期提交）-> 302 而不是裸 JSON ===')
for path, data in [
    ('/api/checkin/save', {'fasting_glucose': '7.2'}),
    ('/api/user/change_password', {'old_password': 'a', 'new_password': 'b', 'confirm_password': 'b'}),
    ('/api/recipes/generate_recipe', {}),
]:
    r = call('POST', path, data=data, headers=BROWSER)
    check('表单 %-34s 跳登录页' % path, is_login_redirect(r),
          'status=%s' % r.status_code)

# ══════════════════ 4. 接口：未登录必须 401 JSON ══════════════════
print('\n=== 4) 未登录 + 接口客户端 -> 401 JSON ===')


def msg_of(resp):
    """jsonify 默认把中文转成 \\uXXXX，按 json 取值别按字节找。"""
    try:
        return (resp.get_json(silent=True) or {}).get('message', '')
    except Exception:
        return ''


for method, path in JSON_ROUTES:
    r = call(method, path)
    m = msg_of(r)
    check('接口 %-6s %-40s 401 JSON' % (method, path),
          r.status_code == 401 and '未登录' in m,
          'status=%s msg=%s' % (r.status_code, m))

# 老式 ajax 标记（即使 Accept 带 text/html）也要 JSON
r = call('POST', '/api/recipes/favorites/1',
         headers=dict(BROWSER, **{'X-Requested-With': 'XMLHttpRequest'}))
check('X-Requested-With 时仍然回 JSON', r.status_code == 401 and '未登录' in msg_of(r),
      'status=%s msg=%s' % (r.status_code, msg_of(r)))

# ══════════════════ 5. 登录后不能误伤 ══════════════════
print('\n=== 5) 已登录 -> 正常出页面 ===')
with client.session_transaction() as sess:
    sess['user_id'] = uid

for path in PAGE_ROUTES:
    r = call('GET', path, headers=BROWSER)
    check('登录后 %-38s 不是 302 登录页' % path,
          not is_login_redirect(r), 'status=%s' % r.status_code)

# 几个主页面要求 200（带 id 的详情页没数据会 302/404，单独放过）
# ⚠️ /health/metrics 是旧地址，登录后本来就 302 到新地址（带 ?key=），别放进 200 名单
for path in ['/dashboard', '/nutrition/intake',
             '/api/recipes/recipes', '/api/recipes/favorites',
             '/api/recipes/meal_plan', '/api/user/settings', '/api/community/']:
    r = call('GET', path, headers=BROWSER)
    check('登录后 %-38s 200' % path, r.status_code == 200, 'status=%s' % r.status_code)

# ══════════════════ 6. 注销后立刻恢复守卫 ══════════════════
print('\n=== 6) 注销 -> 回到未登录行为 ===')
call('GET', '/api/user/logout', headers=BROWSER)
r = call('GET', '/api/recipes/recipes', headers=BROWSER)
check('注销后页面又跳登录页', is_login_redirect(r), 'status=%s' % r.status_code)
r = call('GET', '/api/v1/recipes', headers={'Authorization': 'Bearer 假的'})
check('注销后 JWT 接口仍回 401', r.status_code == 401, 'status=%s' % r.status_code)

fail = sum(1 for _, ok, _ in results if not ok)
print('=' * 46)
print('总计 %d 项，失败 %d 项' % (len(results), fail))
raise SystemExit(1 if fail else 0)
