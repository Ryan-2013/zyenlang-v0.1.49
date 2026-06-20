@echo off
setlocal
cd /d "%~dp0"
zy --help
zy run examples\add.zy
zy run examples\list_basic.zy
pause
