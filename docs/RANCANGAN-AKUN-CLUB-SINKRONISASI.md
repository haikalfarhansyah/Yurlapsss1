# Rancangan: Akun YurLaps, Club, Member, dan Sinkronisasi Hasil Balapan

Status: **rancangan (belum dikode)** · Tanggal: 2026-11-04 · Target: YurLaps Windows 10+

Dokumen ini menjawab satu pertanyaan: bagaimana aplikasi Windows YurLaps bisa
memakai akun website `yurlaps.dpdns.org` (club, member, transponder) dan
mengunggah hasil balapan — **tanpa mengorbankan timing saat internet mati**.

---

## 1. Prinsip yang tidak boleh dilanggar

| # | Prinsip | Konsekuensi kalau dilanggar |
|---|---|---|
| P1 | **Timing jalan penuh tanpa login & tanpa internet.** | Sirkuit dengan jaringan jelek tidak bisa balapan. Tidak bisa diterima. |
| P2 | **Aplikasi = sumber kebenaran untuk lap mentah.** Website tidak pernah mengubah lap. | Hasil bisa "berubah sendiri" setelah diunggah. |
| P3 | **Website = sumber kebenaran untuk akun, club, member, transponder.** | Dua tempat menyimpan daftar member → cepat tidak sinkron. |
| P4 | **Unggah hanya atas izin operator** (tombol Sinkronkan), bukan otomatis tiap lap. | Data setengah jadi masuk ke website, sulit dibersihkan. |
| P5 | **Token tidak pernah ditulis ke log, tidak pernah dikirim ke selain `*.supabase.co`.** | Kredensial bocor lewat berkas log yang dibagikan. |
| P6 | **Tidak ada data pribadi yang diunggah melebihi yang sudah ada di website.** | Pembengkakan tanggung jawab data. |
| P7 | **Satu club aktif per akun.** Berpindah club mengganti cache, bukan mencampur. | Hasil balapan satu club bisa masuk ke club lain. |
| P8 | **Menyembunyikan tombol bukan pengamanan.** Server selalu memeriksa peran sendiri. | Siapa pun bisa memanggil API langsung. |

---

## 2. Data di website hari ini (hasil pembacaan bundle produksi)

Ini yang **sudah ada** dan bisa langsung dipakai — bukan asumsi:

| Tabel | Kolom yang terbaca | Dipakai aplikasi untuk |
|---|---|---|
| `profiles` | `id`, `user_id`, `full_name`, `nickname`, `avatar_url`, `tts_name` | nama tampilan, nama panggilan (untuk TTS), avatar |
| `transponders` | `id`, `user_id`, `transponder_id`, `rc_class`, `rc_brand` | **pemetaan otomatis transponder → driver** |
| `clubs` | `id`, `join_code`, dll. | club aktif |
| `club_members` | `club_id`, `role`, `status`, `joined_at` | daftar member + peran |
| `club_tracks` | `id`, `club_id` | pilihan sirkuit di sesi |
| `races` | `*`, `club_id`, `raced_at` | riwayat balapan yang sudah ada (read-only bagi aplikasi) |

RPC yang tersedia: `join_club`, `join_club_by_code`, `list_public_clubs`,
`review_join_request`, `remove_club_member`, `update_member_role`,
`update_club_profile`, `update_club_settings`, `regenerate_join_code`.

### Peran di club (sesuai nama di website — sudah diverifikasi dari bundle produksi)

Website memakai empat peran dan satu status. Aplikasi **memakai nama yang sama
persis**, tidak menerjemahkan sendiri:

| `role` | Boleh apa di website | Hak di aplikasi |
|---|---|---|
| `leader` | semua, termasuk ubah profil club | **boleh unggah hasil**; lihat semua member |
| `co_leader` | kelola member, tinjau pendaftar, kelola sirkuit & balapan | **boleh unggah hasil**; lihat semua member |
| `officer` | hanya meninjau pendaftar | lihat member; **tidak boleh unggah** |
| `member` | tidak ada | lihat member; **tidak boleh unggah** |

Status: `active` (anggota) dan `pending` (menunggu persetujuan).
Matriks izin lengkap dari website: `co_leader` → `manage_members`,
`review_applications`, `manage_tracks`, `manage_races`; `officer` → hanya
`review_applications`; `leader` → semuanya termasuk `edit_club`.

**Yang belum ada di server** (harus ditambahkan nanti saat online):
`race_sessions`, `race_laps`, dan endpoint/RPC untuk menerima unggahan.
Lihat bagian 6.

---

## 3. Alur pemakaian (yang akan dirasakan operator)

### 3.1 Login (sekali, lalu teringat)

```
Buka YurLaps → panel Akun → "Masuk"
  → email + password akun yurlaps
  → aplikasi menyimpan refresh token di Windows Credential Manager
  → memuat: profil, club yang diikuti, peran di club, transponder
  → menampilkan "Masuk sebagai Budi (Budi Santoso) — Medan RC (leader)"
Buka aplikasi berikutnya: langsung masuk (offline pun tetap masuk, data dari cache)
```

**Keputusan: login diingat selamanya** sampai pengguna menekan Keluar. Refresh
token diperbarui otomatis tiap kali aplikasi online, disimpan di Windows
Credential Manager (bukan berkas teks, bukan sqlite).

Kunci: **login hanya butuh internet sekali.** Sesudahnya, aplikasi memakai cache
lokal. Kalau token kedaluwarsa saat offline, aplikasi tetap jalan — hanya
sinkronisasi yang ditunda.

### 3.2 Pemetaan transponder → driver (jantung fitur ini)

Ini bagian yang membuat club terasa "nyambung":

```
Sebelum: driver lokal diisi manual ("Budi", "Andi", ...)
Sesudah: aplikasi tahu transponder 1615544 milik member club bernama Budi
         (dari tabel transponders milik user Budi)
```

Aturan pemetaan:

1. Saat login/sinkron profil, ambil semua transponder milik member club aktif.
2. Saat decoder mendeteksi transponder baru, aplikasi mencocokkan `transponder_id`:
   - **cocok & member club** → nama driver otomatis terisi, ditandai "dari club"
   - **cocok tapi bukan member** → diberi tanda "lintasan orang luar"
   - **tidak cocok** → tetap bisa diberi nama manual seperti sekarang
