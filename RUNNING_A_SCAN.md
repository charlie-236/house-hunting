# Running a scan from a clone (no access to Charlie's machine)

This file is for a Claude session that has **no device bridge to mint-main**.
Everything the scan needs is in this repository. Results go back via git, and
Charlie pulls them on his own machine.

`README.md` describes the tracker itself — how listings age, what the opinion
score means, how the page is served. **Read it as well.** This file covers only
how to run a scan and get the result back.

---

## The workflow

```
git clone https://github.com/charlie-236/house-hunting.git
cd house-hunting
#   ... run the scan, edit archive.json ...
python3 build_page.py            # regenerates index.html
git add archive.json index.html
git commit -m "Scan YYYY-MM-DD: N new listings"
git push
```

Charlie then runs `git pull` in `~/Desktop/House-Hunting/` on mint-main, and
the pm2-served page picks the new `index.html` up on next reload.

**Do not** try to stage or commit files to `~/Desktop/House-Hunting/` — that
path only exists on Charlie's machine and is unreachable from a clone.

### `status.json` must be present when you build

`build_page.py` reads `status.json` and bakes Charlie's favourite / followed-up
/ rejected flags into the page so they are correct on first paint. It is
committed to this repo. **Always run `build_page.py` from the repo root with
`status.json` in place.** Building in a scratch directory without it silently
emits `let statuses = {}` and the page loses those flags for anyone opening the
file directly. (This has happened.)

`status.json` is also written at runtime by `server.py` on Charlie's machine, so
it can change outside git. If a push conflicts on `status.json`, Charlie's
copy wins — take theirs.

---

## The current scan brief (reset 2 October 2026)

> **Destructive reset authorised:** rebuild `archive.json` from only this scan;
> do not retain any earlier listings or runs, and never edit `status.json`.
> Search only **E4, E5, E8, E10, E17, N4, N5, N7, N8, N15, N16, N17, N19 and
> N22** for genuinely open-market 2–3-bed listings at or below about **£525k**.
> Freshness is the **last seven calendar days** through the run date, controlled
> by Zoopla’s exact newest-first `Listed on` date. Prioritise roughly a
> **12-minute / 0.6-mile walk** to Underground, Overground or Elizabeth line
> stations and direct routes; a realistic interchange/borderline commute is
> capped at **7/10**. Exclude shared ownership, Homewise/lifetime/retirement,
> stale/relisted/reduced, mismarketed, promoted out-of-postcode and over-budget
> stock. For every retained listing, open the full public detail page, read the
> complete description and structured facts, open
> `?console=open&tab=floor_plans` and read images only within
> `aria-label="Floor plan images"`, then navigate and scroll every gallery
> image. Build with `status.json` present, but do not modify it.

## Where to search, and what each source is good for

### Zoopla — the source of truth for what is NEW

Zoopla sorts by date and shows an exact `Listed on` date per card. Use it to
decide what is new.

```
https://www.zoopla.co.uk/for-sale/property/walthamstow/?beds_min=2&beds_max=3&price_max=525000&results_sort=newest_listings
https://www.zoopla.co.uk/for-sale/property/leyton/?beds_min=2&beds_max=3&price_max=525000&results_sort=newest_listings
https://www.zoopla.co.uk/for-sale/property/chingford/?beds_min=2&beds_max=3&price_max=525000&results_sort=newest_listings
```

Add `&pn=2` for page 2 — needed to re-confirm older listings still being live.
Zoopla needs a **browser** (it is not reliably WebFetch-able). Cards are
`div[id^="listing_"]`; the id suffix is the Zoopla listing id.

> **Promoted listings are not area-filtered.** The Leyton search has returned a
> property in NW10. Always check the postcode on the detail page before adding.

### OnTheMarket — supplementary public coverage only

Open the unauthenticated public 2- and 3-bed facets for **each** current
postcode, for example:

