# Cara pakai — decoder ikut dibangun di repo yang sama

Tidak perlu repo kedua. Cukup push repo YurLaps (private) lalu jalankan
workflow **Build Windows**.

## 1. Push
Pastikan folder ini ikut ter-commit:

```
decoder/patch/rename-target.patch
decoder/patch/upstream-ref.txt     <- v260830.1 (versi terkunci)
decoder/ATTRIBUTION.txt
decoder/cara.ps1
decoder/README.md
vendor/openstint/ATTRIBUTION.txt
```

## 2. Jalankan workflow
Tab **Actions** → *Build Windows* → **Run workflow**.

Urutan job otomatis:

```
test  →  decoder  →  build
```

`decoder` menghasilkan `YurLapsDecoder.exe` + DLL + `yurlaps-decoder-src.zip`
(GPL), lalu `build` mengunduhnya ke `vendor/openstint/` sebelum membangun
aplikasi.

## 3. Unduh hasil
Artifact **YurLaps-Windows** berisi installer aplikasi (sudah memuat decoder).
Artifact **yurlaps-decoder** berisi biner decoder terpisah + arsip sumber —
simpan/unggah arsip sumber itu bila kamu mendistribusikan biner decoder secara
terpisah (kewajiban GPL-3.0).

## 4. Memakai decoder di komputer sendiri

```powershell
cd decoder
powershell -ExecutionPolicy Bypass -File .\cara.ps1
# hasil: decoder\out\YurLapsDecoder.exe + DLL
```

Salin isi `decoder\out\` ke `vendor\openstint\`, lalu build aplikasi seperti
biasa.

## Cek cepat bahwa decoder benar-benar ikut di paket

Setelah build, di folder hasil PyInstaller:

```
desktop\engine\_internal\vendor\openstint\YurLapsDecoder.exe
```

Kalau ada, aplikasi akan memakainya (dicari lebih dulu daripada nama lama).

## Kalau build decoder gagal

Job `decoder` akan merah dan job `build` tidak dijalankan — ini disengaja:
lebih baik tahu decoder gagal daripada mendapat installer tanpa decoder.

Penyebab umum:
- `upstream-ref.txt` menunjuk tag yang tidak ada → pakai `v260830.1`
- patch tidak cocok lagi (upstream mengubah `src/CMakeLists.txt`) → perbarui
  `patch/rename-target.patch`
