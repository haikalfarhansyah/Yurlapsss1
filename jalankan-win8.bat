@echo off
REM ============================================================
REM  YurLaps untuk Windows 7 / 8 / 8.1
REM  Pakai ini kalau jendela aplikasi tidak muncul di Windows lama:
REM  WebView2 di Windows 8.1 mentok di versi 109 dan sering tidak ada.
REM ============================================================
setlocal
cd /d "%~dp0"

if not exist .venv (
  echo Membuat virtual env dengan Python 3.11 ...
  py -3.11 -m venv .venv 2>nul || python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q pyzmq

echo.
echo  YurLaps membuka UI production di jendela aplikasi atau browser.
echo  Decoder dijalankan otomatis jika biner tersedia.
echo.
python -m app --app-window --port 8080
pause
