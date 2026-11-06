# Akun website dan sinkronisasi offline

Gunakan tombol **Akun → Login melalui website**. Browser membuka
https://yurlaps.dpdns.org/desktop-login, menggunakan email/password dan CAPTCHA
website. Login pertama dan pengambilan data awal memerlukan internet. Akun
harus menjadi **leader aktif** pada setidaknya satu club. Pilih club di panel
akun bila akun memimpin beberapa club.

## Saat internet terputus

Setelah data awal diterima, track, member, transponder, dan preset tersedia di
SQLite lokal. Timing decoder, hasil, grafik dan PDF tetap berjalan tanpa
internet. Tekan Simpan setelah race. Hasil masuk antrean persisten dan dicoba
unggah setiap 30 detik selama aplikasi berjalan. Antrean juga dilanjutkan saat
aplikasi dibuka kembali. Menutup aplikasi menghentikan unggahan hingga aplikasi
dijalankan kembali; aplikasi tidak memasang layanan background OS.

Tombol Unggah/Sinkronkan menjadwalkan percobaan, bukan menyatakan upload sukses.
Status `terkirim` hanya disimpan setelah server mengonfirmasi ID dan club yang
sesuai. Percobaan ulang memakai ID tetap, sehingga respons yang hilang tidak
membuat hasil duplikat. Semua cache club yang masih diizinkan untuk akun yang
login diperiksa, termasuk hasil tertunda dari club sebelumnya.

## Kepemilikan data

- Akun, role, member website dan kepemilikan transponder dikelola di website.
  Aplikasi mengambil profil, nama TTS, class, brand dan transponder mereka.
  Member lokal/tamu dan impor manual dari PR #2 tetap tersedia; sinkronisasi
  tidak membuat akun website dari nama tamu atau memindahkan kepemilikan alat.
- Track dan preset disinkronkan dua arah. Jarak antarkabel `loop_width_m`
  terpisah dari `width_m` lama. Perubahan memakai nomor revisi. Bila website dan
  perangkat berubah bersamaan, pilih versi website atau lokal di panel akun.
- Hasil race dikirim ke dashboard club, lengkap dengan lap dan snapshot sesi
  (termasuk raw passing, konfigurasi, statistik dan clock decoder).
- Database disimpan terpisah per akun/club di folder data `clubs/<user>/<club>.sqlite3`.
  Sesi yang sedang berjalan atau belum disimpan harus diselesaikan/disimpan
  sebelum ganti akun atau club. Logout tidak menghapus cache atau antrean.
- Race lama yang belum mempunyai club asal **tidak** otomatis dipindahkan atau
  diunggah. Database lokal lama tetap tersedia setelah logout.
- Role leader diperiksa lagi setiap sync dan di RPC server. Jika role dicabut,
  unggahan dihentikan; cache lokal dipertahankan. Sesi auth yang habis meminta
  login ulang tanpa membuang data offline.

## Konfigurasi dan rollout

Default memakai proyek Supabase website `eottbwdcxrhgxadwnlmy` dan domain publik
YurLaps. Hanya publishable key yang disertakan. Service-role key tidak digunakan.
Override pengembangan: `YURLAPS_SUPABASE_URL`, `YURLAPS_SUPABASE_KEY`, dan
`YURLAPS_WEBSITE_URL` (HTTPS). Sesi dari proyek berbeda tidak digunakan ulang.

Website harus menyediakan `/desktop-login` dan migrasi `desktop_club_sync`
sebelum login/sync aplikasi diaktifkan. RPC server adalah
`desktop_sync_entity` dan `desktop_upload_race`, keduanya SECURITY INVOKER dengan
pemeriksaan leader dan RLS. Jalankan tes SQL dalam repositori website sebelum
memasang migrasi.

Sesi disimpan di `cloud-session.json`, dengan DPAPI per pengguna Windows atau
izin file 0600 pada macOS/Linux. Token tidak dikirim ke renderer aplikasi.
Handoff login menggunakan POST ke loopback, nonce sekali pakai, batas lima
menit, dan validasi Origin/Host. Sesi desktop terpisah dari sesi browser website.

## Pengujian

`python -m unittest discover -s tests -p 'test_*.py'` menguji persistensi antrean,
refresh token, role dicabut, isolasi club, callback/replay, konflik, soft delete,
serta regresi timing dan PDF. Jalankan pula `tests/test_smoke.py`,
`tests/test_integration.py`, dan `tests/test_manager.py`.

Login CAPTCHA dengan akun pengguna nyata, perangkat decoder fisik, dan paket
Windows tetap memerlukan uji penerimaan pada lingkungan pengguna.
