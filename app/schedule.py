"""Jadwal event: pembagian grup, seeding antar ronde, urutan, dan jam mulai.

Modul ini murni (tanpa I/O) supaya aturan jadwal bisa diuji terpisah dari
penyimpanan. LapBeeps memakai istilah ladder/round/session; padanannya di
YurLaps adalah plan (rencana), stage (sesi), dan class (kelas event).
"""
from __future__ import annotations

import uuid

from .race_config import normalize

KINDS = ("practice", "qualifying", "heat", "main", "final")
BASES = ("roster", "position", "result", "best", "top3", "consecutive")
SCOPES = ("front", "even")
MODES = ("forward", "reverse")
SCHEMES = ("sequential", "rounds")

KIND_LABEL = {"practice": "Practice", "qualifying": "Kualifikasi", "heat": "Heat",
              "main": "Main", "final": "Final"}
BASIS_LABEL = {"roster": "urutan peserta", "position": "posisi finish",
               "result": "total waktu", "best": "best lap",
               "top3": "rata-rata 3 lap tercepat",
               "consecutive": "3 lap berurutan tercepat"}
RANK_FIELD = {"position": "pos", "result": "total_ms", "best": "best_ms",
              "top3": "top3_ms", "consecutive": "consecutive_ms"}

DEFAULT_MAX_GROUP = 8
DEFAULT_GAP_MS = 300000
DEFAULT_PLAN = {"kind": "qualifying", "max_group_size": DEFAULT_MAX_GROUP,
                "bump_up": 0, "source_kind": "roster",
                "seeding": {"basis": "roster", "scope": "front", "mode": "forward",
                            "rounds": 1, "drop": 0},
                "duration_ms": 0, "estimate_lap_ms": 0, "gap_ms": DEFAULT_GAP_MS,
                "order": 1, "cfg": {}}


def _id() -> str:
    return str(uuid.uuid4())


def _integer(value, label, low, high, default):
    if value in (None, ""):
        return default
    try:
        number = float(value)
        integer = int(number)
    except (TypeError, ValueError):
        raise ValueError(f"{label} harus bilangan bulat")
    if number != integer or not low <= integer <= high:
        raise ValueError(f"{label} harus bilangan bulat {low} sampai {high}")
    return integer


def _text(value, name, limit=80):
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name} wajib diisi")
    if len(value) > limit:
        raise ValueError(f"{name} maksimal {limit} karakter")
    return value


def normalize_plan(data, base=None) -> dict:
    """Validasi dan lengkapi satu rencana (plan) sesi."""
    result = dict(DEFAULT_PLAN)
    result["seeding"] = dict(DEFAULT_PLAN["seeding"])
    if base:
        for key, value in base.items():
            if value is not None:
                result[key] = value
        result["seeding"] = dict(base.get("seeding") or result["seeding"])
    incoming = dict(data or {})
    seeding = dict(result["seeding"])
    seeding.update({k: v for k, v in (incoming.pop("seeding", None) or {}).items() if v is not None})
    for key, value in incoming.items():
        if value is not None:
            result[key] = value
    result["seeding"] = seeding

    kind = str(result.get("kind") or "qualifying")
    if kind not in KINDS:
        raise ValueError("Jenis sesi tidak dikenal")
    result["kind"] = kind
    result["class_id"] = str(result.get("class_id") or "")
    result["id"] = str(result.get("id") or _id())
    result["order"] = _integer(result.get("order"), "Urutan ronde", 1, 99, 1)
    result["max_group_size"] = _integer(result.get("max_group_size"), "Jumlah peserta per grup",
                                        2, 24, DEFAULT_MAX_GROUP)
    result["bump_up"] = _integer(result.get("bump_up"), "Bump-up", 0, 6, 0)
    source = str(result.get("source_kind") or "roster")
    if source != "roster" and source not in KINDS:
        raise ValueError("Sumber seeding tidak dikenal")
    result["source_kind"] = source
    basis = str(seeding.get("basis") or "roster")
    if basis not in BASES:
        raise ValueError("Dasar seeding tidak dikenal")
    scope = str(seeding.get("scope") or "front")
    if scope not in SCOPES:
        raise ValueError("Pembagian grup tidak dikenal")
    mode = str(seeding.get("mode") or "forward")
    if mode not in MODES:
        raise ValueError("Arah seeding tidak dikenal")
    seeding["basis"] = basis
    seeding["scope"] = scope
    seeding["mode"] = mode
    seeding["rounds"] = _integer(seeding.get("rounds"), "Jumlah ronde yang dinilai", 1, 10, 1)
    seeding["drop"] = _integer(seeding.get("drop"), "Jumlah peserta yang dibuang", 0, 99, 0)
    result["seeding"] = seeding
    result["duration_ms"] = _integer(result.get("duration_ms"), "Durasi sesi (ms)",
                                     0, 24 * 3600 * 1000, 0)
    result["estimate_lap_ms"] = _integer(result.get("estimate_lap_ms"), "Estimasi waktu lap (ms)",
                                         0, 600000, 0)
    result["gap_ms"] = _integer(result.get("gap_ms"), "Jeda antar sesi (ms)",
                                0, 4 * 3600 * 1000, DEFAULT_GAP_MS)
    result["name"] = _text(result.get("name") or f"{KIND_LABEL[kind]} {result['order']}",
                           "Nama rencana")
    result["cfg"] = normalize(result.get("cfg") or {})
    return result


