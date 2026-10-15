@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
pushd "%~dp0"
if errorlevel 1 goto bad_directory

py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 goto use_py
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 goto use_python
echo Python 3.10 or newer was not found. Install Python and enable PATH, then reopen this launcher.
echo This launcher does not download or install Python or Node.js.
set "DEV_EXIT=1"
goto finish

:use_py
py -3 "%~dp0scripts\dev.py" --lan %*
set "DEV_EXIT=%ERRORLEVEL%"
goto finish

:use_python
python "%~dp0scripts\dev.py" --lan %*
set "DEV_EXIT=%ERRORLEVEL%"
goto finish

:bad_directory
echo Could not open the source directory. Extract the complete ZIP to a writable folder first.
pause
exit /b 1

:finish
popd
if not "%DEV_EXIT%"=="0" pause
exit /b %DEV_EXIT%
