# Rilis aplikasi YurLaps dari website

Alur update langsung sudah disiapkan untuk Windows:

1. Jalankan migration `final-website/supabase/migrations/20261106180000_yurlaps_release_bucket.sql` dan `20261106193000_yurlaps_activity_and_club_avatar.sql` di Supabase.
2. Tambahkan secrets GitHub Actions `YURLAPS_SUPABASE_URL` dan `YURLAPS_SUPABASE_SERVICE_ROLE_KEY`. Service role hanya dipakai workflow, tidak pernah dimasukkan ke website atau aplikasi.
3. Buat tag versi, misalnya `v0.3.1`, lalu push tag ke repository aplikasi. Workflow Windows membuat `YurLaps-Setup-0.3.1.exe`, `latest.yml`, dan `manifest.json` ke bucket `yurlaps-releases`.
4. Website membaca `/releases/manifest.json`, sehingga tombol download tetap berada di domain YurLaps. Aplikasi Electron memeriksa `/releases/latest.yml`, mengunduh, menunggu race selesai, lalu memasang update dan restart tanpa uninstall.

Versi aplikasi lama yang belum memiliki Electron updater perlu dipasang sekali menggunakan installer terbaru. Setelah itu pembaruan berikutnya dapat dilakukan dari menu Pembaruan di dalam aplikasi.

## Sinkronisasi otomatis dan timing track

Race tersimpan dan sesi practice yang sudah memiliki lap masuk antrean SQLite. Cloud worker mencoba mengirim setiap 15 detik saat akun leader online; jika jaringan putus, antrean dibiarkan dan dicoba lagi saat koneksi pulih. Tidak perlu menekan tombol unggah.

Pengaturan track berada di aplikasi: satu track dapat ditandai aktif untuk decoder, Quick Race, dan Practice. `Lebar loop` dipakai untuk speed km/h, sedangkan `Minimum waktu lap` memakai detik sebagai satuan input dan berlaku untuk kedua mode.
