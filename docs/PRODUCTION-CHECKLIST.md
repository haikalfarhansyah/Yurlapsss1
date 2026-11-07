# Checklist produksi YurLaps 0.4.1

Dokumen ini adalah kontrak penerimaan paket terbaru. Status "kode tersedia"
menunjukkan implementasi dapat diperiksa di source; bukan bukti bahwa hardware,
Windows, Supabase produksi, atau release website sudah diuji dan dipublikasikan.
Tes otomatis dijalankan oleh workflow Build Windows dan harus hijau sebelum rilis.

## Kontrak yang harus dipenuhi

| Area | Perilaku yang harus diverifikasi | Sumber utama |
|---|---|---|
| Startup | Logo, UI lengkap, Practice paling atas, tanpa data atau login demo | app/static/quickrace.html, app/main.py |
| Akun | Login email/password nyata, nama akun, pilihan club, logout | app/cloud.py, app/webapp.py |
| Practice | Otomatis ketika tidak ada race; sesi per driver; log dikelompokkan per member; detail lap dan statistik lengkap | app/practice.py, app/static/quickrace.js |
| Practice cloud | Sesi selesai masuk antrean, dikirim ke dashboard pribadi; privasi di website | app/race_upload.py, website |
| Preset | CRUD/duplikat; countdown; random delay 1-3; lap/time; selesaikan lap saat timeout; grid/staggered/manual/auto/gap; ranking dan penjelasan | app/race_config.py, app/static/quickrace.js |
| Timing | Passing pertama baseline; lap pertama passing kedua; grid waktu global; staggered waktu pribadi; target setiap driver sendiri | app/race.py |
| Finish | Dialog tidak menghentikan race sebelum keputusan; Batal lanjut; Simpan/Buang lalu Reset | app/race.py, app/static/quickrace.js |
| Leaderboard | Posisi emas/perak/perunggu, animasi passing, detail driver, passing log, hasil sesuai preset | app/static/quickrace.js |
| Member | Nama/nick/TTS wajib, foto, mobil/class/transponder; driver offline; konflik transponder ditolak | app/club.py, app/member_sync.py |
| Track | Tambah/edit/hapus, satu track aktif, lebar loop dan min lap detik berlaku race/practice | app/club.py, app/webapp.py |
| Decoder | Biner dibundel, mulai otomatis, cek/retry 5 detik, noise dBFS, gain, log ID/nama/RSSI/hit/passing time | app/decoder_manager.py, app/main.py |
| Gangguan | Setelah 5 detik putus: timing pause/terganggu; clock restart tidak membuat lap palsu | app/race.py |
| Audio | Satu passing satu beep langsung, ignore punya tone berbeda, TTS antre; nama TTS; countdown angka dan horn GO | app/static/sounds.js |
| Announce | Posisi setiap passing/ketika berubah, lap/last/finish, best lap global hanya jika rekor baru pada N lap | app/static/sounds.js |
| Hasil | Otomatis sync, offline queue, status tersinkron hanya setelah server; hapus app dan web | app/cloud.py, app/race_upload.py |
| Identitas | Nama/akun snapshot tidak mengikuti pemilik transponder baru | app/club.py, app/race_upload.py |
| PDF | Download PDF asli, leaderboard/data yang sama, header/tabel tidak terpotong | app/race_report.py |
| Website | Community publik tanpa login, card dapat dibuka, foto club, personal dashboard, top 10 race-only per track | repository website |
| Update | Download via website, unduh di aplikasi, larang install saat race aktif/belum disimpan, data dipertahankan | desktop/src/main/updates.js |

## Gerbang tes otomatis

Jalankan `python tools/verifikasi_file.py`, kemudian seluruh unittest di `tests/`.
Script legacy `test_smoke.py`, `test_integration.py`, dan `test_manager.py` juga
harus dijalankan langsung karena sebagian pengujiannya bukan kelas unittest.
CI memeriksa sintaks JavaScript, membangun mesin, memuat UI melalui Electron,
dan memeriksa decoder bisa dieksekusi. Simulator hanya ada di folder test.

Hasil verifikasi lokal terbaru dilaporkan terpisah oleh agen/CI. Dokumen ini
tidak mengklaim pengujian yang belum dijalankan.

## Penerimaan pada Windows + hardware (masih perlu dijalankan di perangkat)

- Pasang pada Windows 10/11 x64 bersih; buka dengan SDR belum dicolok.
- Colok SDR yang drivernya benar; decoder harus mulai tanpa openstint.exe manual.
- Cabut/pasang saat idle, Practice, dan race; periksa retry 5 detik dan penanda
  gangguan. Tidak boleh muncul lap melintasi reset timestamp.
- Uji dua transponder lewat hampir bersamaan, passing terlalu cepat, driver
  bukan peserta, dan driver yang sudah finish: masing-masing bunyi beep; TTS
  tidak saling menimpa. Uji voice bahasa Indonesia yang terpasang di Windows.
- Uji grid/staggered, target lap/time, selesaikan lap saat timeout, semua ranking,
  Batal Finish, Simpan, Buang, Reset, dan buka ulang aplikasi.
- Putus internet, simpan race/practice, sambungkan kembali, pastikan tidak ganda.
  Hapus hasil dan verifikasi hilang juga pada web setelah sinkronisasi.
- Login sebagai pemilik akun untuk memeriksa dashboard pribadi dan privacy.
  Buka club publik dalam browser tanpa login.
- Periksa PDF dengan nama panjang, banyak peserta, banyak lap, dan beberapa
  halaman. Nama peserta tidak boleh berubah ketika transponder dipindah akun.
- Uji update dari versi Windows sebelumnya, dengan database berisi data dan
  antrean offline; setelah restart pastikan data tetap tersedia.

## Data dan deployment

Backup `%APPDATA%\YurLaps` sebelum uji update. Data SQLite lokal memakai migrasi
`user_version` transaksional (BEGIN IMMEDIATE/ROLLBACK). Token Windows memakai
DPAPI. Installer tidak boleh menghapus folder data untuk melakukan update.

Migration server tambahan:
`final-website/supabase/migrations/20261107180000_production_timing_contract.sql`.
Migration ini harus ditinjau dan diterapkan pada proyek Supabase yang digunakan;
file source tidak berarti SQL sudah diterapkan. Jangan mereset database lama.

Release website memerlukan installer hasil build Windows, secrets CI yang benar,
bucket release, dan deploy website. ZIP source bukan installer dan belum menjadi
release hanya dengan disalin ke folder lokal.
