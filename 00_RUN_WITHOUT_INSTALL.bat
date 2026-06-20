@echo off
setlocal
cd /d "%~dp0"
python zyen.py run examples\add.zy
pause