def normalize_scheme(data) -> dict:
    """Skema urutan jadwal: satu per satu (sequential) atau per ronde (rounds)."""
    data = dict(data or {})
    kind = str(data.get("type") or "sequential")
    if kind not in SCHEMES:
        raise ValueError("Skema jadwal tidak dikenal")
    return {"type": kind, "finals_by_group": bool(data.get("finals_by_group"))}


def merge_rows(sessions, basis) -> list:
    """Gabungkan beberapa sesi hasil menjadi satu peringkat (terbaik per peserta)."""
    field = RANK_FIELD.get(basis, "total_ms")
    best: dict[str, dict] = {}
    for rows in sessions or []:
        for row in rows or []:
            key = str(row.get("transponder"))
            value = row.get(field)
            current = best.get(key)
            if current is None or (value is not None and
                                   (current.get(field) is None or value < current.get(field))):
                best[key] = dict(row)
    return list(best.values())


def _rank_rows(rows, basis) -> list:
    field = RANK_FIELD.get(basis, "total_ms")

    def key(row):
        value = row.get(field)
        return (value is None, value if value is not None else 0)

    return sorted(rows or [], key=key)


def seed_order(entries, source_rows, plan) -> list:
    """Urutkan peserta sesuai rencana seeding.

    Urutan dasar: terbaik lebih dulu. Participating terlemah dibuang
    (``drop``), baru uriutannya dibalik bila mode ``reverse``.
    """
    seeding = plan.get("seeding") or {}
    basis = seeding.get("basis", "roster")
    ordered = list(entries)
    if basis != "roster" and source_rows:
        rank = {str(row.get("transponder")): index
                for index, row in enumerate(_rank_rows(source_rows, basis))}
        ordered = sorted(ordered, key=lambda entry: (
            rank.get(str(entry.get("transponder")), 10 ** 6),
            str(entry.get("name") or "").casefold()))
    drop = int(seeding.get("drop") or 0)
    if drop > 0 and len(ordered) > drop:
        ordered = ordered[:len(ordered) - drop]
    if seeding.get("mode") == "reverse":
        ordered = list(reversed(ordered))
    return ordered


def group_label(index, count) -> str:
    if count <= 1:
        return "Semua"
    if index < 26:
        return "Grup " + chr(ord("A") + index)
    return f"Grup {index + 1}"


