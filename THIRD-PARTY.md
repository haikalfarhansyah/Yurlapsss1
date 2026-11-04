# Komponen pihak ketiga & lisensi

YurLaps sendiri berlisensi **MIT** (lihat `LICENSE`). YurLaps **tidak**
mengandung kode sumber pihak ketiga dan **tidak memodifikasi** program pihak
ketiga. Yang dipakai ada dua, dan keduanya berjalan sebagai **proses terpisah**
atau **alat bantu terpisah**:

| Komponen | Pemilik | Lisensi | Cara YurLaps memakainya |
|---|---|---|---|
| **OpenStint** (`openstint_rtlsdr.exe` / `openstint_hackrf.exe`) | Zsolt Sellera — https://github.com/zsellera/openstint | GPL-3.0 | Dijalankan sebagai proses terpisah; YurLaps membaca pesan ZeroMQ yang memang didokumentasikan untuk integrasi pihak ketiga (`docs/decoder-protocol.md`). |
| **Zadig / libwdi** (`zadig.exe`, `wdi-simple.exe`) | Pete Batard — https://github.com/pbatard/libwdi | GPL-3.0 | Dipanggil sebagai alat untuk memasang driver WinUSB pada dongle RTL-SDR. |

## Kenapa YurLaps tidak "terkena" GPL

GPL-3.0 berlaku untuk *program* dan *karya turunannya*. YurLaps:

1. **tidak menyertakan kode** OpenStint/libwdi di dalam kode sumbernya;
2. **tidak menautkan (link)** pustaka mereka — komunikasi hanya lewat socket
   ZeroMQ pada `tcp://127.0.0.1:5556`, antarmuka yang didokumentasikan publik
   justru untuk dipakai aplikasi lain;
3. **tidak memodifikasi** biner mereka.

Pola ini (program terpisah yang saling bicara lewat protokol terdokumentasi)
umumnya dipandang sebagai *aggregation*, bukan karya turunan. Karena itu
YurLaps boleh memakai lisensi MIT.

## Kalau kamu mendistribusikan biner pihak ketiga

Unduhan otomatis (default YurLaps) membuat **pengguna mengambil sendiri** dari
rilis resmi GitHub — cara paling bersih. Tapi kalau kamu **membundel**
`openstint_*.exe` atau `zadig.exe` ke dalam installer lalu menyebarkannya,
GPL-3.0 mewajibkan kamu untuk:

- menyertakan **teks lisensi GPL-3.0** untuk komponen tersebut;
- menyertakan **URL sumber** (atau sumber lengkap) versi yang kamu sebarkan;
- **menandai** kalau ada perubahan (YurLaps tidak mengubah apa pun);
- tidak melarang penerima menyebarkan ulang komponen itu.

Berkas `vendor/openstint/ATTRIBUTION.txt` dan `vendor/win/LICENSE-libwdi.txt`
dibuat otomatis oleh `tools/fetch_vendor.py` untuk keperluan ini. Jangan hapus
keduanya dari paket distribusi.

## Kredit

Terima kasih kepada Zsolt Sellera (OpenStint & LapBeeps) dan Pete Batard
(libwdi/Zadig). Proyek YurLaps tidak berafiliasi dengan mereka dan tidak
dimaksudkan sebagai pengganti atau tiruan produk mereka.