3. Nama otomatis **tidak menimpa** nama yang sudah diisi manual operator.

**Nama lengkap DAN nama panggilan sama-sama disimpan** (keputusan pengguna),
masing-masing punya tempat:

| Tempat | Yang ditampilkan |
|---|---|
| Kartu driver di UI | nama panggilan, dengan nama lengkap di bawahnya (kecil) |
| Papan skor TV | nama panggilan — paling terbaca dari jauh |
| Pengumuman suara (TTS) | `profiles.tts_name` bila ada; kalau tidak, nama panggilan |
| Tabel hasil di sqlite | kedua kolom, plus `user_id` |
| Yang diunggah ke website | `user_id` + kedua nama, supaya website bisa memilih |

Contoh: `Budi` (panggilan) / `Budi Santoso` (lengkap). Kalau transponder tidak
dikenal club, `name_source = manual` dan hanya kolom `driver` yang terisi.

### 3.3 Sesi balapan (tetap offline penuh)

```
Pilih mode: Latihan / Kualifikasi / Balapan  (tetap 3 menu)
Pilih sirkuit dari daftar club_tracks (opsional, bisa juga ketik manual)
Balapan berjalan → lap tersimpan ke sqlite lokal seperti sekarang
Sesi ditandai: club_id, status sinkron = "belum diunggah"
```

Tidak ada satu pun panggilan jaringan di jalur ini. Kalau internet mati di
tengah balapan, tidak ada yang berubah.

### 3.4 Sinkronisasi hasil (pilihan, bukan otomatis)

```
Sesi selesai → tombol "Sinkronkan hasil"  (hanya muncul untuk leader & co_leader)
  → ringkasan sebelum kirim:
      Sesi: Balapan #12 — Sirkuit Medan RC — 8 driver, 214 lap
      Akan diunggah ke: Medan RC (club kamu)
      [ Batal ]  [ Unggah ]
  → unggah bertahap (per sesi, bukan per lap)
  → status berubah: terkirim ✓ (dengan waktu & tautan ke website)
```

Server tetap memeriksa ulang izin lewat RLS — aplikasi tidak menjadi penjaga
keamanan. Pemilih peran di mockup sudah dihapus: aplikasi di lintasan dianggap
dijalankan oleh `leader`.

Kalau gagal (internet putus, server menolak):

```
status = "menunggu"  → dicoba lagi saat operator menekan Sinkronkan
                      → tidak ada percobaan diam-diam di latar belakang
```

Sesi yang sudah terkirim **tidak dikirim ulang**. Penjaganya `client_uid`
(UUID yang dibuat sekali saat sesi dimulai) + kunci unik di server.

### 3.5 Member & transponder (lihat dari aplikasi)

Panel member menampilkan data club **apa adanya dari website**, bukan data lokal:

```
Medan RC — 14 member (kamu: admin)
  Budi          leader      transponder: 1615544 (Xray X4, Mod)
  Andi          co_leader   transponder: 1712003 (Tamiya, Stock), 1712004
  Citra         member      transponder: —
  Rudi          pending     (menunggu persetujuan leader)
```

Aplikasi **tidak** bisa menambah/menghapus member. Tindakan itu tetap di
website. Aplikasi hanya membaca, supaya tidak ada dua tempat yang mengubah
keanggotaan.

Member berstatus `pending` **tetap ditampilkan** dengan penanda kuning, supaya
operator di lapangan tahu kenapa orang itu belum bisa dipetakan ke transponder:
namenya muncul, tetapi transpondernya belum dipakai untuk pemetaan otomatis.

---

## 4. Perubahan skema lokal (sqlite, sisi aplikasi)

Tabel baru di samping `sessions/drivers/stints/laps` yang sudah ada:

```sql
-- Akun yang sedang masuk (token TIDAK disimpan di sini)
CREATE TABLE IF NOT EXISTS account (
    id            INTEGER PRIMARY KEY CHECK (id = 1),   -- satu baris saja
    user_id       TEXT,
    email         TEXT,
    full_name     TEXT,
    nickname      TEXT,
    avatar_url    TEXT,
    club_id       TEXT,          -- club aktif yang dipilih (satu saja)
    club_name     TEXT,
    role          TEXT,          -- leader / co_leader / officer / member
    can_upload    INTEGER,       -- 1 bila role leader/co_leader
    last_sync_at  REAL,          -- kapan terakhir berhasil memuat dari server
    token_expiry  REAL
);

-- Cache club & member (dibaca dari website, tidak pernah diubah aplikasi)
CREATE TABLE IF NOT EXISTS club_cache (
    club_id     TEXT PRIMARY KEY,
    name        TEXT,
    join_code   TEXT,
    updated_at  REAL
);

CREATE TABLE IF NOT EXISTS member_cache (
    user_id       TEXT PRIMARY KEY,
    club_id       TEXT NOT NULL,
    full_name     TEXT,
    nickname      TEXT,
    role          TEXT,
    status        TEXT,          -- active / pending
    updated_at    REAL
);

CREATE TABLE IF NOT EXISTS transponder_cache (
    transponder_id  INTEGER PRIMARY KEY,
    user_id         TEXT,
    full_name       TEXT,
    rc_class        TEXT,
    rc_brand        TEXT,
    updated_at      REAL
);

-- Nama panggilan untuk tampilan & TTS (dari profiles.nickname)
ALTER TABLE drivers ADD COLUMN nickname      TEXT;   -- nama panggilan website
ALTER TABLE drivers ADD COLUMN full_name     TEXT;   -- nama lengkap website
ALTER TABLE drivers ADD COLUMN user_id       TEXT;   -- pemilik transponder (kalau member)
ALTER TABLE drivers ADD COLUMN name_source   TEXT;   -- manual | club

-- Sesi yang menunggu diunggah
ALTER TABLE sessions ADD COLUMN client_uid   TEXT;   -- UUID, dibuat sekali
ALTER TABLE sessions ADD COLUMN club_id      TEXT;
ALTER TABLE sessions ADD COLUMN track_id     TEXT;   -- dari club_tracks (website)
ALTER TABLE sessions ADD COLUMN track_name   TEXT;   -- simpanan nama saat sesi dibuat
ALTER TABLE sessions ADD COLUMN sync_state   TEXT DEFAULT 'local';
    -- local | pending | sent | failed
ALTER TABLE sessions ADD COLUMN sent_at      REAL;
ALTER TABLE sessions ADD COLUMN remote_id    TEXT;   -- id sesi di server
ALTER TABLE sessions ADD COLUMN last_error   TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_uid ON sessions(client_uid);
CREATE INDEX IF NOT EXISTS idx_sessions_track ON sessions(track_id);

-- Cache track milik club (dibaca dari club_tracks, tidak pernah diubah aplikasi)
CREATE TABLE IF NOT EXISTS track_cache (
    track_id     TEXT PRIMARY KEY,
    club_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    length_m     REAL,
    layout_note  TEXT,
    best_lap_ms  INTEGER,        -- rekor dari server (kalau ada)
    best_holder  TEXT,
    updated_at   REAL
);

-- Preset pengaturan sesi (lokal, boleh menyimpan track kesukaan)
CREATE TABLE IF NOT EXISTS preset_cache (
    preset_id    TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    target       TEXT,           -- time | laps
    duration_s   INTEGER,
    laps         INTEGER,
    ranking_rule TEXT,           -- lapsTime | best | top3 | consecutive
    start_mode   TEXT,           -- grid | staggered
    track_id     TEXT,           -- opsional: track bawaan preset
    updated_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_sessions_sync ON sessions(sync_state);

-- Antrean sinkronisasi eksplisit: satu baris per sesi yang mau diunggah.
-- Dipisah dari sessions supaya kegagalan unggah tidak pernah mengubah data lap.
CREATE TABLE IF NOT EXISTS sync_queue (
    client_uid    TEXT PRIMARY KEY,
    session_id    INTEGER NOT NULL,
    state         TEXT NOT NULL DEFAULT 'pending',  -- pending|sent|failed
    attempts      INTEGER DEFAULT 0,
    last_error    TEXT,
    last_try_at   REAL,
    sent_at       REAL,
    remote_id     TEXT
);
```

