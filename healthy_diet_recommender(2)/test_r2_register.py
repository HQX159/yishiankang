# -*- coding: utf-8 -*-
"""R2 自测：注册去邮箱（用临时数据库，不碰真实数据）"""
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8')

# 用临时数据库，隔离真实 app.db
dbpath = os.path.join(tempfile.gettempdir(), 'ysak_r2_test.db')
if os.path.exists(dbpath):
    os.remove(dbpath)
os.environ['DATABASE_URL'] = 'sqlite:///' + dbpath.replace('\\', '/')

sys.path.insert(0, '.')
from app import create_app            # noqa: E402
from utils.database import db         # noqa: E402
from models.user import User          # noqa: E402

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


print('=== 1) 不填邮箱注册 ===')
r = client.post('/api/user/register', data={'username': '张三', 'password': 'abc123'})
with app.app_context():
    u = User.query.filter_by(username='张三').first()
    check('注册成功且入库', u is not None)
    check('email 自动填了占位值', u is not None and u.email == 'u张三@local',
          'email=%r' % (u.email if u else None))
    check('密码已哈希', u is not None and u.password_hash and u.password_hash != 'abc123')

print('=== 2) 重复用户名 ===')
r = client.post('/api/user/register', data={'username': '张三', 'password': 'abc123'},
                follow_redirects=True)
check('提示用户名已存在', '已存在' in r.get_data(as_text=True))

print('=== 3) 密码太短 ===')
r = client.post('/api/user/register', data={'username': 'shortpw', 'password': '123'},
                follow_redirects=True)
check('提示密码至少 6 位', '至少 6 位' in r.get_data(as_text=True))

print('=== 4) 确认密码不一致 ===')
r = client.post('/api/user/register',
                data={'username': 'mismatch', 'password': 'abc123', 'confirm_password': 'xyz789'},
                follow_redirects=True)
check('提示两次不一致', '不一致' in r.get_data(as_text=True))

print('=== 5) 新账号登录 ===')
r = client.post('/api/user/login', data={'identifier': '张三', 'password': 'abc123'})
check('登录成功（302 跳转）', r.status_code == 302, 'status=%s' % r.status_code)

print('=== 6) 老账号兼容（带邮箱） ===')
with app.app_context():
    old = User(username='olduser', email='old@test.com')
    old.set_password('pass1234')
    db.session.add(old)
    db.session.commit()
r = client.post('/api/user/login', data={'identifier': 'olduser', 'password': 'pass1234'})
check('用用户名登录老账号', r.status_code == 302, 'status=%s' % r.status_code)
r = client.post('/api/user/login', data={'identifier': 'old@test.com', 'password': 'pass1234'})
check('用邮箱登录老账号（兼容保留）', r.status_code == 302, 'status=%s' % r.status_code)

print('=== 7) 填了邮箱的正常注册 + 邮箱查重 ===')
r = client.post('/api/user/register', data={'username': 'withmail', 'password': 'abc123',
                                            'email': 'wm@test.com'}, follow_redirects=True)
with app.app_context():
    u = User.query.filter_by(username='withmail').first()
    check('邮箱按填写值入库', u is not None and u.email == 'wm@test.com')
r = client.post('/api/user/register', data={'username': 'other', 'password': 'abc123',
                                            'email': 'wm@test.com'}, follow_redirects=True)
check('重复邮箱被拦', '邮箱已被注册' in r.get_data(as_text=True))

print('=== 8) api_v1 注册（小程序/鸿蒙接口，不填邮箱） ===')
r = client.post('/api/v1/user/register', json={'username': 'api用户', 'password': 'abc123'})
j = r.get_json() or {}
check('返回成功且带 token', r.status_code == 200 and j.get('code') in (0, 200) or bool(j.get('data')),
      'resp=%s' % str(j)[:160])
r = client.post('/api/v1/user/register', json={'username': 'apishort', 'password': '12'})
check('api_v1 短密码被拦', '至少 6 位' in str(r.get_json()))

print()
print('=' * 46)
print('  通过 %d 项，失败 %d 项' % (passed, failed))
print('=' * 46)

try:
    os.remove(dbpath)
except OSError:
    pass
sys.exit(1 if failed else 0)