```
https://www.onthemarket.com/for-sale/2-bed-property/e4/
https://www.onthemarket.com/for-sale/3-bed-property/e4/
```

Repeat for E4, E5, E8, E10, E17, N4, N5, N7, N8, N15, N16, N17, N19 and N22.
Record a missing or unusable facet as a coverage gap. OnTheMarket relevance
ordering and its “added” buckets are not trustworthy for dates, so it must
never override Zoopla’s listed date.

---

## Reading a Zoopla listing properly

Three things are easy to miss. All three have been missed before.

### 1. Tenure and running costs live in TWO places

Read **both** or you will publish a score for a property that costs thousands a
year more than it looks:

- **The end of the description.** Some agents append a `Tenure Information`
  block after all the prose. Never truncate the description before searching it
  for `Tenure`, `Service Charge`, `Ground Rent`, `Council Tax`.
- **The structured facts panel further down the page**, which is separate:
  `Service charge` / `Council tax band` / `Ground rent` / `Ground rent date of
  next review` / `Ground rent review period`.

On 5 Sep a listing was flagged to Charlie as 8/10 and "best value in the
tracker" while carrying a **£500/month** service charge that sat in the first of
those and was cut off by truncation. It was a 6. A second listing's £483 ground
rent **reviewed every five years** was in the second and never read at all.

`Ask agent` in the facts panel is a real finding — record it as
genuinely-unpublished, which is different from not having looked. A **ground
rent review period** matters as much as the amount: escalating or doubling
clauses can make a flat unmortgageable.

Put annual fixed charges in the opinion, not just the data fields. £6,450/yr
services roughly £90,000 of extra mortgage at current rates — enough to move a
score several points and change which property is actually cheapest.

### 2. The floorplan is lazy-loaded behind its own tab

It is not in the initial HTML. Open `?console=open&tab=floor_plans`, then read
images from inside the container with `aria-label="Floor plan images"`.

Do **not** pattern-match on file extension: floorplans are sometimes `.jpg` and
sometimes `.png`, and the **EPC graph is also a non-JPEG image on the page**. An
earlier run filed EPC charts as floorplans for four listings on exactly that
mistake. Scoping to the gallery container is what makes it safe.

### 3. One call can get everything

Navigating to `?console=open&tab=floor_plans` and extracting in a single
JS evaluation yields description, key facts, photos and floorplan together.

### 4. Gallery completeness validation — mandatory

Before adding or retaining a listing, open its public Images gallery and read
the displayed total from `Property photo X of N` alt text or gallery labels.
Scroll **every** property-gallery `img` node to force lazy loading, capture and
deduplicate every image URL, then assert the captured count is exactly `N`.
Do not publish a one-photo/sample gallery. If any gallery image fails to load
or the count cannot be reconciled, exclude that listing and report why.

For floorplans, trust only images inside
`aria-label="Floor plan images"` after opening
`?console=open&tab=floor_plans`; otherwise record `null`. Never infer a
floorplan from an image filename or EPC artwork.

---

## The quality bar — what to exclude

- **Shared ownership** (Coronation Square, Beck Square, Priory Court…) — the
  headline price is a share, not the property.
- **Homewise / "Home for Life" lifetime leases** — discounted schemes for
  over-60s, not open-market sales.
- **Retirement properties** with age restrictions.
- **Over budget** — above roughly £525k. A small number of notable
  over-budget listings are kept deliberately for comparison; do not add more.
- **Outside the 14 reset postcodes** — all districts other than E4, E5, E8, E10, E17, N4, N5, N7, N8, N15, N16, N17, N19 and N22 are out.
- **Relisted-but-stale.** OnTheMarket may show "Added < 7 days" for a property
  Zoopla shows as `Back to market`, originally listed months ago. Trust Zoopla's
  date. These are not new.
- **Price reductions only.** A reduction is not a new posting.
- **Mismarketed.** A "2 bed" whose own description says one bedroom plus a
  study does not meet the criterion.

