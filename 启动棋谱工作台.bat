@echo off
rem 启动棋谱工作台（无控制台窗口）
cd /d "%~dp0"
set "PYW=C:\Users\zhangwx\AppData\Local\hermes\tools\python-3.14.9+20261021-win32-x64\pythonw.exe"
if exist "%PYW%" (
  start "" "%PYW%" xqdesk.py
) else (
  start "" pythonw xqdesk.py
)