# YurLaps offline: decoder, race, member, dan hasil

## Data decoder

Menu Decoder mengambil data asli dari `/api/state`, bukan log simulasi di browser.
Log tetap menampilkan ID yang belum menjadi member/peserta, dengan nama kosong atau
"Belum terdaftar". Kolom: ID, nama, RSSI (dB), hit count, passing duration (µs),
dan timestamp decoder (ms). Noise diambil dari pesan `S`; proses OpenStint yang
berjalan dibedakan dari aliran data yang benar-benar diterima. Gain/restart dikunci
selama countdown/race karena restart mengubah epoch timestamp decoder.

Protokol rujukan: [OpenStint v260830.1](https://github.com/zsellera/openstint/blob/v260830.1/docs/decoder-protocol.md).

## Waktu race

- Semua lap dan urutan hasil memakai timestamp `P` OpenStint. Tidak ada fallback
  ke jam kedatangan pesan di komputer.
- Grid: GO memakai jam decoder terakhir yang diterima, maksimal berumur 3 detik.
  Ini adalah anchor status/passing terbaru, bukan sinyal GO yang disinkronkan ke
  perangkat; ada ketidakpastian sebesar jeda pelaporan. Nilai anchor dicantumkan
  pada arsip dan PDF. GUI mengumumkan GO setelah API mengonfirmasi start.
- Staggered: passing pertama setiap pembalap menjadi titik nol, tidak menjadi lap.
- Segmen start grid masuk lap count dan total waktu, tetapi dikecualikan dari
  statistik pace (best, top3, P80, consistency).
- Timestamp duplikat/lebih tua untuk peserta yang sama dan lintasan di bawah
  minimum lap (1,5 detik untuk race) tidak menggeser acuan.
- Timestamp status mundur menghentikan race; sesi baru diperlukan setelah restart.
- Target waktu: cutoff tetap berdasarkan timestamp decoder. Ada jendela 2 detik
  waktu decoder untuk menerima passing yang difinalisasi setelah cutoff namun
  timestamp-nya masih sebelum cutoff. Passing setelah cutoff tidak menambah lap.
- Data decoder terputus: timer penilaian ditahan; operator dapat menghentikan sesi.
  Timer tidak diteruskan memakai jam komputer.
- Peserta yang sudah mencapai target lap tidak menambah lap lagi.
- Urutan lap/time: jumlah lap menurun, total waktu meningkat, lalu timestamp akhir.
  Mode best/top3/consecutive memakai statistik yang bersangkutan.

## Kecepatan

Isi **jarak antarkabel loop** dalam meter pada Track: jarak dua kabel penerima
searah gerak mobil, bukan panjang lintasan. Contoh 0,30 m.

`km/h = loop_width_m × 3.600.000 / pass_duration_us`

Contoh 0,30 m dan 90.000 µs menghasilkan 12 km/h. Nilai nol/tidak tersedia tidak
menghasilkan kecepatan. Top speed adalah maksimum sampel passing sah, average
speed adalah rata-rata aritmetik sampel yang tersedia. Hanya tampil di detail
pembalap. Data RF mentah, speed per passing, dan snapshot lebar loop disimpan
bersama hasil agar pengeditan track berikutnya tidak mengubah arsip.

Migrasi v2 menambah `loop_width_m` kosong. Kolom lama `width_m` tetap disimpan
karena sebelumnya berarti keliling track. Isi ulang jarak kabel yang sebenarnya;
angka lama tidak otomatis digunakan untuk speed. Arsip lama yang tidak memiliki
passing duration tidak direkayasa ulang menjadi speed.

## Member tanpa website

Tambah/Edit member bekerja offline, termasuk beberapa ID per pembalap. ID 0 sah;
ID ganda antar-member ditolak. Database baru tidak diisi member/rekor contoh.
Data yang sudah ada di database lama tetap dipertahankan.

Impor member JSON menerima array atau objek `members`, misalnya ekspor yang
nantinya disediakan website. Tidak ada permintaan jaringan atau sinkron otomatis:

```json
{"members":[{"id":"web-123","name":"Budi Santoso","nick":"Budi","kelas":"Mod","mobil":"Xray","transponder":[1615544]}]}
```

ID sumber diarsipkan dengan prefix `web-` agar tidak berbenturan dengan ID lokal.
Impor bersifat transaksional: konflik satu baris membatalkan seluruh impor.
Member impor tetap bisa diedit manual. Kontrak API live yurlaps.dpdns.org belum
tersedia/terverifikasi; tombol impor ini memakai berkas JSON lokal.

## Laporan

History membuka hasil, matriks lap, position progression (peringkat pada tiap
lap selesai), gap to leader, dan distribusi lap (min/max, Q1/Q3, median).
P80 avg adalah rata-rata 80% lap tercepat, jumlah sampel dibulatkan ke atas.
Consistency adalah `max(0, 1 - population_stddev / mean) × 100`.
DNS berarti tanpa lap; DNF berarti belum mencapai target lap pada akhir sesi.

Export PDF bekerja lokal melalui `/api/races/<id>/pdf`, dengan empat bagian yang
sama. Race panjang/banyak pembalap dapat memakai halaman tambahan. Semua status
sinkron tetap lokal; tombol sinkron tidak menandai sukses tanpa koneksi server.

## Validasi

- `python -m unittest tests.test_race_engine`
- `python tests/test_smoke.py`
- `python tests/test_integration.py`
- `python tests/test_manager.py`

Pengujian receiver memakai publisher ZeroMQ/proses decoder uji. Validasi radio
fisik, driver WinUSB, serta pembacaan transponder di track tetap perlu dilakukan
pada perangkat Windows dan SDR sesungguhnya.
