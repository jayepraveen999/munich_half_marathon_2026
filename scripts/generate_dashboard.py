#!/usr/bin/env python3
"""Generate an interactive dashboard for Munich Half Marathon 2026 training.

CSV columns (Intervals.icu export):
  id, Type, Date, Distance (meters), Moving Time (seconds), Name, Avg HR, Norm Power,
  Intensity, Load, FTP, Weight, W'
"""
import json
import re
import pandas as pd
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parents[0]
DATA = ROOT.joinpath('..').joinpath('i644393_activities.csv').resolve()
OUT = ROOT.joinpath('..').joinpath('docs')
OUT.mkdir(parents=True, exist_ok=True)
PLAN_JSON = ROOT.joinpath('..').joinpath('plan.json').resolve()
WELLNESS_CSV = ROOT.joinpath('..').joinpath('i644393_wellness.csv').resolve()
RUN_CURVES_JSON = ROOT.joinpath('..').joinpath('run_curves.json').resolve()

RACE_DATE = date(2026, 10, 11)
PLAN_START = date(2026, 7, 20)
NUM_WEEKS = 12
TARGET_PACE_MIN_KM = 6 + 23.0 / 60.0  # 6:23/km for sub 2:15
SAFE_PACE_MIN_KM = 7 + 6.0 / 60.0     # 7:06/km for sub 2:30

# 12-week half marathon plan: 2 runs per week (Tue quality + Sat long)
# Weekly structure: Mon Strength, Tue Run, Wed Strength, Thu Rest, Fri Strength, Sat Run, Sun Rest/Bike
WEEKLY_PLAN = [
    # (week, date_label, total_km, long_km, quality_description, phase)
    (1,  "Jul 20", 13,  8, "Mon: Strength training · Tue: Easy 5k @7:00–7:30/km · Wed: Strength training · Thu: Rest · Fri: Strength training · Sat: Long 8k @7:00–7:30/km · Sun: Rest or bike",  "Base"),
    (2,  "Jul 27", 16, 10, "Mon: Strength training · Tue: 6k with 4×100m strides @6:00/km · Wed: Strength training · Thu: Rest · Fri: Strength training · Sat: Long 10k @7:00–7:15/km · Sun: Rest or bike",  "Base"),
    (3,  "Aug 3",  18, 11, "Mon: Strength training · Tue: 7k with 4×400m @5:30–5:50/km (2min rest) · Wed: Strength training · Thu: Rest · Fri: Strength training · Sat: Long 11k @6:50–7:15/km · Sun: Rest or bike",  "Base"),
    (4,  "Aug 10", 13,  8, "Mon: Strength training · Tue: Easy 5k @7:00–7:30/km · Wed: Strength training · Thu: Rest · Fri: Strength training · Sat: Easy 8k @7:00–7:30/km · Sun: Rest or bike",  "Base ↩ Recovery"),
    (5,  "Aug 17", 21, 13, "Mon: Strength training · Tue: 8k with 3k tempo @6:00–6:20/km · Wed: Strength training · Thu: Rest · Fri: Strength training · Sat: Long 13k @6:50–7:15/km · Sun: Rest or bike",  "Build"),
    (6,  "Aug 24", 22, 14, "Mon: Strength training · Tue: 8k with 4×1k @5:30–5:50/km (90s rest) · Wed: Strength training · Thu: Rest · Fri: Strength training · Sat: Long 14k @6:45–7:10/km · Sun: Rest or bike",  "Build"),
    (7,  "Aug 31", 25, 16, "Mon: Strength training · Tue: 9k with 5k tempo @5:50–6:10/km · Wed: Strength training · Thu: Rest · Fri: Strength training · Sat: Long 16k @6:45–7:10/km · Sun: Rest or bike",  "Build"),
    (8,  "Sep 7",  16, 10, "Mon: Strength training · Tue: Easy 6k @7:00–7:30/km · Wed: Strength training · Thu: Rest · Fri: Strength training · Sat: Easy 10k @7:00–7:15/km · Sun: Rest or bike",  "Build ↩ Recovery"),
    (9,  "Sep 14", 27, 18, "Mon: Strength training · Tue: 9k with 5k @HMP (6:20/km) · Wed: Strength training · Thu: Rest · Fri: Strength training · Sat: Long 18k (last 5k @6:20/km) · Sun: Rest or bike",  "Specific"),
    (10, "Sep 21", 27, 18, "Mon: Strength training · Tue: 9k with 6k @HMP (6:20/km) · Wed: Strength training · Thu: Rest · Fri: Strength training · Sat: Long 18k (last 6k @6:20/km) · Sun: Rest or bike",  "Specific"),
    (11, "Sep 28", 18, 12, "Mon: Strength training · Tue: 6k easy with 4×100m strides · Wed: Strength training · Thu: Rest · Fri: Strength training · Sat: Easy 12k @7:00–7:15/km · Sun: Rest or bike",  "Taper"),
    (12, "Oct 5",  25,  0, "Mon: Strength (light) · Tue: Easy 4k + 4×100m strides · Wed: Rest · Thu: Rest · Fri: Rest · Sat: Rest · Sun: RACE DAY \U0001F3C1 — 21.1k Half Marathon",  "Race week"),
]

PHASE_COLORS = {
    "Base": "#3b82f6",
    "Base ↩ Recovery": "#93c5fd",
    "Build": "#f97316",
    "Build ↩ Recovery": "#fdba74",
    "Specific": "#8b5cf6",
    "Taper": "#86efac",
    "Race week": "#ef4444",
}


def _load_plan():
    if PLAN_JSON.exists():
        with open(PLAN_JSON) as f:
            return json.load(f)
    return None


def _parse_day_sessions(quality):
    parsed = {}
    for part in quality.split(' · '):
        if ': ' in part:
            day_key, desc = part.split(': ', 1)
            parsed[day_key.strip()] = desc.strip()
    return parsed


_REP_RE = re.compile(r'(\d+)\s*[×x]\s*(\d+(?:\.\d+)?)\s*(m|k)\b', re.IGNORECASE)

INTERVAL_WUCD_KM = 4.0


def _day_planned_km(desc):
    rep = _REP_RE.search(desc)
    m = re.search(r'(\d+(?:\.\d+)?)k', _REP_RE.sub('', desc), re.IGNORECASE)
    if m:
        return float(m.group(1))
    if rep:
        n, dist, unit = int(rep.group(1)), float(rep.group(2)), rep.group(3).lower()
        reps_km = n * (dist / 1000.0 if unit == 'm' else dist)
        return reps_km + INTERVAL_WUCD_KM
    if 'race' in desc.lower() or '21.1' in desc:
        return 21.1
    return 0.0


def _annotate_day_desc(desc):
    if _REP_RE.search(desc) and not re.search(r'(\d+(?:\.\d+)?)k',
                                              _REP_RE.sub('', desc), re.IGNORECASE):
        return f'{desc} — ~{_day_planned_km(desc):g}k est'
    return desc


def _annotate_quality(quality):
    parts = []
    for part in quality.split(' · '):
        if ': ' in part:
            day_key, desc = part.split(': ', 1)
            parts.append(f'{day_key}: {_annotate_day_desc(desc)}')
        else:
            parts.append(part)
    return ' · '.join(parts)


