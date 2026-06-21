#!/usr/bin/env python3
"""Enrich boxers.json with fight records parsed from English Wikipedia.

Two passes:

  (default)      Summary record — wins/losses/draws/KO/total — pulled from the
                 {{Infobox boxer}} of each fighter's article. Fast: batches 50
                 articles per API request (~400 calls for 19k fighters).

  --with-bouts   Bout-by-bout history parsed from the "Professional boxing
                 record" table on each article. Slow: one request per fighter,
                 so run it on its own. Resumable — skips fighters already done.

Wikipedia content is CC BY-SA: attribute it in your app.

Usage:
  python enrich_records.py                 # summary records for everyone
  python enrich_records.py --limit 200     # quick test on first 200
  python enrich_records.py --with-bouts    # deep pass: full fight tables
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests

API = "https://en.wikipedia.org/w/api.php"

# Set a real contact — Wikipedia asks for it and may throttle generic agents.
USER_AGENT = "ManxBoxingAPI/1.0 (https://github.com/HammerThunderr; contact: you@example.com)"

DATA = Path("docs/api/boxers.json")
META = Path("docs/api/meta.json")

BATCH = 50          # titles per summary request
SUMMARY_SLEEP = 0.3
BOUTS_SLEEP = 0.4


# ---------------------------------------------------------------- helpers

def title_from_url(url):
    """https://en.wikipedia.org/wiki/Tyson_Fury -> 'Tyson Fury'."""
    if not url:
        return None
    slug = url.rsplit("/wiki/", 1)[-1]
    return unquote(slug).replace("_", " ")


def _int_after(text, *keys):
    """First integer assigned to any of the given infobox params."""
    for k in keys:
        m = re.search(
            r"\|\s*" + re.escape(k) + r"\s*=[^\d\n\r]{0,15}?(\d{1,4})",
            text,
            re.IGNORECASE,
        )
        if m:
            return int(m.group(1))
    return None


def parse_infobox(wikitext):
    head = wikitext[:8000]  # infobox lives at the top
    rec = {
        "wins": _int_after(head, "wins"),
        "losses": _int_after(head, "losses"),
        "draws": _int_after(head, "draws"),
        "ko_wins": _int_after(head, "KO", "wins_by_KO", "wins by KO"),
        "total": _int_after(head, "total"),
        "no_contests": _int_after(head, "no_contests", "no contests", "nc"),
    }
    if rec["wins"] is None and rec["losses"] is None:
        return None  # no usable record on this article
    return rec


# ---------------------------------------------------------------- summary pass

