#!/usr/bin/env python3
"""Build a free, self-hosted boxing data API from Wikidata (CC0).

Pulls every person on Wikidata whose occupation is 'boxer' (Q11338576),
normalises each into clean JSON, and writes:

    docs/api/boxers/index.json   -> summary list of every boxer
    docs/api/boxers/<QID>.json   -> full record per boxer
    docs/api/meta.json           -> build metadata (count, generated_at)

Served as static files via GitHub Pages this is a free, read-only,
REST-ish API that you own outright.

Wikidata is CC0 (public domain) so you can redistribute it however you like.
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
USER_AGENT = "ManxBoxingAPI/1.0 (https://github.com/HammerThunderr; contact: hammerpunch786@gmail.com)"

OUT_DIR = Path("docs/api/boxers")
META_PATH = Path("docs/api/meta.json")

PAGE_SIZE = 1000   # rows per request
SLEEP = 1.0        # seconds between pages — be polite to WDQS
TIMEOUT = 120

# Wikidata properties used:
#   P106  occupation            (filter: Q11338576 = boxer)
#   P569  date of birth
#   P570  date of death
#   P21   sex or gender
#   P27   country of citizenship   (a boxer can have several -> list)
#   P19   place of birth
#   P2048 height (cm)
#   P18   image (returns a Commons FilePath URL directly)
#
# NOTE: win/loss/draw RECORDS and REACH are not reliably modelled in Wikidata.
#       This script gives you the bio + roster layer. See README for how to
#       enrich it with records from other sources.

QUERY = """
SELECT ?person ?personLabel ?dob ?dod ?genderLabel ?countryLabel ?birthplaceLabel ?height ?image WHERE {{
  ?person wdt:P106 wd:Q11338576 .
  OPTIONAL {{ ?person wdt:P569 ?dob. }}
  OPTIONAL {{ ?person wdt:P570 ?dod. }}
  OPTIONAL {{ ?person wdt:P21 ?gender. }}
  OPTIONAL {{ ?person wdt:P27 ?country. }}
  OPTIONAL {{ ?person wdt:P19 ?birthplace. }}
  OPTIONAL {{ ?person wdt:P2048 ?height. }}
  OPTIONAL {{ ?person wdt:P18 ?image. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }}
}}
ORDER BY ?person
LIMIT {limit} OFFSET {offset}
"""


def run_query(offset):
    resp = requests.get(
        WDQS,
        params={"query": QUERY.format(limit=PAGE_SIZE, offset=offset), "format": "json"},
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["results"]["bindings"]


def qid_from_uri(uri):
    return uri.rsplit("/", 1)[-1]


def build():
    """Fetch all boxers, merging the multiple rows WDQS returns per person
    (one row per citizenship/value combination) into a single record."""
    boxers = {}
    offset = 0
    while True:
        rows = run_query(offset)
        if not rows:
            break
        for row in rows:
            qid = qid_from_uri(row["person"]["value"])
            b = boxers.setdefault(qid, {
                "id": qid,
                "name": qid,
                "date_of_birth": None,
                "date_of_death": None,
                "gender": None,
                "countries": [],
                "birthplace": None,
                "height_cm": None,
                "image": None,
                "wikidata_url": row["person"]["value"],
            })
            if "personLabel" in row:
                b["name"] = row["personLabel"]["value"]
            if "dob" in row:
                b["date_of_birth"] = row["dob"]["value"][:10]
            if "dod" in row:
                b["date_of_death"] = row["dod"]["value"][:10]
            if "genderLabel" in row:
                b["gender"] = row["genderLabel"]["value"]
            if "countryLabel" in row:
                country = row["countryLabel"]["value"]
                if country not in b["countries"]:
                    b["countries"].append(country)
            if "birthplaceLabel" in row:
                b["birthplace"] = row["birthplaceLabel"]["value"]
            if "height" in row:
                try:
                    b["height_cm"] = round(float(row["height"]["value"]))
                except ValueError:
                    pass
            if "image" in row:
                b["image"] = row["image"]["value"]
        offset += PAGE_SIZE
        print(f"  fetched up to offset {offset} | {len(boxers)} unique boxers so far")
        time.sleep(SLEEP)
    return boxers


def write(boxers):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index = []
    for qid, b in sorted(boxers.items()):
        (OUT_DIR / f"{qid}.json").write_text(
            json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        index.append({"id": qid, "name": b["name"], "countries": b["countries"]})
    (OUT_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(index),
        "source": "Wikidata (CC0)",
        "license": "CC0-1.0",
    }, indent=2), encoding="utf-8")
    print(f"wrote {len(index)} boxer files + index.json + meta.json")


if __name__ == "__main__":
    if "@example.com" in USER_AGENT:
        sys.exit("Set a real contact in USER_AGENT first — WDQS blocks generic agents.")
    data = build()
    if not data:
        sys.exit("No data returned — check the query or WDQS status and retry.")
    write(data)
