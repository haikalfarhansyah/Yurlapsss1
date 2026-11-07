"""Validated preset configuration shared by storage and the race engine."""

RANKS = {"position", "result", "lapsTime", "best", "top3", "consecutive"}
DEFAULTS = {"name": "Quick Race", "target": "time", "value": 180,
            "rank": "position", "start_mode": "grid", "cd": 5, "sound": 1,
            "random_delay_max": 1, "target_time_ms": 180000,
            "finish_current_lap": False, "stagger_mode": "manual",
            "stagger_gap_ms": 10000}


def _integer(value, label, low, high):
    try:
        number = float(value)
        integer = int(number)
        if number != integer or not low <= integer <= high:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"{label} harus bilangan bulat {low} sampai {high}")
    return integer


def _boolean(value):
    if value in (True, 1, "1", "true", "on"):
        return True
    if value in (False, 0, "0", "false", "off", None):
        return False
    raise ValueError("Pilihan aktif harus true atau false")


def normalize(data, base=None):
    result = dict(DEFAULTS, **(base or {}))
    incoming = dict(data)
    for old, new in (("nama", "name"), ("random_delay", "random_delay_max"),
                     ("finish_current", "finish_current_lap"), ("stagger_gap", "stagger_gap_ms")):
        if old in incoming and new not in incoming:
            incoming[new] = incoming[old]
    for key in DEFAULTS:
        if key in incoming and incoming[key] is not None:
            result[key] = incoming[key]
    result["name"] = str(result["name"] or "").strip()
    if not result["name"]:
        raise ValueError("Nama preset wajib diisi")
    if result["target"] not in ("time", "laps"):
        raise ValueError("Target harus lap atau waktu")
    if result["start_mode"] not in ("grid", "staggered"):
        raise ValueError("Start harus grid atau staggered")
    if "start_mode" in incoming and "rank" not in incoming:
        result["rank"] = "position" if result["start_mode"] == "grid" else "result"
    if result["rank"] not in RANKS:
        raise ValueError("Penilaian tidak dikenal")
    if result["rank"] == "lapsTime":
        result["rank"] = "result"
    result["value"] = _integer(result["value"], "Target", 1, 86400)
    result["cd"] = _integer(result["cd"], "Countdown", 0, 60)
    result["random_delay_max"] = _integer(result["random_delay_max"], "Random delay", 1, 3)
    result["stagger_gap_ms"] = _integer(result["stagger_gap_ms"], "Jeda panggilan (ms)", 1000, 600000)
    result["stagger_mode"] = "manual" if result["stagger_mode"] == "passing" else result["stagger_mode"]
    if result["stagger_mode"] not in ("manual", "auto"):
        raise ValueError("Start staggered harus manual atau otomatis")
    for key in ("sound", "finish_current_lap"):
        result[key] = _boolean(result[key])
    if result["target"] == "time":
        if ("target_time_ms" not in incoming or incoming.get("target_time_ms") in (None, "")) and ("value" in incoming or not result.get("target_time_ms")):
            result["target_time_ms"] = result["value"] * 1000
        result["target_time_ms"] = _integer(result["target_time_ms"], "Target waktu (ms)", 1000, 86400000)
        result["value"] = (result["target_time_ms"] + 999) // 1000
    else:
        result["target_time_ms"] = None
    return result
