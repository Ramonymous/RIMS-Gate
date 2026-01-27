@echo off
echo ========================================
echo  RIMS Gateway - PyInstaller Build Script
echo ========================================

REM Pastikan venv aktif
if not exist venv\Scripts\activate.bat (
    echo [ERROR] venv tidak ditemukan!
    pause
    exit /b 1
)

call venv\Scripts\activate

echo.
echo [INFO] Building executable...
echo.

pyinstaller ^
 --onefile ^
 --noconsole ^
 --clean ^
 --hidden-import=serial ^
 --hidden-import=serial.tools.list_ports ^
 --name "RIMS-Gateway" ^
 gateway.py

echo.
echo [SUCCESS] Build selesai!
echo Output ada di folder dist\
pause