def _week_planned_km(quality):
    parsed = _parse_day_sessions(quality)
    return sum(_day_planned_km(parsed.get(dk, 'Rest'))
               for dk in ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'))


def load_and_clean(path):
    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame(columns=['id', 'Type', 'Date', 'Distance', 'Moving Time', 'Name',
                                     'Avg HR', 'distance_km', 'moving_time_min', 'pace',
                                     'pace_str', 'week_start', 'avg_hr'])
    df['Date'] = pd.to_datetime(df['Date'])
    runs = df[df['Type'].str.lower() == 'run'].copy()
    if runs.empty:
        return pd.DataFrame(columns=['id', 'Type', 'Date', 'Distance', 'Moving Time', 'Name',
                                     'Avg HR', 'distance_km', 'moving_time_min', 'pace',
                                     'pace_str', 'week_start', 'avg_hr'])
    runs['distance_km'] = runs['Distance'].astype(float) / 1000.0
    runs['moving_time_min'] = runs['Moving Time'].astype(float) / 60.0
    runs = runs[runs['distance_km'] > 0].copy()
    runs['pace'] = runs['moving_time_min'] / runs['distance_km']
    runs = runs[runs['pace'].apply(lambda p: pd.notna(p) and p != float('inf'))].copy()
    runs['pace_str'] = runs['pace'].apply(lambda p: f"{int(p)}:{int((p % 1)*60):02d}")
    runs['week_start'] = runs['Date'].dt.normalize() - pd.to_timedelta(runs['Date'].dt.weekday, unit='D')
    runs['avg_hr'] = pd.to_numeric(runs.get('Avg HR', float('nan')), errors='coerce')
    return runs.sort_values('Date').reset_index(drop=True)


def weekly_aggregates(runs):
    if runs.empty:
        return pd.DataFrame(columns=['week_start', 'total_km', 'avg_pace', 'avg_hr',
                                     'n_runs', 'week_start_str', 'rolling_km_4w',
                                     'rolling_pace_4w', 'avg_pace_str'])
    weekly = (
        runs.groupby('week_start')
        .agg(total_km=('distance_km', 'sum'), avg_pace=('pace', 'mean'),
             avg_hr=('avg_hr', 'mean'), n_runs=('id', 'count'))
        .reset_index().sort_values('week_start')
    )
    weekly['week_start_str'] = weekly['week_start'].dt.strftime('%Y-%m-%d')
    weekly['rolling_km_4w'] = weekly['total_km'].rolling(4, min_periods=1).mean()
    weekly['rolling_pace_4w'] = weekly['avg_pace'].rolling(4, min_periods=1).mean()
    weekly['avg_pace_str'] = weekly['avg_pace'].apply(
        lambda p: f"{int(p)}:{int((p % 1)*60):02d}" if pd.notna(p) else '')
    return weekly


def load_wellness(path):
    cols = ['date', 'ctl', 'atl', 'form']
    if not Path(path).exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame(columns=cols)
    df['date'] = pd.to_datetime(df['date'])
    return df.sort_values('date')


def _load_run_curves():
    if not RUN_CURVES_JSON.exists():
        return {}
    with open(RUN_CURVES_JSON) as f:
        return json.load(f).get('runs', {})


def make_targets():
    return [_week_planned_km(row[4]) for row in WEEKLY_PLAN]


def _esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def _fmt_pace_clock(p):
    return f"{int(p)}:{int((p % 1) * 60):02d}"

# ── Charts ──────────────────────────────────────────────────────────────────


def build_volume_chart(weekly, targets, plan_start):
    n = len(targets)
    plan_mondays = [pd.Timestamp(plan_start).normalize() + pd.Timedelta(weeks=i) for i in range(n)]
    obs_map, roll_map = {}, {}
    for m, k, r in zip(pd.to_datetime(weekly['week_start']), weekly['total_km'], weekly['rolling_km_4w']):
        key = pd.Timestamp(m).normalize()
        obs_map[key] = float(k)
        roll_map[key] = float(r) if pd.notna(r) else None
    observed = [obs_map.get(m) for m in plan_mondays]
    rolling = [roll_map.get(m) for m in plan_mondays]
    today = pd.Timestamp(date.today())

    W, H = 920, 360
    pad_l, pad_r, pad_t, pad_b = 38, 14, 18, 48
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b
    band = plot_w / n
    bar_w = band * 0.5

    vmax = max(list(targets) + [v for v in observed if v]) * 1.12
    tick_step = 10 if vmax > 20 else 5
    y_top = (int(vmax // tick_step) + 1) * tick_step

    def yv(v):
        return pad_t + plot_h * (1 - v / y_top)

    def xc(i):
        return pad_l + band * i + band / 2

    def is_cur(m):
        return m <= today <= (m + pd.Timedelta(days=6))

    p = [f'<svg viewBox="0 0 {W} {H}" class="chart-svg" preserveAspectRatio="xMidYMid meet" role="img">']

    t = 0
    while t <= y_top + 0.1:
        y = yv(t)
        p.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W - pad_r}" y2="{y:.1f}" stroke="#1e293b"/>')
        p.append(f'<text x="{pad_l - 6}" y="{y + 3:.1f}" fill="#64748b" font-size="10" text-anchor="end">{int(t)}</text>')
        t += tick_step

    for i, m in enumerate(plan_mondays):
        if is_cur(m):
            x0 = pad_l + band * i
            p.append(f'<rect x="{x0:.1f}" y="{pad_t}" width="{band:.1f}" height="{plot_h}" fill="#3b82f6" opacity="0.08"/>')

    for i, v in enumerate(observed):
        if not v:
            continue
        x = xc(i) - bar_w / 2
        y = yv(v)
        h = (pad_t + plot_h) - y
        tip = _esc(f"Week {i + 1} ({WEEKLY_PLAN[i][1]}) — {v:.1f} km run")
        p.append(f'<rect class="vbar" data-tip="{tip}" x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="2" fill="#3b82f6"></rect>')
        p.append(f'<text x="{xc(i):.1f}" y="{y - 3:.1f}" fill="#93c5fd" font-size="8.5" text-anchor="middle" pointer-events="none">{v:.1f}</text>')

    tpts = " ".join(f"{xc(i):.1f},{yv(targets[i]):.1f}" for i in range(n))
    p.append(f'<polyline points="{tpts}" fill="none" stroke="#22c55e" stroke-width="2" stroke-dasharray="5 4"/>')
    for i in range(n):
        cx, cy = xc(i), yv(targets[i])
        tip = _esc(f"Week {i + 1} ({WEEKLY_PLAN[i][1]}) — target {targets[i]:.0f} km")
        p.append(f'<rect class="vbar" data-tip="{tip}" x="{cx - 3:.1f}" y="{cy - 3:.1f}" width="6" height="6" transform="rotate(45 {cx:.1f} {cy:.1f})" fill="#22c55e"></rect>')
        p.append(f'<text x="{cx:.1f}" y="{cy - 9:.1f}" fill="#86efac" font-size="8" text-anchor="middle" pointer-events="none">{targets[i]:.0f}</text>')

    rpts = [(xc(i), yv(rolling[i]), i, rolling[i]) for i in range(n) if rolling[i]]
    if len(rpts) >= 2:
        rp = " ".join(f"{x:.1f},{y:.1f}" for x, y, _, __ in rpts)
        p.append(f'<polyline points="{rp}" fill="none" stroke="#f97316" stroke-width="2"/>')
    for x, y, ri, rv in rpts:
        rtip = _esc(f"Week {ri + 1} ({WEEKLY_PLAN[ri][1]}) — 4w avg {rv:.1f} km")
        p.append(f'<circle class="vbar" data-tip="{rtip}" cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#f97316" stroke="#0f172a" stroke-width="1.2"></circle>')

    rx = xc(n - 1)
    p.append(f'<line x1="{rx:.1f}" y1="{pad_t}" x2="{rx:.1f}" y2="{pad_t + plot_h}" stroke="#ef4444" stroke-width="1.5" stroke-dasharray="2 3"/>')
    p.append(f'<text x="{rx:.1f}" y="{pad_t - 5}" fill="#ef4444" font-size="11" text-anchor="end">\U0001F3C1</text>')

    for i, m in enumerate(plan_mondays):
        col = "#94a3b8" if is_cur(m) else "#475569"
        p.append(f'<text x="{xc(i):.1f}" y="{H - pad_b + 16}" fill="{col}" font-size="9" text-anchor="middle">W{i + 1}</text>')
    for i in range(0, n, 2):
        p.append(f'<text x="{xc(i):.1f}" y="{H - pad_b + 30}" fill="#64748b" font-size="9" text-anchor="middle">{WEEKLY_PLAN[i][1]}</text>')

    p.append('</svg>')
    legend = (
        '<div class="chart-legend">'
        '<span class="ci"><span class="sw" style="background:#3b82f6"></span>Observed km</span>'
        '<span class="ci"><span class="sw" style="background:#f97316"></span>4-week avg</span>'
        '<span class="ci"><span class="sw" style="background:#22c55e"></span>Plan target</span>'
        '</div>'
    )
    return '<div class="chart-wrap">' + "".join(p) + legend + '</div>'


def build_pace_chart(weekly, runs):
    runs_sorted = runs.sort_values('Date')
    dates = list(pd.to_datetime(runs_sorted['Date']))
    paces = [float(p) for p in runs_sorted['pace']]
    pstrs = list(runs_sorted['pace_str'])
    names = list(runs_sorted['Name']) if 'Name' in runs_sorted else [''] * len(dates)
    if not dates:
        return '<div class="text-muted small">No sessions yet.</div>'

    dmin, dmax = min(dates), max(dates)
    span_days = max((dmax - dmin).days, 1)
    pall = paces + [TARGET_PACE_MIN_KM, SAFE_PACE_MIN_KM]
    pmin, pmax = min(pall), max(pall)
    pspan = max(pmax - pmin, 0.2)
    pmin_d = pmin - pspan * 0.12
    pmax_d = pmax + pspan * 0.12

    W, H = 920, 340
    pad_l, pad_r, pad_t, pad_b = 46, 14, 18, 40
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b

    def xd(d):
        return pad_l + plot_w * ((pd.Timestamp(d) - dmin).days / span_days)

    def yp(v):
        return pad_t + plot_h * ((v - pmin_d) / (pmax_d - pmin_d))

    p = [f'<svg viewBox="0 0 {W} {H}" class="chart-svg" preserveAspectRatio="xMidYMid meet" role="img">']

    nticks = 4
    for k in range(nticks + 1):
        v = pmin_d + (pmax_d - pmin_d) * k / nticks
        y = yp(v)
        p.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W - pad_r}" y2="{y:.1f}" stroke="#1e293b"/>')
        p.append(f'<text x="{pad_l - 6}" y="{y + 3:.1f}" fill="#64748b" font-size="10" text-anchor="end">{_fmt_pace_clock(v)}</text>')

    for mdt in pd.date_range(dmin.normalize(), dmax.normalize(), freq='MS'):
        if mdt < dmin:
            continue
        x = xd(mdt)
        p.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{pad_t + plot_h}" stroke="#1e293b"/>')
        p.append(f'<text x="{x:.1f}" y="{H - pad_b + 16}" fill="#64748b" font-size="10" text-anchor="middle">{mdt.strftime("%b")}</text>')

    # Stretch target line (sub 2:15)
    ty = yp(TARGET_PACE_MIN_KM)
    p.append(f'<line x1="{pad_l}" y1="{ty:.1f}" x2="{W - pad_r}" y2="{ty:.1f}" stroke="#22c55e" stroke-width="1.5" stroke-dasharray="5 4"/>')
    p.append(f'<text x="{W - pad_r}" y="{ty - 5:.1f}" fill="#22c55e" font-size="10" text-anchor="end">Stretch 6:23 (sub 2:15)</text>')

    # Safe target line (sub 2:30)
    sy = yp(SAFE_PACE_MIN_KM)
    p.append(f'<line x1="{pad_l}" y1="{sy:.1f}" x2="{W - pad_r}" y2="{sy:.1f}" stroke="#fbbf24" stroke-width="1.5" stroke-dasharray="5 4"/>')
    p.append(f'<text x="{W - pad_r}" y="{sy - 5:.1f}" fill="#fbbf24" font-size="10" text-anchor="end">Target 7:06 (sub 2:30)</text>')

    wk_dates = list(pd.to_datetime(weekly['week_start']))
    wk_pace = [float(v) if pd.notna(v) else None for v in weekly['rolling_pace_4w']]
    rpts = [(xd(d), yp(v)) for d, v in zip(wk_dates, wk_pace) if v and dmin <= d <= dmax]
    if len(rpts) >= 2:
        rp = " ".join(f"{x:.1f},{y:.1f}" for x, y in rpts)
        p.append(f'<polyline points="{rp}" fill="none" stroke="#ec4899" stroke-width="2"/>')

    for d, pc, ps, nm in zip(dates, paces, pstrs, names):
        x, y = xd(d), yp(pc)
        tip = _esc(f"{pd.Timestamp(d).strftime('%a %b %d')} · {ps}/km — {nm}")
        p.append(f'<circle class="pdot" data-tip="{tip}" cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="#818cf8" stroke="#0f172a" stroke-width="1.2"></circle>')

    p.append('</svg>')
    legend = (
        '<div class="chart-legend">'
        '<span class="ci"><span class="sw" style="background:#818cf8;width:10px;height:10px;border-radius:50%"></span>Session pace</span>'
        '<span class="ci"><span class="sw" style="background:#ec4899"></span>4-week avg</span>'
        '<span class="ci"><span class="sw" style="background:#22c55e"></span>Stretch (sub 2:15)</span>'
        '<span class="ci"><span class="sw" style="background:#fbbf24"></span>Target (sub 2:30)</span>'
        '</div>'
    )
    return '<div class="chart-wrap">' + "".join(p) + legend + '</div>'


FORM_BANDS = [
    (25, float('inf'), 'Very Fresh (detraining risk)', '#38bdf8'),
    (5, 25, 'Fresh / Race Ready', '#22c55e'),
    (-10, 5, 'Neutral', '#94a3b8'),
    (-30, -10, 'Optimal Training', '#f59e0b'),
    (float('-inf'), -30, 'High Risk — overreaching', '#ef4444'),
]


def _form_status(value):
    for lo, hi, label, color in FORM_BANDS:
        if lo <= value < hi:
            return label, color
    return 'Neutral', '#94a3b8'


def build_load_chart(wellness, plan_start):
    df = wellness[wellness['date'] >= pd.Timestamp(plan_start)]
    if df.empty:
        return '<div class="text-muted small">No wellness data synced yet.</div>'

    dates = list(df['date'])
    ctl, atl, form = list(df['ctl']), list(df['atl']), list(df['form'])
    dmin, dmax = min(dates), max(dates)
    span_days = max((dmax - dmin).days, 1)

    vmax = max(max(ctl), max(atl)) * 1.15
    tick_step = 20 if vmax > 60 else (10 if vmax > 25 else 5)
    y_top = max((int(vmax // tick_step) + 1) * tick_step, tick_step)

    W, H = 920, 280
    pad_l, pad_r, pad_t, pad_b = 38, 14, 18, 40
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b

    def xd(d):
        return pad_l + plot_w * ((pd.Timestamp(d) - dmin).days / span_days)

    def yv(v):
        return pad_t + plot_h * (1 - v / y_top)

    p = [f'<svg viewBox="0 0 {W} {H}" class="chart-svg" preserveAspectRatio="xMidYMid meet" role="img">']

    t = 0
    while t <= y_top + 0.1:
        y = yv(t)
        p.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W - pad_r}" y2="{y:.1f}" stroke="#1e293b"/>')
        p.append(f'<text x="{pad_l - 6}" y="{y + 3:.1f}" fill="#64748b" font-size="10" text-anchor="end">{int(t)}</text>')
        t += tick_step

    for mdt in pd.date_range(dmin.normalize(), dmax.normalize(), freq='MS'):
        if mdt < dmin:
            continue
        x = xd(mdt)
        p.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{pad_t + plot_h}" stroke="#1e293b"/>')
        p.append(f'<text x="{x:.1f}" y="{H - pad_b + 16}" fill="#64748b" font-size="10" text-anchor="middle">{mdt.strftime("%b")}</text>')

    def line(vals, color, width=2):
        pts = " ".join(f"{xd(d):.1f},{yv(v):.1f}" for d, v in zip(dates, vals))
        p.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{width}"/>')

    line(atl, '#f97316')
    line(ctl, '#3b82f6', 2.5)

    for d, c, a, f in zip(dates, ctl, atl, form):
        if pd.Timestamp(d).weekday() != 0:
            continue
        label, _ = _form_status(f)
        tip = _esc(f"{pd.Timestamp(d).strftime('%a %b %d')} — Fitness {c:.0f} · Fatigue {a:.0f} · Form {f:+.0f} ({label})")
        p.append(f'<circle class="pdot" data-tip="{tip}" cx="{xd(d):.1f}" cy="{yv(c):.1f}" r="3.5" fill="#3b82f6" stroke="#0f172a" stroke-width="1"></circle>')

    p.append('</svg>')
    legend = (
        '<div class="chart-legend">'
        '<span class="ci"><span class="sw" style="background:#3b82f6"></span>CTL (Fitness)</span>'
        '<span class="ci"><span class="sw" style="background:#f97316"></span>ATL (Fatigue)</span>'
        '</div>'
    )
    ctl_atl_html = '<div class="chart-wrap">' + "".join(p) + legend + '</div>'

    ymin_d = min(-40, min(form) - 5)
    ymax_d = max(30, max(form) + 5)

    H2 = 220
    plot_h2 = H2 - pad_t - pad_b

    def yv2(v):
        v = max(min(v, ymax_d), ymin_d)
        return pad_t + plot_h2 * (1 - (v - ymin_d) / (ymax_d - ymin_d))

    p2 = [f'<svg viewBox="0 0 {W} {H2}" class="chart-svg" preserveAspectRatio="xMidYMid meet" role="img">']
    for lo, hi, label, color in FORM_BANDS:
        y_top_b = yv2(min(hi, ymax_d))
        y_bot_b = yv2(max(lo, ymin_d))
        p2.append(f'<rect x="{pad_l}" y="{y_top_b:.1f}" width="{plot_w}" height="{(y_bot_b - y_top_b):.1f}" fill="{color}" opacity="0.13"/>')
        p2.append(f'<text x="{W - pad_r - 4}" y="{y_top_b + 11:.1f}" fill="{color}" font-size="9" text-anchor="end" opacity="0.9">{_esc(label)}</text>')

    zero_y = yv2(0)
    p2.append(f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{W - pad_r}" y2="{zero_y:.1f}" stroke="#475569" stroke-width="1" stroke-dasharray="3 3"/>')

    for mdt in pd.date_range(dmin.normalize(), dmax.normalize(), freq='MS'):
        if mdt < dmin:
            continue
        x = xd(mdt)
        p2.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{pad_t + plot_h2}" stroke="#1e293b"/>')
        p2.append(f'<text x="{x:.1f}" y="{H2 - pad_b + 16}" fill="#64748b" font-size="10" text-anchor="middle">{mdt.strftime("%b")}</text>')

    fpts = " ".join(f"{xd(d):.1f},{yv2(v):.1f}" for d, v in zip(dates, form))
    p2.append(f'<polyline points="{fpts}" fill="none" stroke="#e2e8f0" stroke-width="2"/>')

    for d, f in zip(dates, form):
        if pd.Timestamp(d).weekday() != 0:
            continue
        label, color = _form_status(f)
        tip = _esc(f"{pd.Timestamp(d).strftime('%a %b %d')} — Form {f:+.0f} ({label})")
        p2.append(f'<circle class="pdot" data-tip="{tip}" cx="{xd(d):.1f}" cy="{yv2(f):.1f}" r="4" fill="{color}" stroke="#0f172a" stroke-width="1"></circle>')

    p2.append('</svg>')
    form_html = '<div class="chart-wrap">' + "".join(p2) + '</div>'

    return (
        ctl_atl_html
        + '<div class="text-muted small mt-3 mb-1">Form (freshness) — banded by the standard TrainingPeaks/Joe Friel TSB guideline (a heuristic, not personalized medical advice):</div>'
        + form_html
    )


def _mini_line_chart(dist_km, ys, color, fmt, invert=False, W=880, H=120):
    pairs = [(d, y) for d, y in zip(dist_km, ys) if y is not None]
    if len(pairs) < 2:
        return '<div class="text-muted small">Not enough data.</div>'
    xs, ys = zip(*pairs)
    xmin, xmax = min(xs), max(xs)
    xspan = max(xmax - xmin, 0.01)
    ymin, ymax = min(ys), max(ys)
    yspan = max(ymax - ymin, 0.01) * 1.2
    ymid = (ymax + ymin) / 2
    ymin_d, ymax_d = ymid - yspan / 2, ymid + yspan / 2

    pad_l, pad_r, pad_t, pad_b = 40, 10, 10, 20
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b

    def xd(x):
        return pad_l + plot_w * (x - xmin) / xspan

    def yv(y):
        frac = (y - ymin_d) / (ymax_d - ymin_d)
        return pad_t + plot_h * (frac if invert else (1 - frac))

    p = [f'<svg viewBox="0 0 {W} {H}" class="chart-svg" preserveAspectRatio="xMidYMid meet" role="img">']
    for frac in (0, 0.5, 1):
        v = ymin + (ymax - ymin) * frac
        y = yv(v)
        p.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W - pad_r}" y2="{y:.1f}" stroke="#1e293b"/>')
        p.append(f'<text x="{pad_l - 6}" y="{y + 3:.1f}" fill="#64748b" font-size="9" text-anchor="end">{fmt(v)}</text>')
    pts = " ".join(f"{xd(x):.1f},{yv(y):.1f}" for x, y in zip(xs, ys))
    p.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>')
    for km in range(0, int(xmax) + 1, max(1, int(xmax) // 6 or 1)):
        x = xd(km)
        p.append(f'<text x="{x:.1f}" y="{H - pad_b + 14}" fill="#64748b" font-size="9" text-anchor="middle">{km}k</text>')
    p.append('</svg>')
    return '<div class="chart-wrap" style="margin-bottom:.4rem">' + "".join(p) + '</div>'


def build_run_curve_html(curve):
    dist_km = curve.get('distance_km') or []
    hr = curve.get('hr') or []
    pace = curve.get('pace') or []
    hr_html = _mini_line_chart(dist_km, hr, '#ef4444', lambda v: f'{int(v)}')
    pace_html = _mini_line_chart(dist_km, pace, '#60a5fa', _fmt_pace_clock, invert=True)
    return (
        '<div style="padding:.6rem .2rem">'
        '<div style="font-size:.68rem;color:#ef4444;font-weight:700;margin-bottom:.2rem">HEART RATE (bpm)</div>'
        f'{hr_html}'
        '<div style="font-size:.68rem;color:#60a5fa;font-weight:700;margin:.5rem 0 .2rem">PACE (min/km)</div>'
        f'{pace_html}'
        '</div>'
    )


def build_plan_gantt(targets):
    today = date.today()
    max_t = max(targets) if targets else 1

    seen = []
    for row in WEEKLY_PLAN:
        if row[5] not in seen:
            seen.append(row[5])
    legend_chips = "".join(
        f'<span class="ci"><span class="sw" style="background:{PHASE_COLORS.get(ph, "#94a3b8")};width:12px;height:12px;border-radius:3px"></span>{_esc(ph)}</span>'
        for ph in seen
    )

    rows = []
    for wnum, wdate_str, total_km, long_km, quality, phase in WEEKLY_PLAN:
        w_start = PLAN_START + timedelta(weeks=wnum - 1)
        w_end = w_start + timedelta(days=6)
        is_current = w_start <= today <= w_end
        is_past = w_end < today
        tk = targets[wnum - 1] if wnum - 1 < len(targets) else total_km
        color = PHASE_COLORS.get(phase, '#94a3b8')
        pct = max(tk / max_t * 100, 2)
        opacity = '1' if is_current else ('0.5' if is_past else '0.92')
        ring = 'box-shadow:0 0 0 2px #3b82f6;' if is_current else ''
        tip = _esc(f"Week {wnum} ({wdate_str}) · {phase} · target {tk:.0f} km · long {long_km} km — {quality}")
        rows.append(
            f'<div class="gbar" data-tip="{tip}" style="display:flex;align-items:center;gap:.6rem;margin-bottom:.34rem;opacity:{opacity}">'
            f'<div style="flex:0 0 86px;font-size:.7rem;color:#94a3b8">W{wnum}<span style="color:#475569"> · {wdate_str}</span></div>'
            f'<div style="flex:1;background:#0f172a;border-radius:6px;height:22px;position:relative;{ring}">'
            f'<div class="gbar-fill" style="width:{pct:.1f}%;height:100%;background:{color};border-radius:6px"></div>'
            f'<span style="position:absolute;right:8px;top:0;line-height:22px;font-size:.68rem;color:#e2e8f0">{tk:.0f} km</span>'
            f'</div>'
            f'</div>'
        )

    return (
        '<div class="chart-wrap">'
        f'<div class="chart-legend" style="margin:0 0 .8rem">{legend_chips}</div>'
        + "".join(rows) +
        '</div>'
    )


# ── HTML helpers ─────────────────────────────────────────────────────────────

def _week_calendar_html(week_entry, targets, today, plan_days=None):
    wnum, wdate_str, orig_km, long_km, quality, phase = week_entry

    parsed = _parse_day_sessions(quality)

    target_km_val = targets[wnum - 1] if wnum - 1 < len(targets) else orig_km

    _default_icons = {
        'Strength': '\U0001F4AA', 'Rest': '\U0001F4A4', 'Run': '\U0001F3C3',
        'Long': '\U0001F4CF', 'Race': '\U0001F3C1', 'Bike': '\U0001F6B4',
    }

    def _icon_for(desc):
        dl = desc.lower()
        if 'race' in dl or '\U0001F3C1' in desc:
            return '\U0001F3C1'
        if 'long' in dl:
            return '\U0001F4CF'
        if 'strength' in dl:
            return '\U0001F4AA'
        if 'bike' in dl:
            return '\U0001F6B4'
        if 'rest' in dl:
            return '\U0001F4A4'
        return '\U0001F3C3'

    days_info = []
    for dk in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']:
        session = parsed.get(dk, 'Rest')
        icon = _icon_for(session)
        days_info.append((dk, session, icon))

    week_monday = today - timedelta(days=today.weekday())
    phase_color = PHASE_COLORS.get(phase, '#94a3b8')
    plan_day_map = {d['day']: d for d in plan_days} if plan_days else {}
    _icon_map = {'rest': '\U0001F4A4', 'easy': '\U0001F3C3', 'quality': '⚡', 'long': '\U0001F4CF', 'race': '\U0001F3C1'}

    cards = ''
    for i, (short, session, icon) in enumerate(days_info):
        day_date = week_monday + timedelta(days=i)

        actual_km = None
        session_type_str = None
        if short in plan_day_map:
            day_entry = plan_day_map[short]
            session = day_entry.get('planned', session)
            icon = _icon_map.get(day_entry.get('session_type', 'easy'), icon)
            actual_km = day_entry.get('actual_km')
            session_type_str = day_entry.get('session_type')
        session = _annotate_day_desc(session)

        is_today = (day_date == today)
        is_past = day_date < today

        if is_today:
            card_bg, card_border = '#1e3a5f', '2px solid #3b82f6'
            day_color, date_color, text_color, opacity = '#60a5fa', '#93c5fd', '#f1f5f9', '1'
        elif is_past:
            card_bg, card_border = '#0a0f1a', '1px solid #1e293b'
            day_color, date_color, text_color, opacity = '#334155', '#334155', '#475569', '0.55'
        else:
            card_bg, card_border = '#0f172a', '1px solid #334155'
            day_color, date_color, text_color, opacity = '#64748b', '#475569', '#94a3b8', '1'

        today_dot = (
            '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
            'background:#3b82f6;margin-left:5px;vertical-align:middle"></span>'
        ) if is_today else ''

        actual_html = ''
        if actual_km is not None and actual_km > 0:
            pace_val = plan_day_map[short].get('actual_pace_min_km') if short in plan_day_map else None
            hr_val = plan_day_map[short].get('actual_hr') if short in plan_day_map else None
            act_name = plan_day_map[short].get('actual_name') if short in plan_day_map else None
            pace_str = f"{int(pace_val)}:{int((pace_val % 1)*60):02d}/km" if pace_val else ''
            hr_str = f'{int(hr_val)} bpm' if hr_val else ''
            metrics = ' · '.join(filter(None, [f'{actual_km:.1f} km', pace_str, hr_str]))
            name_html = (
                f'<div style="font-size:.6rem;color:#64748b;margin-top:.1rem">{act_name}</div>'
            ) if act_name else ''
            actual_html = (
                f'<div style="margin-top:.45rem;padding:.35rem .4rem;border-radius:6px;'
                f'background:rgba(34,197,94,.12);border:1px solid rgba(34,197,94,.25)">'
                f'<div style="font-size:.6rem;font-weight:700;color:#22c55e;'
                f'letter-spacing:.04em;margin-bottom:.15rem">✓ DONE</div>'
                f'<div style="font-size:.68rem;color:#86efac;line-height:1.5">{metrics}</div>'
                f'{name_html}'
                f'</div>'
            )
        elif is_past and session_type_str not in ('rest', None):
            actual_html = (
                '<div style="margin-top:.45rem;padding:.3rem .4rem;border-radius:6px;'
                'background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.2)">'
                '<div style="font-size:.6rem;font-weight:700;color:#ef4444">✗ NOT DONE</div>'
                '</div>'
            )

        cards += (
            f'<div style="flex:1;min-width:110px;border-radius:10px;padding:.75rem .65rem;'
            f'background:{card_bg};border:{card_border};opacity:{opacity}">'
            f'<div style="font-size:.68rem;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:.07em;color:{day_color};margin-bottom:.15rem">{short}{today_dot}</div>'
            f'<div style="font-size:.65rem;color:{date_color};margin-bottom:.5rem">{day_date.strftime("%b %d")}</div>'
            f'<div style="font-size:1rem;margin-bottom:.3rem">{icon}</div>'
            f'<div style="font-size:.75rem;color:{text_color};line-height:1.4">{session}</div>'
            f'{actual_html}'
            f'</div>'
        )

    return (
        f'<div style="background:#1e293b;border:1px solid #334155;border-radius:14px;'
        f'padding:1.25rem;margin-bottom:1.5rem">'
        f'<div style="font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;'
        f'color:#475569;margin-bottom:.85rem">'
        f'Week {wnum} &nbsp;·&nbsp; {wdate_str} &nbsp;·&nbsp; '
        f'<span style="color:{phase_color}">{phase}</span> &nbsp;·&nbsp; '
        f'Target: <span style="color:#f1f5f9">{target_km_val:.0f} km</span>'
        f'</div>'
        f'<div style="display:flex;gap:.5rem;flex-wrap:wrap">{cards}</div>'
        f'</div>'
    )


def _recent_runs_table(runs, n=10):
    if runs.empty:
        return '<div class="text-muted small">No runs recorded yet.</div>'
    run_curves = _load_run_curves()
    recent = runs.tail(n)[['id', 'Date', 'Name', 'distance_km', 'pace_str', 'avg_hr']].copy()
    recent['Date'] = recent['Date'].dt.strftime('%a %b %d')
    recent['distance_km'] = recent['distance_km'].apply(lambda x: f'{x:.2f} km')
    recent['avg_hr'] = recent['avg_hr'].apply(lambda x: f'{x:.0f} bpm' if pd.notna(x) else '—')

    rows = ''
    for row in recent.iloc[::-1].itertuples(index=False):
        activity_id, r_date, r_name, r_dist, r_pace, r_hr = row
        curve = run_curves.get(str(activity_id))
        name_cell = r_name
        extra_row = ''
        if curve:
            name_cell += (
                f' <button type="button" class="btn btn-sm btn-outline-info py-0 px-1" '
                f'style="font-size:.65rem;line-height:1.4" onclick="toggleCurve(\'{activity_id}\')">\U0001F4C8</button>'
            )
            extra_row = (
                f'<tr id="curve-{activity_id}" style="display:none">'
                f'<td colspan="5">{build_run_curve_html(curve)}</td></tr>'
            )
        rows += (
            f'<tr><td>{r_date}</td><td>{name_cell}</td><td>{r_dist}</td>'
            f'<td>{r_pace}</td><td>{r_hr}</td></tr>{extra_row}'
        )

    headers = ''.join(f'<th>{c}</th>' for c in ['Date', 'Name', 'Distance', 'Pace', 'Avg HR'])
    return (
        '<div class="table-responsive">'
        f'<table class="table table-sm table-striped table-hover align-middle mb-0">'
        f'<thead class="table-dark"><tr>{headers}</tr></thead>'
        f'<tbody>{rows}</tbody></table></div>'
    )


def _weekly_plan_table(targets):
    today = date.today()
    plan_start_d = PLAN_START
    rows = ''
    for wnum, wdate_str, orig_km, long_km, quality, phase in WEEKLY_PLAN:
        w_start = plan_start_d + timedelta(weeks=wnum - 1)
        w_end = w_start + timedelta(days=6)
        is_current = w_start <= today <= w_end
        is_past = w_end < today
        target_km = targets[wnum - 1] if wnum - 1 < len(targets) else orig_km
        color = PHASE_COLORS.get(phase, '#94a3b8')
        row_class = 'table-primary fw-bold' if is_current else ('text-muted' if is_past else '')
        badge = (
            '<span class="badge bg-primary ms-1">Current</span>' if is_current
            else ('<span class="badge bg-secondary ms-1">Done</span>' if is_past else '')
        )
        rows += (
            f'<tr class="{row_class}">'
            f'<td><span class="badge" style="background:{color}">{phase}</span></td>'
            f'<td>W{wnum} · {wdate_str}{badge}</td>'
            f'<td>{target_km:.0f} km</td>'
            f'<td>{long_km} km</td>'
            f'<td class="text-muted small">{_annotate_quality(quality)}</td>'
            f'</tr>'
        )
    return (
        '<div class="table-responsive">'
        '<table class="table table-sm table-hover align-middle mb-0">'
        '<thead class="table-dark"><tr>'
        '<th>Phase</th><th>Week</th><th>Target</th><th>Long run</th><th>Daily sessions</th>'
        '</tr></thead>'
        f'<tbody>{rows}</tbody></table></div>'
    )


def build_weekly_accumulation_chart(runs, current_week_entry, targets):
    wnum, wdate_str, total_km, long_km, quality, phase = current_week_entry
    today_d = date.today()
    week_monday = today_d - timedelta(days=today_d.weekday())
    DAY_KEYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

    parsed = _parse_day_sessions(quality)
    planned_daily = [_day_planned_km(parsed.get(dk, 'Rest')) for dk in DAY_KEYS]

    actual_daily = [0.0] * 7
    for _, row in runs.iterrows():
        rd = row['Date'].date()
        wd = (rd - week_monday).days
        if 0 <= wd <= 6:
            actual_daily[wd] += float(row['distance_km'])

    planned_cum, actual_cum = [], []
    ps = as_ = 0.0
    for i in range(7):
        ps += planned_daily[i]
        as_ += actual_daily[i]
        planned_cum.append(ps)
        actual_cum.append(as_)

    target_total = targets[wnum - 1] if wnum - 1 < len(targets) else planned_cum[-1]
    today_idx = min((today_d - week_monday).days, 6)

    W, H = 920, 290
    pad_l, pad_r, pad_t, pad_b = 46, 14, 28, 48
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b

    vmax = max(max(planned_cum), max(actual_cum), target_total) * 1.18
    tick_step = 10 if vmax > 20 else 5
    y_top = max((int(vmax // tick_step) + 1) * tick_step, tick_step)

    def yv(v): return pad_t + plot_h * (1 - v / y_top)
    def xd(i): return pad_l + plot_w * i / 6

    p = [f'<svg viewBox="0 0 {W} {H}" class="chart-svg" preserveAspectRatio="xMidYMid meet" role="img">']

    t = 0
    while t <= y_top + 0.1:
        yg = yv(t)
        p.append(f'<line x1="{pad_l}" y1="{yg:.1f}" x2="{W - pad_r}" y2="{yg:.1f}" stroke="#1e293b"/>')
        p.append(f'<text x="{pad_l - 6}" y="{yg + 3:.1f}" fill="#64748b" font-size="10" text-anchor="end">{int(t)}</text>')
        t += tick_step

    if 0 <= today_idx <= 6:
        tx = xd(today_idx)
        p.append(f'<line x1="{tx:.1f}" y1="{pad_t}" x2="{tx:.1f}" y2="{pad_t + plot_h}" stroke="#3b82f6" stroke-width="1" stroke-dasharray="3 3" opacity="0.5"/>')

    ty = yv(target_total)
    p.append(f'<line x1="{pad_l}" y1="{ty:.1f}" x2="{W - pad_r}" y2="{ty:.1f}" stroke="#22c55e" stroke-width="1.5" stroke-dasharray="5 4"/>')
    p.append(f'<text x="{W - pad_r}" y="{ty - 5:.1f}" fill="#22c55e" font-size="10" text-anchor="end">Target {target_total:.0f} km</text>')

    plan_pts = " ".join(f"{xd(i):.1f},{yv(planned_cum[i]):.1f}" for i in range(7))
    p.append(f'<polyline points="{plan_pts}" fill="none" stroke="#3b82f6" stroke-width="2" stroke-dasharray="6 3"/>')

    act_end = min(today_idx + 1, 7)
    if act_end >= 2:
        ap = " ".join(f"{xd(i):.1f},{yv(actual_cum[i]):.1f}" for i in range(act_end))
        p.append(f'<polyline points="{ap}" fill="none" stroke="#22c55e" stroke-width="2.5"/>')

    for i in range(7):
        x, y = xd(i), yv(planned_cum[i])
        tip_txt = _esc(f"{DAY_KEYS[i]} — planned cumulative: {planned_cum[i]:.1f} km")
        p.append(f'<circle class="pdot" data-tip="{tip_txt}" cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#3b82f6" stroke="#0f172a" stroke-width="1.2"></circle>')
        p.append(f'<text x="{x:.1f}" y="{y - 7:.1f}" fill="#93c5fd" font-size="9" text-anchor="middle" pointer-events="none">{planned_cum[i]:.1f}</text>')

    for i in range(act_end):
        x, y = xd(i), yv(actual_cum[i])
        tip_txt = _esc(f"{DAY_KEYS[i]} — actual cumulative: {actual_cum[i]:.1f} km")
        p.append(f'<circle class="pdot" data-tip="{tip_txt}" cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#22c55e" stroke="#0f172a" stroke-width="1.5"></circle>')
        p.append(f'<text x="{x:.1f}" y="{y + 16:.1f}" fill="#86efac" font-size="9" text-anchor="middle" pointer-events="none">{actual_cum[i]:.1f}</text>')

    for i, dk in enumerate(DAY_KEYS):
        day_d = week_monday + timedelta(days=i)
        is_tod = (i == today_idx)
        col = '#60a5fa' if is_tod else '#64748b'
        fw = 'bold' if is_tod else 'normal'
        p.append(f'<text x="{xd(i):.1f}" y="{H - pad_b + 16}" fill="{col}" font-size="10" font-weight="{fw}" text-anchor="middle">{dk}</text>')
        p.append(f'<text x="{xd(i):.1f}" y="{H - pad_b + 30}" fill="#475569" font-size="9" text-anchor="middle">{day_d.strftime("%b %d")}</text>')

    p.append('</svg>')
    legend = (
        '<div class="chart-legend">'
        '<span class="ci"><span style="display:inline-block;width:22px;height:0;border-top:2px dashed #3b82f6;vertical-align:middle;margin-right:.3rem"></span>Planned cumulative</span>'
        '<span class="ci"><span class="sw" style="background:#22c55e"></span>Actual cumulative</span>'
        '<span class="ci"><span style="display:inline-block;width:22px;height:0;border-top:2px dashed #22c55e;vertical-align:middle;margin-right:.3rem"></span>Week target</span>'
        '</div>'
    )
    return '<div class="chart-wrap">' + "".join(p) + legend + '</div>'


# ── Dashboard assembly ────────────────────────────────────────────

def build_dashboard(runs, weekly, targets):
    today = date.today()
    plan_start_ts = pd.Timestamp(PLAN_START)
    race_ts = pd.Timestamp(RACE_DATE)

    days_to_race = (race_ts - pd.Timestamp(today)).days
    weeks_to_race = days_to_race // 7
    current_week_num = ((today - PLAN_START).days // 7) + 1
    current_week_num = max(1, min(current_week_num, NUM_WEEKS))

    four_week_avg = float(weekly['total_km'].tail(4).mean()) if not weekly.empty else 0.0
    this_week_monday = pd.Timestamp(today - timedelta(days=today.weekday()))
    last_week_monday = this_week_monday - pd.Timedelta(weeks=1)
    weekly_by_monday = weekly.set_index('week_start')

    def _week_metric(monday, col, default):
        return weekly_by_monday.loc[monday, col] if monday in weekly_by_monday.index else default

    this_week_km = float(_week_metric(this_week_monday, 'total_km', 0.0))
    last_week_km = float(_week_metric(last_week_monday, 'total_km', 0.0))
    last_week_pace_str = _week_metric(last_week_monday, 'avg_pace_str', '—') or '—'
    longest_run = float(runs['distance_km'].max()) if not runs.empty else 0.0
    last_date = runs['Date'].max().strftime('%b %d, %Y') if not runs.empty else 'N/A'
    total_runs = len(runs)
    current_phase = WEEKLY_PLAN[current_week_num - 1][5] if current_week_num <= NUM_WEEKS else '—'
    current_week_entry = WEEKLY_PLAN[current_week_num - 1]
    _plan_data = _load_plan()
    _plan_days = None
    if _plan_data:
        for _wk in _plan_data.get('weeks', []):
            if _wk['week'] == current_week_num:
                _plan_days = _wk['days']
                break
    calendar_html = _week_calendar_html(current_week_entry, targets, today, plan_days=_plan_days)

    last_week_num = ((last_week_monday.date() - PLAN_START).days // 7) + 1
    last_week_target = targets[last_week_num - 1] if 0 <= last_week_num - 1 < len(targets) else 0
    load_status = ''
    load_badge = ''
    if last_week_target and last_week_km < last_week_target * 0.80:
        load_status = 'Underloaded'
        load_badge = 'bg-warning text-dark'
    elif last_week_target and last_week_km > last_week_target * 1.10:
        load_status = 'Overloaded ⚠️'
        load_badge = 'bg-danger'
    else:
        load_status = 'On track ✓'
        load_badge = 'bg-success'

    vol_html = build_volume_chart(weekly, targets, plan_start_ts)
    pace_html = build_pace_chart(weekly, runs)
    wellness_df = load_wellness(WELLNESS_CSV)
    load_html = build_load_chart(wellness_df, PLAN_START)
    if not wellness_df.empty:
        latest_form = float(wellness_df['form'].iloc[-1])
        form_label, form_color = _form_status(latest_form)
        form_badge_html = (
            f'<span class="badge fs-6 px-3 py-2 ms-2" style="background:{form_color};color:#0f172a">'
            f'Fatigue: {latest_form:+.0f} · {form_label}</span>'
        )
    else:
        form_badge_html = ''
    gantt_html = build_plan_gantt(targets)
    recent_table = _recent_runs_table(runs)
    plan_table = _weekly_plan_table(targets)
    week_accum_html = build_weekly_accumulation_chart(runs, current_week_entry, targets)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Munich Half Marathon 2026 — Training Dashboard</title>
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="HM 2026">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
  <style>
    body {{
      background: #0f172a;
      font-family: 'Inter', system-ui, sans-serif;
      color: #e2e8f0;
      min-height: 100vh;
    }}
    .hero {{
      background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 60%, #1d4ed8 100%);
      border-bottom: 1px solid rgba(255,255,255,.08);
      padding: 2.5rem 0 2rem;
      position: relative;
      overflow: hidden;
    }}
    .hero::before {{
      content: '21.1';
      position: absolute; right: -1rem; top: -1rem;
      font-size: 12rem; font-weight: 800; opacity: .04;
      color: white; line-height: 1; pointer-events: none;
    }}
    .hero-title {{ font-size: 2rem; font-weight: 800; letter-spacing: -.02em; }}
    .hero-sub {{ color: #94a3b8; font-size: .9rem; margin-top: .25rem; }}
    .countdown-block {{
      display: flex; gap: 1rem; align-items: flex-end; flex-wrap: wrap;
      margin-top: 1.5rem;
    }}
    .cdown-item {{ text-align: center; }}
    .cdown-num {{
      font-size: 3rem; font-weight: 800; line-height: 1;
      background: linear-gradient(135deg, #60a5fa, #818cf8);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }}
    .cdown-label {{ font-size: .7rem; text-transform: uppercase; letter-spacing: .08em; color: #64748b; }}
    .cdown-sep {{ font-size: 2rem; color: #334155; margin-bottom: .4rem; }}
    .phase-pill {{
      display: inline-block; padding: .3rem .8rem; border-radius: 999px;
      font-size: .8rem; font-weight: 600;
      background: rgba(255,255,255,.1); border: 1px solid rgba(255,255,255,.2);
      color: white;
    }}
    .kpi-card {{
      background: #1e293b; border: 1px solid #334155; border-radius: 14px;
      padding: 1rem 1.25rem; height: 100%;
    }}
    .kpi-value {{ font-size: 1.75rem; font-weight: 800; color: #f1f5f9; line-height: 1.1; }}
    .kpi-label {{ font-size: .72rem; text-transform: uppercase; letter-spacing: .07em; color: #64748b; margin-top: .2rem; }}
    .kpi-icon {{ font-size: 1.5rem; float: right; opacity: .4; }}
    .status-bar {{
      background: #1e293b; border: 1px solid #334155; border-radius: 14px;
      padding: .75rem 1.25rem; display: flex; align-items: center; gap: .75rem;
    }}
    .nav-tabs {{
      border-bottom: 2px solid #1e293b;
      gap: .25rem;
    }}
    .nav-tabs .nav-link {{
      color: #94a3b8; border: none; border-radius: 8px 8px 0 0;
      padding: .65rem 1.25rem; font-weight: 600; font-size: .88rem;
      background: transparent; transition: all .15s;
    }}
    .nav-tabs .nav-link:hover {{ color: #e2e8f0; background: #1e293b; }}
    .nav-tabs .nav-link.active {{
      color: #60a5fa; background: #1e293b;
      border-bottom: 2px solid #3b82f6;
    }}
    .nav-pills .nav-link {{
      color: #94a3b8; font-size: .82rem; font-weight: 600;
      padding: .4rem .9rem; border-radius: 8px; transition: all .15s;
    }}
    .nav-pills .nav-link:hover {{ color: #e2e8f0; background: rgba(255,255,255,.07); }}
    .nav-pills .nav-link.active {{ background: #334155; color: #60a5fa; }}
    .content-card {{
      background: #1e293b; border: 1px solid #334155; border-radius: 0 14px 14px 14px;
      padding: 1.5rem;
    }}
    .section-heading {{
      font-size: .75rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: .1em; color: #475569; margin-bottom: .75rem;
    }}
    .table {{ color: #cbd5e1; --bs-table-striped-bg: rgba(255,255,255,.04); }}
    .table thead.table-dark {{ background: #0f172a; --bs-table-bg: #0f172a; }}
    .table-hover tbody tr:hover {{ background: rgba(255,255,255,.06); color: #f1f5f9; }}
    .strategy-card {{
      background: #0f172a; border: 1px solid #334155; border-radius: 10px;
      padding: 1rem 1.25rem;
    }}
    .strategy-card h6 {{ font-weight: 700; color: #60a5fa; margin-bottom: .5rem; }}
    .strategy-card ul {{ margin: 0; padding-left: 1.25rem; color: #94a3b8; font-size: .88rem; }}
    .pace-zone {{ display: flex; align-items: center; gap: .5rem; font-size: .82rem; margin-bottom: .4rem; }}
    .pace-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
    footer {{ color: #334155; font-size: .78rem; padding: 1.5rem 0; text-align: center; }}
    .chart-wrap {{ width: 100%; }}
    .chart-svg {{ width: 100%; height: auto; display: block; }}
    .vbar {{ transition: filter .12s; cursor: crosshair; }}
    .vbar:hover {{ filter: brightness(1.3) drop-shadow(0 0 3px rgba(255,255,255,.2)); }}
    .pdot {{ transition: filter .12s; cursor: crosshair; }}
    .pdot:hover {{ filter: brightness(1.4) drop-shadow(0 0 5px #818cf8); }}
    .gbar {{ cursor: default; transition: opacity .12s; }}
    .gbar:hover {{ opacity: 1 !important; }}
    .gbar-fill {{ transition: filter .12s; }}
    .gbar:hover .gbar-fill {{ filter: brightness(1.18); }}
    .chart-legend {{ display: flex; gap: 1.1rem; flex-wrap: wrap; margin-top: .6rem;
                     font-size: .76rem; color: #94a3b8; align-items: center; }}
    .chart-legend .ci {{ display: flex; align-items: center; gap: .4rem; }}
    .chart-legend .sw {{ width: 16px; height: 4px; border-radius: 2px; display: inline-block; }}
    #cht {{
      position: fixed; z-index: 9999; display: none; pointer-events: none;
      background: #0f172a; color: #e2e8f0; border: 1px solid #334155;
      border-radius: 9px; padding: .45rem .75rem; font-size: .78rem;
      font-family: 'Inter', system-ui, sans-serif;
      box-shadow: 0 6px 24px rgba(0,0,0,.55); max-width: 360px; line-height: 1.55;
      white-space: pre-wrap;
    }}
  </style>
</head>
<body>

<!-- HERO -->
<div class="hero">
  <div class="container">
    <div class="row align-items-start">
      <div class="col-md-8">
        <div class="d-flex align-items-center gap-2 mb-1">
          <span style="font-size:1.8rem">\U0001F3C3</span>
          <h1 class="hero-title mb-0">Munich Half Marathon 2026</h1>
        </div>
        <p class="hero-sub">Training Dashboard · Jayendra · Goal: Sub 2:15 / Sub 2:30 &nbsp;·&nbsp; <span style="color:#93c5fd;font-weight:600">{today.strftime('%A, %B %d, %Y')}</span> &nbsp;·&nbsp; Last sync: {last_date}</p>
        <div class="countdown-block">
          <div class="cdown-item">
            <div class="cdown-num">{days_to_race}</div>
            <div class="cdown-label">Days to go</div>
          </div>
          <div class="cdown-sep">·</div>
          <div class="cdown-item">
            <div class="cdown-num">W{current_week_num}<span style="font-size:1.4rem;opacity:.6">/{NUM_WEEKS}</span></div>
            <div class="cdown-label">Training week</div>
          </div>
        </div>
      </div>
      <div class="col-md-4 mt-3 mt-md-0 text-md-end">
        <div class="phase-pill mb-2">Week {current_week_num} / {NUM_WEEKS} — {current_phase}</div><br>
        <span class="badge {load_badge} fs-6 px-3 py-2">{load_status}</span>{form_badge_html}
        <div class="text-muted small mt-2">{total_runs} runs logged</div>
      </div>
    </div>
  </div>
</div>

<div class="container py-4">

  <!-- KPI ROW -->
  <div class="row g-3 mb-4">
    <div class="col-6 col-md-2">
      <div class="kpi-card">
        <div class="kpi-icon">\U0001F4C5</div>
        <div class="kpi-value">{four_week_avg:.1f}</div>
        <div class="kpi-label">km/week (4w avg)</div>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="kpi-card">
        <div class="kpi-icon">\U0001F4C6</div>
        <div class="kpi-value">{last_week_km:.1f}</div>
        <div class="kpi-label">km last week</div>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="kpi-card">
        <div class="kpi-icon">\U0001F4CF</div>
        <div class="kpi-value">{longest_run:.1f}</div>
        <div class="kpi-label">km longest run</div>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="kpi-card">
        <div class="kpi-icon">⏱️</div>
        <div class="kpi-value">{last_week_pace_str}</div>
        <div class="kpi-label">avg pace (last week)</div>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="kpi-card">
        <div class="kpi-icon">\U0001F3AF</div>
        <div class="kpi-value">6:23</div>
        <div class="kpi-label">stretch HMP</div>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="kpi-card">
        <div class="kpi-icon">\U0001F3C1</div>
        <div class="kpi-value">Oct 11</div>
        <div class="kpi-label">race day</div>
      </div>
    </div>
  </div>

  <!-- CURRENT WEEK CALENDAR -->
  {calendar_html}

  <!-- TABS -->
  <ul class="nav nav-tabs" id="mainTabs" role="tablist">
    <li class="nav-item">
      <button class="nav-link active" data-bs-toggle="tab" data-bs-target="#tab-overview">
        \U0001F4CA Overview
      </button>
    </li>
    <li class="nav-item">
      <button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-plan">
        \U0001F4CB Training Plan
      </button>
    </li>
    <li class="nav-item">
      <button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-strategy">
        \U0001F9E0 Race Strategy
      </button>
    </li>
    <li class="nav-item">
      <button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-nutrition">
        \U0001F34E Pre-Run Nutrition
      </button>
    </li>
  </ul>

  <!-- TAB: OVERVIEW -->
  <div class="tab-content">
  <div class="tab-pane fade show active content-card" id="tab-overview">

    <ul class="nav nav-pills mb-3" id="overviewPills" role="tablist">
      <li class="nav-item" role="presentation">
        <button class="nav-link active" data-bs-toggle="pill" data-bs-target="#ov-global" type="button">\U0001F4C8 Global Training</button>
      </li>
      <li class="nav-item" role="presentation">
        <button class="nav-link" data-bs-toggle="pill" data-bs-target="#ov-week" type="button">\U0001F4C5 This Week</button>
      </li>
    </ul>

    <div class="tab-content">

      <div class="tab-pane fade show active" id="ov-global" role="tabpanel">
        <p class="section-heading">Weekly Volume — Observed vs Planned</p>
        {vol_html}
        <p class="section-heading mt-4">Session Pace History</p>
        {pace_html}
        <p class="section-heading mt-4">Fitness &amp; Fatigue (Training Load)</p>
        <div class="text-muted small mb-2">CTL = Fitness (long-term load average) &middot; ATL = Fatigue (short-term load average) &middot; Form = CTL &minus; ATL (positive = fresh, negative = digging into fatigue)</div>
        {load_html}
        <p class="section-heading mt-4">Recent Sessions <span class="text-muted small fw-normal">(\U0001F4C8 = click for HR/pace curves)</span></p>
        {recent_table}
      </div>

      <div class="tab-pane fade" id="ov-week" role="tabpanel">
        <p class="section-heading">Week {current_week_num} &mdash; Daily km Accumulation (Planned vs Actual)</p>
        {week_accum_html}
      </div>

    </div>

  </div>

  <!-- TAB: TRAINING PLAN -->
  <div class="tab-pane fade content-card" id="tab-plan">

    <p class="section-heading">{NUM_WEEKS}-Week Plan Overview — Weekly targets by phase</p>
    {gantt_html}

    <p class="section-heading mt-4">Full Week-by-Week Schedule</p>
    {plan_table}

  </div>

  <!-- TAB: RACE STRATEGY -->
  <div class="tab-pane fade content-card" id="tab-strategy">

    <div class="row g-3 mb-4">
      <div class="col-md-6">
        <div class="strategy-card">
          <h6>\U0001F4C5 Training Phases</h6>
          <ul>
            <li><strong>Weeks 1–4 · Base:</strong> Build consistency, easy mileage, 1 long run/week up to 11k.</li>
            <li><strong>Weeks 5–8 · Build:</strong> Introduce tempo &amp; intervals. Long run grows to 16k.</li>
            <li><strong>Weeks 9–10 · Specific:</strong> HM-pace segments in long runs (18k). Race rehearsal.</li>
            <li><strong>Weeks 11–12 · Taper:</strong> Drop volume, keep sharpness, arrive fresh.</li>
          </ul>
        </div>
      </div>
      <div class="col-md-6">
        <div class="strategy-card">
          <h6>\U0001F4C6 Weekly Template (2 runs + 3 strength)</h6>
          <ul>
            <li><strong>Mon:</strong> Strength training.</li>
            <li><strong>Tue:</strong> Quality run — intervals/tempo (Base–Build) or HM pace (Specific).</li>
            <li><strong>Wed:</strong> Strength training.</li>
            <li><strong>Thu:</strong> Rest.</li>
            <li><strong>Fri:</strong> Strength training.</li>
            <li><strong>Sat:</strong> Long run (progressive; HMP segments from W9).</li>
            <li><strong>Sun:</strong> Rest or bike ride.</li>
          </ul>
        </div>
      </div>
    </div>

    <div class="row g-3 mb-4">
      <div class="col-md-6">
        <div class="strategy-card">
          <h6>⏱️ Training Pace Zones</h6>
          <div class="pace-zone"><span class="pace-dot" style="background:#22c55e"></span><span><strong>Easy / Recovery</strong> — 7:00–7:30 min/km</span></div>
          <div class="pace-zone"><span class="pace-dot" style="background:#3b82f6"></span><span><strong>Long run</strong> — 6:45–7:15 min/km</span></div>
          <div class="pace-zone"><span class="pace-dot" style="background:#fbbf24"></span><span><strong>HM Pace (safe)</strong> — ~7:06 min/km (sub 2:30)</span></div>
          <div class="pace-zone"><span class="pace-dot" style="background:#22c55e"></span><span><strong>HM Pace (stretch)</strong> — ~6:23 min/km (sub 2:15)</span></div>
          <div class="pace-zone"><span class="pace-dot" style="background:#f97316"></span><span><strong>Tempo / Threshold</strong> — 5:50–6:20 min/km</span></div>
          <div class="pace-zone"><span class="pace-dot" style="background:#8b5cf6"></span><span><strong>Intervals (400m–1k)</strong> — 5:20–5:50 min/km</span></div>
          <p class="small text-muted mt-2">Keep ≥ 80% of weekly km at easy effort. With 2 runs/week, each one counts — nail the purpose of each session.</p>
        </div>
      </div>
      <div class="col-md-6">
        <div class="strategy-card">
          <h6>\U0001F3C1 Race Day Strategy</h6>
          <ul>
            <li><strong>Target:</strong> Sub 2:30 (safe) / Sub 2:15 (stretch).</li>
            <li><strong>Split strategy:</strong> Even or slight negative split — first 10k conservative, second 11k push if feeling good.</li>
            <li><strong>First 3k:</strong> Resist the crowd, settle into rhythm. Run by feel not GPS.</li>
            <li><strong>Km 15–18:</strong> This is where the race starts. Stay focused, maintain form.</li>
            <li><strong>Fuel:</strong> One gel at km 8–10 if practiced in training.</li>
            <li><strong>Warm-up:</strong> 10 min easy jog + drills, 30 min before gun.</li>
          </ul>
        </div>
      </div>
    </div>

    <div class="row g-3">
      <div class="col-md-6">
        <div class="strategy-card">
          <h6>⚡ Key Session Types</h6>
          <ul>
            <li><strong>Strides (Base, W1–2):</strong> 4×100m relaxed fast running after an easy run — builds neuromuscular speed without fatigue.</li>
            <li><strong>400m intervals (Base, W3):</strong> 4×400m at 5:30–5:50/km with 2 min rest. Short, sharp, develops efficiency.</li>
            <li><strong>Tempo (Build):</strong> 3–5k continuous at 5:50–6:20/km — comfortably hard.</li>
            <li><strong>1k intervals (Build):</strong> 4×1k at 5:30–5:50/km with 90s recovery.</li>
            <li><strong>HMP runs (Specific):</strong> 5–6k blocks at target 6:20/km within a longer run.</li>
          </ul>
        </div>
      </div>
      <div class="col-md-6">
        <div class="strategy-card">
          <h6>\U0001F6E1️ Injury Prevention</h6>
          <ul>
            <li>With 2 runs/week + 3 strength, your running volume is low-risk — keep it that way.</li>
            <li>Step-back week every 4th week (−15% volume).</li>
            <li>Strength focus: glutes, core, single-leg stability — you already do this 3×/week.</li>
            <li>If legs feel heavy from strength → keep run easy, skip the quality component.</li>
            <li>Bike rides are great active recovery between runs.</li>
          </ul>
        </div>
      </div>
    </div>

  </div>

  <!-- TAB: NUTRITION -->
  <div class="tab-pane fade content-card" id="tab-nutrition">

    <div class="text-muted small mb-3">
      Pre-run and race-day nutrition guidance. You eat high-protein already — these tips focus on
      fueling around key training sessions and race day. Nothing new on race day — test everything in training first.
    </div>

    <div class="row g-3 mb-3">
      <div class="col-md-6">
        <div class="strategy-card">
          <h6>\U0001F305 Before Quality Runs (Tuesday)</h6>
          <ul>
            <li><strong>If running in the morning:</strong> eat something small 60–90 min before — a banana, toast with honey, or a few rice cakes.</li>
            <li><strong>If running after work:</strong> have a carb-rich snack 1–2 hours before — a banana, an energy bar, or a small bowl of oats.</li>
            <li>Keep it light and low-fat so your stomach isn’t working hard during intervals/tempo.</li>
            <li>Have a glass of water beforehand. Coffee is fine if that’s your routine.</li>
          </ul>
        </div>
      </div>
      <div class="col-md-6">
        <div class="strategy-card">
          <h6>\U0001F4CF Before Long Runs (Saturday)</h6>
          <ul>
            <li><strong>Night before:</strong> slightly carb-heavier dinner than usual — extra rice/pasta/potatoes. Keep it familiar.</li>
            <li><strong>Morning of:</strong> eat 60–90 min before. Bigger than Tuesday — e.g. oatmeal + banana, or 2 slices toast with honey + a coffee.</li>
            <li><strong>Runs &gt; 90 min (W9–10):</strong> bring a gel or energy chews. Practice taking one around km 8–10 — this is your race day rehearsal.</li>
          </ul>
        </div>
      </div>
    </div>

    <div class="row g-3 mb-3">
      <div class="col-md-6">
        <div class="strategy-card">
          <h6>\U0001F504 After Runs — Recovery</h6>
          <ul>
            <li>Within the hour: something with carbs + protein. You eat high-protein already — just add carbs. Rice + chicken, yogurt with granola, a protein shake + banana.</li>
            <li>Rehydrate steadily. Electrolyte tab if it was hot or sweaty.</li>
            <li>Matters most after long runs and quality sessions.</li>
          </ul>
        </div>
      </div>
      <div class="col-md-6">
        <div class="strategy-card">
          <h6>\U0001F4A7 Hydration</h6>
          <ul>
            <li>Baseline: drink through the day so your pee stays pale.</li>
            <li>On long runs: sip regularly — a mouthful every 15–20 min.</li>
            <li>Hot sessions (Jul/Aug): electrolyte tab or sports drink. Munich in October will be cooler.</li>
          </ul>
        </div>
      </div>
    </div>

    <div class="row g-3">
      <div class="col-12">
        <div class="strategy-card" style="border-color:#22c55e">
          <h6 style="color:#22c55e">\U0001F3C1 Race Day Nutrition — Munich Half Marathon, Oct 11</h6>
          <div style="display:flex;flex-wrap:wrap;gap:.75rem">
            <div style="flex:1;min-width:200px;background:#0f172a;border-radius:8px;padding:.75rem">
              <div style="font-size:.7rem;font-weight:700;color:#22c55e;text-transform:uppercase;margin-bottom:.5rem">Night Before</div>
              <div style="font-size:.82rem;color:#94a3b8">Familiar, carb-rich dinner. Extra pasta/rice. Nothing new, nothing too heavy or spicy. Hydrate well. Sleep early — the sleep 2 nights before matters most.</div>
            </div>
            <div style="flex:1;min-width:200px;background:#0f172a;border-radius:8px;padding:.75rem">
              <div style="font-size:.7rem;font-weight:700;color:#fbbf24;text-transform:uppercase;margin-bottom:.5rem">Race Morning</div>
              <div style="font-size:.82rem;color:#94a3b8">2–3 h before the gun: familiar carb-rich meal — oatmeal + banana, toast + honey, whatever you tested on long-run Saturdays. Optional small gel 15 min before start.</div>
            </div>
            <div style="flex:1;min-width:200px;background:#0f172a;border-radius:8px;padding:.75rem">
              <div style="font-size:.7rem;font-weight:700;color:#60a5fa;text-transform:uppercase;margin-bottom:.5rem">During the Race</div>
              <div style="font-size:.82rem;color:#94a3b8">One gel around km 8–10 if you practiced it in training. Sip water at stations. For a half marathon, you don’t need heavy fueling — glycogen stores are enough if you ate well.</div>
            </div>
            <div style="flex:1;min-width:200px;background:#0f172a;border-radius:8px;padding:.75rem">
              <div style="font-size:.7rem;font-weight:700;color:#a78bfa;text-transform:uppercase;margin-bottom:.5rem">After Finishing</div>
              <div style="font-size:.82rem;color:#94a3b8">Recovery meal (carbs + protein) within an hour. Rehydrate. Then celebrate — you earned it. \U0001F389</div>
            </div>
          </div>
        </div>
      </div>
    </div>

  </div>
  </div><!-- /tab-content -->

</div><!-- /container -->

<footer>Munich Half Marathon 2026 · Jayendra · Auto-generated from Intervals.icu export · {today.strftime('%b %d, %Y')}</footer>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
function toggleCurve(id){{
  var d = document.getElementById('curve-' + id);
  if(d) d.style.display = (d.style.display === 'none' ? 'table-row' : 'none');
}}
</script>
<div id="cht"></div>
<script>
(function(){{
  var tip = document.getElementById('cht');
  document.addEventListener('mousemove', function(e){{
    if(tip.style.display !== 'none'){{
      var x = e.clientX + 16, y = e.clientY - 10;
      if(x + tip.offsetWidth > window.innerWidth - 8) x = e.clientX - tip.offsetWidth - 12;
      if(y + tip.offsetHeight > window.innerHeight - 8) y = e.clientY - tip.offsetHeight - 10;
      tip.style.left = x + 'px';
      tip.style.top  = y + 'px';
    }}
  }});
  function attach(el){{
    el.addEventListener('mouseenter', function(){{
      tip.textContent = el.getAttribute('data-tip');
      tip.style.display = 'block';
    }});
    el.addEventListener('mouseleave', function(){{ tip.style.display = 'none'; }});
  }}
  document.querySelectorAll('[data-tip]').forEach(attach);
  document.addEventListener('shown.bs.tab', function(){{
    document.querySelectorAll('[data-tip]').forEach(attach);
  }});
}})();
</script>
</body>
</html>
"""
    out_path = OUT.joinpath('index.html')
    out_path.write_text(html, encoding='utf-8')
    print(f'Dashboard written to {out_path}')


def build_ics():
    DAY_KEYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

    def _ics_esc(s):
        return (str(s).replace('\\', '\\\\')
                .replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n'))

    def _fold(line):
        encoded = line.encode('utf-8')
        if len(encoded) <= 75:
            return line + '\r\n'
        out = ''
        while len(line.encode('utf-8')) > 75:
            chunk = line[:75]
            while len(chunk.encode('utf-8')) > 75:
                chunk = chunk[:-1]
            out += chunk + '\r\n '
            line = line[len(chunk):]
        return out + line + '\r\n'

    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//Munich Half Marathon 2026//Training Calendar//EN',
        'X-WR-CALNAME:Munich HM 2026 — Training',
        'X-WR-CALDESC:12-week half marathon training plan for Jayendra. Goal: Sub 2:15/2:30 on Oct 11 2026.',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
    ]

    for wnum, wdate_str, total_km, long_km, quality, phase in WEEKLY_PLAN:
        week_monday = PLAN_START + timedelta(weeks=wnum - 1)

        parsed = {}
        for part in quality.split(' · '):
            if ': ' in part:
                dk, desc = part.split(': ', 1)
                parsed[dk.strip()] = desc.strip()

        for i, day_key in enumerate(DAY_KEYS):
            day_date = week_monday + timedelta(days=i)
            desc = parsed.get(day_key, 'Rest')
            dl = desc.lower()

            if 'race' in dl or '\U0001F3C1' in desc:
                summary = '\U0001F3C1 RACE — Munich Half Marathon'
            elif 'long' in dl:
                summary = f'\U0001F4CF Long run — W{wnum} {phase}'
            elif '×' in desc or 'interval' in dl:
                summary = f'⚡ Intervals — W{wnum} {phase}'
            elif 'tempo' in dl:
                summary = f'\U0001F525 Tempo — W{wnum} {phase}'
            elif 'hmp' in dl or ('hm' in dl and 'pace' in dl):
                summary = f'\U0001F3AF HMP run — W{wnum} {phase}'
            elif 'strength' in dl:
                summary = f'\U0001F4AA Strength — W{wnum}'
            elif 'bike' in dl:
                summary = '\U0001F6B4 Bike ride'
            elif 'rest' in dl:
                summary = '\U0001F4A4 Rest'
            else:
                summary = f'\U0001F3C3 Easy run — W{wnum} {phase}'

            uid = (f"{day_date.strftime('%Y%m%d')}-w{wnum}-{day_key.lower()}"
                   f"@munich-hm-2026")
            dtstart = day_date.strftime('%Y%m%d')
            dtend = (day_date + timedelta(days=1)).strftime('%Y%m%d')
            full_desc = f"W{wnum} · {wdate_str} · {phase} | {desc}"

            lines += [
                'BEGIN:VEVENT',
                f'UID:{uid}',
                f'DTSTART;VALUE=DATE:{dtstart}',
                f'DTEND;VALUE=DATE:{dtend}',
                f'SUMMARY:{_ics_esc(summary)}',
                f'DESCRIPTION:{_ics_esc(full_desc)}',
                f'CATEGORIES:{_ics_esc(phase)}',
                'END:VEVENT',
            ]

    lines.append('END:VCALENDAR')

    ics_content = ''.join(_fold(l) for l in lines)
    ics_path = OUT / 'training.ics'
    ics_path.write_text(ics_content, encoding='utf-8')
    print(f'Calendar written to {ics_path}')


if __name__ == '__main__':
    runs = load_and_clean(DATA)
    weekly = weekly_aggregates(runs)
    targets = make_targets()
    build_dashboard(runs, weekly, targets)
    build_ics()
