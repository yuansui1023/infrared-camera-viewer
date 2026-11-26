@echo off
setlocal
cd /d "%~dp0"

set "IR_PYTHON_COMMAND="
for %%V in (3.12 3.13 3.14 3.11 3.10) do (
    if not defined IR_PYTHON_COMMAND (
        py -%%V -c "import sys; assert sys.maxsize > 2**32" >nul 2>nul
        if not errorlevel 1 set "IR_PYTHON_COMMAND=py -%%V"
    )
)
if not defined IR_PYTHON_COMMAND (
    python -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,15) and sys.maxsize > 2**32" >nul 2>nul
    if not errorlevel 1 set "IR_PYTHON_COMMAND=python"
)
if not defined IR_PYTHON_COMMAND (
    echo ERROR: Install Python 3.12 x64 from https://www.python.org/downloads/windows/
    echo Enable the Python launcher or add Python to PATH, then run this file again.
    echo Python 3.15 and 32-bit Python are not supported by the supplied camera SDK.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    %IR_PYTHON_COMMAND% -m venv .venv
    if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,15) and sys.maxsize > 2**32"
if errorlevel 1 (
    echo ERROR: The existing .venv uses an unsupported or missing Python installation.
    echo Rename the .venv folder and run setup again.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install .
if errorlevel 1 goto :failed

set "IR_SDK_ROOT=%~1"
if not defined IR_SDK_ROOT set "IR_SDK_ROOT=%ProgramFiles%\Allied Vision\Vimba X"
set "IR_SDK_WHEEL=%IR_SDK_ROOT%\api\python\vmbpy-1.2.3-py3-none-win_amd64.whl"
if exist "%IR_SDK_WHEEL%" (
    ".venv\Scripts\python.exe" -m pip install "%IR_SDK_WHEEL%"
    if errorlevel 1 goto :failed
) else (
    echo.
    echo NOTE: Vimba X was not found at "%IR_SDK_ROOT%".
    echo Synthetic demo mode is ready. For a real camera, install Vimba X 2026-3,
    echo then run Setup-Windows.cmd "path-to-your-Vimba-X-installation".
)
echo.
echo Setup complete. Open Launch-Windows.cmd, or Demo-Windows.cmd for synthetic images.
pause
exit /b 0

:failed
echo.
echo ERROR: Setup stopped. Read the error above. Check Python, network access, and the SDK path.
pause
exit /b 1
