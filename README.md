# Free boxing data API (Wikidata → JSON → GitHub Pages)

A free, self-hosted, read-only boxing API. No paid feeds, no scraping of
closed databases, no licensing risk — it pulls from **Wikidata**, which is
**CC0 (public domain)**, so you can redistribute the data however you like.

Same shape as the IOM_OPEN_DATA pipeline: GitHub Actions runs a Python script
on a schedule, the script queries Wikidata and writes static JSON, GitHub
Pages serves it.

## Setup (one time)

1. Push this repo to GitHub.
2. Open `wikidata_boxers.py` and set a real contact in `USER_AGENT`
   (Wikidata blocks generic user-agents).
3. **Settings → Pages →** deploy from branch, folder `/docs`.
4. **Actions tab →** run *Update boxing API* once (`workflow_dispatch`) to
   generate the first build. After that it refreshes weekly.

## Endpoints

Once Pages is live (`https://<user>.github.io/<repo>/`):

| Endpoint | Returns |
|---|---|
| `/api/boxers/index.json` | summary list of every boxer (`id`, `name`, `countries`) |
| `/api/boxers/<QID>.json` | full record for one boxer |
| `/api/meta.json` | build time, total count, source/licence |

Example record:

```json
{
  "id": "Q105890",
  "name": "Muhammad Ali",
  "date_of_birth": "1942-01-17",
  "date_of_death": "2016-06-03",
  "gender": "male",
  "countries": ["United States of America"],
  "birthplace": "Louisville",
  "height_cm": 191,
  "image": "http://commons.wikimedia.org/wiki/Special:FilePath/...",
  "wikidata_url": "http://www.wikidata.org/entity/Q105890"
}
```

In Flutter you just `http.get` the index for the roster, then the per-QID
file when a profile opens. Identical to how the app consumes `boroughs.json`.

## What this gives you — and what it doesn't

**In:** the bio + roster layer — name, nationality, DOB/DOD, height, birthplace,
photo, for thousands of notable boxers.

**Not in:** full win/loss/draw **records**, reach, and bout-by-bout history.
Wikidata doesn't model those reliably. This is the honest limit of the free,
clean route — see below for the records layer.

## Adding a records layer later

- **Boxing Data API** (RapidAPI): current records + schedules. Enrich your
  Wikidata records by matching on name; cache server-side, mind the rate limit
  and check its terms permit redistribution in an app.
- **Sanctioning bodies** (WBC / WBA / IBF / WBO) and The Ring publish current
  champions and rankings openly — good primary sources for the live scene.
- **Cross-linking:** Wikidata stores external IDs (incl. a BoxRec ID property).
  Look up the current property on a boxer's Wikidata page and add it as another
  `OPTIONAL` in the query if you want to deep-link out.

## Tuning

- Tens of thousands of tiny per-boxer files are fine for GitHub Pages but bulk
  up the repo. If you'd rather ship one file, drop the per-QID write and keep
  only a single `boxers.json`.
- Widen coverage by also matching `?person wdt:P641 wd:Q32112` (sport = boxing)
  via `UNION` — slower on WDQS, so test for timeouts.
