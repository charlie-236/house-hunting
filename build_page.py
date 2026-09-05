#!/usr/bin/env python3
"""
Builds index.html from archive.json for the Walthamstow house-hunt tracker.

Usage:
    python3 build_page.py

Re-run any time archive.json has been updated with a new day's run
(see README.md for how a new run should be added).
"""
import json
import html
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote

WMC_MAPS_DEST = quote("Walthamstow Central Station, London")

ROOT = Path(__file__).parent
DATA_PATH = ROOT / "archive.json"
OUT_PATH = ROOT / "index.html"
STATUS_PATH = ROOT / "status.json"

# How this ages listings for someone who only checks in roughly weekly:
#  - STALE_DAYS: if a listing hasn't turned up in a run for this many days, it's
#    assumed sold/withdrawn and drops out of "current matches" into the archive
#    automatically -- no manual pruning needed day to day. A few days of grace
#    (rather than exactly 1) absorbs an occasional missed/incomplete daily run
#    without a still-live listing flickering out and back.
#  - The "NEW" ribbon is NOT a day count. A listing is NEW only if the most
#    recent run is the one that first found it; the moment any later run
#    happens it stops being NEW, however frequently runs occur. This replaced
#    an earlier NEW_DAYS = 7 rolling window, which broke as soon as runs became
#    more frequent than weekly: with runs on 24, 26 and 29 Aug 2026 every
#    listing fell inside the 7-day window and all 43 cards showed the ribbon,
#    which is the same as none of them showing it. See is_new() below.
STALE_DAYS = 4

# Default mortgage assumptions for the "total monthly cost" estimate (deposit as a flat
# £ amount, not a %, per Charlie's ~£80k pot minus stamp duty). Adjustable live in the
# page itself via the "Mortgage assumptions" panel -- these are just the first-paint
# defaults and what the page falls back to if nothing's saved in the browser yet.
#
# Updated 29 Aug 2026 to match the broker's indicative quote, which replaces the
# earlier placeholder guesses (£50k / 5.0% / 30yr) with real quoted terms:
#   £525,000 purchase, £52,500 deposit (10% -- 90% LTV), 4.77% 5-year fixed,
#   26-year term, £1,499 product fee, £2,653/month.
# Caveats worth keeping in mind when reading the per-listing totals:
#  - The deposit is a flat £52,500, so it is only 10% on a £525k listing. On a
#    cheaper listing it is a larger %, which in reality would earn a BETTER rate
#    than 4.77% (LTV bands), so those totals are, if anything, slightly pessimistic.
#    On anything above £525k it is a smaller %, pushing past 90% LTV where the
#    quoted rate would no longer be available at all -- treat those as optimistic.
#  - The £1,499 product fee is excluded here. In the real quote it is added to the
#    loan (£472,500 + £1,499 = £473,999), worth about £8/month.
DEFAULT_ASSUMPTIONS = {"deposit": 52500, "rate": 4.77, "term": 26, "other": 0}

def mortgage_payment(principal, annual_rate_pct, years):
    """Standard repayment-mortgage monthly payment."""
    r = annual_rate_pct / 100 / 12
    n = years * 12
    if principal <= 0 or n <= 0:
        return 0
    if r == 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** -n)

def lease_bits(l):
    """Lease term / service charge / ground rent / ground-rent-review clause as
    label-value pairs, for the line shown on the card and in the detail modal.

    Only the parts a listing actually states are returned. An unknown figure is
    left out entirely rather than rendered as £0 -- same reasoning as the
    "(service charge not stated)" note on the total (see README, "Total monthly
    cost"): an unstated service charge on a flat can be hundreds a month, and
    showing it as zero would quietly understate the real cost. A stated £0
    (a freehold house) does show, so "known to be nil" and "nobody has said"
    are visibly different.
    """
    bits = []
    years = l.get("leaseYearsRemaining")
    if years is not None:
        bits.append(("Lease", f"{years} years remaining"))
    service_pa = l.get("serviceChargePA")
    if service_pa is not None:
        bits.append(("Service charge", f"£{service_pa:,.0f}/yr (£{service_pa / 12:,.0f}/mo)"))
    ground_pa = l.get("groundRentPA")
    if ground_pa is not None:
        bits.append(("Ground rent", f"£{ground_pa:,.0f}/yr"))
    review = l.get("groundRentReview")
    if review:
        bits.append(("Ground rent review", review))
    return bits

def total_monthly_cost(l, a=DEFAULT_ASSUMPTIONS):
    loan = max(l["price"] - a["deposit"], 0)
    mortgage = mortgage_payment(loan, a["rate"], a["term"])
    service_pa = l.get("serviceChargePA")
    ground_pa = l.get("groundRentPA")
    service = (service_pa or 0) / 12
    ground = (ground_pa or 0) / 12
    other = a["other"]
    return {
        "mortgage": mortgage, "service": service, "ground": ground, "other": other,
        "total": mortgage + service + ground + other,
        "service_unknown": service_pa is None,
        "ground_unknown": ground_pa is None,
    }

data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
listings = data["listings"]

# Favorite/followed-up/rejected status lives in its own sidecar file, written by
# server.py's /api/status endpoint -- not in archive.json, and not in browser
# localStorage (see README: that used to be per-browser, which is exactly the
# "doesn't follow me across devices" problem this replaced). Baking the current
# values in here means the page shows the right state on first paint even before
# its one JS fetch to /api/status comes back.
try:
    initial_statuses = json.loads(STATUS_PATH.read_text(encoding="utf-8")) if STATUS_PATH.exists() else {}
except json.JSONDecodeError:
    initial_statuses = {}
runs = sorted(data["runs"], key=lambda r: r["date"])
criteria = data["criteria"]
latest_run = runs[-1]
latest_run_date = latest_run["date"]
_latest_dt = datetime.strptime(latest_run_date, "%Y-%m-%d")

def _days_before_latest(d):
    return (_latest_dt - datetime.strptime(d, "%Y-%m-%d")).days

def is_current(l):
    if l.get("currentOverride", {}).get("action") == "exclude":
        return False
    return _days_before_latest(l["lastSeen"]) <= STALE_DAYS

_prev_run_date = runs[-2]["date"] if len(runs) > 1 else None

def is_new(l):
    """True only for listings this latest run was the first to find.

    Anchored to the PREVIOUS run's date, not to a rolling number of days, so
    that a listing is never still flagged NEW after a subsequent run -- which
    is the whole point of the ribbon. Comparing against the previous run date
    rather than requiring firstSeen == latest_run_date also correctly flags a
    listing whose firstSeen fell between two runs.
    """
    if _prev_run_date is None:
        return True  # the very first run: everything it found is genuinely new
    return l["firstSeen"] > _prev_run_date

# "Current" is computed live from lastSeen/firstSeen rather than read off a fixed
# per-run list, so a listing that simply stops turning up in daily runs ages out
# on its own -- the daily update only ever needs to touch lastSeen/firstSeen.
current_ids = {lid for lid, l in listings.items() if is_current(l)}

badge_labels = {
    "pass": ("Within target", "badge-pass"),
    "borderline": ("Verify commute", "badge-borderline"),
    "stretch": ("Likely over 15 min", "badge-stretch"),
}
price_badge_labels = {
    "in-budget": ("In budget", "pbadge-good"),
    "at-budget-edge": ("At budget edge", "pbadge-mid"),
    "above-budget": ("Above £525k guide", "pbadge-over"),
}

def fmt_date(d):
    value = datetime.strptime(d, "%Y-%m-%d")
    return f"{value.day} {value.strftime('%B %Y')}"

def score_tier(score):
    """Reuse the price-badge colour scale (green/amber/red) for Claude's opinion score."""
    if score is None:
        return "pbadge-mid"
    if score >= 7:
        return "pbadge-good"
    if score >= 5:
        return "pbadge-mid"
    return "pbadge-over"

def gmaps_view_url(l):
    # Use the address text, not our own lat/lon -- those are hand-estimated
    # street/postcode-level approximations (see Known limitations) and can be off by
    # enough to land in the wrong field entirely. Google's own geocoding of the actual
    # address is far more accurate than anything we can guess.
    return f"https://www.google.com/maps/search/?api=1&query={quote(l['address'])}"

def gmaps_directions_url(l):
    return (f"https://www.google.com/maps/dir/?api=1&origin={quote(l['address'])}"
            f"&destination={WMC_MAPS_DEST}&travelmode=transit")

