"""Turn raw WHOOP v2 records into one row per local calendar day (pure functions, no I/O)."""

from __future__ import annotations

from datetime import datetime, timedelta

KJ_TO_KCAL = 1 / 4.184

COLUMNS = [
    "date", "recovery", "hrv_ms", "resting_hr", "spo2", "skin_temp_c", "calibrating",
    "sleep_h", "sleep_performance", "sleep_efficiency", "sleep_consistency", "respiratory_rate",
    "in_bed_h", "awake_h", "light_h", "deep_h", "rem_h", "sleep_cycles", "disturbances",
    "sleep_need_h", "sleep_debt_h", "nap_h",
    "day_strain", "day_kcal", "avg_hr", "max_hr", "steps",
    "workout_kcal", "workout_min", "workout_max_hr",
    "zone0_min", "zone1_min", "zone2_min", "zone3_min", "zone4_min", "zone5_min",
    "workouts", "cycle_complete",
]

ZONES = ["zero", "one", "two", "three", "four", "five"]
STAGES = {"in_bed_h": "total_in_bed_time_milli", "awake_h": "total_awake_time_milli",
          "light_h": "total_light_sleep_time_milli", "deep_h": "total_slow_wave_sleep_time_milli",
          "rem_h": "total_rem_sleep_time_milli"}
H = 3.6e6


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def local_date(iso: str, offset: str | None, shift: timedelta = timedelta()) -> str:
    """Calendar date of a UTC timestamp in the record's own `timezone_offset` (e.g. "-03:00")."""
    offset = offset or "+00:00"
    sign = -1 if offset.startswith("-") else 1
    hh, mm = offset.lstrip("+-").split(":")
    delta = sign * timedelta(hours=int(hh), minutes=int(mm))
    return (_parse(iso) + shift + delta).date().isoformat()


def _r1(x):
    return round(x, 1) if isinstance(x, (int, float)) else ""


def _h(ms):
    return round(ms / H, 2) if isinstance(ms, (int, float)) else ""