def fetch_wikitext_batch(titles):
    params = {
        "action": "query",
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
        "titles": "|".join(titles),
    }
    r = requests.get(API, params=params,
                     headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    data = r.json().get("query", {})

    remap = {}
    for n in data.get("normalized", []):
        remap[n["from"]] = n["to"]
    for rd in data.get("redirects", []):
        remap[rd["from"]] = rd["to"]

    content = {}
    for p in data.get("pages", []):
        revs = p.get("revisions")
        if revs:
            content[p["title"]] = revs[0]["slots"]["main"]["content"]

    def resolve(t):
        seen = set()
        while t in remap and t not in seen:
            seen.add(t)
            t = remap[t]
        return t

    return {t: content.get(resolve(t)) for t in titles}


def enrich_summary(boxers, limit=None):
    todo = [b for b in boxers if b.get("wikipedia")]
    if limit:
        todo = todo[:limit]

    by_title = {}
    for b in todo:
        t = title_from_url(b["wikipedia"])
        if t:
            by_title.setdefault(t, []).append(b)

    titles = list(by_title.keys())
    found = 0
    for i in range(0, len(titles), BATCH):
        chunk = titles[i:i + BATCH]
        try:
            contents = fetch_wikitext_batch(chunk)
        except Exception as e:
            print(f"  batch at {i} failed: {e}")
            continue
        for t, wt in contents.items():
            if not wt:
                continue
            rec = parse_infobox(wt)
            if rec:
                for b in by_title[t]:
                    b["record"] = rec
                found += 1
        done = min(i + BATCH, len(titles))
        print(f"  summary {done}/{len(titles)} titles | {found} records found")
        time.sleep(SUMMARY_SLEEP)
    return found


# ---------------------------------------------------------------- bouts pass

def fetch_html(title):
    params = {
        "action": "parse", "page": title, "prop": "text",
        "format": "json", "formatversion": "2", "redirects": "1",
    }
    r = requests.get(API, params=params,
                     headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    return r.json().get("parse", {}).get("text")


def parse_record_table(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    for table in soup.find_all("table", class_="wikitable"):
        header_text = " ".join(
            th.get_text(strip=True).lower() for th in table.find_all("th")
        )
        if "opponent" not in header_text:
            continue
        if "result" not in header_text and "record" not in header_text:
            continue

        first = table.find("tr")
        cols = [c.get_text(strip=True).lower()
                for c in first.find_all(["th", "td"])]

        def idx(*names):
            for n in names:
                for k, c in enumerate(cols):
                    if n in c:
                        return k
            return None

        ci = {
            "result": idx("result"),
            "record": idx("record"),
            "opponent": idx("opponent"),
            "type": idx("type", "method"),
            "round": idx("round"),
            "date": idx("date"),
            "location": idx("location"),
            "notes": idx("notes"),
        }

        bouts = []
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) < 4:
                continue

            def cell(key):
                k = ci.get(key)
                if k is None or k >= len(cells):
                    return None
                return cells[k].get_text(" ", strip=True) or None

            res = cell("result")
            if not res or res.lower() not in (
                "win", "loss", "draw", "nc", "no contest"
            ):
                continue
            bouts.append({
                "result": res,
                "record": cell("record"),
                "opponent": cell("opponent"),
                "type": cell("type"),
                "round": cell("round"),
                "date": cell("date"),
                "location": cell("location"),
                "notes": cell("notes"),
            })
        if bouts:
            return bouts
    return None


def enrich_bouts(boxers, limit=None):
    # Prioritise fighters that already have a summary record.
    todo = [b for b in boxers
            if b.get("wikipedia") and "bouts" not in b]
    todo.sort(key=lambda b: 0 if b.get("record") else 1)
    if limit:
        todo = todo[:limit]

    found = 0
    for n, b in enumerate(todo, 1):
        title = title_from_url(b["wikipedia"])
        try:
            html = fetch_html(title)
            bouts = parse_record_table(html) if html else None
            if bouts:
                b["bouts"] = bouts
                found += 1
        except Exception as e:
            print(f"  {title}: {e}")
        if n % 50 == 0:
            print(f"  bouts {n}/{len(todo)} | {found} fighters with fights")
        time.sleep(BOUTS_SLEEP)
    return found


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-bouts", action="store_true",
                    help="deep pass: parse full bout tables (slow)")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N fighters (for testing)")
    args = ap.parse_args()

    if "@example.com" in USER_AGENT:
        sys.exit("Set a real contact in USER_AGENT first.")
    if not DATA.exists():
        sys.exit(f"{DATA} not found — run wikidata_boxers.py first.")

    boxers = json.loads(DATA.read_text(encoding="utf-8"))

    if args.with_bouts:
        n = enrich_bouts(boxers, limit=args.limit)
        print(f"added bouts for {n} fighters")
    else:
        n = enrich_summary(boxers, limit=args.limit)
        print(f"added records for {n} fighters")

    DATA.write_text(json.dumps(boxers, ensure_ascii=False, indent=2),
                    encoding="utf-8")

    if META.exists():
        meta = json.loads(META.read_text(encoding="utf-8"))
        meta["with_record"] = sum(1 for b in boxers if b.get("record"))
        meta["with_bouts"] = sum(1 for b in boxers if b.get("bouts"))
        META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"meta: {meta.get('with_record')} with record, "
              f"{meta.get('with_bouts')} with bouts")


if __name__ == "__main__":
    main()