def listing_card(l, seen_dates=None):
    photos = l.get("photos") or []
    main_photo = photos[0] if photos else ""
    thumbs = "".join(
        f'<img src="{html.escape(p)}" loading="lazy" data-full="{html.escape(p)}" class="thumb" onclick="swapMain(this)" alt="">'
        for p in photos[1:8]
    )
    floorplan_url = l.get("floorplan")
    if floorplan_url:
        thumbs = (f'<button type="button" class="floorplan-selector" aria-label="Open floorplan" '
                  f"onclick='event.stopPropagation(); openFloorplan({json.dumps(floorplan_url)}); return false;'>"
                  f'<span aria-hidden="true">📐</span><span>Floor plan</span></button>') + thumbs
    commute_label, commute_class = badge_labels.get(l["commuteBadge"], ("Unknown", ""))
    price_label, price_class = price_badge_labels.get(l["priceBadge"], ("", ""))
    if floorplan_url:
        floorplan_html = (f'<a class="fp-link" href="{html.escape(floorplan_url)}" '
                           f"onclick='event.preventDefault(); event.stopPropagation(); openFloorplan({json.dumps(floorplan_url)}); return false;'>📐 Floorplan</a>")
    else:
        floorplan_html = '<span class="fp-none">No floorplan provided</span>'

    seen_html = ""
    if seen_dates:
        seen_html = f'<div class="seen-dates">Seen: {", ".join(fmt_date(d) for d in seen_dates)}</div>'

    new_ribbon = '<span class="new-ribbon">NEW</span>' if is_new(l) else ""
    status_html = ""
    if not is_current(l):
        override = l.get("currentOverride")
        if override and override.get("action") == "exclude":
            status_html = f'<div class="status-line status-excluded">Not in current matches: {html.escape(override.get("reason", ""))}</div>'
        else:
            status_html = f'<div class="status-line status-gone">Likely no longer listed — last seen {fmt_date(l["lastSeen"])}</div>'

    opinion = l.get("opinion")
    score = opinion["score"] if opinion else None
    score_html = (f'<span class="pbadge {score_tier(score)} score-badge" title="Claude\'s opinion">🤖 {score}/10</span>'
                  if score is not None else "")

    maps_html = (f'<div class="maps-links">'
                 f'<a href="{gmaps_view_url(l)}" target="_blank" rel="noopener">📍 View on Google Maps</a>'
                 f'<a href="{gmaps_directions_url(l)}" target="_blank" rel="noopener">🧭 Directions to Walthamstow Central</a>'
                 f'</div>')

    bits = lease_bits(l)
    lease_html = ""
    if bits:
        lease_html = ('<div class="lease-details">🔑 ' + " &middot; ".join(
            f'<span class="ld-k">{html.escape(k)}:</span> {html.escape(v)}' for k, v in bits) + '</div>')

    commute_warning_html = ""
    if l.get("commuteWarning"):
        commute_warning_html = f'<div class="commute-warning">⚠️ {html.escape(l["commuteWarning"])}</div>'

    tm = total_monthly_cost(l)
    unknowns = []
    if tm["service_unknown"]:
        unknowns.append("service charge")
    if tm["ground_unknown"]:
        unknowns.append("ground rent")
    tm_unknown_html = f' <span class="tm-unknown">({" &amp; ".join(unknowns)} not stated)</span>' if unknowns else ""
    total_monthly_html = (f'<div class="total-monthly" data-id="{l["id"]}">'
                          f'<span class="tm-label">💷 Est. total monthly</span> '
                          f'<span class="tm-amount">£{tm["total"]:,.0f}/mo</span>{tm_unknown_html}'
                          f'</div>')

    return f"""
    <article class="card" data-id="{l['id']}" data-price="{l['price']}" data-beds="{l['beds']}"
             data-commute="{l['commuteBadge']}" data-lat="{l['lat']}" data-lon="{l['lon']}"
             data-first-seen="{l['firstSeen']}" data-score="{score if score is not None else -1}"
             data-total-monthly="{tm['total']:.0f}" tabindex="0">
      <div class="card-media">
        {new_ribbon}
        <div class="status-btns" data-id="{l['id']}">
          <button class="status-btn" data-key="favorite" title="Favorite" aria-pressed="false"><span aria-hidden="true">❤️</span><span class="status-label">Favorite</span></button>
          <button class="status-btn" data-key="followedUp" title="Followed up" aria-pressed="false"><span aria-hidden="true">✅</span><span class="status-label">Followed up</span></button>
          <button class="status-btn" data-key="rejected" title="Not interested" aria-pressed="false"><span aria-hidden="true">✕</span><span class="status-label">Reject</span></button>
        </div>
        <img class="main-photo" src="{html.escape(main_photo)}" alt="{html.escape(l['address'])}" loading="lazy" onerror="this.onerror=null;this.style.display='none';this.insertAdjacentHTML('afterend','<div class=&quot;photo-fallback&quot;>Photo unavailable — see listing</div>')">
        <div class="thumbs">{thumbs}</div>
      </div>
      <div class="card-body">
        <div class="card-top">
          <span class="price">{html.escape(l['priceText'])}</span>
          <span class="pbadge {price_class}">{price_label}</span>
          {score_html}
        </div>
        <h3 class="address">{html.escape(l['address'])}</h3>
        {status_html}
        <div class="meta">{l['beds']} bed &middot; {l['baths']} bath &middot; {html.escape(l['type'])} &middot; {html.escape(l['tenure'])}</div>
        {lease_html}
        {total_monthly_html}
        <div class="badges">
          <span class="badge {commute_class}">{commute_label}: {html.escape(l['commuteToWMC'])}</span>
        </div>
        {commute_warning_html}
        <div class="station">🚉 {html.escape(l['nearestStation'])} &mdash; {html.escape(l['walkToStation'])}</div>
        {maps_html}
        <p class="desc">{html.escape(l['description'])}</p>
        <div class="card-footer">
          <div class="fp">{floorplan_html}</div>
          <div class="added">Added: {html.escape(l['dateAddedText'])} &middot; {html.escape(l['agent'])}</div>
        </div>
        {seen_html}
        <a class="view-listing" href="{html.escape(l['listingUrl'])}" target="_blank" rel="noopener">View full listing on {html.escape(l['sourcePortal'])} ↗</a>
      </div>
    </article>"""

# ---- Current listings ----
# Default order is newest-first (by firstSeen, price as tiebreak) rather than
# price -- for a weekly check-in, what's changed since last time matters more
# than re-sorting a list that's mostly the same as before.
# sorted() so ties are broken deterministically: current_ids is a set, and set
# iteration order over strings varies run to run (PYTHONHASHSEED), which made two
# listings on the same price *and* firstSeen swap places between otherwise
# identical builds -- spurious churn in every rebuild's diff.
current_listings = [listings[i] for i in sorted(current_ids)]
current_listings.sort(key=lambda l: l["price"])  # secondary: price asc within same day
current_listings.sort(key=lambda l: l["firstSeen"], reverse=True)  # primary: newest first (stable)
current_cards_html = "\n".join(listing_card(l) for l in current_listings)
new_count = sum(1 for l in current_listings if is_new(l))

# ---- Archive: group every listing ever seen by firstSeen date ----
all_by_first_seen = {}
for lid, l in listings.items():
    all_by_first_seen.setdefault(l["firstSeen"], []).append(l)

archive_sections = []
for date in sorted(all_by_first_seen.keys(), reverse=True):
    items = sorted(all_by_first_seen[date], key=lambda l: l["price"])
    cards = "\n".join(listing_card(l) for l in items)
    archive_sections.append(f"""
      <section class="archive-run">
        <h3 class="archive-date">{fmt_date(date)} <span class="archive-count">({len(items)} listing{'s' if len(items) != 1 else ''} first seen)</span></h3>
        <div class="grid">{cards}</div>
      </section>""")
archive_html = "\n".join(archive_sections)

# ---- Map data ----
map_points = [
    {
        "id": l["id"], "lat": l["lat"], "lon": l["lon"], "price": l["priceText"],
        "address": l["address"], "beds": l["beds"], "type": l["type"],
        "url": l["listingUrl"], "commute": l["commuteBadge"],
        "photo": (l["photos"][0] if l.get("photos") else "")
    }
    for l in current_listings
]

run_history_rows = "\n".join(
    f'<li><strong>{fmt_date(r["date"])}</strong> &mdash; {len(r["listingIds"])} matching listings &middot; {", ".join(r["portalsChecked"])}'
    f'{" — " + r["notesForRun"] if r.get("notesForRun") else ""}</li>'
    for r in sorted(runs, key=lambda r: r["date"], reverse=True)
)

total_ever_seen = len(listings)

