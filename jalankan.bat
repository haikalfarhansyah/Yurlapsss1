@echo off
REM ============================================================
REM  Jalankan YurLaps production UI dan decoder otomatis
REM ============================================================
setlocal
cd /d "%~dp0"

if not exist .venv (
  echo Membuat virtual env...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt
if errorlevel 1 goto failed

echo.
echo  YurLaps akan menjalankan YurLaps Decoder otomatis dan mencoba kembali
echo  setiap 5 detik jika RTL-SDR belum tersambung.
echo.
python run_stintlab.py %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo YurLaps gagal dijalankan. Periksa pesan di atas.
pause
exit /b 1
