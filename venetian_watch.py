#!/usr/bin/env python3
"""
venetian_watch.py — personal hotel rate monitor.

Checks the nightly rate for a fixed set of dates and pings your phone when it
changes (and shouts louder when it drops below your locked rate). Designed to be
run on a schedule: cron, Windows Task Scheduler, or GitHub Actions.

The ONLY part you need to wire up is fetch_rates(). Everything else — diffing
against the last run and notifying you — already works. Flip MOCK to False once
your real fetch is in place.
"""

import json
import os
import random
import datetime as dt
from pathlib import Path

import requests

# ------------------------------- CONFIG --------------------------------------
HOTEL       = "The Venetian, Las Vegas"
NIGHTS      = ["2026-08-19", "2026-08-20", "2026-08-21"]   # the nights you care about
LOCKED_RATE = 380.0          # per night; alert is emphasized when a night drops below this
# In GitHub Actions this comes from a repo secret; locally it uses the fallback.
NTFY_TOPIC  = os.environ.get("NTFY_TOPIC", "venetian-rate-CHANGE-ME-a7x93q")
STATE_FILE  = Path.home() / ".venetian_watch.json"
RATES_JSON  = Path(__file__).parent / "docs" / "rates.json"  # served by GitHub Pages
MOCK        = False          # True = fake rates; False = call the real Venetian endpoint
DEBUG_DUMP  = False          # True = also write rates_raw.json to inspect the shape

# --- real endpoint (found in the Network tab) ---
API_URL   = "https://vlv.dolli.cloud/api/hotel/room-stays"
ADULTS    = 3                # NOTE: your search used 3 adults; set to match your booking
# Which price to track. "beforeTax" = room + resort fee, pre-tax (matches the site's
# calendar). "afterTax" = the all-in nightly total including sales tax.
RATE_BASIS = "beforeTax"
# Pin the exact room + rate you're locked into, or leave None to track the CHEAPEST
# available each night. Find your codes in your confirmation or the response (e.g.
# roomTypeCode "VNKK", ratePlanCode "GVNS3X").
TARGET_ROOM_TYPE = None
TARGET_RATE_PLAN = None
# ⚠ This token is an ANONYMOUS session and expires ~1 HOUR after it's minted.
# Fine for a one-off test. For scheduled runs, get_token() must fetch a fresh one
# each run (see the note in get_token()). Paste a fresh token here to test now:
BEARER_TOKEN = "PASTE_A_FRESH_TOKEN_HERE"
# How to get the token each run:
#   "browser" = launch a headless browser, load the booking page, and grab the
#               fresh token off the wire (survives the 1-hour expiry, runs unattended).
#   "paste"   = just use BEARER_TOKEN above (fine for a quick one-off test).
TOKEN_MODE  = "browser"
# The page that loads the rate widget. Paste the URL from your browser's address
# bar when the calendar/rates are showing. The generic booking start page usually
# mints a token too, so this default often works as-is.
BOOKING_URL = "https://www.venetianlasvegas.com/booking/room-results.html"
USER_AGENT  = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
HEADLESS    = True           # set False to watch the browser (useful for debugging)
# The long code lists from your cURL — leave as-is unless you want to narrow rooms:
OFFER_CODES = ("EVFDSPPV,EPFDSPPP,MVFD75PV,MPFD75PP,MVFDE1PV,MPFDE1PP,MVFDE2PV,"
               "MPFDE2PP,MVFDLDPV,MPFDLDPP")
ROOM_TYPE_CODES = ("PRE1,PAE1,PRET,PRE2,PAGK,PAGA,PAGC,PAGX,PAGZ,PAGQ,PAKK,PAKC,PAKS,"
                   "PAK1,PAKV,PAK2,PAKZ,PAK3,PAK4,PAK5,PAK6,PAQQ,PAQC,PAQS,PAQ1,PAQV,"
                   "PAQ2,PAQZ,PAQ3,PAQ4,PAQ5,PAQ6,PARK,PAR2,PARR,PCGK,PCKK,PCK1,PCK2,"
                   "PCQQ,PCQ1,PCQT,PCQ2,VSCS,VNEB,VNET,VNEW,VSKL,VSK7,VSK8,VSQL,VSQ7,"
                   "VSQ8,VNDK,VNDC,VNDV,VNDA,VNDZ,VND3,VND5,VNGV,VNG3,VNG5,VNKK,VSKK,"
                   "VNKC,VNKS,VSKR,VNKV,VSKV,VNKZ,VNK5,VSK5,VNK3,VNQQ,VSQQ,VNQC,VNQS,"
                   "VNQV,VSQV,VNQZ,VNQ5,VSQ5,VNQ3,VNRQ,VNRA,VNRD,VNRT,VNRV,VZPQ,VCDV,"
                   "VCKV,VCQV,VCRV,VSGS")
