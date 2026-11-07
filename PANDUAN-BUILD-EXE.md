# Build YurLaps Windows 0.4.1

Paket ini adalah source repository. Installer Windows belum terbentuk hanya
karena ZIP source sudah dibuat. Gunakan repository yang sudah ada:
`https://github.com/Khasya207/Yurlapsss`.

## Build lewat GitHub Actions

1. Ekstrak ZIP sumber terbaru, lalu salin **seluruh isi** ke checkout repository
   aplikasi. Pastikan `.github/workflows/build-windows.yml` ikut.
2. Commit dan push perubahan melalui GitHub Desktop atau Git. Jangan menambahkan
   `node_modules`, `.venv`, database pribadi, atau hasil build.
3. Buka **Actions -> Build Windows -> Run workflow**. Push ke `main` juga memicu build.
4. Workflow menjalankan tes, membangun YurLaps Decoder dan DLL, membundel mesin
   Python beserta UI, kemudian membangun installer Electron.
5. Setelah semua job hijau, unduh artifact `YurLaps-Windows`.
6. Jalankan **YurLaps-Setup-0.4.1.exe**. Jangan memilih file `*-full.exe` sebagai
   installer utama. ZIP portable, bila dibutuhkan, harus diekstrak seluruhnya.

Jika build merah, installer belum siap. Baca langkah yang gagal; jangan mengambil
artifact versi sebelumnya lalu menamainya sebagai versi baru.

## Build lokal pada Windows

Prasyarat: Python 3.12 x64, Node.js 22, npm, dan binary decoder beserta DLL.
Cara paling mudah memperoleh decoder adalah artifact `yurlaps-decoder` dari
workflow yang sama. Salin isi `decoder-out` ke `vendor/openstint` dan sertakan
`ATTRIBUTION.txt` serta arsip sumber decoder GPL.

Jalankan `build_exe.bat`. Script memasang `requirements.txt`, memverifikasi file,
membangun `YurLapsEngine.exe`, kemudian menjalankan `npm ci` dan `npm run dist`
di `desktop`. Hasil ada di `dist-electron`.

Build lokal sengaja gagal jika binary decoder tidak tersedia, agar tidak
menghasilkan installer yang terlihat lengkap tetapi gagal memulai SDR.
Jalur `stintlab.spec` adalah fallback developer dan bukan installer utama.

## Menerbitkan ke website

1. Deploy website terbaru dan migration Supabase yang belum diterapkan. Patch
   kontrak terbaru adalah
   `final-website/supabase/migrations/20261107180000_production_timing_contract.sql`.
   Ini perubahan tambahan pada schema lama; bukan perintah menghapus database.
2. Pastikan bucket release dan proxy `/releases/` website sudah dikonfigurasi
   sesuai `DEPLOY-UPDATE-WEBSITE.md`.
3. Tambahkan secrets repository `YURLAPS_SUPABASE_URL` dan
   `YURLAPS_SUPABASE_SERVICE_ROLE_KEY`. Service-role key hanya ada di CI.
4. Pastikan `app/config.py`, `desktop/package.json`, dan lockfile memiliki versi
   yang sama. Buat tag `v0.4.1` pada commit tersebut, lalu push tag.
5. Workflow membangun installer dan mengunggahnya beserta `latest.yml`, blockmap,
   dan `manifest.json` ke storage website.
6. Verifikasi halaman download website dan menu Pembaruan di aplikasi Windows
   versi sebelumnya. Pengguna mengunduh dari domain website tanpa pindah GitHub.

Jangan menimpa binary untuk versi yang sudah terpasang di pengguna. Naikkan versi
jika memperbaiki release yang sudah dipublikasikan, karena updater membandingkan
versi aplikasi. Perubahan source lokal belum mengubah release yang ada di website.

## Data ketika memasang pembaruan

Data di `%APPDATA%\YurLaps` dipertahankan. Lakukan backup sebelum uji migrasi dan
uji dari instalasi lama yang mempunyai race/practice belum tersinkron. Instalasi
update harus ditolak saat race berjalan atau belum disimpan; setelah aman,
aplikasi menyimpan data, berhenti, memasang program baru, lalu dibuka kembali.

## Pemeriksaan sebelum rilis

Lihat `docs/PRODUCTION-CHECKLIST.md`. Jangan menyimpulkan decoder plug-and-play,
TTS, atau update Windows berhasil hanya dari tes Python/JavaScript pada macOS.