def summarize_days(data: dict[str, list[dict]]) -> dict[str, dict]:
    """Aggregate cycles, recoveries, sleeps and workouts into {date: row}.

    Day assignment rules:
    - A WHOOP cycle starts when you fall asleep (often before midnight), so it is
      assigned to the day you are awake in: local date of start + 12 h.
    - Recovery belongs to its cycle's day.
    - Sleep (naps excluded) belongs to the local date it ends; several sleeps that
      end on the same day are added together. Naps go to `nap_h` only.
    - Workouts belong to the local date they start.
    """
    days: dict[str, dict] = {}

    def day(d: str) -> dict:
        return days.setdefault(d, {"date": d, "_workouts": [], "_workout_kcal": 0.0, "_workout_ms": 0,
                                   "_zones": [0] * 6, "_sleep_ms": 0, "_nap_ms": 0,
                                   "_stages": dict.fromkeys(STAGES, 0), "_cycles": 0, "_dist": 0})

    cycle_day = {}
    for c in data.get("cycles", []):
        d = local_date(c["start"], c.get("timezone_offset"), timedelta(hours=12))
        cycle_day[c["id"]] = d
        s = c.get("score") or {}
        row = day(d)
        row["day_strain"] = _r1(s.get("strain"))
        row["day_kcal"] = round(s["kilojoule"] * KJ_TO_KCAL) if s.get("kilojoule") else ""
        row["avg_hr"] = s.get("average_heart_rate", "")
        row["max_hr"] = s.get("max_heart_rate", "")
        row["steps"] = c.get("step_count", "")
        row["cycle_complete"] = "yes" if c.get("end") else "no"

    for r in data.get("recoveries", []):
        d = cycle_day.get(r.get("cycle_id"))
        if not d or r.get("score_state") != "SCORED":
            continue
        s = r.get("score") or {}
        row = day(d)
        row["recovery"] = s.get("recovery_score", "")
        row["hrv_ms"] = _r1(s.get("hrv_rmssd_milli"))
        row["resting_hr"] = s.get("resting_heart_rate", "")
        row["spo2"] = _r1(s.get("spo2_percentage"))
        row["skin_temp_c"] = _r1(s.get("skin_temp_celsius"))
        if "user_calibrating" in s:
            row["calibrating"] = "yes" if s["user_calibrating"] else "no"

    for sl in data.get("sleeps", []):
        if sl.get("score_state") != "SCORED":
            continue
        s = sl.get("score") or {}
        st = s.get("stage_summary") or {}
        asleep = sum(st.get(k, 0) for k in (
            "total_light_sleep_time_milli", "total_slow_wave_sleep_time_milli", "total_rem_sleep_time_milli"))
        row = day(local_date(sl["end"], sl.get("timezone_offset")))
        if sl.get("nap"):
            row["_nap_ms"] += asleep
            continue
        row["_sleep_ms"] += asleep
        for col, key in STAGES.items():
            row["_stages"][col] += st.get(key) or 0
        row["_cycles"] += st.get("sleep_cycle_count") or 0
        row["_dist"] += st.get("disturbance_count") or 0
        # Scores come from the longest sleep of the day.
        if asleep >= row.get("_main_sleep_ms", -1):
            row["_main_sleep_ms"] = asleep
            row["sleep_performance"] = _r1(s.get("sleep_performance_percentage"))
            row["sleep_efficiency"] = _r1(s.get("sleep_efficiency_percentage"))
            row["respiratory_rate"] = _r1(s.get("respiratory_rate"))
            row["sleep_consistency"] = _r1(s.get("sleep_consistency_percentage"))
            need = s.get("sleep_needed") or {}
            if need:
                row["sleep_need_h"] = _h(sum(v for v in need.values() if isinstance(v, (int, float))))
                row["sleep_debt_h"] = _h(need.get("need_from_sleep_debt_milli"))

    for w in sorted(data.get("workouts", []), key=lambda w: w["start"]):
        s = w.get("score") or {}
        row = day(local_date(w["start"], w.get("timezone_offset")))
        minutes = (_parse(w["end"]) - _parse(w["start"])).total_seconds() / 60
        kcal = (s.get("kilojoule") or 0) * KJ_TO_KCAL
        row["_workout_kcal"] += kcal
        row["_workout_ms"] += minutes * 60000
        zd = s.get("zone_durations") or {}
        for i, z in enumerate(ZONES):
            row["_zones"][i] += zd.get(f"zone_{z}_milli") or 0
        if isinstance(s.get("max_heart_rate"), (int, float)):
            row["workout_max_hr"] = max(s["max_heart_rate"], row.get("workout_max_hr") or 0)
        row["_workouts"].append(
            f"{w.get('sport_name', '?')} {minutes:.0f}min {kcal:.0f}kcal strain {_r1(s.get('strain'))}")

    out = {}
    for d, row in days.items():
        row["sleep_h"] = round(row["_sleep_ms"] / 3.6e6, 2) if row["_sleep_ms"] else ""
        row["nap_h"] = _h(row["_nap_ms"]) if row["_nap_ms"] else ""
        if row["_sleep_ms"]:
            for col in STAGES:
                row[col] = _h(row["_stages"][col])
            row["sleep_cycles"] = row["_cycles"]
            row["disturbances"] = row["_dist"]
        row["workout_kcal"] = round(row["_workout_kcal"]) if row["_workouts"] else ""
        if row["_workouts"]:
            row["workout_min"] = round(row["_workout_ms"] / 60000)
            for i in range(6):
                row[f"zone{i}_min"] = round(row["_zones"][i] / 60000, 1)
        row["workouts"] = " | ".join(row["_workouts"])
        out[d] = {c: row.get(c, "") for c in COLUMNS}
    return out
