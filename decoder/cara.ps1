# cara.ps1 — build decoder YurLaps di Windows tanpa GitHub Actions
#
# Untuk kamu yang ingin build sendiri di laptop/PC Windows (sekali saja, atau
# setiap kali upstream berubah). Hasilnya:
#   decoder\out\YurLapsDecoder.exe  + DLL yang dibutuhkan
#
# Prasyarat:
#   1. MSYS2         https://www.msys2.org/     (install ke C:\msys64)
#   2. Git           https://git-scm.com/
#
# Jalankan dari PowerShell biasa (bukan MSYS2):
#   cd decoder
#   powershell -ExecutionPolicy Bypass -File .\cara.ps1
#
# Skrip ini melakukan hal yang sama dengan .github/workflows/build-decoder.yml,
# jadi kalau satu jalur berhasil, jalur lain juga.

$ErrorActionPreference = "Stop"

$MSYS   = "C:\msys64"
$BASH   = Join-Path $MSYS "usr\bin\bash.exe"
$HERE   = Split-Path -Parent $MyInvocation.MyCommand.Path
$PATCH  = Join-Path $HERE "patch"
$OUT    = Join-Path $HERE "out"

if (-not (Test-Path $BASH)) {
  throw "MSYS2 tidak ditemukan di $MSYS. Pasang dari https://www.msys2.org/ lalu jalankan lagi."
}

# Path Windows -> gaya MSYS2 (/c/...)
function To-Msys([string]$p) {
  $p = $p -replace '\\', '/'
  if ($p -match '^([A-Za-z]):(.*)$') { return "/" + $matches[1].ToLower() + $matches[2] }
  return $p
}

$MSYS_HERE  = To-Msys $HERE
$MSYS_PATCH = To-Msys $PATCH
$MSYS_OUT   = To-Msys $OUT

Write-Host "== 1/4 pasang paket MSYS2 (sekali, butuh beberapa menit) ==" -ForegroundColor Cyan
& $BASH -lc @"
set -eu
pacman -S --needed --noconfirm \
  mingw-w64-x86_64-toolchain mingw-w64-x86_64-cmake mingw-w64-x86_64-ninja \
  mingw-w64-x86_64-zeromq mingw-w64-x86_64-cppzmq mingw-w64-x86_64-libusb \
  git make autoconf automake libtool pkg-config zip
"@

Write-Host "== 2/4 ambil sumber + patch ==" -ForegroundColor Cyan
# Catatan: langkah ini memakai git + patch dari MSYS2, jadi harus SETELAH
# paket di langkah 1 terpasang (kalau tidak: "command not found").
& $BASH -lc @"
set -eu
cd '$MSYS_HERE'
rm -rf upstream
git clone --depth 1 https://github.com/zsellera/openstint.git upstream
cd upstream
for p in '$MSYS_PATCH'/*.patch; do echo "  patch: \$p"; git apply --verbose "\$p"; done
echo "  sumber siap"
"@

Write-Host "== 3/4 liquid-dsp + libfec ==" -ForegroundColor Cyan
& $BASH -lc @"
set -eu
LIQUID=https://raw.githubusercontent.com/cjcliffe/CubicSDR/refs/heads/master/external/liquid-dsp
mkdir -p /mingw64/include/liquid
curl -sSfL -o /mingw64/include/liquid/liquid.h "\$LIQUID/include/liquid/liquid.h"
curl -sSfL -o /mingw64/lib/libliquid.a        "\$LIQUID/gcc/64/libliquid.a"

cd '$MSYS_HERE'
rm -rf libfec
git clone --depth 1 https://github.com/quiet/libfec.git libfec
cd libfec
./configure --prefix=/mingw64
sed -i 's/-lc\$//' Makefile
sed -i 's/libfec\.so/libfec.dll/' Makefile
make
cp libfec.dll libfec.a /mingw64/lib
cp fec.h /mingw64/include
"@

Write-Host "== 4/4 build decoder ==" -ForegroundColor Cyan
& $BASH -lc @"
set -eu
cd '$MSYS_HERE/upstream'
cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DUSE_HACKRF=OFF -DUSE_RTLSDR=ON -B build .
ninja -C build
mkdir -p '$MSYS_OUT'
cp build/YurLapsDecoder.exe '$MSYS_OUT/' 2>/dev/null || cp build/src/YurLapsDecoder.exe '$MSYS_OUT/'
# DLL yang dibutuhkan
for dll in \$(ntldd -R '$MSYS_OUT/YurLapsDecoder.exe' | grep -i mingw64 | awk '{print \$3}' | tr -d '<>' | sort -u); do
  cp "\$dll" '$MSYS_OUT/' || true
done
ls -la '$MSYS_OUT'
"@

Write-Host ""
Write-Host "SELESAI. Biner ada di: $OUT" -ForegroundColor Green
Write-Host "Salin seluruh isi folder itu ke  ..\vendor\openstint\  pada folder YurLaps."
