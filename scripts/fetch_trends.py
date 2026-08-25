from __future__ import annotations
import json
import os
from pathlib import Path
from datetime import date, timedelta
from google.cloud import bigquery

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ARCHIVE = DATA / "archive"
DATA.mkdir(exist_ok=True)
ARCHIVE.mkdir(exist_ok=True)

client = bigquery.Client(project=os.environ["GCP_PROJECT_ID"])
MAX_BYTES = 250 * 1024 * 1024


def query(table: str, target: date):
    sql = f"""
    SELECT refresh_date, region_code, region_name, term, rank, score
    FROM `bigquery-public-data.google_trends.{table}`
    WHERE refresh_date = @refresh_date
      AND country_code = 'TR'
      AND region_name IS NOT NULL
    ORDER BY region_name, rank
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


def normalize(rows):
    out = {}
    for r in rows:
        region = r["region_name"]
        out.setdefault(region, []).append({
            "term": r["term"],
            "rank": int(r["rank"]) if r.get("rank") is not None else None,
            "score": float(r["score"]) if r.get("score") is not None else None,
        })
    return out


d, top_rows, rising_rows = find_latest()
top, rising = normalize(top_rows), normalize(rising_rows)
regions = {r: {"top": top.get(r, []), "rising": rising.get(r, [])} for r in sorted(set(top) | set(rising))}
payload = {"refresh_date": d.isoformat(), "country_code": "TR", "source": "bigquery-public-data.google_trends", "regions": regions}
raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
(ARCHIVE / f"{d.isoformat()}.json").write_text(raw, encoding="utf-8")
(DATA / "latest.json").write_text(raw, encoding="utf-8")
dates = sorted(p.stem for p in ARCHIVE.glob("*.json"))
(DATA / "manifest.json").write_text(json.dumps({"latest": d.isoformat(), "dates": dates, "region_count": len(regions)}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"{d}: {len(regions)} regions; {len(top_rows)} top; {len(rising_rows)} rising")
