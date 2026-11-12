# Jadwal Event (Schedule)

Dokumen ini menjelaskan fitur jadwal di YurLaps: dari mana asalnya, bagaimana
aturannya, dan apa yang harus diperiksa operator sebelum hari raceday.

## Asal-usul

Fitur ini adalah port dari fitur **Schedule** milik LapBeeps 2.10.1 (aplikasi
timing yang terpasang di `C:\Program Files\LapBeeps`). Yang dipindah adalah
*kemampuannya*, bukan model datanya:

| LapBeeps | YurLaps |
|---|---|
| RaceMeeting | Event (`club_events.document`) |
| Ladder (kompetisi) | Class / kelas event |
| LadderRound (qualifier/final + aturan seeding) | Rencana (plan) + jenis sesi |
| Session / heat | Stage (sesi) |
| Group | `stage.group` (Grup A, Grup B, ...) |
| Participant | Entry (disetujui + check-in) |
| ScheduleScheme (sequential / rounds) | `event.schedule_scheme` |
| RoundSettings (max/min peserta, bump-up, evaluation, drop) | `plan` |
| Seeding (scope / mode / basis / rounds / drop) | `plan.seeding` |
| Schedule + start time + provenance | `event.schedule` + `stage.seed_source` |
| Print schedule | PDF jadwal (reportlab di aplikasi, jsPDF di website) |

Model meeting LapBeeps (event sourcing, 50 command, validator typia) tidak
diadopsi karena YurLaps sudah punya padanannya sendiri dan memakai model
dokumen yang bisa disinkronkan ke website.

## Aturan main

Semua aturan ada di `app/app/schedule.py` (murni, tanpa I/O) dan diuji di
`app/tests/test_schedule.py`.

- **Pembagian grup.** Peserta yang sudah *disetujui* pada satu kelas dipecah
  menjadi grup. `max_group_size` = jumlah maksimal peserta per grup.
  - `scope=front` (default): pembalap tercepat di grup A, lalu grup B, dan seterusnya.
  - `scope=even`: peserta dibagi rata, jadi tiap grup punya pembalap cepat dan lambat.
- **Bump-up.** `bump_up` adalah jumlah peserta maksimum di grup terakhir yang
  boleh "naik" ke grup sebelumnya. Contoh: 9 peserta, grup maksimal 4, bump-up 1
  → grup pertama berisi 4, grup kedua berisi 5 (grup terakhir dengan 1 peserta
  dilebur ke atas). Grup yang dilebur tidak perlu dijalankan sendiri.
- **Seeding.** `basis` memilih sumber peringkat dari hasil sesi sebelumnya:
  `roster` (urutan peserta), `position` (posisi lintasan), `result` (total
  waktu), `best` (best lap), `top3` (rata-rata 3 lap tercepat),
  `consecutive` (3 lap berurutan tercepat). `rounds` = berapa sesi terakhir
  yang dinilai (nilai terbaik per peserta yang dipakai). `drop` = jumlah
  pembalap terburuk yang dibuang, jadi tidak masuk ronde berikutnya.
- **Arah seeding.** `mode=forward` (terbaik lebih dulu) atau `reverse`
  (balik grid). Pembalikan terjadi setelah pembalap terburuk dibuang.
- **Jam mulai.** `event.start_at` (waktu mulai event) + durasi sesi + jeda
  (`plan.gap_ms`). Durasi diambil dari `plan.duration_ms`; bila kosong, dari
  target preset sesi (waktu), atau dari `plan.estimate_lap_ms` untuk target
  jumlah lap. Tanpa `start_at` sesi tidak punya jam absolut.
- **Urutan sesi.** `schedule_scheme.type=sequential` (kelas demi kelas) atau
  `rounds` (semua kelas di ronde yang sama berurutan). `finals_by_group`
  mengurutkan final per grup, bukan per kelas.
- **Sumber peserta (provenance).** Tiap sesi menyimpan `seed_source`, misalnya
  `Roster` atau `Hasil Kualifikasi · total waktu · balik grid · buang 1 terburuk`,
  sehingga operator tahu asal pembalap di setiap grup.

## Alur operator

1. Tab **Jadwal** pada halaman Event. Isi **jam mulai event** dan **urutan sesi**.
2. Tambahkan **rencana**: kelas, jenis sesi, jumlah peserta per grup, bump-up,
   sumber peserta, dasar seeding, pembagian grup, arah, jumlah ronde yang
   dinilai, peserta yang dibuang, durasi, dan jeda.
3. Tekan **Buat sesi** pada rencana. Sesi otomatis dibuat lengkap dengan
   peserta dan label sumber.
4. Jalankan sesi seperti biasa. Setelah sesi **disimpan**, sesi berikutnya yang
   sumbernya sesi itu **di-seed ulang otomatis**. Sesi yang pesertanya diubah
   manual (`seed_manual`) tidak ditimpa — tekan **Seed ulang** bila memang mau.
5. Unduh **PDF jadwal** untuk ditempel di pit.

## Yang tampil di website

Jadwal dihitung satu kali di aplikasi dan ikut di dalam dokumen Event
(`event.schedule`), jadi website menampilkan urutan, jam mulai, dan peserta
yang sama. Website hanya menampilkan peserta yang **disetujui**, dan untuk tamu
club publik peserta harus **sudah check-in**. Aturan ini ditegakkan di
`web/supabase/migrations/20261113090000_club_schedule.sql`.

Migrasi itu wajib diterapkan di Supabase (SQL Editor) sebelum jadwal tampil di
website. Kalau belum, halaman Jadwal di aplikasi tetap berfungsi; website
menampilkan daftar sesi kosong.

## Pemeriksaan

```bash
python tools/verifikasi_file.py
python -m unittest tests.test_schedule tests.test_event_schedule tests.test_event_schedule_api
cd ../web && npm test && npm run test:schedule
```

`npm run test:schedule` menjalankan database PGlite: menyimpan event berisi
jadwal, memastikan server menolak `schedule`/`plans` yang bukan array, dan
memastikan hanya peserta yang disetujui + check-in yang terlihat oleh tamu.
