from __future__ import annotations
import json
import os
from collections import defaultdict
from pathlib import Path
from datetime import date, timedelta
from google.cloud import bigquery

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ARCHIVE = DATA / "archive"
DATA.mkdir(exist_ok=True)
ARCHIVE.mkdir(exist_ok=True)

client = bigquery.Client(project=os.environ["GCP_PROJECT_ID"])
MAX_BYTES = 750 * 1024 * 1024
PERIOD_WEEKS = {"weekly": 1, "monthly": 4, "yearly": 52}
# Longer-period views should not be dominated by a single current-week spike.
MIN_APPEARANCES = {"weekly": 1, "monthly": 1, "yearly": 2}


def query(table: str, target: date):
    sql = f"""
    SELECT refresh_date, region_code, region_name, term, rank, week, score
    FROM `bigquery-public-data.google_trends.{table}`
    WHERE refresh_date = @refresh_date
      AND country_code = 'TR'
      AND region_name IS NOT NULL
      AND week >= DATE_SUB(@refresh_date, INTERVAL 370 DAY)
    ORDER BY week DESC, region_name, rank
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("refresh_date", "DATE", target.isoformat())],
        maximum_bytes_billed=MAX_BYTES,
        use_query_cache=True,
    )
    return [dict(row) for row in client.query(sql, job_config=cfg).result()]


def find_latest():
    for days_ago in range(0, 5):
        d = date.today() - timedelta(days=days_ago)
        top = query("international_top_terms", d)
        if top:
            rising = query("international_top_rising_terms", d)
            return d, top, rising
    raise RuntimeError("Son 5 günde Türkiye Google Trends partition'ı bulunamadı.")


def aggregate(rows, week_count: int, min_appearances: int = 1):
    weeks = sorted({r["week"] for r in rows if r.get("week")}, reverse=True)[:week_count]
    week_set = set(weeks)
    if not weeks:
        return {}, []

    buckets = defaultdict(lambda: {"score_sum": 0.0, "weeks_seen": set(), "best_rank": 999, "last_week": None})
    for r in rows:
        if r.get("week") not in week_set:
            continue
        key = (r["region_name"], r["term"])
        b = buckets[key]
        if r.get("score") is not None:
            b["score_sum"] += float(r["score"])
        if r.get("week") is not None:
            b["weeks_seen"].add(r["week"])
        if r.get("rank") is not None:
            b["best_rank"] = min(b["best_rank"], int(r["rank"]))
        if b["last_week"] is None or r["week"] > b["last_week"]:
            b["last_week"] = r["week"]

    per_region = defaultdict(list)
    denom = len(weeks)
    for (region, term), b in buckets.items():
        appearances = len(b["weeks_seen"])
        if appearances < min_appearances:
            continue
        per_region[region].append({
            "term": term,
            "rank": b["best_rank"] if b["best_rank"] != 999 else None,
            "week": b["last_week"].isoformat() if b["last_week"] else None,
            "score": round(b["score_sum"] / denom, 2),
            "appearances": appearances,
        })

    for region, items in per_region.items():
        items.sort(key=lambda x: (-x["score"], -x["appearances"], x["rank"] or 999, x["term"]))
        per_region[region] = items[:25]

    return dict(per_region), [w.isoformat() for w in sorted(weeks)]


d, top_rows, rising_rows = find_latest()
periods = {}
for period, n_weeks in PERIOD_WEEKS.items():
    min_app = MIN_APPEARANCES[period]
    top, top_weeks = aggregate(top_rows, n_weeks, min_app)
    rising, rising_weeks = aggregate(rising_rows, n_weeks, min_app)
    regions = {
        r: {"top": top.get(r, []), "rising": rising.get(r, [])}
        for r in sorted(set(top) | set(rising))
    }
    weeks = top_weeks or rising_weeks
    periods[period] = {
        "weeks": weeks,
        "week_count": len(weeks),
        "regions": regions,
    }

payload = {
    "refresh_date": d.isoformat(),
    "country_code": "TR",
    "source": "bigquery-public-data.google_trends",
    "periods": periods,
    "regions": periods["weekly"]["regions"],
}
raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
(ARCHIVE / f"{d.isoformat()}.json").write_text(raw, encoding="utf-8")
(DATA / "latest.json").write_text(raw, encoding="utf-8")
dates = sorted(p.stem for p in ARCHIVE.glob("*.json"))
(DATA / "manifest.json").write_text(
    json.dumps({
        "latest": d.isoformat(),
        "dates": dates,
        "region_count": len(periods["weekly"]["regions"]),
        "periods": {k: v["week_count"] for k, v in periods.items()},
    }, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(
    f"{d}: weekly={periods['weekly']['week_count']}w, "
    f"monthly={periods['monthly']['week_count']}w, yearly={periods['yearly']['week_count']}w; "
    f"{len(periods['weekly']['regions'])} regions"
)
