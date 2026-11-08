"""
生产环境启动脚本 — 使用 Waitress WSGI 服务器（Windows 兼容）

启动方式：
    python run_prod.py

然后通过局域网 IP 分享给其他人访问：
    http://<你的IP>:5000
"""
from app import create_app
from waitress import serve

app = create_app()
app.config.from_object('config.ProductionConfig')

print("=" * 50)
print("  益食安康 — 糖尿病饮食管理系统")
print("  生产模式启动（Waitress WSGI）")
print("=" * 50)
print()
print("  本机访问: http://127.0.0.1:5000")
print()
print("  分享给局域网其他人访问:")
print("  查看你的 IP 地址，然后发给对方")
print("=" * 50)

# 监听所有网络接口，允许局域网内其他设备访问
serve(app, host="0.0.0.0", port=5000, threads=8)
