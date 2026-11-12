# YurLaps 0.4.1

YurLaps mencatat passing dari YurLaps Decoder/OpenStint, menjalankan Practice dan
Quick Race, menyimpan hasil lokal, lalu menyinkronkannya ke website YurLaps.
Target installer: Windows 10/11 x64. Source Python juga dapat dijalankan sebagai
server web lokal; dukungan hardware ARM memerlukan build dan pengujian tersendiri.

## Paket yang benar

ZIP ini berisi **source untuk repository aplikasi**, bukan installer Windows.
Upload seluruh isinya ke repository `Khasya207/Yurlapsss`, termasuk `.github`.
Jangan memakai folder aplikasi lama di luar paket ini, dan jangan menjalankan
HTML dokumentasi sebagai aplikasi. Semua launcher memakai UI produksi yang sama.

Installer dibangun oleh workflow **Build Windows**. Pilih
`YurLaps-Setup-0.4.1.exe` dari artifact `YurLaps-Windows`. File `*-full.exe`
adalah installer jalur pembaruan lama, bukan installer utama. Nama ZIP portable
mengikuti `desktop/package.json` (pola `YurLaps-Setup-<versi>.zip`).

Lihat [panduan build](PANDUAN-BUILD-EXE.md) dan
[checklist penerimaan](docs/PRODUCTION-CHECKLIST.md) untuk batas verifikasi.

## Menu aplikasi

- **Practice**: aktif ketika tidak ada race; leaderboard, session log per member,
  detail per sesi/per lap, penutupan sesi setelah idle, dan antrean sinkronisasi.
- **Quick Race**: track aktif, preset, peserta, countdown, grid/staggered, target
  lap/waktu, pilihan ranking, leaderboard, detail driver, dan passing log.
- **Event**: pendaftaran dan check-in peserta per kelas, sesi (kualifikasi,
  heat, main, final, practice), hasil per sesi, dan klasemen event.
- **Jadwal**: rencana sesi (bagi peserta menjadi grup, seeding dari ronde
  sebelumnya, bump-up), urutan sesi dengan jam mulai, sumber peserta tiap sesi,
  dan cetak PDF jadwal.
- **History Race**: hasil tersimpan, status sinkronisasi otomatis, PDF, dan hapus.
- **Member**: cache member club, nama/nick/TTS/foto/mobil/transponder dan driver offline.
- **Track**: tambah/edit/hapus, track aktif, lebar loop dalam meter, minimum lap
  time dalam detik. Hasil lama memakai snapshot track dan peserta.
- **Decoder**: noise dBFS, gain, diagnostik perangkat, dan passing log.
- **Suara**: beep normal/ignore, countdown/GO, TTS berurutan, posisi, lap,
  last lap, finish, dan best lap global.

Aplikasi baru tidak mengisi track, peserta, atau hasil contoh. Simulator berada
hanya di `tests/` dan tidak dipanggil oleh aplikasi. HTML mockup lama tidak
termasuk dalam ZIP produksi.

## Mulai menggunakan

1. Pasang installer Windows dan buka YurLaps.
2. Colok RTL-SDR. Decoder yang dibundel dijalankan otomatis dan supervisor
   mencoba lagi setiap 5 detik saat perangkat terputus/belum tersedia.
3. Windows tetap memerlukan driver WinUSB yang benar. Gunakan diagnostik Decoder;
   pemasangan driver USB dapat memerlukan izin administrator.
4. Buat track, isi lebar loop dan minimum lap time, lalu tandai aktif.
5. Tambahkan driver offline atau login menggunakan akun leader club, lalu pilih club.
6. Practice mencatat passing secara otomatis. Untuk race, buat preset, pilih
   peserta, lalu Start. Finish membuka pilihan Simpan/Buang/Batal; Batal tidak
   menghentikan race. Reset tersedia setelah sesi diputuskan.
7. Untuk event dengan beberapa sesi, pakai tab **Jadwal**: susun rencana
   (jumlah peserta per grup, sumber seeding), tekan **Buat sesi**, lalu isi jam
   mulai event agar urutan dan jam tiap sesi tampil. Jadwal ikut tersinkron
   ke website club.