# -----------------------------------------------------------------------------


def get_token() -> str:
    """
    Return a valid Bearer token.

    "paste"   -> use BEARER_TOKEN as-is (expires in ~1 hour; test only).
    "browser" -> load the booking page in a headless browser and capture the
                 fresh Bearer it sends to vlv.dolli.cloud — no need to find the
                 mint endpoint. Requires:  pip install playwright
                                           playwright install chromium
    """
    if TOKEN_MODE == "paste":
        return BEARER_TOKEN

    import time
    from playwright.sync_api import sync_playwright

    captured = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        ctx = browser.new_context(user_agent=USER_AGENT)
        page = ctx.new_page()

        def on_request(req):
            # Grab the Bearer off any call the page makes to the rate backend.
            if "vlv.dolli.cloud" in req.url and "t" not in captured:
                auth = req.headers.get("authorization", "")
                if auth.lower().startswith("bearer "):
                    captured["t"] = auth.split(" ", 1)[1]

        page.on("request", on_request)

        # Don't wait for the network to go idle (it never does on big sites).
        # Load the DOM, then poll until the page fires a token-bearing request.
        try:
            page.goto(BOOKING_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"(navigation note: {e} — continuing, token may still arrive)")

        deadline = time.time() + 30
        while "t" not in captured and time.time() < deadline:
            page.wait_for_timeout(500)

        browser.close()

    if "t" not in captured:
        raise SystemExit(
            "No token captured — the page likely didn't run a rate search.\n"
            "Fix: in your normal browser, open the booking page and get to where the "
            "calendar/rates are showing, copy that URL from the address bar, and paste "
            "it into BOOKING_URL (it carries your dates and triggers the API call).\n"
            "Still failing? Set HEADLESS = False to watch what the page does."
        )
    return captured["t"]
# -----------------------------------------------------------------------------


def fetch_rates() -> dict[str, float]:
    """Call the real room-stays endpoint and return {night: nightly_rate}."""
    if MOCK:
        base = LOCKED_RATE
        return {n: round(base + random.uniform(-40, 40), 2) for n in NIGHTS}

    checkout = (dt.date.fromisoformat(NIGHTS[-1]) + dt.timedelta(days=1)).isoformat()
    params = {
        "clientId": "vlv", "hotelCode": "VLV", "adult": ADULTS,
        "start": NIGHTS[0], "end": checkout,   # 'end' is EXCLUSIVE — use checkout day
        "offerCode": OFFER_CODES, "ratePlanCode": "", "roomTypeCode": ROOM_TYPE_CODES,
        "blackoutDates": "false",
    }
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {get_token()}",
        "origin": "https://www.venetianlasvegas.com",
        "referer": "https://www.venetianlasvegas.com/",
        "user-agent": USER_AGENT,
    }
    resp = requests.get(API_URL, params=params, headers=headers, timeout=25)
    if resp.status_code == 401:
        raise SystemExit("401 Unauthorized — the token has expired. Paste a fresh one "
                         "(or wire get_token() to mint one automatically).")
    resp.raise_for_status()
    data = resp.json()

    if DEBUG_DUMP:
        Path("rates_raw.json").write_text(json.dumps(data, indent=2)[:300000])
        print("Wrote rates_raw.json — paste it back and I'll finish parse_rates().")

    return parse_rates(data)