Catatan: `drivers` yang sudah ada tetap dipakai. Kolom `driver` diisi otomatis
dari `transponder_cache` **hanya jika masih kosong**.

---

## 5. Perubahan skema server (diusulkan, dikerjakan saat fase online)

```sql
-- Satu sesi balapan dari satu aplikasi
CREATE TABLE race_sessions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    club_id      uuid NOT NULL REFERENCES clubs(id),
    track_id     uuid NOT NULL REFERENCES club_tracks(id),  -- WAJIB
    track_name   text,                     -- simpanan nama saat sesi dibuat
    mode         text NOT NULL,            -- practice | qualifying | race
    name         text,
    started_at   timestamptz NOT NULL,
    ended_at     timestamptz,
    client_uid   uuid NOT NULL,            -- dibuat aplikasi, kunci idempotensi
    uploaded_by  uuid NOT NULL REFERENCES auth.users(id),
    created_at   timestamptz DEFAULT now(),
    UNIQUE (club_id, client_uid)            -- unggah ulang tidak menggandakan
);

-- Satu baris per lap
CREATE TABLE race_laps (
    id               bigserial PRIMARY KEY,
    session_id       uuid NOT NULL REFERENCES race_sessions(id) ON DELETE CASCADE,
    transponder_id   bigint NOT NULL,
    driver_name      text,
    user_id          uuid REFERENCES auth.users(id),  -- kalau dikenal sebagai member
    seq              int,
    lap_time_ms      int NOT NULL,
    passed_at        timestamptz NOT NULL,
    rssi             real,
    mer              real
);
CREATE INDEX ON race_laps (session_id, transponder_id);

-- Rekor per track (dihitung server, bukan aplikasi)
CREATE VIEW track_records AS
SELECT s.track_id, l.transponder_id, MIN(l.lap_time_ms) AS best_lap_ms
FROM race_laps l JOIN race_sessions s ON s.id = l.session_id
GROUP BY s.track_id, l.transponder_id;

-- Ringkasan per driver supaya halaman club tidak perlu menjumlah 200+ baris
CREATE TABLE race_entries (
    session_id     uuid REFERENCES race_sessions(id) ON DELETE CASCADE,
    transponder_id bigint,
    driver_name    text,
    user_id        uuid REFERENCES auth.users(id),
    total_laps     int,
    best_lap_ms    int,
    PRIMARY KEY (session_id, transponder_id)
);
```

Keamanan (RLS) yang harus berlaku:

- `race_sessions`: hanya `uploaded_by` atau owner/admin club yang boleh INSERT;
  seluruh member club boleh SELECT.
- `race_laps`: tidak boleh INSERT langsung dari klien; hanya lewat RPC.
- RPC `upload_race_session(payload jsonb)` menerima seluruh sesi dalam satu
  transaksi, memeriksa: pemanggil adalah member aktif club tersebut, dan
  `(club_id, client_uid)` belum ada. Kalau sudah ada → kembalikan `remote_id`
  yang lama, **jangan** buat duplikat.

---

## 6. Bentuk data yang diunggah

Satu permintaan, satu sesi. Ringkas, tanpa data pribadi berlebih:

```json
{
  "client_uid": "8f14e45f-ea4c-4f0b-99c1-1c2f8c7a1b23",
  "club_id": "…",
  "track_id": "…",              -- WAJIB: pangkal semua data
  "mode": "quick_race",
  "name": "Balapan #12",
  "track_name": "Sirkuit Medan RC · Layout A",
  "started_at": "2026-11-04T03:12:00Z",
  "ended_at":   "2026-11-04T04:05:00Z",
  "drivers": [
    { "transponder_id": 1615544,
      "user_id": "…",                  -- kalau dikenal sebagai member club
      "nickname": "Budi", "full_name": "Budi Santoso",
      "name_source": "club",           -- club | manual
      "total_laps": 32, "best_lap_ms": 9412,
      "laps": [ { "seq": 1, "lap_time_ms": 9550, "passed_at": "…", "rssi": -52.1 } ] }
  ]
}
```

Yang **tidak** ikut dikirim: alamat IP, nama perangkat, isi disk, log, setelan
decoder, dan nama driver yang tidak dikenal club.

---

## 7. Penempatan di UI

**Keputusan revisi 2026-11-04 (ke-2):** menu kiri berisi empat hal operasional —
aplikasi ini dipakai di lintasan, bukan di ruang kerja.

