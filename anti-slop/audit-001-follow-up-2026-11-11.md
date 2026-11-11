# Tindak lanjut audit UI YurLaps 001

Tanggal: 11 November 2026  
Arah yang disetujui: minimalis, simpel, elegan, futuristik, gerak ringan. Semua fitur, ID elemen, dan JavaScript produksi dipertahankan.

## Perubahan

- **1, kontras:** token teks dan aksen diperbarui. Kontras teks sekunder `#526779` pada putih 5,87:1; `#5d7082` pada putih 5,12:1. Aksen tombol `#086d99` terhadap putih 5,74:1.
- **2, navigasi dan ukuran target:** sidebar memakai logo YurLaps asli; navigasi ponsel menampilkan delapan menu dalam dua baris. Tombol pada lebar ponsel berukuran minimum 44 px. Track dan preset disusun per baris agar tidak bertabrakan.
- **5, gerak:** orbit splash tanpa akhir dihapus; transisi halaman singkat dan penghormatan `prefers-reduced-motion` ditambahkan.
- **6, Quick Race:** papan timing diberi hierarki lebih jelas, angka dan aksi utama diperbesar, semua 13 kolom hasil tetap tersedia.
- **7, Decoder:** status/noise, perangkat/gain, lalu passing log disusun sebagai tiga tingkat informasi. Semua fungsi dan ID Decoder tetap ada.
- **8, identitas:** logo yang dikirim pemilik tampil di splash dalam warna aslinya dan di atas sidebar; navy dan biru logo menjadi dasar palet.

## Di luar perubahan visual ini

- **3:** operasi keyboard penuh untuk baris leaderboard dan pengelolaan fokus dialog memerlukan perubahan perilaku JavaScript. Tidak diubah agar logika fitur tetap persis seperti sebelumnya.
- **4:** error permanen pada formulir juga memerlukan perubahan perilaku. Toast lama tetap bekerja; tidak diubah dalam pekerjaan tampilan ini.

## Pemeriksaan

- Semua ID elemen HTML sebelum dan sesudah perubahan identik; `quickrace.js` tidak diubah.
- `node tests/test_ui.js`: 8/8 lolos. `python3 -S tools/verifikasi_file.py`: lolos.
- Pratinjau browser diperiksa pada lebar 320, 390, 768, dan 1280 px; `scrollWidth` dokumen sama dengan lebar viewport. Layout Decoder dan Quick Race ditinjau secara visual pada desktop dan ponsel.
- Belum diuji sebagai installer Windows dengan decoder fisik; pengujian visual menggunakan pratinjau statis dan tes produksi UI yang tersedia.
