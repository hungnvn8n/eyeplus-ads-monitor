@echo off
REM Build .exe installer cho Windows — output 1 file duy nhất
REM Chạy: build_win.bat

setlocal
cd /d "%~dp0"

echo ===============================================
echo   Build EyePlus Ads cho Windows
echo ===============================================

if not exist "venv" (
  echo Loi: Chua co venv. Chay:
  echo   python -m venv venv
  echo   venv\Scripts\activate
  echo   pip install -r requirements.txt
  pause
  exit /b 1
)

call venv\Scripts\activate.bat

REM Clean
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if not exist installer mkdir installer
del /q installer\EyePlusAds-Setup-*.exe 2>nul

REM Step 1: PyInstaller build .exe
echo.
echo Step 1/3: Build .exe via PyInstaller...
pip install --quiet pyinstaller==6.3.0
pyinstaller --clean fb_ad_local.spec
if not exist "dist\EyePlusAds.exe" (
  echo Loi: Build .exe fail.
  pause
  exit /b 1
)
echo    OK: dist\EyePlusAds.exe

REM Step 2: Inno Setup tao installer .exe
echo.
echo Step 2/3: Tao installer qua Inno Setup...

set ISCC_PATH=
for %%P in (
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
  "C:\Program Files\Inno Setup 6\ISCC.exe"
) do (
  if exist %%P set ISCC_PATH=%%P
)

if "%ISCC_PATH%"=="" (
  echo.
  echo CANH BAO: Khong tim thay Inno Setup.
  echo Tai ve: https://jrsoftware.org/isdl.php
  echo.
  echo Hien tai chi co .exe standalone: dist\EyePlusAds.exe
  echo Sau khi cai Inno Setup, chay lai script de tao installer.
  pause
  exit /b 0
)

%ISCC_PATH% /Q installer\win_installer.iss
if errorlevel 1 (
  echo Loi: Inno Setup compile fail.
  pause
  exit /b 1
)

REM Step 3: Clean intermediate
echo.
echo Step 3/3: Clean intermediate files...
rmdir /s /q build
rmdir /s /q dist
echo    OK: Xoa build\ va dist\

echo.
echo ===============================================
echo   BUILD XONG
echo ===============================================
echo.
echo 1 file duy nhat de gui:
echo.
echo    installer\EyePlusAds-Setup-1.0.0.exe
echo.
echo User cai:
echo   1. Double-click installer
echo   2. Next - Next - Install
echo   3. App tu mo sau khi cai xong
echo.
pause
