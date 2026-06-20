@echo off
setlocal
cd /d "%~dp0"
echo [ZyenLang] current folder: %CD%
if not exist pyproject.toml (
  echo ERROR: pyproject.toml not found.
  echo You are not in the real ZyenLang folder. Extract the ZIP again and run this BAT from the folder that contains pyproject.toml.
  pause
  exit /b 1
)
python -m pip uninstall zyenlang -y
python -m pip install -e .
python tools\install_vscode_extension.py
echo.
echo Testing zy...
zy --help
echo.
echo Done. Try: zy run examples\add.zy
pause