HTML = f"""<title>Walthamstow House Hunt</title>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"/>
<style>
  :root {{
    --bg: #f6f4f0; --surface: #ffffff; --surface-2: #fbfaf8;
    --text: #1c1b19; --text-dim: #6b6559; --border: #e7e2d9;
    --accent: #2f6f4f; --accent-2: #b5652e;
    --pass-bg:#e4f3e8; --pass-fg:#1f6b3d;
    --border-bg:#fdf1de; --border-fg:#8a5a10;
    --stretch-bg:#fbe7e4; --stretch-fg:#a13a2b;
    --good-bg:#e4f3e8; --good-fg:#1f6b3d;
    --mid-bg:#fdf1de; --mid-fg:#8a5a10;
    --over-bg:#fbe7e4; --over-fg:#a13a2b;
    --shadow: 0 1px 2px rgba(28,27,25,0.06), 0 4px 16px rgba(28,27,25,0.06);
    color-scheme: light;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #17181a; --surface: #202225; --surface-2: #1b1c1e;
      --text: #ecebe8; --text-dim: #a2a09b; --border: #34363a;
      --accent: #6fbf95; --accent-2: #e0995e;
      --pass-bg:#173629; --pass-fg:#7fd6a4;
      --border-bg:#3a2f14; --border-fg:#e3b45c;
      --stretch-bg:#3a1f1b; --stretch-fg:#f2a291;
      --good-bg:#173629; --good-fg:#7fd6a4;
      --mid-bg:#3a2f14; --mid-fg:#e3b45c;
      --over-bg:#3a1f1b; --over-fg:#f2a291;
      --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 4px 20px rgba(0,0,0,0.35);
      color-scheme: dark;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #17181a; --surface: #202225; --surface-2: #1b1c1e;
    --text: #ecebe8; --text-dim: #a2a09b; --border: #34363a;
    --accent: #6fbf95; --accent-2: #e0995e;
    --pass-bg:#173629; --pass-fg:#7fd6a4;
    --border-bg:#3a2f14; --border-fg:#e3b45c;
    --stretch-bg:#3a1f1b; --stretch-fg:#f2a291;
    --good-bg:#173629; --good-fg:#7fd6a4;
    --mid-bg:#3a2f14; --mid-fg:#e3b45c;
    --over-bg:#3a1f1b; --over-fg:#f2a291;
    --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 4px 20px rgba(0,0,0,0.35);
    color-scheme: dark;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.5;
  }}
  header.top {{
    padding: 28px 24px 20px; border-bottom: 1px solid var(--border);
    background: var(--surface);
  }}
  .top-inner {{ max-width: 1180px; margin: 0 auto; }}
  h1 {{ margin: 0 0 6px; font-size: 1.7rem; letter-spacing: -0.01em; }}
  .subtitle {{ color: var(--text-dim); font-size: 0.95rem; max-width: 760px; }}
  .criteria-row {{ display: flex; flex-wrap: wrap; gap: 8px 22px; margin-top: 14px; font-size: 0.85rem; color: var(--text-dim); }}
  .criteria-row b {{ color: var(--text); font-weight: 600; }}
  .file-mode-notice {{
    margin-top: 12px; font-size: 0.8rem; background: var(--border-bg); color: var(--border-fg);
    border: 1px solid var(--border-fg); border-radius: 8px; padding: 8px 12px;
  }}
  .file-mode-notice code {{ background: rgba(0,0,0,0.08); padding: 1px 5px; border-radius: 4px; }}
  nav.tabs {{
    max-width: 1180px; margin: 18px auto 0; display: flex; gap: 4px;
  }}
  .tab-btn {{
    border: 1px solid var(--border); background: var(--surface-2); color: var(--text-dim);
    padding: 8px 16px; border-radius: 8px 8px 0 0; cursor: pointer; font-size: 0.9rem; font-weight: 600;
    border-bottom: none; position: relative; top: 1px;
  }}
  .tab-btn.active {{ background: var(--bg); color: var(--text); border-bottom: 1px solid var(--bg); }}
  main {{ max-width: 1180px; margin: 0 auto; padding: 20px 24px 60px; }}
  .view {{ display: none; }}
  .view.active {{ display: block; }}
  .toolbar {{
    display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 18px;
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px;
  }}
  .toolbar .count {{ font-weight: 600; margin-right: auto; }}
  select, .toggle-btn {{
    border: 1px solid var(--border); background: var(--surface-2); color: var(--text);
    border-radius: 7px; padding: 6px 10px; font-size: 0.85rem;
  }}
  .toggle-group {{ display: flex; border: 1px solid var(--border); border-radius: 7px; overflow: hidden; }}
  .toggle-group button {{
    border: none; background: var(--surface-2); color: var(--text-dim); padding: 6px 14px; cursor: pointer; font-size: 0.85rem;
  }}
  .toggle-group button.active {{ background: var(--accent); color: #fff; }}
  label.chk {{ display: flex; align-items: center; gap: 6px; font-size: 0.85rem; color: var(--text-dim); }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(310px, 1fr)); gap: 18px; }}
  .card {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    overflow: hidden; box-shadow: var(--shadow); display: flex; flex-direction: column;
  }}
  .card-media {{ position: relative; }}
  .new-ribbon {{
    position: absolute; top: 10px; left: 10px; z-index: 2;
    background: var(--accent); color: #fff; font-size: 0.7rem; font-weight: 700;
    letter-spacing: 0.04em; padding: 3px 9px; border-radius: 20px; box-shadow: var(--shadow);
  }}
  .new-ribbon-inline {{
    display: inline-block; background: var(--accent); color: #fff; font-size: 0.68rem; font-weight: 700;
    letter-spacing: 0.04em; padding: 1px 7px; border-radius: 10px; vertical-align: middle;
  }}
  .status-btns {{ position: absolute; top: 10px; right: 10px; z-index: 2; display: flex; gap: 6px; }}
  .status-btns.modal-status-btns {{ position: static; }}
  .status-btn {{
    width: 30px; height: 30px; border-radius: 50%; border: none; cursor: pointer;
    background: rgba(20,20,18,0.55); color: #fff; font-size: 0.9rem; line-height: 1;
    display: flex; align-items: center; justify-content: center; padding: 0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.3); transition: transform .1s ease, background .15s ease;
    filter: grayscale(100%); opacity: 0.85;
  }}
  .status-btn:hover {{ transform: scale(1.1); opacity: 1; }}
  .status-btn.active {{ filter: none; opacity: 1; background: rgba(20,20,18,0.7); }}
  .status-label {{ display: none; }}
  .card.status-favorite {{ border-color: #e0455c; border-width: 2px; }}
  .card.status-followedup {{ border-color: var(--accent); border-width: 2px; }}
  .card.status-rejected {{ opacity: 0.45; filter: grayscale(65%); }}
  .mortgage-calc {{
    max-width: 1180px; margin: 0 auto 18px; background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 10px 16px; font-size: 0.85rem;
  }}
  .mortgage-calc summary {{ cursor: pointer; font-weight: 600; color: var(--text); }}
  .mortgage-calc-inputs {{ display: flex; flex-wrap: wrap; gap: 14px 20px; margin-top: 12px; }}
  .mortgage-calc-inputs label {{ display: flex; flex-direction: column; gap: 4px; font-size: 0.78rem; color: var(--text-dim); }}
  .mortgage-calc-inputs input {{
    border: 1px solid var(--border); background: var(--surface-2); color: var(--text);
    border-radius: 6px; padding: 5px 8px; width: 140px; font-size: 0.85rem;
  }}
  .total-monthly {{
    font-size: 0.83rem; background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px;
    padding: 5px 10px; display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap;
  }}
  .tm-label {{ color: var(--text-dim); }}
  .tm-amount {{ font-weight: 700; }}
  .tm-unknown {{ font-size: 0.7rem; color: var(--border-fg); }}
  .tm-row {{ display: flex; justify-content: space-between; font-size: 0.85rem; padding: 3px 0; }}
  .tm-caveat {{ font-size: 0.72rem; color: var(--text-dim); margin-top: 8px; }}
  .status-line {{ font-size: 0.78rem; font-weight: 600; margin: 2px 0; }}
  .status-gone {{ color: var(--stretch-fg); }}
  .status-excluded {{ color: var(--border-fg); }}
  .lease-details {{ font-size: 0.8rem; color: var(--text-dim); }}
  .lease-details .ld-k {{ font-weight: 600; }}
  .commute-warning {{
    font-size: 0.76rem; font-weight: 600; line-height: 1.45;
    color: var(--stretch-fg); background: var(--stretch-bg);
    border-radius: 6px; padding: 5px 9px;
  }}
  .main-photo {{ width: 100%; height: 190px; object-fit: cover; display: block; background: var(--surface-2); }}
  .photo-fallback {{ height: 190px; display: flex; align-items: center; justify-content: center; background: var(--surface-2); color: var(--text-dim); font-size: 0.8rem; }}
  .thumbs {{ display: flex; gap: 4px; padding: 6px; overflow-x: auto; background: var(--surface-2); }}
  .thumb {{ width: 46px; height: 34px; object-fit: cover; border-radius: 4px; cursor: pointer; flex: none; opacity: 0.85; }}
  .thumb:hover {{ opacity: 1; outline: 2px solid var(--accent); }}
  .floorplan-selector {{
    height: 34px; flex: none; display: inline-flex; align-items: center; gap: 4px;
    border: 1.5px solid var(--accent-2); border-radius: 5px; padding: 0 8px;
    background: var(--surface); color: var(--text); font: inherit; font-size: 0.72rem; font-weight: 700;
    cursor: pointer; white-space: nowrap;
  }}
  .floorplan-selector:hover {{ background: var(--border-bg); }}
  .card-body {{ padding: 14px 16px 16px; display: flex; flex-direction: column; gap: 6px; }}
  .card-top {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .price {{ font-size: 1.15rem; font-weight: 700; margin-right: auto; }}
  .address {{ margin: 0; font-size: 1rem; font-weight: 600; }}
  .meta {{ font-size: 0.83rem; color: var(--text-dim); }}
  .badges {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 2px; }}
  .badge, .pbadge {{ font-size: 0.72rem; font-weight: 700; padding: 3px 9px; border-radius: 20px; white-space: nowrap; }}
  .badge-pass {{ background: var(--pass-bg); color: var(--pass-fg); }}
  .badge-borderline {{ background: var(--border-bg); color: var(--border-fg); }}
  .badge-stretch {{ background: var(--stretch-bg); color: var(--stretch-fg); }}
  .pbadge-good {{ background: var(--good-bg); color: var(--good-fg); }}
  .pbadge-mid {{ background: var(--mid-bg); color: var(--mid-fg); }}
  .pbadge-over {{ background: var(--over-bg); color: var(--over-fg); }}
  .station {{ font-size: 0.82rem; color: var(--text-dim); }}
  .maps-links {{ display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 0.78rem; margin: -2px 0 2px; }}
  .maps-links a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
  .maps-links a:hover {{ text-decoration: underline; }}
  .desc {{ font-size: 0.86rem; color: var(--text); margin: 2px 0 4px; }}
  .card-footer {{ display: flex; justify-content: space-between; align-items: baseline; font-size: 0.75rem; color: var(--text-dim); gap: 10px; flex-wrap: wrap; }}
  .fp-link {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
  .fp-none {{ color: var(--text-dim); }}
  .seen-dates {{ font-size: 0.72rem; color: var(--text-dim); }}
  .view-listing {{
    display: inline-block; margin-top: 6px; text-align: center; background: var(--accent); color: #fff !important;
    text-decoration: none; padding: 8px 12px; border-radius: 7px; font-size: 0.85rem; font-weight: 600;
  }}
  .view-listing:hover {{ opacity: 0.9; }}
  #map {{ height: 640px; border-radius: 12px; border: 1px solid var(--border); }}
  .archive-date {{ margin: 26px 0 12px; font-size: 1.05rem; border-bottom: 1px solid var(--border); padding-bottom: 6px; }}
  .archive-count {{ color: var(--text-dim); font-weight: 400; font-size: 0.85rem; }}
  .run-history {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 18px; margin-bottom: 22px; }}
  .run-history h3 {{ margin: 0 0 8px; font-size: 0.95rem; }}
  .run-history ul {{ margin: 0; padding-left: 18px; font-size: 0.85rem; color: var(--text-dim); }}
  .run-history li {{ margin-bottom: 4px; }}
  .caveat {{ font-size: 0.78rem; color: var(--text-dim); margin-top: 26px; border-top: 1px solid var(--border); padding-top: 14px; }}
  footer {{ text-align: center; padding: 20px; color: var(--text-dim); font-size: 0.78rem; }}
  .leaflet-popup-content {{ font-size: 0.85rem; }}
  .popup-photo {{ width: 100%; height: 90px; object-fit: cover; border-radius: 6px; margin-bottom: 6px; }}
  .leaflet-marker-icon {{ cursor: pointer; }}
  .leaflet-tooltip.map-tip-wrap {{
    background: var(--surface); color: var(--text); border: 1px solid var(--border);
    border-radius: 8px; box-shadow: var(--shadow); padding: 0; opacity: 1 !important;
  }}
  .leaflet-tooltip.map-tip-wrap::before {{ border-top-color: var(--border); }}
  .map-tip {{ display: flex; gap: 8px; align-items: center; padding: 8px 10px; font-size: 0.8rem; max-width: 220px; pointer-events: none; }}
  .map-tip img {{ width: 52px; height: 44px; object-fit: cover; border-radius: 5px; flex: none; }}
  .card {{ cursor: pointer; transition: transform .12s ease, box-shadow .12s ease; }}
  .card:hover {{ transform: translateY(-2px); box-shadow: 0 2px 4px rgba(28,27,25,0.08), 0 8px 24px rgba(28,27,25,0.1); }}
  .card:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

  /* ---- Detail modal ---- */
  .modal-overlay {{
    display: none; position: fixed; inset: 0; background: rgba(10,10,8,0.72);
    z-index: 1000; align-items: center; justify-content: center; padding: 24px;
  }}
  .modal-overlay.open {{ display: flex; }}
  .modal-content {{
    background: var(--surface); border-radius: 14px; max-width: 1080px; width: 100%;
    max-height: 92vh; overflow-y: auto; position: relative; box-shadow: 0 20px 60px rgba(0,0,0,0.4);
  }}
  .modal-close {{
    position: absolute; top: 10px; right: 10px; z-index: 5; width: 34px; height: 34px; border-radius: 50%;
    border: none; background: rgba(20,20,18,0.55); color: #fff; font-size: 1rem; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
  }}
  .modal-close:hover {{ background: rgba(20,20,18,0.8); }}
  .modal-gallery {{
    position: relative; background: #111; display: flex; align-items: center; justify-content: center;
    height: 56vh; min-height: 320px; border-radius: 14px 14px 0 0; overflow: hidden;
  }}
  .zoom-wrap {{ width: 100%; height: 100%; overflow: hidden; display: flex; align-items: center; justify-content: center; touch-action: none; }}
  .zoom-wrap img {{
    max-width: 100%; max-height: 100%; object-fit: contain; cursor: zoom-in; user-select: none;
    transform-origin: center center; will-change: transform;
  }}
  .zoom-wrap.zoomed img {{ cursor: grab; }}
  .zoom-wrap.dragging img {{ cursor: grabbing; }}
  .zoom-hint {{
    position: absolute; bottom: 8px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.55);
    color: #fff; font-size: 0.7rem; padding: 3px 10px; border-radius: 20px; pointer-events: none;
  }}
  .slide-label {{
    position: absolute; top: 10px; left: 12px; background: rgba(0,0,0,0.55); color: #fff;
    font-size: 0.75rem; font-weight: 600; padding: 3px 10px; border-radius: 20px;
  }}
  .nav-arrow {{
    position: absolute; top: 50%; transform: translateY(-50%); z-index: 4; width: 40px; height: 40px;
    border-radius: 50%; border: none; background: rgba(20,20,18,0.5); color: #fff; font-size: 1.3rem;
    cursor: pointer; display: flex; align-items: center; justify-content: center;
  }}
  .nav-arrow:hover {{ background: rgba(20,20,18,0.8); }}
  .nav-prev {{ left: 10px; }}
  .nav-next {{ right: 10px; }}
  .modal-thumbs {{ display: flex; gap: 6px; padding: 10px 16px; overflow-x: auto; background: var(--surface-2); }}
  .modal-thumb {{
    width: 62px; height: 46px; object-fit: cover; border-radius: 5px; cursor: pointer; flex: none; opacity: 0.7;
    border: 2px solid transparent; padding: 0; background: transparent;
  }}
  .modal-thumb img {{ width: 100%; height: 100%; object-fit: cover; display: block; border-radius: 3px; }}
  .modal-thumb.active {{ opacity: 1; border-color: var(--accent); }}
  .modal-thumb.floorplan-selector {{
    width: auto; padding: 0 10px; background: var(--surface); color: var(--text);
    border-color: var(--accent-2); opacity: 1;
  }}
  .modal-info {{ padding: 20px 24px 26px; }}
  .modal-info .price-row {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .modal-info .price {{ font-size: 1.5rem; margin-right: auto; }}
  .modal-info h2 {{ margin: 4px 0 6px; font-size: 1.25rem; }}
  .modal-info .meta {{ font-size: 0.9rem; margin-bottom: 8px; }}
  .modal-info .badges {{ margin-bottom: 10px; }}
  .modal-info .lease-details {{ font-size: 0.88rem; margin-bottom: 8px; }}
  .modal-info .commute-warning {{ font-size: 0.82rem; margin-bottom: 10px; }}
  .modal-info .station {{ font-size: 0.88rem; margin-bottom: 4px; }}
  .modal-info .maps-links {{ margin-bottom: 12px; }}
  .modal-info .desc {{ font-size: 0.92rem; line-height: 1.6; margin-bottom: 14px; }}
  .modal-info .modal-footer-row {{
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;
    border-top: 1px solid var(--border); padding-top: 14px;
  }}
  .modal-info .added {{ font-size: 0.8rem; color: var(--text-dim); }}
  .opinion-block {{
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px; margin-bottom: 14px;
  }}
  .opinion-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }}
  .opinion-title {{ font-weight: 700; font-size: 0.92rem; }}
  .opinion-cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  .opinion-col h4 {{ margin: 0 0 6px; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-dim); }}
  .opinion-col ul {{ margin: 0; padding-left: 18px; font-size: 0.85rem; line-height: 1.5; }}
  .opinion-col.pros li {{ color: var(--pass-fg); }}
  .opinion-col.cons li {{ color: var(--stretch-fg); }}
  .opinion-col li::marker {{ color: var(--text-dim); }}
  @media (max-width: 560px) {{
    .opinion-cols {{ grid-template-columns: 1fr; }}
  }}
  @media (max-width: 640px) {{
    .modal-gallery {{ height: 42vh; }}
    .modal-content {{ max-height: 96vh; }}
  }}

  /* ---- Narrow screens: layout that needs the extra width back ----
     Deliberately a viewport-width breakpoint, not user-agent sniffing: sniffing
     is brittle (new devices, in-app browsers, iPads that ID as desktop Safari),
     and width is what actually determines whether a row of controls fits. This
     is about SPACE, not touch -- see the separate pointer:coarse block below
     for tap-target sizing, which is a different axis (an 11" tablet has plenty
     of width but is still a finger, not a mouse, and a folded phone vs. that
     same phone unfolded flips this multiple times a day on the same device). */
  @media (max-width: 600px) {{
    header.top {{ padding: 18px 14px 14px; }}
    h1 {{ font-size: 1.35rem; }}
    .subtitle {{ font-size: 0.85rem; }}
    .criteria-row {{ font-size: 0.78rem; gap: 6px 14px; margin-top: 10px; }}
    nav.tabs {{ margin-top: 14px; }}
    .tab-btn {{ flex: 1; text-align: center; padding: 9px 10px; font-size: 0.85rem; }}
    main {{ padding: 14px 12px 40px; }}
    .toolbar {{ flex-direction: column; align-items: stretch; padding: 12px; }}
    .toolbar .count {{ margin-right: 0; text-align: center; }}
    .toggle-group {{ width: 100%; }}
    .toggle-group button {{ flex: 1; padding: 9px 0; }}
    #sortSelect {{ width: 100%; }}
    label.chk {{ justify-content: center; }}
    .mortgage-calc {{ margin-left: 12px; margin-right: 12px; }}
    .mortgage-calc-inputs {{ flex-direction: column; gap: 10px; }}
    .mortgage-calc-inputs label {{ width: 100%; }}
    .mortgage-calc-inputs input {{ width: 100%; }}
    .grid {{ grid-template-columns: 1fr; gap: 14px; }}
    /* Two short link/button labels don't need a full-width stack until space
       is actually tight -- above 600px they stay side by side (see below). */
    .maps-links {{ flex-direction: column; gap: 8px; margin: 4px 0; }}
    .maps-links a {{ display: block; }}
    .modal-info {{ padding: 16px 14px 22px; }}
    .modal-info .price {{ font-size: 1.3rem; }}
  }}

  /* ---- Any touch input, regardless of screen size ----
     A 10.9"/11" Android tablet (e.g. Galaxy Tab A9+) reports a CSS viewport
     around 800-1340px depending on orientation -- comfortably wider than the
     600px breakpoint above, so it would never get bigger tap targets from a
     width query alone. A folding phone (e.g. Pixel Fold) is worse: folded and
     using the cover screen it's under 600px like a normal phone, but unfolded
     to the inner screen it jumps to roughly 700-750px -- crossing the width
     breakpoint back and forth throughout the day on the very same device,
     while staying a finger the whole time either way. `pointer: coarse` asks
     "is the primary input imprecise" instead of "how wide is the screen",
     which is the actual thing tap-target sizing should key off. */
  @media (pointer: coarse) {{
    /* Small underlined text links are a poor touch target; button-ize them
       (still side by side above 600px -- see the width query for stacking). */
    .maps-links a {{
      padding: 9px 10px; border-radius: 7px; text-align: center;
      background: var(--surface-2); border: 1px solid var(--border); text-decoration: none;
    }}
    .nav-arrow {{ width: 46px; height: 46px; }}
    .modal-close, .fp-modal-content .modal-close {{ width: 40px; height: 40px; }}
  }}

  @media (max-width: 600px), (pointer: coarse) {{
    .card-media {{ display: flex; flex-direction: column; }}
    .main-photo, .photo-fallback {{ order: 1; }}
    .thumbs {{ order: 2; min-height: 54px; align-items: center; }}
    .thumb {{ width: 56px; height: 42px; }}
    .floorplan-selector {{ height: 42px; padding: 0 12px; font-size: 0.8rem; }}
    .status-btns {{
      position: static; order: 3; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px; padding: 8px 10px; background: var(--surface);
    }}
    .status-btns.modal-status-btns {{ width: 100%; margin-top: 2px; padding: 0; }}
    .status-btn {{
      width: auto; height: 44px; border: 1px solid var(--border); border-radius: 8px; padding: 0 8px;
      gap: 5px; background: var(--surface-2); color: var(--text); filter: none; opacity: 1; box-shadow: none;
      font-size: 0.9rem;
    }}
    .status-btn:hover {{ transform: none; }}
    .status-label {{ display: inline; font-size: 0.74rem; font-weight: 700; }}
    .status-btn[data-key="favorite"].active {{ background: #e0455c; border-color: #e0455c; color: #fff; }}
    .status-btn[data-key="followedUp"].active {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
    .status-btn[data-key="rejected"].active {{ background: var(--stretch-fg); border-color: var(--stretch-fg); color: #fff; }}
    .modal-thumb.floorplan-selector {{ min-height: 46px; font-size: 0.78rem; }}
  }}

  /* ---- Floorplan-only lightbox (bigger, no gallery/info clutter) ---- */
  .fp-modal-content {{
    background: var(--surface-2); border-radius: 14px; position: relative;
    width: 90vw; height: 90vh; max-width: 1400px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.4); overflow: hidden;
  }}
  .fp-modal-content .zoom-wrap {{ width: 100%; height: 100%; }}
</style>

<header class="top">
  <div class="top-inner">
    <h1>🏡 Walthamstow House Hunt</h1>
    <div class="subtitle">Daily-refreshed tracker of 2-3 bed flats and houses within a 10-15 minute public-transport journey of Walthamstow Central, and within walking distance of an Underground or Overground station. Last run: <b>{fmt_date(latest_run_date)}</b>. Listings tagged <span class="new-ribbon-inline">NEW</span> were first found by the latest run and stop being tagged as soon as another run happens; anything not turned up by a run in {STALE_DAYS}+ days is assumed sold or withdrawn and moves to the archive automatically. Each listing also has a <b>🤖 Claude's opinion</b> score (1-10) with pros/cons in its detail view — sort by it, or open a card for the full breakdown.</div>
    <div class="criteria-row">
      <span><b>{criteria['beds']} beds</b></span>
      <span><b>{' / '.join(criteria['types'])}</b></span>
      <span>Guide budget <b>£{criteria['budget']:,}</b></span>
      <span><b>{criteria['commuteTarget']}</b></span>
      <span><b>{criteria['stationWalkTarget']}</b></span>
      <span>{criteria['freshness']}</span>
    </div>
  </div>
  <nav class="tabs">
    <button class="tab-btn active" data-tab="current">Current matches ({len(current_listings)})</button>
    <button class="tab-btn" data-tab="archive">Archive ({total_ever_seen} ever seen)</button>
  </nav>
</header>

<main>
  <section id="tab-current" class="view active">
    <div class="toolbar">
      <span class="count">{len(current_listings)} live listings{f' &middot; {new_count} new this week' if new_count else ''} &middot; updated {fmt_date(latest_run_date)}</span>
      <div class="toggle-group" id="viewToggle">
        <button class="active" data-view="list">List</button>
        <button data-view="map">Map</button>
      </div>
      <select id="sortSelect">
        <option value="newest" selected>Newest first</option>
        <option value="score-desc">Claude's score: high to low</option>
        <option value="favorite-desc">Favorites first</option>
        <option value="price-asc">Price: low to high</option>
        <option value="price-desc">Price: high to low</option>
        <option value="total-asc">Total monthly: low to high</option>
        <option value="beds-desc">Bedrooms: most first</option>
      </select>
      <label class="chk"><input type="checkbox" id="hideStretch"> Hide "likely over 15 min"</label>
      <label class="chk"><input type="checkbox" id="hideRejected"> Hide rejected</label>
    </div>

    <details class="mortgage-calc">
      <summary>💷 Mortgage assumptions (tap to adjust — every listing's total recalculates live)</summary>
      <div class="mortgage-calc-inputs">
        <label>Deposit (£)<input type="number" id="mDeposit" value="{DEFAULT_ASSUMPTIONS['deposit']}" step="1000" min="0"></label>
        <label>Rate (%)<input type="number" id="mRate" value="{DEFAULT_ASSUMPTIONS['rate']}" step="0.1" min="0"></label>
        <label>Term (years)<input type="number" id="mTerm" value="{DEFAULT_ASSUMPTIONS['term']}" step="1" min="1"></label>
        <label>Other £/mo (insurance etc.)<input type="number" id="mOther" value="{DEFAULT_ASSUMPTIONS['other']}" step="10" min="0"></label>
      </div>
    </details>

    <div id="listPane">
      <div class="grid" id="cardGrid">
        {current_cards_html}
      </div>
    </div>

    <div id="mapPane" style="display:none;">
      <div id="map"></div>
    </div>
  </section>

  <section id="tab-archive" class="view">
    <div class="run-history">
      <h3>Run history</h3>
      <ul>{run_history_rows}</ul>
    </div>
    {archive_html}
  </section>

  <p class="caveat">Commute and walk times are estimated from known station/line geography, not a live TfL journey-planner query — treat "verify commute" / "likely over 15 min" tags as prompts to double-check on Google or TfL Journey Planner before booking a viewing. Listings currently sourced from OnTheMarket only: Rightmove and Zoopla block automated fetching, so anything listed only there won't appear here yet.</p>
</main>

<footer>Built for Charlie's house search &middot; data last refreshed {fmt_date(latest_run_date)}</footer>

<div class="modal-overlay" id="modalOverlay">
  <div class="modal-content" id="modalContent">
    <button class="modal-close" id="modalCloseBtn" aria-label="Close">✕</button>
    <div class="modal-gallery">
      <button class="nav-arrow nav-prev" id="modalPrev" aria-label="Previous image">‹</button>
      <div class="zoom-wrap" id="zoomWrap">
        <img id="modalImg" src="" alt="">
      </div>
      <button class="nav-arrow nav-next" id="modalNext" aria-label="Next image">›</button>
      <div class="slide-label" id="slideLabel"></div>
      <div class="zoom-hint">Scroll or click to zoom &middot; drag to pan &middot; ← → to browse</div>
    </div>
    <div class="modal-thumbs" id="modalThumbs"></div>
    <div class="modal-info" id="modalInfo"></div>
  </div>
</div>

<div class="modal-overlay" id="fpOverlay">
  <div class="fp-modal-content" id="fpModalContent">
    <button class="modal-close" id="fpCloseBtn" aria-label="Close">✕</button>
    <div class="zoom-wrap" id="fpZoomWrap">
      <img id="fpImg" src="" alt="Floorplan">
    </div>
    <div class="zoom-hint">Scroll or click to zoom &middot; drag to pan</div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
  const mapPoints = {json.dumps(map_points)};
  const WMC = {{lat: 51.5825, lon: -0.0201}};
  const allListings = {json.dumps(listings)};
  const badgeLabels = {json.dumps(badge_labels)};
  const priceBadgeLabels = {json.dumps(price_badge_labels)};
  function scoreTier(score) {{
    if (score === null || score === undefined) return 'pbadge-mid';
    if (score >= 7) return 'pbadge-good';
    if (score >= 5) return 'pbadge-mid';
    return 'pbadge-over';
  }}

  // ---- Favorite / followed-up / rejected status ----
  // Stored server-side in status.json (via server.py's /api/status endpoint),
  // shared by every device/browser that opens this page through the pm2 server --
  // NOT in localStorage/cookies, which only ever lived in one browser and didn't
  // follow Charlie between phone/laptop/opening-the-file-directly. `initialStatuses`
  // below is what build_page.py baked in from status.json at build time, so the
  // page is correct on first paint even before the live fetch below returns.
  //
  // Opened straight from disk (file://) there's no server to talk to, so status
  // still updates on screen for that session but can't be saved anywhere -- see
  // README. CAN_SYNC is how the page tells the two situations apart.
  const CAN_SYNC = (location.protocol === 'http:' || location.protocol === 'https:');
  const OLD_LOCALSTORAGE_KEY = 'houseHuntStatus_v1';
  const MIGRATED_FLAG_KEY = 'houseHuntStatusMigratedToServer_v1';
  let statuses = {json.dumps(initial_statuses)};

  function applyStatusToCard(id) {{
    const s = statuses[id] || {{}};
    document.querySelectorAll(`[data-id="${{id}}"]`).forEach(el => {{
      if (el.classList.contains('card')) {{
        el.classList.toggle('status-favorite', !!s.favorite);
        el.classList.toggle('status-followedup', !!s.followedUp);
        el.classList.toggle('status-rejected', !!s.rejected);
      }}
      if (el.classList.contains('status-btns')) {{
        el.querySelectorAll('.status-btn').forEach(btn => {{
          const active = !!s[btn.dataset.key];
          btn.classList.toggle('active', active);
          btn.setAttribute('aria-pressed', String(active));
        }});
      }}
    }});
  }}

  function setStatus(id, key, value, opts) {{
    opts = opts || {{}};
    const cur = statuses[id] || {{}};
    if (value && key === 'rejected') {{ cur.favorite = false; cur.followedUp = false; }}
    if (value && (key === 'favorite' || key === 'followedUp')) {{ cur.rejected = false; }}
    cur[key] = value;
    statuses[id] = cur;
    applyStatusToCard(id);
    applyFilters();
    if (CAN_SYNC && !opts.skipSync) {{
      fetch(`/api/status/${{encodeURIComponent(id)}}`, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(cur),
      }}).catch(() => {{ /* offline/server down -- stays applied on screen this session, just not saved */ }});
    }}
  }}

  function toggleStatus(id, key) {{
    const cur = statuses[id] || {{}};
    setStatus(id, key, !cur[key]);
  }}

  // One-time migration: earlier versions of this page kept favorite/rejected in
  // this browser's localStorage. If that's still sitting here, push it up to the
  // shared server once so it isn't silently lost, then stop checking. Anything
  // already set server-side for a given listing wins (it's more likely to be the
  // newer value, e.g. from a different device) -- this only fills in gaps.
  function migrateLegacyLocalStorage() {{
    if (!CAN_SYNC) return;
    try {{
      if (localStorage.getItem(MIGRATED_FLAG_KEY)) return;
      const raw = localStorage.getItem(OLD_LOCALSTORAGE_KEY);
      if (!raw) {{ localStorage.setItem(MIGRATED_FLAG_KEY, '1'); return; }}
      const legacy = JSON.parse(raw);
      const ids = Object.keys(legacy || {{}});
      if (!ids.length) {{ localStorage.setItem(MIGRATED_FLAG_KEY, '1'); return; }}
      Promise.all(ids.map(id => {{
        if (statuses[id]) return Promise.resolve(); // server already has a value -- don't clobber it
        return fetch(`/api/status/${{encodeURIComponent(id)}}`, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(legacy[id]),
        }}).then(() => {{ statuses[id] = legacy[id]; applyStatusToCard(id); }}).catch(() => {{}});
      }})).then(() => {{
        localStorage.setItem(MIGRATED_FLAG_KEY, '1');
        applyFilters();
      }});
    }} catch (e) {{ /* malformed old data -- nothing to migrate */ }}
  }}

  // Pull the latest from the server on load too (in case another device changed
  // something since this page was last built), then run the one-time migration.
  function refreshStatusesFromServer() {{
    if (!CAN_SYNC) {{ migrateLegacyLocalStorage(); return; }}
    fetch('/api/status').then(r => r.ok ? r.json() : null).then(fresh => {{
      if (fresh && typeof fresh === 'object') {{
        statuses = fresh;
        Object.keys(statuses).forEach(id => applyStatusToCard(id));
        applyFilters();
      }}
      migrateLegacyLocalStorage();
    }}).catch(() => {{ migrateLegacyLocalStorage(); }});
  }}

  // ---- Mortgage assumptions + "total monthly cost" calculator ----
  // Deposit is a flat £ amount (Charlie thinks in "£52.5k down"), not a %. Assumptions are
  // saved per-browser (same caveat as favorite/reject status -- see README) and every
  // listing's total recalculates live whenever they're changed.
  //
  // Key bumped v1 -> v2 on 29 Aug 2026 alongside the switch to the broker's quoted
  // figures. Without the bump, any browser that had already opened the panel would keep
  // showing its saved 5.0%/30yr/£50k values and the new defaults would silently never
  // appear -- the update would look like it hadn't worked. The bump costs one set of
  // hand-tweaked assumptions, which is the right trade against showing a stale rate.
  const MORTGAGE_KEY = 'houseHuntMortgage_v2';
  function loadAssumptions() {{
    try {{
      const saved = JSON.parse(localStorage.getItem(MORTGAGE_KEY));
      if (saved && typeof saved === 'object') return saved;
    }} catch (e) {{ /* ignore */ }}
    return null;
  }}
  function saveAssumptions(a) {{
    try {{ localStorage.setItem(MORTGAGE_KEY, JSON.stringify(a)); }} catch (e) {{ /* ignore */ }}
  }}
  function getCurrentAssumptions() {{
    return {{
      deposit: parseFloat(document.getElementById('mDeposit').value) || 0,
      rate: parseFloat(document.getElementById('mRate').value) || 0,
      term: parseFloat(document.getElementById('mTerm').value) || 1,
      other: parseFloat(document.getElementById('mOther').value) || 0,
    }};
  }}
  function mortgagePayment(principal, annualRatePct, years) {{
    const r = annualRatePct / 100 / 12, n = years * 12;
    if (principal <= 0 || n <= 0) return 0;
    if (r === 0) return principal / n;
    return principal * r / (1 - Math.pow(1 + r, -n));
  }}
  function computeTotalMonthly(listing, a) {{
    const loan = Math.max(listing.price - a.deposit, 0);
    const mortgage = mortgagePayment(loan, a.rate, a.term);
    const service = (listing.serviceChargePA || 0) / 12;
    const ground = (listing.groundRentPA || 0) / 12;
    return {{
      mortgage, service, ground, other: a.other, total: mortgage + service + ground + a.other,
      serviceUnknown: listing.serviceChargePA === null || listing.serviceChargePA === undefined,
      groundUnknown: listing.groundRentPA === null || listing.groundRentPA === undefined,
    }};
  }}
  function fmtGBP(n) {{ return '£' + Math.round(n).toLocaleString('en-GB'); }}
  // Mirrors lease_bits() in build_page.py -- keep the two in step.
  function leaseBits(l) {{
    const bits = [];
    if (l.leaseYearsRemaining != null) bits.push(['Lease', l.leaseYearsRemaining + ' years remaining']);
    if (l.serviceChargePA != null) bits.push(['Service charge', fmtGBP(l.serviceChargePA) + '/yr (' + fmtGBP(l.serviceChargePA / 12) + '/mo)']);
    if (l.groundRentPA != null) bits.push(['Ground rent', fmtGBP(l.groundRentPA) + '/yr']);
    if (l.groundRentReview) bits.push(['Ground rent review', l.groundRentReview]);
    return bits;
  }}
  function leaseDetailsHtml(l) {{
    const bits = leaseBits(l);
    if (!bits.length) return '';
    const inner = bits.map(([k, v]) => `<span class="ld-k">${{k}}:</span> ${{v}}`).join(' &middot; ');
    return `<div class="lease-details">🔑 ${{inner}}</div>`;
  }}
  function commuteWarningHtml(l) {{
    return l.commuteWarning ? `<div class="commute-warning">⚠️ ${{l.commuteWarning}}</div>` : '';
  }}
  function totalMonthlyInline(c) {{
    const notes = [];
    if (c.serviceUnknown) notes.push('service charge');
    if (c.groundUnknown) notes.push('ground rent');
    const unknownHtml = notes.length ? ` <span class="tm-unknown">(${{notes.join(' &amp; ')}} not stated)</span>` : '';
    return `<span class="tm-label">💷 Est. total monthly</span> <span class="tm-amount">${{fmtGBP(c.total)}}/mo</span>${{unknownHtml}}`;
  }}
  function totalMonthlyBlock(c) {{
    const rows = [
      ['Mortgage payment', c.mortgage, false],
      ['Service charge', c.service, c.serviceUnknown],
      ['Ground rent', c.ground, c.groundUnknown],
    ];
    if (c.other) rows.push(['Other (insurance etc.)', c.other, false]);
    const rowsHtml = rows.map(([label, val, unknown]) =>
      `<div class="tm-row"><span>${{label}}${{unknown ? ' <span class="tm-unknown">(not stated, shown as £0)</span>' : ''}}</span><span>${{fmtGBP(val)}}</span></div>`
    ).join('');
    return `
      <div class="opinion-header">
        <span class="opinion-title">💷 Est. total monthly cost</span>
        <span class="pbadge pbadge-mid">${{fmtGBP(c.total)}}/mo</span>
      </div>
      ${{rowsHtml}}
      <div class="tm-caveat">Based on the deposit/rate/term in the toolbar's "Mortgage assumptions" panel above the listings — adjust there and every listing updates.</div>
    `;
  }}
  function refreshAllTotals() {{
    const a = getCurrentAssumptions();
    saveAssumptions(a);
    Object.values(allListings).forEach(l => {{
      const c = computeTotalMonthly(l, a);
      document.querySelectorAll(`.card[data-id="${{l.id}}"]`).forEach(card => {{
        card.dataset.totalMonthly = Math.round(c.total);
      }});
      document.querySelectorAll(`.total-monthly[data-id="${{l.id}}"]`).forEach(el => {{
        el.innerHTML = totalMonthlyInline(c);
      }});
      document.querySelectorAll(`.total-monthly-block[data-id="${{l.id}}"]`).forEach(el => {{
        el.innerHTML = totalMonthlyBlock(c);
      }});
    }});
  }}
  (function initMortgageCalc() {{
    const saved = loadAssumptions();
    if (saved) {{
      document.getElementById('mDeposit').value = saved.deposit;
      document.getElementById('mRate').value = saved.rate;
      document.getElementById('mTerm').value = saved.term;
      document.getElementById('mOther').value = saved.other;
    }}
    ['mDeposit', 'mRate', 'mTerm', 'mOther'].forEach(id => {{
      document.getElementById(id).addEventListener('input', refreshAllTotals);
    }});
    refreshAllTotals();
  }})();

  // Tabs
  document.querySelectorAll('.tab-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    }});
  }});

  // List / Map toggle
  let map, markersLayer, mapInitialised = false;
  document.querySelectorAll('#viewToggle button').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('#viewToggle button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      if (btn.dataset.view === 'list') {{
        document.getElementById('listPane').style.display = '';
        document.getElementById('mapPane').style.display = 'none';
      }} else {{
        document.getElementById('listPane').style.display = 'none';
        document.getElementById('mapPane').style.display = '';
        if (!mapInitialised) initMap();
        if (map) setTimeout(() => map.invalidateSize(), 50);
      }}
    }});
  }});

  function initMap() {{
    if (typeof L === 'undefined') {{
      document.getElementById('map').innerHTML =
        '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-dim);text-align:center;padding:24px;">' +
        'Map couldn\\'t load (needs an internet connection to fetch the map library and tiles from cdnjs.cloudflare.com / cartocdn.com). ' +
        'The list view above still has every listing.</div>';
      mapInitialised = true;
      return;
    }}
    map = L.map('map').setView([WMC.lat, WMC.lon], 13);
    // Plain OSM tile servers now require a Referer header, which file:// pages
    // don't send, so they block every tile ("Access blocked... Referer is
    // required"). CARTO's basemap tiles are free, keyless, and don't enforce
    // that check, so they work when this page is opened directly from disk.
    L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
      maxZoom: 19,
      subdomains: 'abcd',
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
    }}).addTo(map);

    const wmcIcon = L.divIcon({{
      html: '<div style="background:#2f6f4f;color:#fff;border-radius:50%;width:16px;height:16px;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.4)"></div>',
      className: '', iconSize: [16,16], iconAnchor: [8,8]
    }});
    L.marker([WMC.lat, WMC.lon], {{icon: wmcIcon}}).addTo(map)
      .bindPopup('<strong>Walthamstow Central</strong><br>Reference point');

    mapPoints.forEach(p => {{
      const color = p.commute === 'pass' ? '#2f6f4f' : (p.commute === 'borderline' ? '#b5652e' : '#a13a2b');
      const icon = L.divIcon({{
        html: `<div style="background:${{color}};color:#fff;border-radius:50% 50% 50% 0;width:22px;height:22px;transform:rotate(-45deg);border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.4)"></div>`,
        className: '', iconSize: [22,22], iconAnchor: [11,22]
      }});
      const marker = L.marker([p.lat, p.lon], {{icon}}).addTo(map);
      // Hover/tap shows a quick preview tooltip (no click needed to see it, and it
      // never intercepts the click), so clicking the pin itself can go straight to
      // the same full detail modal the list cards use -- matching how the list view
      // behaves, instead of requiring a popup-then-button two-step.
      marker.bindTooltip(
        `<div class="map-tip">${{p.photo ? `<img src="${{p.photo}}">` : ''}}<div><strong>${{p.price}}</strong><br>${{p.address}}<br>${{p.beds}} bed &middot; ${{p.type}}</div></div>`,
        {{direction: 'top', offset: [0, -22], opacity: 1, className: 'map-tip-wrap'}}
      );
      marker.on('click', () => {{ openModal(p.id); }});
    }});
    mapInitialised = true;
  }}

  // Sorting
  document.getElementById('sortSelect').addEventListener('change', (e) => {{
    const grid = document.getElementById('cardGrid');
    const cards = Array.from(grid.children);
    const val = e.target.value;
    cards.sort((a, b) => {{
      if (val === 'newest') return a.dataset.firstSeen < b.dataset.firstSeen ? 1 : -1;
      if (val === 'score-desc') return b.dataset.score - a.dataset.score;
      if (val === 'favorite-desc') {{
        const af = (statuses[a.dataset.id] && statuses[a.dataset.id].favorite) ? 1 : 0;
        const bf = (statuses[b.dataset.id] && statuses[b.dataset.id].favorite) ? 1 : 0;
        return bf - af;
      }}
      if (val === 'price-asc') return a.dataset.price - b.dataset.price;
      if (val === 'price-desc') return b.dataset.price - a.dataset.price;
      if (val === 'beds-desc') return b.dataset.beds - a.dataset.beds;
      if (val === 'total-asc') return a.dataset.totalMonthly - b.dataset.totalMonthly;
    }});
    cards.forEach(c => grid.appendChild(c));
  }});

  // Hide stretch commute / hide rejected (combined so neither checkbox clobbers the other)
  function applyFilters() {{
    const hideStretch = document.getElementById('hideStretch').checked;
    const hideRejected = document.getElementById('hideRejected').checked;
    document.querySelectorAll('#cardGrid .card').forEach(c => {{
      const isStretch = c.dataset.commute === 'stretch';
      const isRejected = !!(statuses[c.dataset.id] && statuses[c.dataset.id].rejected);
      c.style.display = ((hideStretch && isStretch) || (hideRejected && isRejected)) ? 'none' : '';
    }});
  }}
  document.getElementById('hideStretch').addEventListener('change', applyFilters);
  document.getElementById('hideRejected').addEventListener('change', applyFilters);

  // Thumbnail swap
  function swapMain(imgEl) {{
    const card = imgEl.closest('.card');
    const main = card.querySelector('.main-photo');
    const full = imgEl.dataset.full;
    const prevMain = main.src;
    main.src = full;
    imgEl.src = prevMain;
    imgEl.dataset.full = prevMain;
  }}

  // ---- Detail modal ----
  const modalOverlay = document.getElementById('modalOverlay');
  const modalImg = document.getElementById('modalImg');
  const zoomWrap = document.getElementById('zoomWrap');
  const modalThumbs = document.getElementById('modalThumbs');
  const modalInfo = document.getElementById('modalInfo');
  const slideLabel = document.getElementById('slideLabel');

  let currentSlides = [];
  let currentSlideIndex = 0;
  let zoomState = {{ scale: 1, tx: 0, ty: 0 }};

  function buildSlides(listing) {{
    const slides = (listing.photos || []).map(p => ({{ url: p, label: 'Photo', isFp: false }}));
    if (listing.floorplan) slides.push({{ url: listing.floorplan, label: 'Floorplan', isFp: true }});
    return slides;
  }}

  function openModal(id, startIndex) {{
    const listing = allListings[id];
    if (!listing) return;
    currentSlides = buildSlides(listing);
    currentSlideIndex = 0;

    // Thumbnail strip
    const thumbnailIndexes = currentSlides.map((_, i) => i);
    const floorplanIndex = currentSlides.findIndex(s => s.isFp);
    if (floorplanIndex > 0) thumbnailIndexes.unshift(thumbnailIndexes.splice(floorplanIndex, 1)[0]);
    modalThumbs.innerHTML = thumbnailIndexes.map(i => {{
      const s = currentSlides[i];
      return s.isFp
        ? `<button type="button" class="modal-thumb floorplan-selector" data-i="${{i}}" aria-label="Show floorplan"><span aria-hidden="true">📐</span><span>Floor plan</span></button>`
        : `<button type="button" class="modal-thumb" data-i="${{i}}" aria-label="Show photo ${{i + 1}}"><img src="${{s.url}}" alt=""></button>`;
    }}).join('');
    modalThumbs.querySelectorAll('.modal-thumb').forEach(thumb => {{
      thumb.addEventListener('click', () => setSlide(parseInt(thumb.dataset.i, 10)));
    }});

    // Info panel
    const commute = badgeLabels[listing.commuteBadge] || ['Unknown', ''];
    const priceB = priceBadgeLabels[listing.priceBadge] || ['', ''];
    // Use the address text, not our own lat/lon -- those are hand-estimated approximations
    // (see Known limitations) and Google's own geocoding of the real address is more accurate.
    const gmapsView = `https://www.google.com/maps/search/?api=1&query=${{encodeURIComponent(listing.address)}}`;
    const gmapsDir = `https://www.google.com/maps/dir/?api=1&origin=${{encodeURIComponent(listing.address)}}&destination=${{encodeURIComponent('Walthamstow Central Station, London')}}&travelmode=transit`;
    const op = listing.opinion;
    const opinionHtml = op ? `
      <div class="opinion-block">
        <div class="opinion-header">
          <span class="opinion-title">🤖 Claude's opinion</span>
          <span class="pbadge ${{scoreTier(op.score)}}">${{op.score}}/10</span>
        </div>
        <div class="opinion-cols">
          <div class="opinion-col pros"><h4>Pros</h4><ul>${{op.pros.map(p => `<li>${{p}}</li>`).join('')}}</ul></div>
          <div class="opinion-col cons"><h4>Cons</h4><ul>${{op.cons.map(c => `<li>${{c}}</li>`).join('')}}</ul></div>
        </div>
      </div>` : '';
    modalInfo.innerHTML = `
      <div class="price-row">
        <span class="price">${{listing.priceText}}</span>
        <span class="pbadge ${{priceB[1]}}">${{priceB[0]}}</span>
        ${{op ? `<span class="pbadge ${{scoreTier(op.score)}}">🤖 ${{op.score}}/10</span>` : ''}}
        <div class="status-btns modal-status-btns" data-id="${{listing.id}}">
          <button class="status-btn" data-key="favorite" title="Favorite" aria-pressed="false"><span aria-hidden="true">❤️</span><span class="status-label">Favorite</span></button>
          <button class="status-btn" data-key="followedUp" title="Followed up" aria-pressed="false"><span aria-hidden="true">✅</span><span class="status-label">Followed up</span></button>
          <button class="status-btn" data-key="rejected" title="Not interested" aria-pressed="false"><span aria-hidden="true">✕</span><span class="status-label">Reject</span></button>
        </div>
      </div>
      <h2>${{listing.address}}</h2>
      <div class="meta">${{listing.beds}} bed &middot; ${{listing.baths}} bath &middot; ${{listing.type}} &middot; ${{listing.tenure}}</div>
      ${{leaseDetailsHtml(listing)}}
      <div class="badges">
        <span class="badge ${{commute[1]}}">${{commute[0]}}: ${{listing.commuteToWMC}}</span>
      </div>
      ${{commuteWarningHtml(listing)}}
      <div class="station">🚉 ${{listing.nearestStation}} &mdash; ${{listing.walkToStation}}</div>
      <div class="maps-links">
        <a href="${{gmapsView}}" target="_blank" rel="noopener">📍 View on Google Maps</a>
        <a href="${{gmapsDir}}" target="_blank" rel="noopener">🧭 Directions to Walthamstow Central</a>
      </div>
      <p class="desc">${{listing.description}}</p>
      <div class="opinion-block total-monthly-block" data-id="${{listing.id}}"></div>
      ${{opinionHtml}}
      <div class="modal-footer-row">
        <div class="added">Added: ${{listing.dateAddedText}} &middot; ${{listing.agent}}</div>
        <a class="view-listing" href="${{listing.listingUrl}}" target="_blank" rel="noopener">View full listing on ${{listing.sourcePortal}} ↗</a>
      </div>
    `;
    applyStatusToCard(listing.id);
    refreshAllTotals();

    setSlide(startIndex || 0);
    modalOverlay.classList.add('open');
    document.body.style.overflow = 'hidden';
  }}

  function closeModal() {{
    modalOverlay.classList.remove('open');
    document.body.style.overflow = '';
  }}

  function setSlide(i) {{
    if (!currentSlides.length) return;
    currentSlideIndex = (i + currentSlides.length) % currentSlides.length;
    const slide = currentSlides[currentSlideIndex];
    modalImg.src = slide.url;
    modalImg.alt = slide.label;
    slideLabel.textContent = `${{slide.label}} ${{currentSlideIndex + 1}} / ${{currentSlides.length}}`;
    modalThumbs.querySelectorAll('.modal-thumb').forEach(thumb => thumb.classList.toggle('active', parseInt(thumb.dataset.i, 10) === currentSlideIndex));
    resetZoom();
  }}

  function navSlide(delta) {{ setSlide(currentSlideIndex + delta); }}

  function resetZoom() {{
    zoomState = {{ scale: 1, tx: 0, ty: 0 }};
    applyZoom();
    zoomWrap.classList.remove('zoomed');
  }}

  function applyZoom() {{
    modalImg.style.transform = `translate(${{zoomState.tx}}px, ${{zoomState.ty}}px) scale(${{zoomState.scale}})`;
  }}

  function clamp(v, min, max) {{ return Math.min(max, Math.max(min, v)); }}

  // Zoom via wheel
  zoomWrap.addEventListener('wheel', (e) => {{
    e.preventDefault();
    const delta = e.deltaY < 0 ? 0.35 : -0.35;
    zoomState.scale = clamp(zoomState.scale + delta, 1, 4);
    if (zoomState.scale === 1) {{ zoomState.tx = 0; zoomState.ty = 0; zoomWrap.classList.remove('zoomed'); }}
    else {{ zoomWrap.classList.add('zoomed'); }}
    applyZoom();
  }}, {{ passive: false }});

  // Click/tap to toggle zoom (only if not dragged), drag to pan when zoomed.
  // Pointer Events (not mousedown/mousemove/mouseup) so this works identically
  // for a mouse, a trackpad, and a finger on a phone -- a single-finger drag
  // did nothing at all under the old mouse-only handlers.
  let dragging = false, moved = false, startX = 0, startY = 0, startTx = 0, startTy = 0;
  modalImg.addEventListener('pointerdown', (e) => {{
    e.preventDefault();
    dragging = true; moved = false;
    startX = e.clientX; startY = e.clientY;
    startTx = zoomState.tx; startTy = zoomState.ty;
    zoomWrap.classList.add('dragging');
    modalImg.setPointerCapture(e.pointerId);
  }});
  modalImg.addEventListener('pointermove', (e) => {{
    if (!dragging) return;
    const dx = e.clientX - startX, dy = e.clientY - startY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved = true;
    if (zoomState.scale > 1) {{
      zoomState.tx = startTx + dx;
      zoomState.ty = startTy + dy;
      applyZoom();
    }}
  }});
  function endModalDrag() {{
    if (!dragging) return;
    dragging = false;
    zoomWrap.classList.remove('dragging');
    if (!moved) {{
      // simple click/tap: toggle zoom in/out centered
      if (zoomState.scale === 1) {{ zoomState.scale = 2.2; zoomWrap.classList.add('zoomed'); }}
      else {{ zoomState.scale = 1; zoomState.tx = 0; zoomState.ty = 0; zoomWrap.classList.remove('zoomed'); }}
      applyZoom();
    }}
  }}
  modalImg.addEventListener('pointerup', endModalDrag);
  modalImg.addEventListener('pointercancel', endModalDrag);
  modalImg.addEventListener('dblclick', () => {{ resetZoom(); }});

  // Modal controls
  document.getElementById('modalCloseBtn').addEventListener('click', closeModal);
  document.getElementById('modalPrev').addEventListener('click', () => navSlide(-1));
  document.getElementById('modalNext').addEventListener('click', () => navSlide(1));
  modalOverlay.addEventListener('click', (e) => {{ if (e.target === modalOverlay) closeModal(); }});
  document.addEventListener('keydown', (e) => {{
    if (!modalOverlay.classList.contains('open')) return;
    if (e.key === 'Escape') closeModal();
    if (e.key === 'ArrowLeft') navSlide(-1);
    if (e.key === 'ArrowRight') navSlide(1);
  }});

  // ---- Floorplan-only lightbox ----
  const fpOverlay = document.getElementById('fpOverlay');
  const fpImg = document.getElementById('fpImg');
  const fpZoomWrap = document.getElementById('fpZoomWrap');
  let fpZoomState = {{ scale: 1, tx: 0, ty: 0 }};

  function openFloorplan(url) {{
    fpImg.src = url;
    resetFpZoom();
    fpOverlay.classList.add('open');
    document.body.style.overflow = 'hidden';
  }}

  function closeFloorplan() {{
    fpOverlay.classList.remove('open');
    document.body.style.overflow = '';
  }}

  function resetFpZoom() {{
    fpZoomState = {{ scale: 1, tx: 0, ty: 0 }};
    applyFpZoom();
    fpZoomWrap.classList.remove('zoomed');
  }}

  function applyFpZoom() {{
    fpImg.style.transform = `translate(${{fpZoomState.tx}}px, ${{fpZoomState.ty}}px) scale(${{fpZoomState.scale}})`;
  }}

  fpZoomWrap.addEventListener('wheel', (e) => {{
    e.preventDefault();
    const delta = e.deltaY < 0 ? 0.35 : -0.35;
    fpZoomState.scale = clamp(fpZoomState.scale + delta, 1, 4);
    if (fpZoomState.scale === 1) {{ fpZoomState.tx = 0; fpZoomState.ty = 0; fpZoomWrap.classList.remove('zoomed'); }}
    else {{ fpZoomWrap.classList.add('zoomed'); }}
    applyFpZoom();
  }}, {{ passive: false }});

  // Same Pointer Events approach as the main modal image, so a finger drag
  // pans the floorplan on a phone instead of doing nothing.
  let fpDragging = false, fpMoved = false, fpStartX = 0, fpStartY = 0, fpStartTx = 0, fpStartTy = 0;
  fpImg.addEventListener('pointerdown', (e) => {{
    e.preventDefault();
    fpDragging = true; fpMoved = false;
    fpStartX = e.clientX; fpStartY = e.clientY;
    fpStartTx = fpZoomState.tx; fpStartTy = fpZoomState.ty;
    fpZoomWrap.classList.add('dragging');
    fpImg.setPointerCapture(e.pointerId);
  }});
  fpImg.addEventListener('pointermove', (e) => {{
    if (!fpDragging) return;
    const dx = e.clientX - fpStartX, dy = e.clientY - fpStartY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) fpMoved = true;
    if (fpZoomState.scale > 1) {{
      fpZoomState.tx = fpStartTx + dx;
      fpZoomState.ty = fpStartTy + dy;
      applyFpZoom();
    }}
  }});
  function endFpDrag() {{
    if (!fpDragging) return;
    fpDragging = false;
    fpZoomWrap.classList.remove('dragging');
    if (!fpMoved) {{
      if (fpZoomState.scale === 1) {{ fpZoomState.scale = 2.2; fpZoomWrap.classList.add('zoomed'); }}
      else {{ fpZoomState.scale = 1; fpZoomState.tx = 0; fpZoomState.ty = 0; fpZoomWrap.classList.remove('zoomed'); }}
      applyFpZoom();
    }}
  }}
  fpImg.addEventListener('pointerup', endFpDrag);
  fpImg.addEventListener('pointercancel', endFpDrag);
  fpImg.addEventListener('dblclick', () => resetFpZoom());

  document.getElementById('fpCloseBtn').addEventListener('click', closeFloorplan);
  fpOverlay.addEventListener('click', (e) => {{ if (e.target === fpOverlay) closeFloorplan(); }});
  document.addEventListener('keydown', (e) => {{
    if (!fpOverlay.classList.contains('open')) return;
    if (e.key === 'Escape') closeFloorplan();
  }});

  // Favorite / followed-up / rejected buttons (checked before the modal-open handler below)
  document.addEventListener('click', (e) => {{
    const btn = e.target.closest('.status-btn');
    if (!btn) return;
    const holder = btn.closest('[data-id]');
    if (holder) toggleStatus(holder.dataset.id, btn.dataset.key);
  }});

  // Open modal on card click (but not when clicking a thumbnail, an outbound link, or a status button)
  document.addEventListener('click', (e) => {{
    if (e.target.closest('.status-btn')) return;
    const card = e.target.closest('.card');
    if (!card) return;
    if (e.target.closest('.thumb, a')) return;
    openModal(card.dataset.id);
  }});
  document.addEventListener('keydown', (e) => {{
    if (e.key !== 'Enter' && e.key !== ' ') return;
    if (e.target.closest && e.target.closest('.thumb, a, .status-btn')) return;
    const card = e.target.closest && e.target.closest('.card');
    if (!card) return;
    e.preventDefault();
    openModal(card.dataset.id);
  }});

  // Apply the statuses baked in at build time, then sync with the server for
  // anything newer, then check for legacy localStorage data to migrate.
  Object.keys(statuses).forEach(id => applyStatusToCard(id));
  applyFilters();
  refreshStatusesFromServer();
  if (!CAN_SYNC) {{
    const notice = document.createElement('div');
    notice.className = 'file-mode-notice';
    notice.innerHTML = 'Opened directly from a file, not via the server — favorite/followed-up/rejected clicks will show here but <b>won\\'t be saved</b>. Open via <code>http://&lt;lan-ip&gt;:8080/</code> (see README) for those to stick.';
    document.querySelector('header.top .top-inner').appendChild(notice);
  }}
</script>
"""

OUT_PATH.write_text(HTML, encoding="utf-8")
print(f"Wrote {OUT_PATH} ({len(HTML):,} bytes) with {len(current_listings)} current listings and {total_ever_seen} total ever seen.")
