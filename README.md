# Walthamstow House Hunt tracker

> **Running a scan from a clone, without access to Charlie's machine?**
> Read [`RUNNING_A_SCAN.md`](RUNNING_A_SCAN.md) first — it covers the git-based
> workflow, which portal to trust for freshness, and the extraction traps that
> have caused real scoring errors.


`index.html` is the page to open — self-contained, works by double-clicking it
(needs an internet connection for photos and the map tiles/library, since those
load from OnTheMarket / Zoopla's photo CDN, cdnjs.cloudflare.com and cartocdn.com).

## Serving it on the local network (pm2)

To make it reachable from any device on the home network (phone, laptop, etc.)
instead of only by opening the file locally, run it under pm2 as a tiny static
file server. One-time setup, from a terminal on this machine:

```
cd ~/Desktop/House-Hunting
pm2 start server.py --name house-hunt -- 8090
pm2 save
```

That serves this whole folder (so `index.html` is at `/`) on port 8090, on
all network interfaces — so from any other device on the same network, go to
`http://<this-pc's-LAN-IP>:8090/`. Find the LAN IP with `hostname -I` (usually
the first address printed).

**Do not put this back on port 8080.** That is Open WebUI's default port, and
Open WebUI is pip-installed on this machine. When house-hunt held 8080 it won
the race, Open WebUI could not bind, and any browser tab or PWA still pointing
at `localhost:8080` reached this static server instead — which correctly 404s
Open WebUI's entire app shell (`/_app/immutable/...`, `/_app/version.json`,
`/static/splash.png`, `/api/config`) and drops it into an `/error` retry loop.
The symptom looks like the tracker is broken; it isn't, the two apps are just
fighting over one port.

It is `server.py` rather than `pm2 serve` because plain static serving gave no
way to persist favourites — see "Favorite / followed up / rejected" below.

Because it's just serving static files straight from this folder, you do
**not** need to restart pm2 after each day's run — as soon as
`build_page.py` rewrites `index.html`, the live page picks it up on next
reload.

To have it survive a reboot: `pm2 startup` (one-time; run the sudo command
it prints), then `pm2 save` again. Useful commands afterwards:
`pm2 status`, `pm2 logs house-hunt`, `pm2 restart house-hunt`,
`pm2 delete house-hunt` (to stop serving it).

## Confirmed target areas (24 Aug 2026)

Walthamstow (E17, including the Blackhorse Road vicinity) and Chingford /
Highams Park (E4) are both user-confirmed acceptable commute areas — treat
listings there as in-scope even where Zoopla's own travel-time calculator
doesn't surface them (it doesn't return E4 at all, for reasons unknown; the
user knows the direct Chingford branch Overground works fine in practice).
Leyton (E10) has turned up genuine Zoopla-verified matches too. Clapton (E5)
and South Tottenham (N15/N17) are still fair game to search but nothing
there is currently confirmed — see `currentOverride` below.

## How new listings age: current vs. archive

Because this is checked roughly weekly rather than daily, `build_page.py`
computes "current matches" live from each listing's `lastSeen` date rather
than reading a fixed list:

- A listing counts as **current** if some run has seen it within the last
  `STALE_DAYS` (4) of the latest run date. Stop finding it for longer than
  that and it's assumed sold/withdrawn, and it quietly drops into the
  Archive tab — no manual pruning needed.
- A listing gets the **NEW** ribbon only if the **most recent run is the one
  that first found it**. It is deliberately *not* a rolling day count: a
  listing must stop being marked NEW as soon as any later run happens, no
  matter how close together the runs are. Concretely, `is_new()` compares
  `firstSeen` against the **previous** run's date, so a listing first seen at
  any point since that run is NEW and everything older is not.

  This replaced an earlier `NEW_DAYS = 7` rolling window. That window silently
  broke once runs became more frequent than weekly: with runs on 24, 26 and 29
  August 2026 every listing fell inside the 7 days and **all 43 cards showed
  the ribbon**, which conveys exactly as much as none of them showing it. If
  runs ever go back to being weeks apart, the run-anchored rule still behaves
  correctly — it does not need retuning.
