@echo off
chcp 65001 >nul
setlocal
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"

pushd "%PROJECT_DIR%" || goto :error
if not exist "%PYTHON_EXE%" goto :not_installed
"%PYTHON_EXE%" "%PROJECT_DIR%app.py" template-wizard || goto :error
popd
pause
exit /b 0

:not_installed
echo 未找到项目的 Python 环境。请先双击 00_安装环境.bat。

:error
echo 模板生成失败，请查看上方的中文提示。
popd
pause
exit /b 1
