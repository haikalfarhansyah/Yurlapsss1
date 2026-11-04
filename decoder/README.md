# Decoder YurLaps — satu repo dengan aplikasi

Decoder **`YurLapsDecoder.exe`** dibangun dari sumber
[OpenStint](https://github.com/zsellera/openstint) (GPL-3.0) dengan **satu patch**:
nama target CMake diganti. Tidak ada perubahan pada algoritma deteksi.

Tidak perlu repo terpisah. Workflow di `.github/workflows/build-windows.yml`
punya job **`decoder`** yang berjalan sebelum job `build`, dan hasilnya diunduh
ke `vendor/openstint/` sebelum aplikasi dibangun.

```
repo YurLaps (private, satu saja)
├── .github/workflows/build-windows.yml
│     ├── job test             → uji logika (Linux)
│     ├── job decoder          → build YurLapsDecoder.exe (Windows/MSYS2)
│     ├── job build            → mesin + Electron + installer   ← butuh decoder
│     └── (jalur Windows 8 sudah dihapus — YurLaps kini Windows 10+)
├── decoder/
│     ├── patch/rename-target.patch     satu-satunya perubahan pada sumber
│     ├── patch/upstream-ref.txt        versi OpenStint dikunci (v260830.1)
│     ├── ATTRIBUTION.txt               atribusi GPL + sumber lengkap
│     └── cara.ps1                      build manual di Windows
└── vendor/openstint/                   hasil: YurLapsDecoder.exe + DLL
```

## Alur di CI

```
job decoder  ──artifact "yurlaps-decoder"──►  job build
  clone OpenStint v260830.1                    unduh ke vendor/openstint/
  git apply patch                              PyInstaller membundel vendor/
  cmake + ninja                                Electron membungkus semuanya
  YurLapsDecoder.exe + DLL + ATTRIBUTION       YurLaps-Setup-0.3.0.exe
  yurlaps-decoder-src.zip  (GPL-3.0)
```

Artifact `yurlaps-decoder` menyertakan **arsip sumber lengkap**
(`yurlaps-decoder-src.zip`) supaya kewajiban GPL-3.0 terpenuhi. Repo boleh
private karena sumber disebar bersama biner, bukan lewat repo.

## Di dalam paket aplikasi

`vendor/openstint/` dibundel ke `_internal/vendor/openstint/` dan itulah lokasi
yang dicari `app/decoder_manager.py`. Sudah diuji: setelah build, manager
menemukan

```
_internal/vendor/openstint/YurLapsDecoder
```

Di Task Manager pengguna terlihat **`YurLapsDecoder.exe`**.

## Build manual (tanpa CI)

```powershell
# prasyarat: MSYS2 (https://www.msys2.org/) + Git
cd decoder
powershell -ExecutionPolicy Bypass -File .\cara.ps1
```

Hasil: `decoder\out\YurLapsDecoder.exe` + DLL. Salin isinya ke
`vendor\openstint\` lalu jalankan `python build_exe.bat` / PyInstaller seperti
biasa, atau langsung `npm run dist` di folder `desktop`.

## Mengunci / memperbarui versi OpenStint

Ubah `decoder/patch/upstream-ref.txt` (mis. `v260830.1`). Semua build berikutnya
memakai versi itu — hasilnya bisa direproduksi.

Kalau patch perlu diperbarui (mis. upstream mengubah `src/CMakeLists.txt`),
perbarui `decoder/patch/rename-target.patch` dan uji dengan `cara.ps1`.

## Soal patch perilaku deteksi

Belum dibuat, dan itu disengaja. Dari sumber `src/passing.cpp`, OpenStint
menentukan titik lintasan sebagai:

| Kondisi | Titik yang dipakai |
|---|---|
| hit < 16 | rata-rata berbobot RSSI (ambang `max − 6 dB`) |
| transponder berhenti lama | titik saat kendaraan pergi |
| ada 2 lembah RSSI | titik tengah antara dua lembah |
| ada 2 puncak | titik tengah antara dua puncak |
| selain itu | titik tengah antara persilangan `max − 6 dB` |

Jadi sudah **bukan** "hit terakhir" — sudah pusat lintasan. Mengubahnya tanpa
data ukur berisiko menurunkan akurasi. Kalau nanti terbukti ada bias, patch
ditambahkan ke folder `patch/` yang sama.
