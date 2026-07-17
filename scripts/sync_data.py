#!/usr/bin/env python3
"""Sync activities from Intervals.icu API and write to i644393_activities.csv.

Usage (local):
  export INTERVALS_ICU_API_KEY="your_key_here"
  python scripts/sync_data.py

In GitHub Actions the key is read from the INTERVALS_ICU_API_KEY secret.
Never hardcode the key in this file.
"""
import json
import os
import sys
import requests
import pandas as pd
from pathlib import Path

ATHLETE_ID = "i644393"
BASE_URL = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities"
WELLNESS_URL = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness"
STREAMS_URL = "https://intervals.icu/api/v1/activity/{id}/streams.json"

N_CURVE_RUNS = 10
CURVE_MAX_POINTS = 150

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "i644393_activities.csv"
WELLNESS_CSV_PATH = ROOT / "i644393_wellness.csv"
CURVES_JSON_PATH = ROOT / "run_curves.json"

COLUMNS = ["id", "Type", "Date", "Distance", "Moving Time", "Name", "Avg HR", "Intensity", "Load"]


def fetch_activities(api_key: str, oldest: str = "2024-01-01") -> list:
    response = requests.get(
        BASE_URL,
        params={"oldest": oldest},
        auth=("API_KEY", api_key),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def activities_to_df(activities: list) -> pd.DataFrame:
    rows = []
    for act in activities:
        rows.append({
            "id":           act.get("id", ""),
            "Type":         act.get("type", ""),
            "Date":         act.get("start_date_local", ""),
            "Distance":     act.get("distance") or 0,
            "Moving Time":  act.get("moving_time") or 0,
            "Name":         act.get("name", ""),
            "Avg HR":       act.get("average_heartrate", ""),
            "Intensity":    act.get("icu_intensity", ""),
            "Load":         act.get("icu_training_load", ""),
        })
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%dT%H:%M:%S")
    return df


def fetch_wellness(api_key: str, oldest: str = "2024-01-01") -> list:
    response = requests.get(
        WELLNESS_URL,
        params={"oldest": oldest, "fields": "id,ctl,atl,rampRate"},
        auth=("API_KEY", api_key),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def wellness_to_df(records: list) -> pd.DataFrame:
    rows = []
    for rec in records:
        ctl = rec.get("ctl")
        atl = rec.get("atl")
        if ctl is None or atl is None:
            continue
        rows.append({
            "date": rec.get("id"),
            "ctl": float(ctl),
            "atl": float(atl),
            "form": float(ctl) - float(atl),
        })
    return pd.DataFrame(rows, columns=["date", "ctl", "atl", "form"])


def fetch_streams(api_key: str, activity_id: str) -> dict:
    response = requests.get(
        STREAMS_URL.format(id=activity_id),
        params={"types": "heartrate,velocity_smooth,distance,time"},
        auth=("API_KEY", api_key),
        timeout=30,
    )
    response.raise_for_status()
    raw = response.json()
    if isinstance(raw, list):
        return {s["type"]: s.get("data", []) for s in raw if isinstance(s, dict) and "type" in s}
    return raw or {}


def _downsample(*series):
    n = len(series[0])
    if n == 0:
        return series
    step = max(1, n // CURVE_MAX_POINTS)
    idx = range(0, n, step)
    return tuple([s[i] for i in idx] for s in series)


def build_run_curves(runs_df: pd.DataFrame, api_key: str) -> dict:
    recent = runs_df.sort_values("Date").tail(N_CURVE_RUNS)
    out = {}
    for _, row in recent.iterrows():
        activity_id = row["id"]
        try:
            streams = fetch_streams(api_key, activity_id)
            dist = streams.get("distance") or []
            hr = streams.get("heartrate") or []
            vel = streams.get("velocity_smooth") or []
            if not dist or not (hr or vel):
                print(f"  No usable streams for {activity_id}, skipping.")
                continue
            n = min(len(dist), len(hr) or len(dist), len(vel) or len(dist))
            dist, hr = dist[:n], (hr[:n] if hr else [None] * n)
            vel = vel[:n] if vel else [None] * n
            pace = [round(1000 / 60 / v, 3) if v and v > 0.3 else None for v in vel]
            dist_km, hr_ds, pace_ds = _downsample(dist, hr, pace)
            out[str(activity_id)] = {
                "name": row.get("Name", ""),
                "date": row["Date"].strftime("%Y-%m-%d") if hasattr(row["Date"], "strftime") else str(row["Date"]),
                "distance_km": [round(d / 1000.0, 3) for d in dist_km],
                "hr": hr_ds,
                "pace": pace_ds,
            }
        except requests.RequestException as e:
            print(f"  Streams fetch failed for {activity_id}: {e}", file=sys.stderr)
    return out


def sync():
    api_key = os.environ.get("INTERVALS_ICU_API_KEY", "").strip()
    if not api_key:
        print("ERROR: INTERVALS_ICU_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching activities for athlete {ATHLETE_ID} from Intervals.icu...")
    activities = fetch_activities(api_key)
    print(f"  Received {len(activities)} activities from the API.")

    df = activities_to_df(activities)
    df.to_csv(CSV_PATH, index=False)
    print(f"  Wrote {len(df)} rows to {CSV_PATH.relative_to(ROOT)}")

    print("Fetching wellness (CTL/ATL/Form)...")
    wellness = wellness_to_df(fetch_wellness(api_key))
    wellness.to_csv(WELLNESS_CSV_PATH, index=False)
    print(f"  Wrote {len(wellness)} rows to {WELLNESS_CSV_PATH.relative_to(ROOT)}")

    print(f"Fetching HR/pace streams for the last {N_CURVE_RUNS} runs...")
    runs_df = df[df["Type"].str.lower() == "run"].copy()
    runs_df["Date"] = pd.to_datetime(runs_df["Date"])
    curves = build_run_curves(runs_df, api_key)
    CURVES_JSON_PATH.write_text(json.dumps({"runs": curves}, indent=2, ensure_ascii=False))
    print(f"  Wrote curves for {len(curves)} runs to {CURVES_JSON_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    sync()
