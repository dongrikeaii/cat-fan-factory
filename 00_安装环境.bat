@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"

pushd "%PROJECT_DIR%" || goto :error

set "BASE_PYTHON="
py -3.11 -c "import sys" >nul 2>nul && set "BASE_PYTHON=py -3.11"
if not defined BASE_PYTHON (
  python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" >nul 2>nul && set "BASE_PYTHON=python"
)
if not defined BASE_PYTHON goto :no_python

if not exist "%PYTHON_EXE%" (
  echo Creating the local Python 3.11 environment...
  %BASE_PYTHON% -m venv "%PROJECT_DIR%.venv" || goto :error
)

echo Installing the pinned dependencies...
"%PYTHON_EXE%" -m pip install -r "%PROJECT_DIR%requirements.txt" || goto :error
"%PYTHON_EXE%" "%PROJECT_DIR%app.py" prepare-templates || goto :error
"%PYTHON_EXE%" -m unittest discover -s "%PROJECT_DIR%tests" -v || goto :error

echo.
echo Installation and verification completed.
popd
pause
exit /b 0

:no_python
echo Python 3.11 was not found. Install 64-bit Python 3.11 from python.org.
goto :error

:error
echo.
echo Setup failed. See the error message above.
popd
pause
exit /b 1