| Menu kiri | Isi |
|---|---|
| **Latihan** | sesi bebas, tidak masuk hasil balapan |
| **Quick Race** | balapan langsung — **tanpa babak kualifikasi** |
| **Member** | daftar member club + transponder (baca saja dari website) |
| **Decoder** | status penerima, endpoint, gain, lintasan terakhir — seperti versi sebelumnya |

Akun **bukan menu**: panel yang dibuka dari sudut kanan atas, berisi dua tab:

| Tab panel | Isi |
|---|---|
| Akun | masuk/keluar, email, club aktif, peran, ganti club, sinkron sekarang |
| Sinkronisasi | sesi lokal + status (`local/pending/sent/failed`), tombol unggah, pesan galat |

### Yang dihapus dari rancangan sebelumnya

| Sebelumnya | Sekarang | Alasan |
|---|---|---|
| Menu **Kualifikasi** | dihapus | tidak dipakai; balapan langsung jalan |
| Menu **Balapan** | menjadi **Quick Race** | satu tombol mulai, tanpa babak kualifikasi |
| Tab **Member** di panel akun | menjadi **menu samping** | operator perlu melihatnya sambil bekerja |
| **Pemilih peran** di mockup | dihapus | yang membuka aplikasi dianggap `leader` |

### Izin tetap diperiksa, hanya tidak ditampilkan

Menghilangkan tombol bukan berarti membuka akses. Server tetap menolak
unggahan dari akun yang bukan `leader`/`co_leader`. Yang berubah hanya
tampilan: tidak ada tombol mati dan tidak ada pesan "kamu tidak berhak" yang
membingungkan operator di lapangan.

Indikator jaringan tetap di bilah atas: `Offline` / `Online — n sesi belum
diunggah`. Tidak pernah berupa jendela mengambang.

**Mockup yang bisa diklik:** `docs/ui-mockup-akun-club.html` — mandiri (tanpa
CDN). Tangkapan layar: `ui-mockup-1-quickrace.png`,
`ui-mockup-2-panel-akun.png`, `ui-mockup-3-menu-preset.png`,
`ui-mockup-4-member.png`, `ui-mockup-5-decoder.png`.

## 8. Tahapan pengerjaan (offline dulu, sesuai permintaan)

| Fase | Isi | Kriteria selesai |
|---|---|---|
| **F0 — Kerangka offline** | Skema lokal baru (akun, club/member/transponder cache, `sync_queue`, `client_uid`), panel **Akun & Club** 3 tab, kantong `can_upload` menurut peran, tombol "Klaim ke club". **Tanpa jaringan sama sekali** — data club/member dari berkas contoh `demo-club.json`. | Aplikasi 100% berfungsi tanpa internet; panel menampilkan data contoh; `member`/`officer` tidak melihat tombol unggah; tes lulus |
| **F1 — Klien akun** | Login Supabase, simpan token aman, muat profil + club + member + transponder ke cache. | Login sungguhan berhasil; keluar menghapus token; log bersih dari token |
| **F2 — Pemetaan otomatis** | Decoder → transponder → nama driver club; penanda "orang luar"; tidak menimpa nama manual. | Uji dengan 3 transponder contoh: dikenal / luar / tak dikenal |
| **F3 — Unggah** | RPC `upload_race_session` di server + tombol Sinkronkan + status & galat. | Unggah dobel tidak menggandakan data; gagal jaringan bisa diulang |
| **F4 — Halaman club di web** | Menampilkan hasil balapan yang masuk ke halaman club. | Hasil muncul di website setelah sinkron |

F0 sengaja dikerjakan penuh sebelum F1: supaya kalau integrasi online ternyata
bermasalah, aplikasi tetap utuh dan tidak pernah bergantung padanya.

---

## 9. Keputusan yang sudah diambil

| # | Hal | Keputusan |
|---|---|---|
| 1 | Umur login | **Diingat selamanya** sampai menekan Keluar. Refresh token di Windows Credential Manager, diperbarui otomatis saat online. |
| 2 | Jumlah club | **Satu club aktif**, bisa diganti kapan saja dari panel Akun. Daftar club yang diikuti tetap tersimpan untuk berpindah. |
| 3 | Siapa boleh unggah | **Hanya `leader` dan `co_leader`** (server memeriksa lewat RLS). Di UI tidak ada pemilih peran — yang membuka aplikasi dianggap leader. |
| 4 | Nama driver | **Keduanya disimpan**: `full_name` dan `nickname`. UI & papan skor memakai panggilan, TTS memakai `tts_name`. |
| 5 | Sesi sebelum login | **Bisa diklaim** ke club setelah login — satu tombol "Klaim ke club" di panel Sinkronisasi. `client_uid` sudah dibuat sejak sesi dimulai, jadi klaim tidak mengubah identitas sesi. |
| 6 | Member `pending` | **Ditampilkan** dengan penanda, nama muncul tetapi transpondernya belum dipakai untuk pemetaan otomatis. |
| 7 | Susunan menu | **Latihan · Quick Race · Member · Decoder.** Kualifikasi dihapus; Balapan menjadi Quick Race; Member menjadi menu samping; Decoder dipertahankan. |

## 10. Yang sengaja TIDAK dilakukan

- Tidak ada unggahan otomatis tiap lap atau tiap detik.
- Tidak ada penyuntingan hasil balapan di website (kalau salah, hapus sesi lalu
  unggah ulang dari aplikasi).
- Tidak ada port yang dibuka ke jaringan lokal untuk sinkronisasi; semua lewat
  HTTPS keluar.
- Tidak ada member yang ditambah/dihapus dari aplikasi.
- Tidak ada unggahan otomatis oleh `officer`/`member` — tombolnya tidak
  ditampilkan, dan server tetap menolak.
- Tidak ada penyimpanan password di berkas biasa.
- Tidak ada dukungan Windows 8/8.1 — jalur itu sudah dihapus dari CI
  (2026-11-04).

---

## 11. Data & pembaruan aplikasi (diputuskan 2026-11-04)

Bagian ini menjawab satu pertanyaan: **kalau aplikasi diperbarui, data race,
akun, dan club jadi apa?** Jawabannya ditentukan sekarang, bukan saat rilis
berikutnya.

### 11.1 Dua tempat penyimpanan, dua aturan berbeda

