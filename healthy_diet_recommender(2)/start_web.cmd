@echo off
REM ============================================================
REM  YiShiAnKang - one-click Web (Flask) launcher
REM  URL: http://localhost:5000
REM
REM  2026-10-08 fixed. The old version did:
REM      cd ...\healthy_diet_recommender(2)\healthy_diet_recommender
REM      .venv\Scripts\python.exe start_stable.py
REM  Both parts were wrong: that subdirectory holds no code (only
REM  the sqlite files), and .venv has no Scripts folder at all.
REM  Now it runs from this folder with the system Python 3.14,
REM  which is the interpreter that actually has flask +
REM  sqlalchemy + torch installed.
REM
REM  Keep this file PURE ASCII and do not add "chcp 65001".
REM  cmd.exe parses a .cmd byte-wise using cp936 on Chinese
REM  Windows; UTF-8 comments shift the offsets and the following
REM  lines get silently truncated into garbage. ASCII is safe
REM  under every code page.
REM ============================================================
cd /d "%~dp0"
"C:\Users\123\AppData\Local\Programs\Python\Python314\python.exe" start_stable.py
pause
