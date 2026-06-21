#!/usr/bin/env python3
"""Build a free, self-hosted boxing data API from Wikidata (CC0).

Pulls every person on Wikidata whose occupation is 'boxer' (Q11338576),
normalises each into clean JSON, and writes:

    docs/api/boxers.json   -> every boxer, full records
    docs/api/index.json    -> slim list for search / list views
    docs/api/meta.json     -> build metadata (count, generated_at)

Served as static files via GitHub Pages this is a free, read-only,
REST-ish API that you own outright.

Wikidata is CC0 (public domain) so you can redistribute it however you like.

Pagination uses a keyset cursor (everyone after the last fighter seen) rather
than OFFSET, so deep pages don't slow down and time out. The query aggregates
to one row per fighter to keep each request light.
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

WDQS = "https://query.wikidata.org/sparql"

# WDQS REQUIRES a descriptive User-Agent with real contact info, or it blocks
# you. Put your own repo URL / email here before running.
USER_AGENT = "ManxBoxingAPI/1.0 (https://github.com/HammerThunderr; contact: you@example.com)"

OUT_DIR = Path("docs/api")
FULL_PATH = OUT_DIR / "boxers.json"
INDEX_PATH = OUT_DIR / "index.json"
META_PATH = OUT_DIR / "meta.json"

PAGE_SIZE = 500     # fighters per request
SLEEP = 1.0         # seconds between pages — be polite to WDQS
TIMEOUT = 180
MAX_RETRIES = 5

# Wikidata properties used:
#   P106  occupation            (filter: Q11338576 = boxer)
#   P569  date of birth
#   P570  date of death
#   P21   sex or gender
#   P27   country of citizenship   (several -> joined list)
#   P19   place of birth
#   P2048 height (cm)
#   P18   image (Commons FilePath URL)
#
# NOTE: win/loss/draw records are added afterwards by enrich_records.py.

# Placeholders __AFTER__ / __LIMIT__ are substituted at runtime (avoids having
# to double every brace for str.format).
QUERY = """
SELECT ?person ?personLabel
       (SAMPLE(?dob) AS ?dob2)
       (SAMPLE(?dod) AS ?dod2)
       (SAMPLE(?genderLabel) AS ?gender2)
       (GROUP_CONCAT(DISTINCT ?countryLabel; separator="|") AS ?countries2)
       (SAMPLE(?birthplaceLabel) AS ?birthplace2)
       (SAMPLE(?height) AS ?height2)
       (SAMPLE(?image) AS ?image2)
       (SAMPLE(?article) AS ?article2)
WHERE {
  ?person wdt:P106 wd:Q11338576 .
  FILTER(STR(?person) > "__AFTER__")
  OPTIONAL { ?person wdt:P569 ?dob. }
  OPTIONAL { ?person wdt:P570 ?dod. }
  OPTIONAL { ?person wdt:P21 ?gender. }
  OPTIONAL { ?person wdt:P27 ?country. }
  OPTIONAL { ?person wdt:P19 ?birthplace. }
  OPTIONAL { ?person wdt:P2048 ?height. }
  OPTIONAL { ?person wdt:P18 ?image. }
  OPTIONAL { ?article schema:about ?person ; schema:isPartOf <https://en.wikipedia.org/> . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }
}
GROUP BY ?person ?personLabel
ORDER BY ?person
LIMIT __LIMIT__
"""


def run_query(after, attempt=1):
    q = QUERY.replace("__AFTER__", after).replace("__LIMIT__", str(PAGE_SIZE))
    try:
        resp = requests.get(
            WDQS,
            params={"query": q, "format": "json"},
            headers={"User-Agent": USER_AGENT,
                     "Accept": "application/sparql-results+json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["results"]["bindings"]
    except requests.exceptions.HTTPError:
        code = resp.status_code
        if code in (429, 500, 502, 503, 504) and attempt <= MAX_RETRIES:
            wait = 5 * attempt
            print(f"  WDQS {code} — retrying in {wait}s (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            return run_query(after, attempt + 1)
        raise


def qid_from_uri(uri):
    return uri.rsplit("/", 1)[-1]


def _val(row, key):
    cell = row.get(key)
    return cell["value"] if cell and cell.get("value") else None


def _to_cm(value):
    if not value:
        return None
    try:
        return round(float(value))
    except ValueError:
        return None


def build():
    boxers = {}
    after = ""
    page = 0
    while True:
        rows = run_query(after)
        if not rows:
            break
        for row in rows:
            uri = row["person"]["value"]
            qid = qid_from_uri(uri)
            countries_raw = _val(row, "countries2") or ""
            countries = [c for c in countries_raw.split("|") if c]
            dob = _val(row, "dob2")
            dod = _val(row, "dod2")
            boxers[qid] = {
                "id": qid,
                "name": _val(row, "personLabel") or qid,
                "date_of_birth": dob[:10] if dob else None,
                "date_of_death": dod[:10] if dod else None,
                "gender": _val(row, "gender2"),
                "countries": countries,
                "birthplace": _val(row, "birthplace2"),
                "height_cm": _to_cm(_val(row, "height2")),
                "image": _val(row, "image2"),
                "wikipedia": _val(row, "article2"),
                "wikidata_url": uri,
            }
        after = rows[-1]["person"]["value"]
        page += 1
        print(f"  page {page} | through {qid_from_uri(after)} | {len(boxers)} boxers")
        time.sleep(SLEEP)
    return boxers


def write(boxers):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = [b for _, b in sorted(boxers.items())]

    FULL_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    index = [{"id": b["id"], "name": b["name"], "countries": b["countries"]}
             for b in records]
    INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    META_PATH.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "source": "Wikidata (CC0)",
        "license": "CC0-1.0",
    }, indent=2), encoding="utf-8")
    print(f"wrote boxers.json ({len(records)} records) + index.json + meta.json")


if __name__ == "__main__":
    if "@example.com" in USER_AGENT:
        sys.exit("Set a real contact in USER_AGENT first — WDQS blocks generic agents.")
    data = build()
    if not data:
        sys.exit("No data returned — check the query or WDQS status and retry.")
    write(data)