| | Folder aplikasi | Folder data |
|---|---|---|
| Lokasi | `%LOCALAPPDATA%\Programs\YurLaps` (NSIS) | `%APPDATA%\YurLaps` |
| Isi | `YurLaps.exe`, mesin, **decoder**, UI | `yurlaps.sqlite3`, `config.json`, `backup\` |
| Saat update | **ditimpa seluruhnya** | **tidak disentuh** |
| Saat uninstall | dihapus | **dibiarkan** |

Ini sudah berlaku sekarang: `installer/stintlab.iss` memuat komentar
"JANGAN hapus `%APPDATA%\YurLaps` — data sesi & pembalap milik pengguna", dan
decoder ikut di folder aplikasi sehingga **ikut diperbarui** setiap versi baru.

Aturan yang mengikat: **tidak ada satu pun proses update/uninstall yang boleh
menulis ke `%APPDATA%\YurLaps`.**

### 11.2 Skema basis data harus punya versi (dan ini belum ada)

Hari ini `app/storage.py` menjalankan `CREATE TABLE IF NOT EXISTS` dan
**tidak menyimpan versi skema apa pun**. Itu cukup selama bentuk tabel tidak
berubah — tapi F0 akan menambah kolom (`client_uid`, `sync_state`, `nickname`,
…), dan cara yang sekarang akan gagal:

```
ALTER ke-1: berhasil
ALTER ke-2: GAGAL -> duplicate column name: club_id
```

Migrasi harus idempoten. Aturan yang dipakai mulai F0:

```python
SCHEMA_VERSION = 1        # dinaikkan setiap kali bentuk tabel berubah

