@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"

pushd "%PROJECT_DIR%" || goto :error
if not exist "%PYTHON_EXE%" goto :not_installed
"%PYTHON_EXE%" "%PROJECT_DIR%comment_prototype.py" || goto :error

echo.
echo Comment screenshot test completed. Check output\comment_batches.
popd
pause
exit /b 0

:not_installed
echo Please run 00_setup first.

:error
echo.
echo Comment screenshot test failed. See the message above.
popd
pause
exit /b 1
