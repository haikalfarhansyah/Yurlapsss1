# Panduan: dari folder ini → `YurLaps.exe` + installer (gratis, tanpa install apa pun)

Waktu: ±10 menit, sebagian besar menunggu GitHub. Tidak perlu Python, tidak perlu
Windows SDK — build-nya dikerjakan komputer GitHub, hasilnya kamu unduh.

---

## Langkah 0 — siapkan akun & Git

1. Buat akun GitHub (gratis): <https://github.com/signup>
2. Install **GitHub Desktop**: <https://desktop.github.com/> → login dengan akun tadi.
   *(Alternatif tanpa install: unggah manual lewat web di Langkah 2b.)*

---

## Langkah 1 — buat repository kosong

1. Buka <https://github.com/new>
2. **Repository name**: `stintlab`
3. Pilih **Private** (kode tetap milikmu, Actions tetap gratis)
4. Jangan centang apa pun (tanpa README / .gitignore) → **Create repository**

---

## Langkah 2a — unggah dengan GitHub Desktop (disarankan)

1. Ekstrak dulu `stintlab-source.zip` ke folder biasa, mis.
   `D:\proyek\stintlab`
2. GitHub Desktop → **File → Add local repository…** → pilih folder `stintlab`
3. Muncul tulisan *"this directory does not appear to be a Git repository"* →
   klik **create a repository** → **Create repository**
4. Klik **Publish repository** → pastikan **Keep this code private** tercentang →
   **Publish repository**

## Langkah 2b — unggah manual lewat web (kalau tidak mau install apa pun)

1. Ekstrak `stintlab-source.zip`
2. Di halaman repo yang baru dibuat → **uploading an existing file**
3. Seret **isi** folder `stintlab` (bukan foldernya) ke browser, termasuk folder
   tersembunyi `.github` — kalau `.github` tidak bisa diseret, lihat catatan di bawah
4. **Commit changes**

> Folder `.github/workflows/build-windows.yml` adalah "resep" build. Kalau folder
> tersembunyi tidak ikut terunggah, buat manual: **Add file → Create new file**,
> ketik nama `.github/workflows/build-windows.yml`, lalu salin isi file itu dari
> folder `stintlab/.github/workflows/`.

---

## Langkah 3 — jalankan build

1. Buka repo → tab **Actions**
2. Kalau ada banner *"Workflows aren't being run on this repository"* →
   klik **I understand my workflows, go ahead and enable them**
3. Di panel kiri pilih **Build Windows EXE** → tombol **Run workflow** (kanan) →
   **Run workflow**
4. Tunggu ±2–4 menit. Status berubah dari kuning (●) ke centang hijau (✓).
   Kalau merah, klik run-nya → job `build` → buka langkah yang merah dan kirim
   isi log-nya ke saya; saya perbaiki.

---

## Langkah 4 — unduh & pakai

1. Klik run yang sudah hijau → scroll ke bawah ke **Artifacts** →
   unduh **YurLaps-windows**
2. Isinya tiga file:
   - **`YurLaps-Setup-0.2.0.exe`** → installer (paling nyaman: shortcut,
     uninstall, opsi pasang driver RTL-SDR)
   - **`YurLaps-portable.zip`** → versi portable, ekstrak lalu jalankan
     `YurLaps.exe` (tanpa instalasi)
   - **`update.json`** → manifest untuk fitur pembaruan otomatis
3. Jalankan salah satu (installer atau portable) → jendela aplikasi terbuka.

Kalau Windows SmartScreen muncul (*"Windows protected your PC"*): klik
**More info → Run anyway**. Itu normal untuk .exe tanpa sertifikat digital.

**Yang perlu diketahui soal .exe ini:**

- Ini **one-folder app**: `YurLaps.exe` butuh folder di sebelahnya
  (`_internal`). Jangan pindahkan .exe sendirian — bagikan/zip
  seluruh foldernya.
- Taruh di lokasi tetap, mis. `D:\YurLaps\`. Kalau ingin data tersimpan di
  folder itu (portable, bisa dibawa di flashdisk), buat file kosong bernama
  `portable.txt` di sebelah `YurLaps.exe`.
- Windows 10/11 sudah punya WebView2 (dipakai untuk jendela aplikasi). Kalau
  jendela gagal muncul, aplikasi otomatis membuka UI di browser default.
- Firewall: saat pertama jalan Windows bertanya → **Allow** untuk jaringan
  privat, supaya HP/TV di LAN bisa membuka `http://IP-KOMPUTER:8080`.
- Installer butuh **izin Administrator** (UAC satu kali) karena menawarkan
  pemasangan driver SDR. Versi portable tidak butuh admin kecuali kamu menekan
  **Perangkat → Pasang driver**.

---

