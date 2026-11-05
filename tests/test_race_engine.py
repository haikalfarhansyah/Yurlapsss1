"""Uji mesin Quick Race offline (app/race.py + app/club.py).

Fokus pada kesalahan yang mudah lolos kalau hanya dicoba lewat tampilan:
- hitungan waktu lap dari selisih lintasan, bukan jam dinding,
- km/h dari lebar loop ÷ waktu lap (bukan pass_duration),
- peserta ganda tidak boleh muncul dua kali,
- "bukan member" hanya untuk yang tidak ada di club,
- sesi tersimpan bolak-balik tanpa kehilangan lap,
- penyimpanan bertahan setelah dibuka ulang (tanpa jaringan).
"""
from __future__ import annotations

import pathlib
import json
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.club import ClubStore, CONTOH_MEMBER, CONTOH_TRACK  # noqa: E402
from app.race import RaceEngine, kmh    # noqa: E402


def main() -> int:
    gagal = 0

    def cek(nama: str, syarat: bool, info: str = "") -> None:
        nonlocal gagal
        if syarat:
            print(f"  ok   {nama}{(' — ' + info) if info else ''}")
        else:
            print(f"  GAGAL {nama}{(' — ' + info) if info else ''}")
            gagal += 1

    folder = pathlib.Path(tempfile.mkdtemp())
    db = folder / "yurlaps_club.sqlite3"
    store = ClubStore(db)
    cek("club baru tidak berisi data contoh", not store.club()["name"])
    cek("track baru kosong", not store.tracks())
    cek("member baru kosong", not store.members())

    # Fixture hanya untuk pengujian, bukan data awal pada perangkat pengguna.
    store.set_club(club_id="club-medan", name="Medan RC", role="leader")
    for tid, name, addr, width, note, best, holder, synced in CONTOH_TRACK:
        store._conn.execute(
            "INSERT INTO track (id, name, addr, width_m, note, best_ms, holder, synced, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (tid, name, addr, width, note, best, holder, synced, time.time()))
    for mid, name, nick, kelas, mobil, role, status, tps in CONTOH_MEMBER:
        store._conn.execute(
            "INSERT INTO member (id, name, nick, kelas, mobil, role, status, transponder, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, name, nick, kelas, mobil, role, status, json.dumps(tps), time.time()))
    store._conn.commit()
    race = RaceEngine(store)

    # -- data awal ---------------------------------------------------------
    cek("club contoh tersedia", store.club()["name"] == "Medan RC")
    cek("track contoh 3", len(store.tracks()) == 3, f"{len(store.tracks())}")
    cek("member contoh 5", len(store.members()) == 5)

    # -- km/h --------------------------------------------------------------
    cek("km/h 42 m / 9,412 s = 16", kmh(42, 9412) == 16, str(kmh(42, 9412)))
    cek("km/h tanpa lebar loop kosong", kmh(None, 9412) is None)
    cek("km/h tidak memakai pass_duration", kmh(42, 112) != 16)

    # -- peserta -----------------------------------------------------------
    race.set_track("t-medan-a")
    race.set_peserta([
        {"member_id": "u-budi"}, {"member_id": "u-andi"}, {"member_id": "u-sari"},
    ])
    n_member = len({p["member_id"] for p in race.peserta})
    cek("tiga peserta, tidak ada yang ganda", len(race.peserta) == 3 and n_member == 3)
    sari = next(p for p in race.peserta if p["member_id"] == "u-sari")
    cek("member tanpa transponder bukan 'bukan member'", sari["luar"] is False)
    cek("member tanpa transponder -> transponder kosong", sari["transponder"] is None)

    # -- jalankan sesi -----------------------------------------------------
    race.set_cfg({"nama": "Uji Sprint", "target": "laps", "value": 3,
                  "rank": "lapsTime", "cd": 3, "sound": 0})
    race.mulai()
    race.go()
    cek("status live setelah GO", race.status == "live")

    transponder = [p["transponder"] for p in race.peserta if p["transponder"]]
    t0 = time.monotonic()
    for lap in range(1, 4):
        for tp in transponder:
            race.catat(tp, rssi=-55.0, dur_us=98000, waktu_mono=t0 + lap * 9.4)
    cek("sesi selesai otomatis saat target lap tercapai", race.status == "done")
    cek("lap tercatat 3 per pembalap",
        all(len(p["laps"]) == 3 for p in race.peserta if p["transponder"]))

    kal = race.klasemen()
    cek("klasemen berisi 3 pembalap", len(kal) == 3)
    cek("best lap dari selisih lintasan (9.399-9.401 ms)",
        9399 <= kal[0]["best_ms"] <= 9401, f"{kal[0]['best_ms']} ms")
    cek("km/h wajar (<= 40)", kal[0]["kmh"] is not None and kal[0]["kmh"] <= 40,
        f"{kal[0]['kmh']} km/h")

    # -- simpan -> riwayat -------------------------------------------------
    info = race.simpan()
    cek("sesi tersimpan", bool(info["id"]), info["nama"])
    daftar = store.races()
    cek("riwayat berisi 1 sesi", len(daftar) == 1)
    # Sari tidak punya transponder, jadi hanya 2 pembalap x 3 lap yang tercatat
    cek("jumlah lap tersimpan benar (2 pembalap x 3)", daftar[0]["n_lap"] == 6,
        str(daftar[0]["n_lap"]))
    cek("status sinkron 'belum' (offline)", daftar[0]["sync"] == "belum")

    detail = store.race(info["id"])
    lap_budi = next(d["laps"] for d in detail["drivers"] if d["nick"] == "Budi")
    cek("lap per pembalap utuh setelah dibaca ulang", len(lap_budi) == 3, str(lap_budi))
    cek("cfg tersimpan sebagai objek", isinstance(detail["cfg"], dict))

    # -- rekor track -------------------------------------------------------
    track = store.track("t-medan-a")
    cek("rekor track diperbarui", track["best_ms"] is not None, str(track["best_ms"]))

    # -- penyimpanan bertahan setelah dibuka ulang -------------------------
    store2 = ClubStore(db)
    cek("data bertahan setelah aplikasi dibuka ulang",
        len(store2.races()) == 1 and len(store2.tracks()) == 3,
        f"{len(store2.races())} sesi · {len(store2.tracks())} track")

    # -- track tidak boleh habis -------------------------------------------
    for t in store2.tracks()[1:]:
        store2.delete_track(t["id"])
    try:
        store2.delete_track(store2.tracks()[0]["id"])
        cek("track terakhir tidak boleh dihapus", False)
    except ValueError:
        cek("track terakhir tidak boleh dihapus", True)

    # -- idempoten: simpan ulang id yang sama tidak menggandakan lap --------
    store.simpan_race({"id": info["id"], "name": "Uji Sprint (ulang)",
                       "track_id": "t-medan-a", "track_name": "Sirkuit Medan RC · Layout A",
                       "track_width": 42, "cfg": {}, "started_at": 1, "ended_at": 2,
                       "drivers": [{"pos": 1, "name": "Budi Santoso", "nick": "Budi",
                                    "transponder": 1615544, "best_ms": 9400,
                                    "laps": [9400, 9500]}]})
    cek("simpan ulang tidak menggandakan sesi", len(store.races()) == 1)
    cek("lap sesi lama diganti, bukan ditumpuk",
        store.races()[0]["n_lap"] == 2, str(store.races()[0]["n_lap"]))

    print()
    if gagal:
        print(f"{gagal} pemeriksaan GAGAL")
        return 1
    print("Semua uji mesin race lulus [OK]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
