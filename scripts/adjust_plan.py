#!/usr/bin/env python3
"""
Plan-tracking agent — runs daily in GitHub Actions.

1. Loads the latest activity CSV.
2. Fills in actuals for every past day in plan.json.
3. Detects mismatches in the current training week.

No automatic rewriting of the plan happens — the static WEEKLY_PLAN in
generate_dashboard.py is always the source of truth.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
from generate_dashboard import WEEKLY_PLAN, PLAN_START, _week_planned_km  # type: ignore

DATA = ROOT / "i644393_activities.csv"
PLAN_JSON = ROOT / "plan.json"

DAYS_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAYS_DEFAULTS = {
    "Mon": "Strength training",
    "Tue": "Quality run",
    "Wed": "Strength training",
    "Thu": "Rest",
    "Fri": "Strength training",
    "Sat": "Long run",
    "Sun": "Rest or bike",
}


def classify_session(desc: str) -> str:
    d = desc.lower()
    if 'race' in d or '\U0001F3C1' in desc:
        return "race"
    if 'long' in d:
        return "long"
    if 'strength' in d:
        return "rest"
    if 'rest' in d or 'bike' in d:
        return "rest"
    d_check = d.replace("no strides", "").replace("without strides", "")
    if any(x in d_check for x in ["interval", "tempo", "vo2", "strides", "progressive", "quality", "hmp", "hm pace"]):
        return "quality"
    return "easy"


def _parse_day_sessions(quality: str) -> dict:
    result = {}
    for part in quality.split(" · "):
        if ": " in part:
            day, desc = part.split(": ", 1)
            result[day.strip()] = desc.strip()
    return result


def generate_plan_json() -> dict:
    weeks = []
    for wnum, wdate_str, total_km, long_km, quality, phase in WEEKLY_PLAN:
        week_start = PLAN_START + timedelta(weeks=wnum - 1)
        day_sessions = _parse_day_sessions(quality)
        days = []
        for i, day_name in enumerate(DAYS_ORDER):
            day_date = week_start + timedelta(days=i)
            desc = day_sessions.get(day_name, DAYS_DEFAULTS.get(day_name, "Rest"))
            days.append({
                "date": day_date.isoformat(),
                "day": day_name,
                "session_type": classify_session(desc),
                "planned": desc,
                "actual_km": None,
                "actual_pace_min_km": None,
                "actual_hr": None,
                "actual_name": None,
            })
        weeks.append({
            "week": wnum,
            "date": week_start.isoformat(),
            "phase": phase,
            "target_km": _week_planned_km(quality),
            "long_km": long_km,
            "days": days,
        })
    return {
        "generated": date.today().isoformat(),
        "last_adjusted": None,
        "weeks": weeks,
    }


def load_or_init_plan() -> dict:
    if PLAN_JSON.exists():
        with open(PLAN_JSON) as f:
            return json.load(f)
    plan = generate_plan_json()
    PLAN_JSON.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    print("plan.json created from WEEKLY_PLAN.")
    return plan


def fill_actuals(plan_data: dict, runs_df: pd.DataFrame) -> dict:
    today_iso = date.today().isoformat()

    activity_by_date: dict = {}
    for _, row in runs_df.iterrows():
        d = row["Date"].date().isoformat()
        if d not in activity_by_date:
            activity_by_date[d] = {"km": 0.0, "paces": [], "hrs": [], "names": []}
        activity_by_date[d]["km"] += row["distance_km"]
        activity_by_date[d]["paces"].append(row["pace"])
        hr = row.get("avg_hr") if "avg_hr" in row.index else None
        if hr and pd.notna(hr):
            activity_by_date[d]["hrs"].append(float(hr))
        name = row.get("Name") if "Name" in row.index else None
        if name and pd.notna(name):
            activity_by_date[d]["names"].append(str(name))

    for week in plan_data["weeks"]:
        for day in week["days"]:
            d = day["date"]
            if d > today_iso:
                continue
            if d in activity_by_date:
                act = activity_by_date[d]
                day["actual_km"] = round(act["km"], 2)
                if act["paces"]:
                    day["actual_pace_min_km"] = round(
                        sum(act["paces"]) / len(act["paces"]), 4
                    )
                if act["hrs"]:
                    day["actual_hr"] = round(sum(act["hrs"]) / len(act["hrs"]), 0)
                if act["names"]:
                    day["actual_name"] = " + ".join(act["names"])
            else:
                if day["session_type"] in ("easy", "quality", "long"):
                    day["actual_km"] = 0.0

    return plan_data


_QUALITY_KEYWORDS = (
    "interval", "intervals", "tempo", "hmp", "hm pace",
)


def _activity_looks_like_quality(day: dict) -> bool:
    name = (day.get("actual_name") or "").lower()
    if any(kw in name for kw in _QUALITY_KEYWORDS):
        return True
    pace = day.get("actual_pace_min_km")
    km = day.get("actual_km") or 0
    if pace and pace < 5.5 and km < 12:
        return True
    return False


def detect_mismatches(week: dict, today: date) -> list:
    mismatches = []
    for day in week["days"]:
        if day["date"] > today.isoformat():
            break
        stype = day["session_type"]
        actual_km = day.get("actual_km")

        if stype in ("quality", "easy", "long") and (actual_km is None or actual_km < 1.0):
            mismatches.append({
                "date": day["date"],
                "day": day["day"],
                "issue": "missed",
                "planned": day["planned"],
                "actual_km": actual_km,
            })
        elif stype == "quality" and actual_km and actual_km > 1.0:
            if not _activity_looks_like_quality(day):
                mismatches.append({
                    "date": day["date"],
                    "day": day["day"],
                    "issue": "quality_missed",
                    "planned": day["planned"],
                    "actual_km": actual_km,
                    "actual_pace": day.get("actual_pace_min_km"),
                    "actual_name": day.get("actual_name"),
                })
    return mismatches


def main():
    if not DATA.exists():
        print(f"Data file not found: {DATA}. Skipping.")
        return

    df = pd.read_csv(DATA)
    df["Date"] = pd.to_datetime(df["Date"])
    runs = df[df["Type"].str.lower() == "run"].copy()
    runs["distance_km"] = runs["Distance"].astype(float) / 1000.0
    runs["moving_time_min"] = runs["Moving Time"].astype(float) / 60.0
    runs = runs[runs["distance_km"] > 0].copy()
    runs["pace"] = runs["moving_time_min"] / runs["distance_km"]
    runs = runs[runs["pace"].apply(lambda p: pd.notna(p) and p != float("inf"))].copy()
    runs["avg_hr"] = pd.to_numeric(runs.get("Avg HR", float("nan")), errors="coerce")

    plan_data = load_or_init_plan()

    quality_by_week = {row[0]: row[4] for row in WEEKLY_PLAN}
    for week in plan_data["weeks"]:
        if week["week"] in quality_by_week:
            week["target_km"] = _week_planned_km(quality_by_week[week["week"]])

    plan_data = fill_actuals(plan_data, runs)

    today = date.today()
    current_week_num = ((today - PLAN_START).days // 7) + 1
    current_week_num = max(1, min(current_week_num, len(plan_data["weeks"])))
    current_week = plan_data["weeks"][current_week_num - 1]

    mismatches = detect_mismatches(current_week, today)
    if mismatches:
        issues = [m["issue"] for m in mismatches]
        print(f"Week {current_week_num}: mismatches detected (no auto-adjustment) — {issues}")
    else:
        print(f"Week {current_week_num}: plan is on track.")

    plan_data["last_adjusted"] = today.isoformat()
    PLAN_JSON.write_text(json.dumps(plan_data, indent=2, ensure_ascii=False))
    print(f"plan.json saved → {PLAN_JSON}")


if __name__ == "__main__":
    main()
