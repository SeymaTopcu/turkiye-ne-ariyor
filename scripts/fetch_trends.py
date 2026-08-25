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


def query_snapshots(table: str, target: date, days: int):
    start = target - timedelta(days=days - 1)
    sql = f"""
    WITH rows AS (
      SELECT refresh_date, region_name, term, rank, week, score
      FROM `bigquery-public-data.google_trends.{table}`
      WHERE refresh_date BETWEEN @start_date AND @end_date
        AND country_code = 'TR'
        AND region_name IS NOT NULL
    ), latest_week_per_snapshot AS (
      SELECT refresh_date, MAX(week) AS week
      FROM rows
      GROUP BY refresh_date
    )
    SELECT r.refresh_date, r.region_name, r.term, r.rank, r.score
    FROM rows r
    JOIN latest_week_per_snapshot w USING (refresh_date, week)
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY r.refresh_date, r.region_name, r.term
      ORDER BY r.rank, r.score DESC
    ) = 1
    ORDER BY r.refresh_date DESC, r.region_name, r.rank
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("start_date", "DATE", start.isoformat()),
            bigquery.ScalarQueryParameter("end_date", "DATE", target.isoformat()),
        ],
        maximum_bytes_billed=MAX_BYTES,
        use_query_cache=True,
    )
    return [dict(row) for row in client.query(sql, job_config=cfg).result()]


def find_latest():
    for days_ago in range(0, 5):
        d = date.today() - timedelta(days=days_ago)
        top = query_snapshots("international_top_terms", d, 1)
        if top:
            return d
    raise RuntimeError("Son 5 günde Türkiye Google Trends partition'ı bulunamadı.")


def aggregate(rows):
    # rank is comparable within each daily Top-25 set. Reward both frequency
    # and high placement: rank 1 => 25 points, rank 25 => 1 point.
    buckets = defaultdict(lambda: {"points": 0, "days": set(), "best_rank": 999, "last_date": None})
    for r in rows:
        key = (r["region_name"], r["term"])
        b = buckets[key]
        rank = int(r["rank"]) if r.get("rank") is not None else 25
        b["points"] += max(1, 26 - rank)
        b["days"].add(r["refresh_date"])
        b["best_rank"] = min(b["best_rank"], rank)
        if b["last_date"] is None or r["refresh_date"] > b["last_date"]:
            b["last_date"] = r["refresh_date"]

    per_region = defaultdict(list)
    for (region, term), b in buckets.items():
        per_region[region].append({
            "term": term,
            "rank": b["best_rank"],
            "score": b["points"],
            "appearances": len(b["days"]),
            "last_seen": b["last_date"].isoformat(),
        })
    for region, items in per_region.items():
        items.sort(key=lambda x: (-x["score"], -x["appearances"], x["rank"], x["term"]))
        per_region[region] = items[:25]
    return dict(per_region)


def make_period(top_rows, rising_rows, snapshot_days):
    top = aggregate(top_rows)
    rising = aggregate(rising_rows)
    regions = {r: {"top": top.get(r, []), "rising": rising.get(r, [])} for r in sorted(set(top) | set(rising))}
    dates = sorted({r["refresh_date"].isoformat() for r in top_rows + rising_rows})
    return {"snapshot_days": len(dates), "dates": dates, "regions": regions}


d = find_latest()
top_1 = query_snapshots("international_top_terms", d, 1)
rising_1 = query_snapshots("international_top_rising_terms", d, 1)
top_7 = query_snapshots("international_top_terms", d, 7)
rising_7 = query_snapshots("international_top_rising_terms", d, 7)
top_30 = query_snapshots("international_top_terms", d, 30)
rising_30 = query_snapshots("international_top_rising_terms", d, 30)
periods = {
    "current": make_period(top_1, rising_1, 1),
    "weekly": make_period(top_7, rising_7, 7),
    "monthly": make_period(top_30, rising_30, 30),
}
payload = {
    "refresh_date": d.isoformat(),
    "country_code": "TR",
    "source": "bigquery-public-data.google_trends",
    "period_score_method": "daily_top25_rank_points",
    "periods": periods,
    "regions": periods["current"]["regions"],
}
raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
(ARCHIVE / f"{d.isoformat()}.json").write_text(raw, encoding="utf-8")
(DATA / "latest.json").write_text(raw, encoding="utf-8")
dates = sorted(p.stem for p in ARCHIVE.glob("*.json"))
(DATA / "manifest.json").write_text(json.dumps({"latest": d.isoformat(), "dates": dates, "region_count": len(periods["current"]["regions"]), "periods": {k: v["snapshot_days"] for k, v in periods.items()}, "period_score_method": "daily_top25_rank_points"}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"{d}: current={periods['current']['snapshot_days']}d weekly={periods['weekly']['snapshot_days']}d monthly={periods['monthly']['snapshot_days']}d; {len(periods['current']['regions'])} regions")