def migrate(conn):
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    if v >= SCHEMA_VERSION:
        return 0                # sudah terbaru, tidak menyentuh apa pun

    # PENTING: "with conn:" TIDAK cukup. Dengan isolation_level bawaan,
    # sqlite3 hanya membuka transaksi untuk DML (INSERT/UPDATE/DELETE) —
    # ALTER TABLE berjalan di autocommit, sehingga kalau migrasi mati di
    # tengah jalan, kolom setengah jadi TETAP tertinggal. Diuji: kolom
    # "baru" tersisa True dengan pola "with conn:", dan False dengan
    # BEGIN/ROLLBACK eksplisit. Karena itu transaksi dibuka manual.
    lama_level = conn.isolation_level
    conn.isolation_level = None          # autocommit; kita kendalikan sendiri
    dibuat = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        if v < 1:
            dibuat += _tambah_kolom(conn, "sessions", "client_uid", "TEXT")
            dibuat += _tambah_kolom(conn, "sessions", "sync_state", "TEXT DEFAULT 'local'")
            # … kolom lain
            _buat_tabel_cache(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")          # tidak ada perubahan sama sekali
        raise
    finally:
        conn.isolation_level = lama_level
    return dibuat

def _tambah_kolom(conn, tabel, kolom, tipe):
    ada = {r[1] for r in conn.execute(f"PRAGMA table_info({tabel})")}
    if kolom not in ada:            # tanpa ini, ALTER menggagalkan migrasi
        conn.execute(f"ALTER TABLE {tabel} ADD COLUMN {kolom} {tipe}")
        return 1
    return 0
```

Empat hal yang dijamin oleh pola ini:

1. **Idempoten** — dijalankan berkali-kali hasilnya sama.
2. **Satu transaksi sungguhan** — `BEGIN IMMEDIATE` + `ROLLBACK` eksplisit,
   bukan `with conn:` (lihat catatan di kode: pola itu meninggalkan kolom
   setengah jadi). Diuji: 3 kolom ditambah, gagal di tengah → 0 kolom tertinggal,
   `user_version` tetap 0, 3 lap tetap utuh.
3. **Tidak ada data hilang** — hanya `ADD COLUMN`; DROP/RENAME dilarang tanpa
   persetujuan terpisah.
4. **Aplikasi lama tidak merusak data baru** — versi lama melihat `user_version`
   lebih tinggi dari dirinya lalu berhenti dengan pesan jelas, bukan menulis
   dengan skema yang salah.

### 11.3 Cadangan sebelum menyentuh apa pun

```
%APPDATA%\YurLaps\
  yurlaps.sqlite3
  yurlaps.sqlite3-wal          (WAL — ikut harus disalin)
  config.json
  backup\
    sebelum-0.4.0-2026-11-04T03-12-00\   ← dibuat otomatis SEBELUM migrasi
      yurlaps.sqlite3 · config.json · meta.txt
```

Aturan:
- Cadangan dibuat **hanya bila `user_version` akan berubah**.
- Disimpan **3 terbaru**; yang lebih lama dihapus. Satu sesi balapan
  ± 50–200 KB, jadi 3 cadangan masih jauh di bawah 5 MB.
- `meta.txt` berisi versi aplikasi, versi skema, waktu, jumlah sesi, jumlah lap.
- Cadangan **tidak** diunggah ke mana pun.

Kalau migrasi gagal: aplikasi **tidak** lanjut. Muncul dialog dengan dua pilihan
— *Coba lagi* atau *Pulihkan cadangan* — dan berkas aslinya tidak pernah
ditimpa di tengah jalan.

### 11.4 Tiga hal yang bisa hilang saat update, dan penangkalnya

| Risiko | Kenyataan hari ini | Perbaikan |
|---|---|---|
| **Sesi berjalan hilang** | Menutup aplikasi di tengah balapan = lap yang belum ditulis bisa hilang | Setiap sesi ditandai `ended_at IS NULL` saat mulai. Saat aplikasi dibuka lagi: "Ada sesi yang belum ditutup (Balapan #12, 47 lap). Lanjutkan atau tutup?" |
| **Data hilang saat migrasi** | Belum ada cadangan | 11.3 |
| **Token login hilang saat update** | Belum ada login | Token di Windows Credential Manager — **di luar** folder aplikasi dan folder data, jadi selamat dari update, uninstall, dan bahkan pindah versi |

### 11.5 Club/member tidak perlu disimpan permanen — cukup dilewati

Ini yang membuat pembaruan aplikasi tidak berisiko ke data club:

- **Lap mentah**: sumber kebenaran = aplikasi. Tidak pernah dihapus, tidak
  pernah ditimpa, tidak pernah diubah oleh update.
- **Club/member/transponder**: sumber kebenaran = website. Yang disimpan lokal
  hanya **tiruan** (cache) yang boleh kapan saja dibuang. Kalau bentuknya
  berubah di versi baru, cukup `DELETE FROM member_cache` lalu muat ulang dari
  server. **Tidak ada data pengguna yang hilang**, karena memang bukan miliknya.
- **Hasil yang sudah terkirim**: salinan tetap ada di aplikasi (`sync_state =
  'sent'` + `remote_id`). Menghapus salinan lokal pun tidak memengaruhi website.

### 11.6 Kanal pembaruan: dua jenis, pemisahan tegas

| | Pembaruan otomatis (silent) | Pembaruan dengan persetujuan |
|---|---|---|
| Kapan | hanya perbaikan celah keamanan | fitur baru, perubahan skema |
| Sifat | **tidak mengubah skema** | boleh mengubah skema (dengan cadangan) |
| Contoh | 0.4.1 → 0.4.2 | 0.4.x → 0.5.0 |

Aturannya: **nomor patch (x.y.Z) tidak boleh menyentuh skema.** Kalau sebuah
perbaikan butuh kolom baru, itu berarti versi minor dan harus lewat
persetujuan operator — supaya tidak ada perubahan data diam-diam saat sirkuit
sedang dipakai.

Saat ini aplikasi memperbarui lewat installer dengan `/UPDATEZIP` (yang lebih
dulu jalan **sebelum** data dibuka), sehingga urutannya selalu:
`backup → migrasi → aplikasi jalan`.

### 11.7 Mode portable: data di samping program

Kalau dijalankan dengan `YURLAPS_DATA=.` atau dari zip portable, folder data
berada **di samping** `YurLaps.exe`. Konsekuensinya harus disadari pengguna —
mengekstrak zip baru ke folder baru berarti data lama tidak terbawa:

```
Saat mulai di mode portable, tampilkan:
  "Data disimpan di folder ini (…\YurLaps\data). Kalau kamu mengekstrak
   versi baru ke folder lain, data ini tidak ikut. Pindahkan foldernya,
   atau pakai versi installer untuk data di %APPDATA%."
```

### 11.8 Yang harus dibuktikan di F0 (bukan sekadar ditulis)

| Uji | Kriteria lulus |
|---|---|
| Migrasi dari DB 0.3.0 | `user_version` 0 → 1, semua sesi/lap lama masih utuh, kolom baru kosong |
| Migrasi dijalankan dua kali | tidak ada galat, tidak ada perubahan |
| Mati di tengah migrasi | **0 kolom tertinggal**, `user_version` tetap 0, isi tabel utuh |
| Cadangan dibuat | isi cadangan bisa dibuka dan berisi jumlah sesi yang sama |
| Update 0.3.0 → 0.4.0 | sesi, nama pembalap, config, dan decoder baru semuanya benar |
| Uninstall | `%APPDATA%\YurLaps` masih utuh; install ulang memakai data lama |
| Sesi belum ditutup | setelah aplikasi dibuka lagi, sesi ditemukan dan bisa dilanjutkan |
| Mode portable | peringatan lokasi data muncul |

### 11.9 Yang sengaja TIDAK dilakukan

- Tidak ada penghapusan otomatis data lama untuk "menghemat ruang".
- Tidak ada migrasi yang menulis ulang tabel inti (hanya `ADD COLUMN`).
- Tidak ada pembaruan paksa tanpa persetujuan bila skema berubah.
- Tidak ada data race yang dikirim ke luar hanya karena update.
- Tidak ada cadangan yang diunggah ke cloud pihak ketiga.

---

## 12. Hasil uji pola migrasi (dijalankan nyata, bukan klaim)

Dijalankan di sandbox pada salinan skema 0.3.0 (2 sesi, 3 lap):

```
migrasi normal  : 2 kolom | user_version = 1
migrasi kedua   : 0 kolom (idempoten)
data utuh       : 2 sesi / 3 lap
gagal disengaja  : mati listrik di tengah migrasi
sesudah gagal   : kolom client_uid = False | user_version = 0 | lap = 3
```

Perhatikan baris keempat: **tidak ada kolom setengah jadi yang tertinggal**.
Itu bukan hasil otomatis — pola `with conn:` yang wajar dipakai orang
menghasilkan `True` pada pemeriksaan yang sama, dan baru `BEGIN IMMEDIATE` +
`ROLLBACK` eksplisit menghasilkan `False`. Karena itu bentuk kode di 11.2
ditulis begitu, bukan lebih sederhana.

---

## 13. Track sebagai pangkal data (diputuskan 2026-11-04)

Satu club bisa punya beberapa track (beberapa layout). Karena best lap 9.402
hanya berarti **di satu layout**, track bukan sekadar pilihan di pengaturan —
ia yang menentukan arti semua angka.

### 13.1 Aturan

| # | Aturan | Alasan |
|---|---|---|
| T1 | **Track dipilih di paling atas**, sebelum sesi dimulai | operator melihat konteks sebelum menekan Mulai |
| T2 | **Track terkunci selama sesi berjalan** — ditandai, bukan tombol mati | lap dua layout tidak boleh tercampur |
| T3 | **Setiap sesi menyimpan `track_id`** sejak dibuat | hasil bisa dikelompokkan tanpa menebak |
| T4 | **Rekor, leaderboard, dan riwayat dihitung per track** | membandingkan lintasan di layout berbeda = angka palsu |
| T5 | **Track dikelola di website** (`club_tracks`), aplikasi hanya memilih | satu sumber kebenaran |
| T6 | **Sesi tanpa track tidak bisa masuk leaderboard** — harus diklaim ke track dulu | mencegah lintasan "nyasar" |
| T7 | **Ganti club = ganti daftar track** | cache track ikut diganti, tidak digabung (P7) |
| T8 | **Preset boleh menyimpan track bawaan**, tetapi operator tetap bisa menggantinya | preset "Sprint Layout A" langsung siap |

### 13.2 Yang dihapus dari track

Tidak ada. Track tidak pernah dihapus dari aplikasi — hanya dari website, dan
aplikasi sekadar berhenti menampilkannya. Sesi lama yang memakai track yang
sudah dihapus **tetap menyimpan `track_id` dan `track_name`**, sehingga riwayat
lama tidak rusak dan masih bisa diunggah.

### 13.3 Tampilan

```
┌ TRACK ──────────────────────┬ PRESET ──────────────┬ 1.284 lap di track ini  ┐
│ Sirkuit Medan RC · Layout A │ Sprint 3 menit       │ rekor 9.402 · Budi      │
└─────────────────────────────┴──────────────────────┴─────────────────────────┘
```
- Bar track ada di **Quick Race** dan **Latihan**, di atas preset.
- Selama sesi berjalan muncul penanda `terkunci · sesi berjalan`.
- Menu **Member** juga menampilkan daftar track club + rekor masing-masing.
- Panel Sinkronisasi menampilkan track tiap sesi (`Layout A · 8 pembalap …`).

### 13.4 Yang harus dibuktikan di F0

| Uji | Kriteria lulus |
|---|---|
| Sesi disimpan dengan track | `sessions.track_id` terisi, bukan NULL |
| Sesi tanpa track | ditolak masuk leaderboard, muncul tombol "Klaim ke track" |
| Ganti track saat sesi jalan | ditolak dengan pesan, lap tidak tercampur |
| Dua track, rekor berbeda | rekor Layout A dan Layout B tidak saling menimpa |
| Track dihapus di website | sesi lama tetap utuh, track hilang dari pilihan |
| Ganti club | daftar track berganti seluruhnya |

### 13.5 Kecepatan (km/h) — dan kesalahan yang harus dihindari

```python
kmh = keliling_loop_m / (lap_ms / 1000) * 3.6
```

| Contoh | Hasil |
|---|---|
| 42 m ÷ 9,412 s | **16 km/h** |
| 58 m ÷ 12,118 s | **17 km/h** |

**Yang salah:** memakai `pass_duration` (≈0,09–0,10 s) sebagai pembagi.
42 m ÷ 0,112 s = **1.350 km/h** — angka mustahil. `pass_duration` adalah lama
mobil berada **di bawah antena**, bukan waktu menempuh satu loop. Nilai itu
tetap ditampilkan di menu Decoder sebagai informasi kualitas sinyal, tetapi
**tidak** dipakai untuk km/h.

### 13.6 Alur Quick Race yang berfungsi

```
[Peserta]  pilih dari member → transponder terisi otomatis (bisa diubah)
[Start]    hitung mundur 3/5/10 → GO → lap tercatat
[Finish]   modal "Simpan race?" → Simpan ke History / Buang sesi
[History]  daftar sesi + unggah + buka rincian per pembalap
```

Berkas: **`docs/quickrace-app.html`** — satu berkas, tanpa CDN, tanpa server.
Data contoh (member, track, preset) di dalam berkas; sesi tersimpan memakai
`localStorage` sehingga tetap ada setelah aplikasi ditutup.

Tangkapan layar: `ui-qr-1-countdown.png`, `ui-qr-2-live.png`,
`ui-qr-3-peserta.png`, `ui-qr-4-save.png`, `ui-qr-5-history.png`,
`ui-qr-6-track.png`, `ui-qr-7-detail-sesi.png`.

---

## 14. Aplikasi nyata — Quick Race full offline

Bagian ini menggantikan pemakaian `localStorage` pada mockup. Aplikasi
sungguhan menyimpan data club dan hasil race di **SQLite** lewat API lokal,
sehingga data tidak hilang saat peramban dibersihkan dan siap disinkronkan
begitu fitur daring dinyalakan.

### 14.1 Susunan berkas

| Berkas | Isi |
|---|---|
| `app/club.py` | `ClubStore` — SQLite club: club, track, member, preset, race, race_driver, race_lap. Skema versi 1, migrasi `BEGIN IMMEDIATE`. |
| `app/race.py` | `RaceEngine` — satu sesi Quick Race: peserta, hitung mundur, catat lintasan, klasemen, simpan ke riwayat. |
| `app/webapp.py` | Rute `/api/club/*`, `/api/track*`, `/api/preset*`, `/api/race/*`, halaman `/quickrace`. |
| `app/static/quickrace.html` + `quickrace.js` | UI aplikasi. Tidak memakai `localStorage`, tidak memanggil internet. |
| `tests/test_race_engine.py` | Uji mesin race & penyimpanan (26 pemeriksaan). |

Alamat data: `<folder data>/yurlaps_club.sqlite3` — **terpisah** dari
`yurlaps.sqlite3` milik mesin timing lama, supaya keduanya tidak saling
merusak.

### 14.2 Rute API (semua lokal, tanpa jaringan)

```
GET  /api/club                 GET  /api/club/bootstrap
GET  /api/tracks               GET  /api/members
GET  /api/presets              GET  /api/races[?track_id=]
GET  /api/races/<id>           GET  /api/race/state
POST /api/race/track           POST /api/race/cfg
POST /api/race/peserta         POST /api/race/start
POST /api/race/go              POST /api/race/pass
POST /api/race/finish          POST /api/race/reset
POST /api/race/save            POST /api/race/discard
POST /api/track                POST /api/track/delete
POST /api/preset               POST /api/preset/delete
POST /api/race/upload          → 409 "belum ada koneksi" (jujur, bukan palsu)
POST /api/tracks/sync          → 409 "belum ada koneksi"
```

`/api/health` tetap bebas token; rute lain memakai aturan token yang sudah ada
(`?t=<token>`, header, atau cookie sesi).

### 14.3 Alur yang sudah diuji ujung-ke-ujung

```
buka /quickrace → pilih track & peserta → Start → hitung mundur → GO
→ lap tercatat (decoder / tombol uji) → Finish → simpan (nama otomatis)
→ History Race → buka rincian per pembalap → tutup aplikasi → buka lagi
```

Hasil uji Playwright (14 langkah, seluruhnya lulus):

```
1 muat: club=Medan RC track=t-deli-oval
2 member tampil 5
3 peserta tersimpan 5
4 countdown tampil 5
5 status setelah countdown: LIVE
6 papan 5 baris · log 9 lintasan
7 modal simpan: 5 pembalap · 9 lap
8 history: 1 sesi
9 rincian: 5 baris per pembalap
10 unggah → ditolak jujur (409, belum ada koneksi)
11 track baru lebar 48 m → 18 km/h
12 reload: sesi & track tetap ada (SQLite)
13 localStorage dipakai? 0 kunci
14 permintaan ke luar localhost: 0
```

### 14.4 Aturan yang dijaga otomatis

`python3 -S tools/verifikasi_file.py` (84 pemeriksaan) kini juga menolak:

- `localStorage.setItem` / `sessionStorage` di `quickrace.js`
  (data club wajib di SQLite),
- alamat `http(s)://` selain localhost,
- hilangnya pesan jujur "belum ada koneksi ke website",
- hilangnya `BEGIN IMMEDIATE` pada migrasi club,
- hilangnya `paksa_lap_ms` (jalur tombol uji) atau `class RaceEngine`.

### 14.5 Kesalahan yang sudah pernah terjadi (jangan diulang)

| Gejala | Sebab | Perbaikan |
|---|---|---|
| Peserta & lap hilang tiap klik | `ClubStore`/`RaceEngine` dibuat per handler HTTP | disimpan di `AppContext` |
| Aplikasi menggantung total | `RLock` kurang: properti `race` memanggil `club` yang mengunci lock sama | `threading.RLock()` |
| Start tidak berjalan (404) | `fetch` tanpa badan memakai GET | daftar `RUTE_POST` di `quickrace.js` |
| Lap 0,2 detik, 745 km/h | klik uji beruntun memakai jam komputer | `paksa_lap_ms` khusus tombol uji |
| Sesi tak pernah selesai otomatis | peserta tanpa transponder ikut dihitung | hanya peserta bertransponder |
| Member bertanda "bukan member" | `luar` diisi `not tps.length` | `luar` hanya bila bukan member club |

### 14.6 Tangkapan layar aplikasi nyata

`app-qr-1-siap.png`, `app-qr-2-peserta.png`, `app-qr-3-countdown.png`,
`app-qr-4-live.png`, `app-qr-5-simpan.png`, `app-qr-6-history.png`,
`app-qr-7-detail.png`, `app-qr-8-track.png`, `app-qr-9-member.png`,
`app-qr-10-decoder.png`.

### 14.7 Yang sengaja BELUM dikerjakan (menunggu tahap daring)

- login akun & pemilihan club aktif,
- unggah hasil ke `yurlaps.dpdns.org` (antrean sudah ada: `race.sync='belum'`),
- sinkron track ke website,
- penarikan member dari server (sekarang data club dari SQLite lokal),
- decoder sungguhan mengisi lap (tombol "Lintasan (simulasi)" hanya untuk uji UI).

### 14.8 Audit tampilan (2026-11-05) — yang diperbaiki

Pemeriksaan ulang setiap layar menemukan delapan hal yang membuat aplikasi
terlihat belum jadi. Semuanya sudah diperbaiki dan dikunci penjaga verifikasi.

| Temuan | Akibatnya | Perbaikan |
|---|---|---|
| "0 sesi di track ini" padahal rekor ada | angka tidak dipercaya pengguna | `ClubStore.tracks()` menghitung `n_race` di SQL |
| Menu Decoder: endpoint `tcp://*:5556`, gain `1.41`, status selalu "aktif" | nilai tulisan tetap, menyesatkan | dibaca dari `/api/state` (gain `20.00`, endpoint `127.0.0.1:5556`, status "menunggu decoder") |
| Tombol Stop / Mulai ulang hanya memunculkan toast | tombol berbohong | memanggil `/api/decoder/restart` sungguhan |
| Panel akun: nama "Budi" padahal belum ada login; avatar "RS" dari nama club | data palsu | nama club sungguhan + "lokal · belum masuk" |
| Baris passing log memakai kelas `.pline` (tidak ada di CSS) | teks berdesakan, RSSI `-52.0` tanpa satuan | memakai `.prow`/`.skip` + satuan dB + label "luar" |
| `#saveTrack` melipat "Sirkuit Medan RC · Layout A" jadi dua baris | modal terlihat berantakan | satu baris + ellipsis (`title` lengkap) |
| Teks "SIAP" saat aplikasi dibuka ulang di tengah balapan | sesi seolah berhenti | status & timer dipulihkan, `mulaiTick()` jalan lagi |
| Kotak agenda Decoder kosong tanpa keterangan | terlihat rusak | baris "Belum ada lintasan." + catatan ambang |

Tambahan kecil: kalimat panduan berubah sesuai keadaan
("Siap. Tekan Start untuk hitung mundur 5 detik."), peserta tanpa transponder
ditulis "transponder belum dipetakan" (bukan "TP —"), dan pil atas memakai
angka antrean yang benar (`DATA.ringkasan`, bukan `DATA.club.ringkasan`).

Tangkapan layar tambahan: `app-qr-11-log-luar.png` (transponder di luar peserta).

### 14.9 Mengatur gain penerima

Gain = penguatan tuner (dB, 0–49,6), bukan pengaturan balapan. Ada di menu
**Decoder**, kartu "Pengaturan penerima".

Cara pakai:

1. Buka **Decoder** → geser slider **Gain tuner**.
2. Angka berubah saat digeser; nilai **baru dikirim saat slider dilepas**,
   disertai konfirmasi karena decoder akan dijalankan ulang.
3. Status di kanan atas kartu menyatakan hasilnya:
   - `disimpan · decoder dijalankan ulang` — YurLaps mengelola decoder,
   - `disimpan · decoder tidak sedang dikelola YurLaps` — nilai disimpan,
     decoder dijalankan sendiri oleh pengguna,
   - `tidak diubah saat sesi berjalan`, `gagal disimpan`, `tidak berubah`.

Jalur teknis: UI → `POST /api/decoder/gain {gain}` → `apply_gain()` membatasi
0–49,6 dB → `cfg.save()` → `restart_later()` (jeda 1,2 s, beberapa perubahan
cepat = satu restart) → argumen `-g <nilai>` disusun oleh
`Config.effective_decoder_args()`.

Aturan:

- **Tidak bisa diubah saat sesi berjalan** (UI menolak + mengembalikan angka
  ke nilai tersimpan). Alasannya: decoder dijalankan ulang dapat memutus
  pencatatan lap yang sedang berlangsung.
- Gain terlalu tinggi membuat penerima jenuh (RSSI mentok, lintasan hilang);
  terlalu rendah membuat mobil terlewat. Ini sifat perangkat, bukan bug.
- Tanpa dongle terpasang, nilai tetap tersimpan dan dipakai saat decoder
  dijalankan — UI menyebut terus terang "menunggu decoder".

Tangkapan layar: `app-qr-14-gain.png`.
