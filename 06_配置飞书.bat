@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"

pushd "%PROJECT_DIR%" || goto :error
if not exist "%PYTHON_EXE%" goto :not_installed
"%PYTHON_EXE%" "%PROJECT_DIR%app.py" configure-feishu || goto :error

echo.
echo Feishu setup completed. The table URL is stored only in data.
popd
pause
exit /b 0

:not_installed
echo Please run 00_setup first.

:error
echo.
echo Feishu setup failed. See the error message above.
popd
pause
exit /b 1