def parse_rates(data) -> dict[str, float]:
    """
    Turn the room-stays response into {"YYYY-MM-DD": nightly_rate}.

    Response shape: a list; data[0]["roomRates"] is a list of
      {ratePlanCode, roomTypeCode, rates: [{date, total:{amountBeforeTax,
       amountAfterTax}, ...}]}. Amounts are in CENTS, so divide by 100.

    If TARGET_ROOM_TYPE/TARGET_RATE_PLAN are set, tracks that exact rate.
    Otherwise tracks the cheapest available rate per night.
    """
    root = data[0] if isinstance(data, list) else data
    room_rates = root.get("roomRates", [])
    field = "amountAfterTax" if RATE_BASIS == "afterTax" else "amountBeforeTax"

    per_night: dict[str, list[float]] = {n: [] for n in NIGHTS}
    for rr in room_rates:
        if TARGET_ROOM_TYPE and rr.get("roomTypeCode") != TARGET_ROOM_TYPE:
            continue
        if TARGET_RATE_PLAN and rr.get("ratePlanCode") != TARGET_RATE_PLAN:
            continue
        for r in rr.get("rates", []):
            d = r.get("date")
            if d in per_night:
                cents = r.get("total", {}).get(field)
                if cents is not None:
                    per_night[d].append(cents / 100.0)

    out = {}
    for n in NIGHTS:
        if per_night[n]:
            out[n] = round(min(per_night[n]), 2)
    if not out:
        raise SystemExit("No rates matched your dates/room filter — check ADULTS, "
                         "the date range, or TARGET_ROOM_TYPE/TARGET_RATE_PLAN.")
    missing = [n for n in NIGHTS if n not in out]
    if missing:
        print(f"Heads up: no rate returned for {missing} — likely sold out, or the "
              f"date range needs widening (remember 'end' must be checkout day).")
    return out


def fetch_rates_selenium() -> dict[str, float]:
    """
    Fallback for JS-heavy / bot-protected pages. Keep the polling frequency LOW.
    pip install undetected-chromedriver selenium
    """
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    url = (
        "https://www.venetianlasvegas.com/booking/room-results.html"
        f"?checkInDate={NIGHTS[0]}&checkOutDate=2026-08-22&adults={ADULTS}"
    )  # <- confirm the real URL/params from your browser's address bar

    opts = uc.ChromeOptions()
    # opts.add_argument("--headless=new")  # try WITHOUT headless first; headless gets flagged more
    driver = uc.Chrome(options=opts)
    try:
        driver.get(url)
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "[data-rate], .room-rate, .price"))
        )
        # You'll adjust these selectors to the actual page. Prefer reading a
        # single nightly rate and applying it, or scrape a per-night calendar.
        el = driver.find_element(By.CSS_SELECTOR, "[data-rate], .room-rate, .price")
        rate = float("".join(c for c in el.text if c.isdigit() or c == "."))
        return {n: rate for n in NIGHTS}
    finally:
        driver.quit()


# ------------------------------- plumbing ------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def notify(title: str, message: str, urgent: bool = False) -> None:
    """Push to your phone via ntfy (install the ntfy app, subscribe to NTFY_TOPIC)."""
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "urgent" if urgent else "default",
                "Tags": "money_with_wings" if urgent else "hotel",
            },
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"[notify failed] {e}")
    print(f"{title}\n{message}\n")


def main() -> None:
    prev = load_state().get("rates", {})
    now = fetch_rates()

    changes, below_lock = [], []
    for night, price in now.items():
        old = prev.get(night)
        if old is None:
            changes.append(f"{night}: ${price:.0f} (first check)")
        elif price != old:
            direction = "down" if price < old else "up"
            changes.append(f"{night}: ${old:.0f} -> ${price:.0f} ({direction})")
        if price < LOCKED_RATE:
            below_lock.append(f"{night}: ${price:.0f}  (${LOCKED_RATE - price:.0f} under your lock)")

    if below_lock:
        notify(
            "Venetian rate dropped below your lock",
            "Rebook-worthy:\n" + "\n".join(below_lock)
            + ("\n\nAlso changed:\n" + "\n".join(changes) if changes else ""),
            urgent=True,
        )
    elif changes:
        notify("Venetian rate changed", "\n".join(changes))
    else:
        print("No change since last check.")

    save_state({"rates": now, "checked_at": dt.datetime.now().isoformat(timespec="minutes")})

    # Feed the dashboard: write rates.json in the shape it reads.
    RATES_JSON.parent.mkdir(parents=True, exist_ok=True)
    RATES_JSON.write_text(json.dumps({
        "updated": dt.datetime.now().astimezone().isoformat(timespec="minutes"),
        "nights": {night: round(rate, 2) for night, rate in now.items()},
    }, indent=2))


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        # get_token() and parse_rates() raise SystemExit with a message.
        notify("Venetian watcher stopped", str(e) or "Run halted.", urgent=True)
        raise
    except Exception as e:
        notify("Venetian watcher error", f"{type(e).__name__}: {e}", urgent=True)
        raise