Passing pertama menjadi titik awal waktu driver, bukan lap 1. Passing berikutnya
menghasilkan lap. Passing terlalu cepat tidak menjadi lap tetapi tetap tercatat
di log dan berbunyi ignore. Gangguan timing tidak boleh menghasilkan lap yang
melintasi restart decoder; lihat checklist untuk skenario pengujian perangkat.

## Akun, data, dan sinkronisasi

Login memakai Supabase asli. Setelah login leader dan memilih club, hasil yang
tersimpan masuk antrean SQLite. Saat offline data tetap lokal; worker mengirim
ulang otomatis ketika koneksi pulih. Status tersinkron baru muncul setelah server
mengonfirmasi. Tidak perlu menekan upload manual.

Identitas peserta disimpan sebagai snapshot akun/nama saat sesi dibuat. Pengalihan
kepemilikan transponder tidak boleh mengganti nama hasil lama. Practice pribadi
masuk dashboard akun pemilik; pilihan publik/private diatur pemilik di website.
Top 10 lap record track hanya mengambil hasil race.

Konfigurasi proyek default berada di `app/cloud.py` dan dapat diganti lewat
`YURLAPS_SUPABASE_URL`, `YURLAPS_SUPABASE_KEY`, dan `YURLAPS_WEBSITE_URL`.
Kunci aplikasi adalah publishable key, bukan service-role key.

Data Windows berada di `%APPDATA%\YurLaps`; Linux/macOS memakai `~/.yurlaps`.
`YURLAPS_DATA` dapat menentukan lokasi lain. Backup folder data sebelum perubahan
besar. Installer/update mengganti program, bukan menghapus database SQLite.
Token Windows dilindungi DPAPI. Migrasi database lokal dijalankan saat aplikasi
dibuka; SQL server dikelola terpisah.

## Website dan pembaruan

Saat aplikasi dibuka, jendela splash tampil lebih dulu (logo + status proses:
menyiapkan konfigurasi, membuka database, menghubungkan decoder, menyiapkan
antarmuka) lalu jendela utama muncul setelah data siap. Kalau mesin gagal
start, pesan galatnya tampil di dalam splash.

Website dan Supabase harus diperbarui bersama agar kontrak sinkronisasi cocok.
Migration tambahan:
`final-website/supabase/migrations/20261107180000_production_timing_contract.sql`.
File tersebut harus diterapkan pada proyek Supabase yang digunakan; keberadaan
file dalam paket tidak berarti migration sudah dijalankan di server.

Installer Electron membaca rilis dari `https://yurlaps.dpdns.org/releases/`.
Pembaruan diunduh dari domain website, lalu dipasang dengan restart tanpa
uninstall. Gunakan menu Pembaruan. Source/browser mode tidak memasang update
Windows. Rilis baru baru tersedia setelah build dan publikasi berhasil.

Bucket rilis terisi **hanya** setelah tag `v<versi>` di-push: tag itu memicu
workflow yang mengunggah installer, `latest.yml`, dan `manifest.json`. Sebelum
itu, menu Pembaruan menampilkan "Belum ada versi baru dipublikasikan."
(bukan dump respons server). Lihat `PANDUAN-BUILD-EXE.md`.

## Menjalankan source

Windows: `jalankan.bat` membuat virtual environment, memasang `requirements.txt`,
lalu membuka UI lengkap. Decoder `.exe` dan DLL harus ada di `vendor/openstint`
untuk menjalankan SDR otomatis; source ZIP tidak berisi binary hasil build.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m app --web --host 0.0.0.0 --port 8080 --no-browser
```

Buka `http://IP-KOMPUTER:8080` dari LAN. Untuk desktop source di Windows,
`python run_stintlab.py` membuka UI melalui Edge/Chrome mode aplikasi. Argumen
`--native` lama menjadi alias UI lengkap; tidak membuka UI Tk tiga menu.

## Verifikasi source

```bash
python tools/verifikasi_file.py
python -m unittest discover -s tests -p 'test_*.py'
python tests/test_smoke.py
python tests/test_integration.py
python tests/test_manager.py
node --check app/static/quickrace.js
node --check app/static/sounds.js
node --check app/static/event.js
```

Tes memakai data terisolasi dan simulator khusus test. Hasil tes software tidak
menggantikan pengujian RTL-SDR, audio, installer, dan update pada Windows nyata.
Untuk membuat ulang ZIP sumber dari checkout ini:

```bash
python tools/package_source.py --output ../YurLaps-application-production.zip
```
