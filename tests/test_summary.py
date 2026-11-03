from datetime import timedelta

import pytest

from whoop_sync.cli import merge_csv
from whoop_sync.client import WhoopClient, WhoopError
from whoop_sync.summary import COLUMNS, local_date, summarize_days


def cycle(id, start, end, offset="-03:00", strain=10.0, kj=9000):
    return {"id": id, "start": start, "end": end, "timezone_offset": offset, "step_count": 8500,
            "score": {"strain": strain, "kilojoule": kj, "average_heart_rate": 60}}


def sleep(start, end, offset="-03:00", nap=False, light=3, deep=2, rem=2, perf=80):
    h = 3_600_000
    return {"start": start, "end": end, "timezone_offset": offset, "nap": nap, "score_state": "SCORED",
            "score": {"sleep_performance_percentage": perf, "sleep_efficiency_percentage": 90,
                      "sleep_consistency_percentage": 75, "respiratory_rate": 16,
                      "sleep_needed": {"baseline_milli": 7 * h, "need_from_sleep_debt_milli": h // 2,
                                       "need_from_recent_strain_milli": h // 4, "need_from_recent_nap_milli": 0},
                      "stage_summary": {"total_in_bed_time_milli": (light + deep + rem + 1) * h,
                                        "total_awake_time_milli": h,
                                        "total_light_sleep_time_milli": light * h,
                                        "total_slow_wave_sleep_time_milli": deep * h,
                                        "total_rem_sleep_time_milli": rem * h,
                                        "sleep_cycle_count": 4, "disturbance_count": 6}}}


def test_local_date_applies_offset_and_shift():
    assert local_date("2026-09-27T02:30:00.000Z", "-03:00") == "2026-09-26"
    assert local_date("2026-09-27T12:00:00.000Z", "-03:00") == "2026-09-27"
    assert local_date("2026-09-27T02:30:00.000Z", "-03:00", timedelta(hours=12)) == "2026-09-27"
    assert local_date("2026-09-27T23:30:00.000Z", "+05:30") == "2026-09-28"


def test_cycle_starting_before_midnight_belongs_to_next_day():
    # Fell asleep 23:04 local on the 29th -> the cycle is the 30th's day.
    rows = summarize_days({"cycles": [cycle(1, "2026-08-30T03:04:00.000Z", "2026-08-31T03:39:00.000Z")]})
    assert list(rows) == ["2026-08-30"]
    assert rows["2026-08-30"]["day_kcal"] == round(9000 / 4.184)
    assert rows["2026-08-30"]["cycle_complete"] == "yes"
    assert rows["2026-08-30"]["steps"] == 8500


def test_recovery_follows_its_cycle_and_skips_unscored():
    data = {
        "cycles": [cycle(1, "2026-09-02T04:00:00.000Z", None)],
        "recoveries": [
            {"cycle_id": 1, "score_state": "SCORED",
             "score": {"recovery_score": 83, "hrv_rmssd_milli": 23.04, "resting_heart_rate": 52,
                       "spo2_percentage": 95.7, "skin_temp_celsius": 33.5}},
            {"cycle_id": 99, "score_state": "SCORED", "score": {"recovery_score": 10}},
        ],
    }
    row = summarize_days(data)["2026-09-02"]
    assert (row["recovery"], row["hrv_ms"], row["resting_hr"]) == (83, 23.0, 52)
    assert row["cycle_complete"] == "no"


def test_sleeps_are_summed_naps_ignored_scores_from_longest():
    data = {"sleeps": [
        sleep("2026-09-10T04:00:00.000Z", "2026-09-10T11:00:00.000Z", light=3, deep=1, rem=1, perf=70),
        sleep("2026-09-10T13:00:00.000Z", "2026-09-10T15:00:00.000Z", light=1, deep=0, rem=0, perf=99),
        sleep("2026-09-10T18:00:00.000Z", "2026-09-10T19:00:00.000Z", nap=True),
    ]}
    row = summarize_days(data)["2026-09-10"]
    assert row["sleep_h"] == 6.0
    assert row["sleep_performance"] == 70
    assert row["nap_h"] == 7.0
    assert (row["light_h"], row["deep_h"], row["rem_h"]) == (4.0, 1.0, 1.0)
    assert (row["in_bed_h"], row["awake_h"]) == (8.0, 2.0)
    assert (row["sleep_cycles"], row["disturbances"]) == (8, 12)
    # Need and consistency come from the longest sleep.
    assert (row["sleep_need_h"], row["sleep_debt_h"], row["sleep_consistency"]) == (7.75, 0.5, 75)


def test_workouts_are_listed_and_totalled():
    data = {"workouts": [
        {"sport_name": "basketball", "start": "2026-09-26T12:00:00.000Z", "end": "2026-09-26T13:30:00.000Z",
         "timezone_offset": "-03:00", "score": {"strain": 11.3, "kilojoule": 4184, "max_heart_rate": 150,
                                                "zone_durations": {"zone_zero_milli": 600000, "zone_one_milli": 4800000}}},
        {"sport_name": "functional-fitness", "start": "2026-09-26T16:00:00.000Z",
         "end": "2026-09-26T17:00:00.000Z", "timezone_offset": "-03:00",
         "score": {"strain": 9.0, "kilojoule": 2092, "max_heart_rate": 165,
                   "zone_durations": {"zone_one_milli": 1800000, "zone_three_milli": 1800000}}},
    ]}
    row = summarize_days(data)["2026-09-26"]
    assert row["workout_kcal"] == 1500
    assert (row["workout_min"], row["workout_max_hr"]) == (150, 165)
    assert [row[f"zone{i}_min"] for i in range(6)] == [10.0, 110.0, 0.0, 30.0, 0.0, 0.0]
    assert row["workouts"] == "basketball 90min 1000kcal strain 11.3 | functional-fitness 60min 500kcal strain 9.0"
    assert set(row) == set(COLUMNS)


def test_merge_csv_upserts_by_date(tmp_path):
    path = tmp_path / "daily.csv"
    merge_csv(path, {"2026-09-01": {"date": "2026-09-01", "recovery": 50}})
    merge_csv(path, {"2026-09-01": {"date": "2026-09-01", "recovery": 60},
                     "2026-08-31": {"date": "2026-08-31", "recovery": 40}})
    lines = path.read_text().splitlines()
    assert lines[0].startswith("date,recovery")
    assert [l.split(",")[:2] for l in lines[1:]] == [["2026-08-31", "40"], ["2026-09-01", "60"]]


def test_redirect_state_is_checked():
    url = "http://127.0.0.1:3000/callback?code=abc&state=s1"
    assert WhoopClient.code_from_redirect_url(url, "s1") == "abc"
    with pytest.raises(WhoopError):
        WhoopClient.code_from_redirect_url(url, "other")
    with pytest.raises(WhoopError):
        WhoopClient.code_from_redirect_url("http://127.0.0.1:3000/callback?error=access_denied&state=s1", "s1")


def test_merge_csv_keeps_existing_values_when_new_ones_are_empty(tmp_path):
    path = tmp_path / "daily.csv"
    merge_csv(path, {"2026-09-25": {"date": "2026-09-25", "recovery": 59, "day_kcal": 2385}})
    merge_csv(path, {"2026-09-25": {"date": "2026-09-25", "recovery": "", "day_kcal": 2400}})
    row = path.read_text().splitlines()[1].split(",")
    assert row[COLUMNS.index("recovery")] == "59"
    assert row[COLUMNS.index("day_kcal")] == "2400"