## Langkah 5 — colok dongle, sisanya otomatis

1. Colok dongle RTL-SDR.
2. Buka **Perangkat** di YurLaps:
   - kalau tertulis "perlu WinUSB" → klik **Pasang driver** (setujui UAC),
     lalu **Ulangi pemeriksaan**;
   - klik **Uji dongle** untuk memastikan decoder benar-benar bisa membuka
     dongle (output asli decoder ditampilkan).
3. Decoder OpenStint dijalankan otomatis di latar belakang. Kalau belum ada
   biner-nya, YurLaps mengunduh rilis resmi Windows dari GitHub sekali saja.
4. Mobil lewat → kartu pembalap muncul → klik kartu → isi nama/kelas/mobil.

Decoder di komputer lain (mis. Raspberry Pi)? Buka halaman **Decoder → Koneksi**,
matikan *YurLaps menjalankan decoder*, lalu isi **Endpoint ZeroMQ**
(`tcp://192.168.1.50:5556`).

Tidak ada mode demo: kalau dongle belum terpasang, halaman Practice akan kosong
dan halaman Decoder menunjukkan apa yang kurang. Jadi angka yang muncul di layar
selalu berasal dari lintasan sungguhan.

---

## Langkah 6 — memasang pembaruan nanti

Kalau nanti aplikasi diubah (versi baru):

1. Build ulang (push ke GitHub atau **Run workflow**).
2. Unggah `YurLaps-portable.zip` + `update.json` ke hosting milikmu
   (mis. `https://hasil.klubku.id/unduh/`), sesuaikan URL di `update.json`.
3. Di komputer pengguna: **Setelan → Pembaruan → URL manifest** diisi sekali.
   Setelah itu, saat ada versi baru muncul tombol **Unduh → Restart & pasang**
   di panel **Perangkat**. Data sesi & pembalap tidak terhapus.

Catatan: `update.json` yang dihasilkan workflow memakai `--base-url`
`https://example.com/unduh` sebagai contoh — ubah URL-nya (atau jalankan
`python tools/buat_update_manifest.py --dir dist --version 0.3.0 --base-url https://…`)
sebelum diunggah.

---

## Ringkasan alur

| # | Aksi | Hasil |
|---|---|---|
| 1 | Buat repo `stintlab` (private) | wadah kode |
| 2 | Publish folder `stintlab` | kode + resep build terunggah |
| 3 | Actions → Build Windows EXE → Run workflow | build di server GitHub |
| 4 | Unduh artifact `YurLaps-windows` | `YurLaps.exe` siap pakai |

---

## Kalau ingin build ulang setelah mengubah kode

Setiap **push** ke branch `main` otomatis memicu build lagi; artifact baru
muncul di tab Actions. Atau **Run workflow** manual kapan saja.

---

## Alternatif: build di komputermu sendiri

Kalau kamu punya Python di Windows:

1. Install Python 3.12 dari <https://www.python.org/downloads/windows/>
   (**centang "Add python.exe to PATH"**)
2. Klik dua kali `build_exe.bat`
3. Hasil: `dist\YurLaps\YurLaps.exe`

Hasil build lokal dan build GitHub identik isinya.

## 8. Kalau kompilasi installer gagal (`Compiling [Code] section`)

Pesan dari ISCC sering terpotong di log CI. Tiga penyebab yang pernah terjadi di
proyek ini, semuanya bisa dicek lebih dulu tanpa Windows:

```powershell
python tools/verifikasi_file.py     # memeriksa installer/stintlab.iss
```

Yang diperiksa:

| Gejala di log ISCC | Penyebab | Aturan |
|---|---|---|
| `column 53: Syntax error` | komentar `{ ... }` yang isinya kurung kurawal, mis. `{param:X}` — komentar Pascal berakhir di `}` pertama | di `[Code]` hanya pakai komentar `//` |
| `Unknown identifier 'ewWaitUntilTerminate'` | salah tulis | namanya `ewWaitUntilTerminated` |
| `Unknown identifier 'UseZadig'` | fungsi dipanggil sebelum dideklarasikan | deklarasikan fungsi di atas pemakainya |
| `Mismatched or misplaced quotes` | `""` di dalam nilai `Parameters:` pada `[Run]` | hindari kutip bersarang (nama perangkat dibuat satu kata) |

Mau mencoba mengompilasi di luar Windows? ISCC jalan di Wine:

```bash
WINEPREFIX=~/.wineinno32 WINEARCH=win32 wineboot -i      # sekali saja
wine innosetup-6.7.4.exe /VERYSILENT /NORESTART /SP-     # pasang Inno Setup
wine "$WINEPREFIX/drive_c/Program Files/Inno Setup 6/ISCC.exe" installer/stintlab.iss
```