- A listing can be force-excluded from current matches regardless of
  freshness by adding `"currentOverride": {"action": "exclude", "reason":
  "..."}` to its entry — used for e.g. Almack Road, Clapton, whose commute
  isn't confirmed. Remove the field (or the user confirms the area) to let
  it rejoin current matches normally.

`STALE_DAYS` lives at the top of `build_page.py` if it needs adjusting.
There is no longer a `NEW_DAYS` constant to tune — the NEW rule is derived
from the `runs` log itself, in `is_new()`.

## Google Maps links

Every card and modal has two links, built from the listing's `lat`/`lon`:

- **View on Google Maps** — pins the property itself.
- **Directions to Walthamstow Central** — opens Google Maps directions from the
  property to Walthamstow Central Station, defaulting to transit mode.

These are computed in `build_page.py` (and mirrored in the modal's inline JS)
from `lat`/`lon`, so nothing needs to be stored per-listing beyond the
coordinates that already exist.

## Claude's opinion

Each listing carries an `"opinion"` object: `{"score": 1-10, "pros": [...],
"cons": [...]}`. It shows as a badge next to the price (on both the card and
in the modal) and as a full pros/cons breakdown inside the modal. The
`"Claude's score: high to low"` sort option in the toolbar sorts by it.

This is a judgement call, not a formula — but it's applied consistently
against the same rubric every time (also recorded in `archive.json`'s
`criteria.opinionCriteria`):

- Price vs the £525k guide budget
- Commute quality/reliability to Walthamstow Central (confirmed vs estimated;
  walk+bus is weaker than walk+train)
- Walk distance to the nearest station
- Tenure — freehold/share of freehold preferred; for leasehold, years
  remaining, ground rent, service charge
- Livable space — square footage where stated, bed/bath count, layout
- Outdoor space — private vs shared garden, balcony, parking
- Condition — move-in ready vs needs modernisation, and what that adds to
  real total cost
- Chain status and format quirks (e.g. sale by auction)
- Extension/improvement potential

A listing without an `"opinion"` field (e.g. one added by a daily run before
it's been evaluated) just shows no score badge and sorts to the bottom of
the score sort — it doesn't break anything.

## Favorite / followed up / rejected

Each card has three small buttons top-right of its photo (also mirrored in
the detail modal): ❤️ favorite, ✅ followed up, ✕ not interested / reject.
Clicking one toggles it; favorite and followed-up can combine, but marking
something rejected clears the other two (and vice versa).

This state is stored **server-side** in `status.json`, written by
`server.py`'s API — not in browser `localStorage`, and not in
`archive.json`. Practically that means:

- It is shared across every device and browser that reaches the same
  server, so a reject marked on your phone shows up on the laptop.
- It survives a full `build_page.py` rebuild. `build_page.py` reads
  `status.json` at build time and bakes the current flags into the page so
  they are right on first paint, then the page re-fetches `GET /api/status`
  on load to pick up anything newer.
- **Always rebuild with `status.json` present in the same folder.** Running
  `build_page.py` in a directory without it silently bakes in an empty set
  (`let statuses = {}`). Nothing is lost server-side and the page self-heals
  via its fetch, but opening `index.html` directly over `file://` would then
  show no flags at all.
- Opening the file directly over `file://` has no server to talk to, so
  flags cannot be read or saved that way. Use the pm2 URL.
- Any old `localStorage` state from before this change is migrated up to the
  server automatically on first load, once, then flagged as migrated.

The toolbar has a "Hide rejected" checkbox and a "Favorites first" sort
option to go with it.

## Daily automation — currently manual only

The original plan was a scheduled task firing automatically once a day, but
that turned out not to work in this setup: `requires_local_device`
scheduled tasks need a one-time device-binding approval that never
surfaced as a prompt, and once the session was attached to this Claude
Project, moving the task to run on-device instead of in the cloud stopped
being an option entirely ("connected to a project" is unsupported for that
switch). Automating around that would mean giving up the property photos
and the map (see below), which isn't a fair trade. So for now, refreshing
the tracker is a manual step: open a new chat and paste in the run prompt
Charlie was given when this was set up (search chat history for "daily
automated scan for Charlie's Walthamstow house-hunt tracker" to find it
again, or just ask Claude to re-generate it from this README's
description of what a run does). The scheduled task itself still exists,
disabled, in case device binding becomes possible later.

A run does the following:

1. Stages the current `archive.json` / `build_page.py` from
   `~/Desktop/House-Hunting/` on the user's machine (mint-main) via the
   device bridge.
2. Searches OnTheMarket via WebFetch (reliably fetchable unattended, no
   browser needed) across the confirmed areas above, 2-3 bed, posted in the
   last 2 weeks.
3. If a connected browser happens to be available that run, optionally also
   checks Zoopla's travel-time search for extra verified candidates — but
   never blocks on this.
4. For each candidate: updates `lastSeen` if already known, or adds it
   fresh with today as both `firstSeen` and `lastSeen`, estimating
   commute/station details the same way the original run did, and writing
   an `"opinion"` object (score 1-10 + pros/cons) using the rubric in
   `criteria.opinionCriteria` / the "Claude's opinion" section above.
5. Appends a new entry to `runs` (kept as a historical log; it no longer
   drives what counts as "current" — see above).
6. Runs `python3 build_page.py` and commits the regenerated `index.html`
   plus updated `archive.json` back to `~/Desktop/House-Hunting/`.
7. If anything scores 8/10 or higher, says so immediately rather than
   waiting to be asked — otherwise stays quiet, since Charlie checks in on
   their own schedule.

## Reading tenure and running costs (learn from the 5 Sep miss)

A listing's service charge, ground rent and lease length live in **two different
places** on a Zoopla page, and a run must read **both** or it will publish a
score based on a property that costs thousands a year more than it appears to:

1. **The end of the description text.** Some agents append a `Tenure
   Information` block after all the prose. On 5 Sep the extractor truncated the
   description at 1,500 characters and cut this off, missing Ruckholt Road's
   **£500 per month** service charge entirely -- a listing that was flagged to
   Charlie as 8/10 and "best value in the tracker" when it was neither.
2. **The structured facts panel further down the page.** Separate from the
   description, Zoopla renders `Service charge` / `Council tax band` /
   `Ground rent` / `Ground rent date of next review` / `Ground rent review
   period`. The 5 Sep run never looked at this panel at all. It is where
   Shingly Place's £2,047 service charge and its **five-yearly ground rent
   review** were sitting.

Practical rules:

- Never truncate the description before searching it for `Tenure`, `Service
  Charge`, `Ground Rent` or `Council Tax`.
- Always read the facts panel as well; `Ask agent` there is a real finding
  (it means genuinely unpublished) and should be recorded as such, which is
  different from not having looked.
- A **ground rent review period** is as important as the amount. Escalating or
  doubling clauses can make a flat unmortgageable and are a lender red flag.
- Fixed annual charges belong in the opinion, not just the data. £6,450 a year
  is roughly £90,000 of extra mortgage at current rates -- enough to move a
  listing several points and to change which property is actually cheapest.

## Known limitations

- Commute times for any listing *not* confirmed via Zoopla's travel-time
  search are estimates from known station/line geography, not a TfL
  Journey Planner API call per listing.
- OnTheMarket floorplan images aren't discoverable from a plain WebFetch of
  the listing page (they load via a client-side route) — the daily
  automation infers the floorplan URL by reusing the timestamp segment
  from a listing's own photo URLs against OnTheMarket's
  `floor-plan-0-{size}.jpg` naming convention, which has worked reliably so
  far but could occasionally end up wrong or missing if OnTheMarket changes
  that convention.
- Map pin coordinates are street/postcode-level approximations, not
  geocoded to the exact building.
- One listing sourced from Zoopla (44 Knotts Green Road, Leyton) is for
  sale by auction — its `price` is a guide price only; the final sale
  price is likely to be higher.
- `index.html` needs an internet connection to load photos and the map
  tiles/library (OnTheMarket / Zoopla's photo CDNs, `cdnjs.cloudflare.com`
  for the Leaflet map library, `basemaps.cartocdn.com` for map tiles).
