"""Public scoreboard snapshot; excludes account IDs, transponders and passing logs."""
from __future__ import annotations

import time


def _number(value):
    try:
        return max(0, int(round(float(value)))) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def live_payload(race, event_stage, practice, track_name="", *, finished_at=None, now=None):
    now = time.time() if now is None else now
    status = race.get("status") or "idle"
    race_visible = status in ("live", "countdown", "paused") or (
        status == "done" and finished_at is not None and now - finished_at < 120)
    if race_visible:
        rows = []
        for position, driver in enumerate(race.get("klasemen") or [], 1):
            gap = driver.get("gap_leader") or {}
            rows.append({
                "pos": _number(driver.get("pos")) or position,
                "name": str(driver.get("nick") or driver.get("name") or "Driver")[:80],
                "laps": _number(driver.get("n_lap")) or 0,
                "total_ms": _number(driver.get("elapsed_ms") or driver.get("total_ms")),
                "best_ms": _number(driver.get("best_ms")),
                "gap_ms": _number(gap.get("ms")) if isinstance(gap, dict) else None,
                "status": str(driver.get("result_status") or driver.get("status") or "")[:20],
            })
        mode = "event" if event_stage else "quick"
        event, stage = event_stage if event_stage else ({}, {})
        return mode, {"name": event.get("name") if event else race.get("cfg", {}).get("name", "Quick Race"),
                      "stage": stage.get("name", ""), "track": (race.get("track") or {}).get("name") or track_name,
                      "status": status, "elapsed_ms": _number(race.get("elapsed_ms")), "rows": rows[:50]}

    sessions = practice.get("active_sessions") or []
    drivers = [driver for session in sessions for driver in session.get("drivers", [])]
    drivers.sort(key=lambda driver: (driver.get("best_ms") is None, driver.get("best_ms") or 10**12))
    rows = [{"pos": index, "name": f"Mobil {index}", "laps": _number(driver.get("n_lap")) or 0,
             "total_ms": _number(driver.get("total_ms")), "best_ms": _number(driver.get("best_ms")),
             "gap_ms": None, "status": "practice"} for index, driver in enumerate(drivers[:50], 1)]
    track = practice.get("session") or {}
    return "practice", {"name": "Practice", "stage": "", "track": track.get("track_name") or track_name,
                        "status": "live" if rows else "waiting", "elapsed_ms": None, "rows": rows}
