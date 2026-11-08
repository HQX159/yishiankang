# -*- coding: utf-8 -*-
"""修改密码功能自测：Web 会话 + JWT 两条链路，基于临时 SQLite，不动真实库"""
import os
import json
import tempfile

tmp_db = os.path.join(tempfile.mkdtemp(), "test_change_password.db")
os.environ["DATABASE_URL"] = "sqlite:///" + tmp_db

from app import app

app.config["TESTING"] = True
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + tmp_db

from utils.database import db
from models.user import User

results = []

def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(("PASS" if cond else "FAIL"), name, detail)

with app.app_context():
    db.drop_all()
    db.create_all()
    u = User(username="selftest", email="selftest@test.com")
    u.set_password("oldpass1")
    db.session.add(u)
    db.session.commit()
    uid = u.id

# ── 1. Web 链路：原密码错误 ──
client = app.test_client()
with client.session_transaction() as sess:
    sess["user_id"] = uid
r = client.post("/api/user/change_password", data={
    "old_password": "wrongpass", "new_password": "newpass1",
    "confirm_password": "newpass1"}, follow_redirects=True)
check("Web 原密码错误被拒", r.status_code == 200 and "原密码错误" in r.get_data(as_text=True))

# ── 2. Web 链路：两次新密码不一致 ──
r = client.post("/api/user/change_password", data={
    "old_password": "oldpass1", "new_password": "newpass1",
    "confirm_password": "diffpass"}, follow_redirects=True)
check("Web 两次新密码不一致被拒",
      "两次输入的新密码不一致" in r.get_data(as_text=True))

# ── 3. Web 链路：新密码太短 ──
r = client.post("/api/user/change_password", data={
    "old_password": "oldpass1", "new_password": "123",
    "confirm_password": "123"}, follow_redirects=True)
check("Web 新密码过短被拒", "新密码至少6位" in r.get_data(as_text=True))

# ── 4. Web 链路：正确修改成功，跳转登录 ──
r = client.post("/api/user/change_password", data={
    "old_password": "oldpass1", "new_password": "newpass1",
    "confirm_password": "newpass1"}, follow_redirects=False)
check("Web 修改成功跳登录", r.status_code == 302 and "/login" in r.headers.get("Location", ""))

with app.app_context():
    check("Web 修改后密码已更新", db.session.get(User, uid).check_password("newpass1"))
    db.session.get(User, uid).set_password("oldpass1")  # 还原，供 JWT 链路使用
    db.session.commit()

# ── 5. JWT 链路：未带 token 被拒 ──
r = client.post("/api/v1/user/change_password", json={
    "old_password": "oldpass1", "new_password": "newpass1"})
check("JWT 未认证返回 401", r.status_code == 401)

# ── 6. JWT 链路：正常修改成功 ──
token_resp = client.post("/api/v1/user/login", json={
    "identifier": "selftest", "password": "oldpass1"})
token = token_resp.get_json()["data"]["token"]
r = client.post("/api/v1/user/change_password", json={
    "old_password": "oldpass1", "new_password": "newpass1"},
    headers={"Authorization": "Bearer " + token})
data = r.get_json()
check("JWT 修改成功", r.status_code == 200 and data.get("code") == 0 and data.get("message") == "密码修改成功")

with app.app_context():
    check("JWT 修改后密码已更新", db.session.get(User, uid).check_password("newpass1"))

fail_count = sum(1 for _, c, _ in results if not c)
print("=" * 40)
print(f"总计 {len(results)} 项，失败 {fail_count} 项")
raise SystemExit(1 if fail_count else 0)