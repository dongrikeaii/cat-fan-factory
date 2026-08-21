@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"

pushd "%PROJECT_DIR%" || goto :error
if not exist "%PYTHON_EXE%" goto :not_installed
"%PYTHON_EXE%" "%PROJECT_DIR%app.py" prepare-templates || goto :error
popd
pause
exit /b 0

:not_installed
echo Please run 00_setup first.

:error
echo Mask generation failed. See the error message above.
popd
pause
exit /b 1
