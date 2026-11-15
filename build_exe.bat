@echo off
chcp 65001 >nul
title Build Jiansz

cd /d "%~dp0"

echo ========================================
echo Build Jiansz
echo ========================================
echo.

echo [1/6] Searching Python...

set "PYTHON_CMD="

py -3 --version >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"

if "%PYTHON_CMD%"=="" (
    python --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if "%PYTHON_CMD%"=="" (
    if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
)

if "%PYTHON_CMD%"=="" (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)

if "%PYTHON_CMD%"=="" (
    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
)

if "%PYTHON_CMD%"=="" (
    echo Python was not found.
    echo.
    echo Please run this in PowerShell to check:
    echo py -3 --version
    echo.
    echo If not found, install Python and check "Add python.exe to PATH".
    pause
    exit /b 1
)

echo Using Python:
%PYTHON_CMD% --version
echo.

echo [2/6] Installing dependencies...

%PYTHON_CMD% -m pip install --upgrade pip
if errorlevel 1 goto BUILD_FAIL

if exist requirements.txt (
    %PYTHON_CMD% -m pip install -r requirements.txt
    if errorlevel 1 goto BUILD_FAIL
)

%PYTHON_CMD% -m pip install pyinstaller
if errorlevel 1 goto BUILD_FAIL

echo.
echo [3/6] Cleaning old build files...

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo [4/6] Checking packaging manifest...
if not exist "Jiansz.spec" goto BUILD_FAIL

echo.
echo [5/6] Building Jiansz.exe...

%PYTHON_CMD% -m PyInstaller --noconfirm Jiansz.spec
if errorlevel 1 goto BUILD_FAIL

echo.
echo [6/6] Copying release files...

if exist "config.json" copy /Y "config.json" "dist\Jiansz\config.json" >nul
if exist "README.md" copy /Y "README.md" "dist\Jiansz\README.md" >nul
if exist "USER_GUIDE.md" copy /Y "USER_GUIDE.md" "dist\Jiansz\USER_GUIDE.md" >nul
if exist "FAQ.md" copy /Y "FAQ.md" "dist\Jiansz\FAQ.md" >nul
if exist "CHANGELOG.md" copy /Y "CHANGELOG.md" "dist\Jiansz\CHANGELOG.md" >nul
if exist "LICENSE" copy /Y "LICENSE" "dist\Jiansz\LICENSE" >nul

if exist "docs" xcopy /E /I /Y "docs" "dist\Jiansz\docs" >nul

echo.
echo ========================================
echo Build finished successfully.
echo Output:
echo dist\Jiansz\Jiansz.exe
echo ========================================
echo.
echo Next:
echo 1. Run dist\Jiansz\Jiansz.exe
echo 2. Test UI/assets/logs/tasks
echo 3. Remove logs/runtime_stats before final release
echo 4. Check config.json does not contain private paths
echo 5. Zip dist\Jiansz as Jiansz.zip
echo.
pause
exit /b 0

:BUILD_FAIL
echo.
echo ========================================
echo Build failed.
echo Please copy the error log above and send it to ChatGPT.
echo ========================================
pause
exit /b 1
