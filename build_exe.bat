@echo off
REM Build aplikasi Electron lengkap. Gunakan GitHub Actions untuk membangun decoder.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto failed
where npm >nul 2>nul
if errorlevel 1 goto failed
if not exist vendor\openstint\YurLapsDecoder.exe (
  echo Decoder belum tersedia. Ambil artifact yurlaps-decoder dari Actions
  echo dan ekstrak decoder-out ke vendor\openstint, termasuk semua DLL.
  echo Atau gunakan Actions - Build Windows untuk membangun semuanya.
  goto failed
)
if not exist .venv python -m venv .venv
if errorlevel 1 goto failed
call .venv\Scripts\activate.bat
if errorlevel 1 goto failed
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto failed
python tools\verifikasi_file.py
if errorlevel 1 goto failed
python -m PyInstaller --noconfirm --clean tools\engine.spec
if errorlevel 1 goto failed
pushd desktop
call npm ci
if errorlevel 1 goto node_failed
call npm run dist
if errorlevel 1 goto node_failed
popd
echo Selesai. Installer ada di dist-electron\YurLaps-Setup-0.4.1.exe
pause
exit /b 0

:node_failed
popd
:failed
echo Build gagal. Periksa pesan di atas dan PANDUAN-BUILD-EXE.md.
pause
exit /b 1
