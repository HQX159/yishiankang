# -*- coding: utf-8 -*-
"""益食安康 · 稳定启动入口（Windows + Python 3.14）

背景：app.py 里 app.run(debug=True) 会启动 werkzeug reloader，
本机 Python 3.14 + 新版 werkzeug 在 Windows 上 reloader 子进程
socket fd 继承失败（socket.fromfd → WinError 10022），一跑就崩。

本入口直接以非 debug 模式启动，完全绕开 reloader，功能不受影响。

用法（双击 start_web.cmd，或在 cmd / PowerShell 里）：
    cd /d "E:\\益食安康\\healthy_diet_recommender(2)"
    "%LOCALAPPDATA%\\Programs\\Python\\Python314\\python.exe" start_stable.py
启动后访问 http://localhost:5000

⚠️ 路径说明（2026-10-08 修正）：
  * 本文件所在目录 `healthy_diet_recommender(2)\\` 就是后端根目录，
    **不要再 cd 进里面那个同名的子目录**（它只有 instance/app.db，没有代码）。
  * **不要用 `.venv\\Scripts\\python`** —— 仓库里那份 .venv 是残缺的
    （只有 Lib\\site-packages，没有 Scripts），用了必然失败。
    用系统 Python 3.14：C:\\Users\\123\\AppData\\Local\\Programs\\Python\\Python314\\python.exe
    （flask / flask_sqlalchemy / torch 都装在这一个里）。
  * 也别用 run_prod.py：它依赖 waitress，未装。
"""
from app import app  # 触发 app.py 的 else 分支：app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