def build_groups(ordered, max_group_size, bump_up=0, scope="front") -> list:
    """Bagi peserta terurut menjadi grup.

    ``scope='front'`` mengisi grup dari depan (pembalap tercepat di grup A),
    ``scope='even'`` membagi rata agar setiap grup punya pembalap cepat dan
    lambat. ``bump_up`` adalah jumlah maksimum peserta di grup terakhir yang
    boleh "naik" ke grup sebelumnya: grup dengan peserta paling sedikit tidak
    perlu dijalankan sendiri, pembalapnya bergabung ke grup di atasnya.
    """
    total = len(ordered)
    if total == 0:
        return []
    size = max(2, int(max_group_size or DEFAULT_MAX_GROUP))
    count = max(1, -(-total // size))
    groups: list[list] = [[] for _ in range(count)]
    if scope == "even":
        for index, entry in enumerate(ordered):
            groups[index % count].append(entry)
    else:
        for index, entry in enumerate(ordered):
            groups[min(index // size, count - 1)].append(entry)
    if bump_up > 0:
        for index in range(count - 1, 0, -1):
            if len(groups[index]) <= bump_up:
                groups[index - 1].extend(groups[index])
                groups[index] = []
    return [group for group in groups if group]


def seed_label(plan, source_label=None) -> str:
    """Label asal peserta (provenance) untuk ditampilkan di jadwal."""
    seeding = plan.get("seeding") or {}
    basis = seeding.get("basis", "roster")
    parts = []
    if basis == "roster":
        parts.append("Roster")
    else:
        kind = plan.get("source_kind")
        parts.append(source_label or ("Hasil " + KIND_LABEL.get(kind, str(kind))))
        parts.append(BASIS_LABEL.get(basis, basis))
    if seeding.get("mode") == "reverse":
        parts.append("balik grid")
    drop = int(seeding.get("drop") or 0)
    if drop:
        parts.append(f"buang {drop} terburuk")
    return " · ".join(parts)


def stage_duration_ms(stage, plan) -> int:
    """Durasi satu sesi untuk perhitungan jam mulai."""
    plan = plan or {}
    if plan.get("duration_ms"):
        return int(plan["duration_ms"])
    cfg = stage.get("cfg") or plan.get("cfg") or {}
    if cfg.get("target") == "time" and cfg.get("target_time_ms"):
        return int(cfg["target_time_ms"])
    if cfg.get("target") == "laps" and plan.get("estimate_lap_ms"):
        return int(plan["estimate_lap_ms"]) * int(cfg.get("value") or 0)
    return 0


def generate_stages(event, plan, source_rows=None) -> list:
    """Buat daftar sesi dari satu rencana: bagi peserta kelas menjadi grup."""
    class_id = plan.get("class_id")
    kind = plan.get("kind", "qualifying")
    entries = [entry for entry in event.get("entries") or []
               if entry.get("class_id") == class_id and entry.get("approved")]
    if not entries:
        raise ValueError("Belum ada peserta yang disetujui di kelas ini")
    seeding = plan.get("seeding") or {}
    ordered = seed_order(entries, source_rows or [], plan)
    groups = build_groups(ordered, int(plan.get("max_group_size") or DEFAULT_MAX_GROUP),
                          int(plan.get("bump_up") or 0), seeding.get("scope", "front"))
    if not groups:
        raise ValueError("Tidak ada peserta untuk dijadwalkan")
    label = seed_label(plan)
    stages = []
    for index, group in enumerate(groups):
        stages.append({
            "id": _id(), "class_id": class_id, "kind": kind,
            "name": f"{KIND_LABEL.get(kind, kind)} {plan.get('order') or 1}",
            "group": group_label(index, len(groups)),
            "cfg": dict(plan.get("cfg") or {}), "status": "planned", "race_id": None,
            "entry_ids": [entry["id"] for entry in group],
            "plan_id": plan.get("id"), "seed_source": label,
            "seed_basis": seeding.get("basis", "roster"),
            "seed_mode": seeding.get("mode", "forward"),
            "seed_scope": seeding.get("scope", "front"),
            "seed_drop": int(seeding.get("drop") or 0),
        })
    return stages


def _group_rank(stage, items) -> int:
    labels = sorted({str(item.get("group") or "") for item in items})
    try:
        return labels.index(str(stage.get("group") or ""))
    except ValueError:
        return len(labels)


# Alias supaya pemanggil tidak tertukar dengan method EventStore.generate_stages.
build_plan_stages = generate_stages


def compute_schedule(event, stages) -> list:
    """Hitung urutan sesi, jam mulai, dan durasi."""
    scheme = normalize_scheme(event.get("schedule_scheme"))
    plans = {plan.get("id"): plan for plan in event.get("plans") or []}
    classes = event.get("classes") or []
    class_order = {item.get("id"): index for index, item in enumerate(classes)}
    class_name = {item.get("id"): item.get("name") for item in classes}
    entries = {entry.get("id"): entry for entry in event.get("entries") or []}
    items = list(stages or [])

    if scheme["type"] == "rounds":
        rounds: dict[int, list] = {}
        for stage in items:
            plan = plans.get(stage.get("plan_id"))
            index = int(plan.get("order") or 0) if plan else int(stage.get("sequence") or 0)
            rounds.setdefault(index, []).append(stage)
        ordered = []
        for index in sorted(rounds):
            group = rounds[index]
            finals = [s for s in group if s.get("kind") in ("main", "final")]
            if scheme["finals_by_group"] and finals:
                ordered.extend(sorted(group, key=lambda s: (
                    0 if s in finals else 1, _group_rank(s, group),
                    class_order.get(s.get("class_id"), 0), int(s.get("sequence") or 0))))
            else:
                ordered.extend(sorted(group, key=lambda s: (
                    class_order.get(s.get("class_id"), 0), int(s.get("sequence") or 0))))
    else:
        ordered = sorted(items, key=lambda s: (class_order.get(s.get("class_id"), 0),
                                               int(s.get("sequence") or 0)))

    start_at = event.get("start_at")
    cursor = float(start_at) if start_at not in (None, "") else None
    rows = []
    for stage in ordered:
        plan = plans.get(stage.get("plan_id")) or {}
        duration = stage_duration_ms(stage, plan)
        gap = int(plan.get("gap_ms") if plan.get("gap_ms") is not None else DEFAULT_GAP_MS)
        drivers = []
        for entry_id in stage.get("entry_ids") or []:
            entry = entries.get(entry_id)
            if not entry:
                continue
            drivers.append({"entry_id": entry_id, "name": entry.get("name"),
                            "nick": entry.get("nick"), "transponder": entry.get("transponder")})
        rows.append({
            "stage_id": stage.get("id"), "class_id": stage.get("class_id"),
            "class_name": class_name.get(stage.get("class_id")) or "",
            "group": stage.get("group"), "kind": stage.get("kind"),
            "name": stage.get("name"), "status": stage.get("status"),
            "plan_id": stage.get("plan_id"),
            "start_ms": None if cursor is None else int(cursor * 1000),
            "duration_ms": duration, "gap_ms": gap,
            "seed_source": stage.get("seed_source"), "drivers": drivers,
        })
        if cursor is not None:
            cursor += (duration + gap) / 1000.0
    return rows