---

## Editing `archive.json`

### Reset exception and normal updates

The 2 October 2026 run was an explicitly authorised destructive reset. After that reset, runs must never rewrite history. Touch `lastSeen` on an existing listing and
nothing else; add new listings; append one `runs` entry. Assert it:

```python
for lid, old in BEFORE["listings"].items():
    assert lid in AFTER["listings"], f"LOST {lid}"
    for k, v in old.items():
        if k == "lastSeen":
            continue
        assert AFTER["listings"][lid].get(k) == v, f"MUTATED {lid}.{k}"
```

This matters: a session once rebuilt the file from a stale copy and silently
dropped twelve in-scope listings.

### Ageing and the NEW ribbon

- `lastSeen` — set to today for every listing you actually re-confirm as live
  on a portal today. `STALE_DAYS = 4`: anything not re-confirmed drops out of
  current matches into the archive automatically. **Re-confirming is a real
  part of the job**, not an afterthought — skip it and the tracker empties.
- `firstSeen` — the run that first found it. Never edit afterwards.
- The **NEW** ribbon is derived, not stored: `is_new()` compares `firstSeen`
  against the *previous* run's date. Nothing to set.

### Listing shape

```jsonc
{
  "id": "zoopla-74109978",          // "<portal>-<portal id>"
  "address": "Ruckholt Road, Leyton, London E10",
  "postcode": "E10",                 // district only
  "price": 425000,                   // integer, no separators
  "priceText": "£425,000 (offers over)",
  "priceBadge": "in-budget",         // in-budget | at-budget-edge | above-budget
  "beds": 3, "baths": 2,
  "type": "Flat",
  "tenure": "Leasehold (approx. 240 years remaining, £450 pa ground rent, ...)",
  "description": "...",
  "agent": "OC Homes",
  "listingUrl": "https://www.zoopla.co.uk/for-sale/details/74109978/",
  "sourcePortal": "Zoopla",
  "dateAddedText": "Added 01/09/2026",
  "firstSeen": "2026-09-05", "lastSeen": "2026-09-05",
  "commuteBadge": "borderline",      // pass | borderline | stretch
  "commuteToWMC": "~18 min (walk + change)",
  "nearestStation": "Leyton (Central line)",
  "walkToStation": "~8 min / 0.4 mi",
  "lat": 51.5595, "lon": -0.0125,    // street/postcode level is fine
  "groundRentPA": 450,               // number or null
  "serviceChargePA": 6000,           // number or null
  "leaseYearsRemaining": 240,        // number or null
  "groundRentReview": "…",           // string or null
  "commuteWarning": null,            // string or null — shown as a caveat
  "floorplan": "https://lid.zoocdn.com/u/1024/768/<hash>.jpg",  // or null
  "photos": ["https://lid.zoocdn.com/u/1024/768/<hash>.jpg", "…"],
  "opinion": { "score": 6, "pros": ["…"], "cons": ["…"] }
}
```

`currentOverride: {"action": "exclude", "reason": "…"}` force-excludes a
listing from current matches regardless of freshness.

### Scoring

Score 1-10 against `criteria.opinionCriteria` in `archive.json` — price vs the
£525k guide, commute quality to Walthamstow Central, walk to station, tenure,
livable space, outdoor space, condition, chain/format quirks, extension
potential.

Be consistent with what is already scored rather than inventing a new scale:
8s have gone to strong tenure plus a genuine `pass` commute; a `borderline`
commute realistically caps a listing at 7 however good the rest is. **Never
write pros/cons for a listing you have not actually opened and read** — if
there is not time to assess everything, add what you assessed and list the rest
plainly in the summary as unassessed.

---

## Reporting back

Flag anything scoring **8/10 or higher** immediately and prominently, with the
price, the headline facts and the listing URL. Otherwise stay brief — Charlie
checks in on his own schedule. Always say which areas were searched, and be
straight about anything that was not covered.
