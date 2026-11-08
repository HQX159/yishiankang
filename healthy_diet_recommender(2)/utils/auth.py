# utils/auth.py
"""JWT 认证模块 — token 生成/验证 + login_required 装饰器（同时兼容 session）"""
import functools
import jwt
from flask import request, jsonify, session, redirect, url_for, flash, current_app, g
from datetime import datetime, timedelta, timezone


# ──────────────────────────────────────────────
# JWT Token 操作
# ──────────────────────────────────────────────
def generate_jwt_token(user_id, expiration_hours=720):
    """生成 JWT token（默认 30 天有效期）"""
    payload = {
        "user_id": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=expiration_hours),
        "iat": datetime.now(timezone.utc),
    }
    secret = current_app.config.get("SECRET_KEY", "dev-secret")
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_jwt_token(token):
    """验证 JWT token，成功返回 payload，失败返回 None"""
    try:
        secret = current_app.config.get("SECRET_KEY", "dev-secret")
        return jwt.decode(token, secret, algorithms=["HS256"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ──────────────────────────────────────────────
# 装饰器：JWT 认证（小程序 API 使用）
# ──────────────────────────────────────────────
def jwt_login_required(view_func):
    """
    要求请求头携带有效的 JWT token: Authorization: Bearer <token>
    验证通过后将 user_id 注入 g.user_id
    """

    @functools.wraps(view_func)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else auth_header

        if not token:
            return jsonify({"code": 401, "message": "未登录，请先授权"}), 401

        payload = verify_jwt_token(token)
        if not payload:
            return jsonify({"code": 401, "message": "登录已过期，请重新登录"}), 401

        g.user_id = payload["user_id"]
        return view_func(*args, **kwargs)

    return wrapper


# ──────────────────────────────────────────────
# 未登录时该回什么：JSON 还是跳登录页
# ──────────────────────────────────────────────
def wants_json_response():
    """判断"这次请求是不是在等一个 JSON"，而不是一张 HTML 页面。

    为什么不再用 `request.path.startswith("/api/")` 判断：
    本项目**所有页面路由都挂在 `/api/` 下**（`/api/recipes/recipes`、`/api/user/settings`、
    `/api/recipes/meal_plan` …），按前缀判断会把整片页面判成接口 —— session 过期后
    用户点导航，看到的是一屏白底黑字的 `{"message": "未登录"}`，而不是登录页。
    社区那套 `_page_guard` / `_json_guard` 是写对的，这里把判据统一到一处。

    判据按"客户端想要什么"来，不看路径长什么样：

    1. 请求体是 JSON（`Content-Type: application/json`）→ 接口
       （fetch/curl 发 JSON 就是要 JSON 回来，这个信号最硬）
    2. `X-Requested-With: XMLHttpRequest` → 接口（老式 ajax 标记）
    3. `Accept` 里有 `text/html` → 页面
       浏览器点链接、提交表单、刷新页面都会带；`fetch()` 默认是 `*/*`，不会带
    4. 其它（`*/*`、`application/json`、干脆没写）→ 按接口处理
       小程序/鸿蒙/脚本都属于这一类，拿 401 JSON 才好处理

    ⚠️ 别改成"按 HTTP 方法猜"（GET=页面）：`/api/recipes/<id>` 是 GET 但它是 JSON 接口。
    """
    # 不在 /api/ 下的一定是页面（/dashboard、/health/metrics、/nutrition/intake）——
    # 这条最硬，先判掉，免得测试客户端/爬虫没带 Accept 就被当成接口。
    if not request.path.startswith("/api/"):
        return False
    if request.is_json:
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = (request.headers.get("Accept") or "").lower()
    return "text/html" not in accept


def login_guard():
    """统一的登录守卫。返回 `(user_id, error_response, status_code)`。

    已登录 → `(user_id, None, 200)`；未登录 → `(None, 响应, 状态码)`。
    调用方照老写法用：

        user_id, error_response, status_code = login_guard()
        if error_response:
            return error_response, status_code

    路由文件里各自那份 `check_user_logged_in()` 都转调这里，**别再各写一套** ——
    以前五份拷贝全是 `jsonify(401)`，页面表单提交时就会弹出裸 JSON。
    """
    user_id = session.get("user_id")
    if user_id:
        return user_id, None, 200
    if wants_json_response():
        return None, jsonify({"message": "未登录"}), 401
    flash("请先登录后再访问", "warning")
    return None, redirect(url_for("user.login")), 302


# ──────────────────────────────────────────────
# 装饰器：Session 认证（Web 页面使用，保持兼容）
# ──────────────────────────────────────────────
def login_required(view_func):
    """
    Session 认证装饰器 — 兼容原有 Web 页面。
    页面请求重定向到登录页；接口请求（含 fetch/小程序）返回 401 JSON。
    判据见 `wants_json_response()`。
    """

    @functools.wraps(view_func)
    def wrapper(*args, **kwargs):
        _user_id, error_response, status_code = login_guard()
        if error_response:
            return error_response, status_code
        return view_func(*args, **kwargs)

    return wrapper


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────
def get_current_user_id():
    """获取当前用户 ID（优先 JWT 的 g.user_id，其次 session）"""
    jwt_id = getattr(g, "user_id", None)
    if jwt_id:
        return jwt_id
    return session.get("user_id")


def get_current_user():
    """获取当前登录用户对象"""
    from models.user import User

    user_id = get_current_user_id()
    if not user_id:
        return None
    return User.query.get(user_id)


def logout_user():
    """清除当前用户登录状态"""
    session.pop("user_id", None)


# ──────────────────────────────────────────────
# 自测（在项目根目录跑，要用 -m，不然 sys.path 里没有 app）
#     "C:/Users/123/AppData/Local/Programs/Python/Python314/python.exe" -m utils.auth
# ──────────────────────────────────────────────
if __name__ == "__main__":
    from app import app

    cases = [
        # (说明, 请求头, 期望「是否按接口回 JSON」)
        ("浏览器导航", {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9"}, False),
        ("浏览器表单提交", {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                       "Content-Type": "application/x-www-form-urlencoded"}, False),
        ("fetch 默认 Accept", {"Accept": "*/*"}, True),
        ("小程序/脚本 application/json", {"Accept": "application/json"}, True),
        ("干脆不写 Accept", {}, True),
        ("JSON 请求体", {"Content-Type": "application/json"}, True),
        ("X-Requested-With 老式 ajax", {"X-Requested-With": "XMLHttpRequest",
                                    "Accept": "text/html"}, True),
    ]
    bad = 0
    for name, headers, expect in cases:
        with app.test_request_context("/api/recipes/recipes", method="GET", headers=headers):
            got = wants_json_response()
        ok = got == expect
        bad += 0 if ok else 1
        print(("PASS" if ok else "FAIL"), name, "->", got, "(期望 %s)" % expect)

    # 不在 /api/ 下的一律是页面，哪怕没带 Accept（测试客户端就是这样）
    with app.test_request_context("/health/metrics", method="GET"):
        got = wants_json_response()
    ok = not got
    bad += 0 if ok else 1
    print(("PASS" if ok else "FAIL"), "非 /api 路径一律按页面 ->", got, "(期望 False)")

    print("-" * 40)
    print("总计 %d 项，失败 %d 项" % (len(cases) + 1, bad))
    raise SystemExit(1 if bad else 0)
