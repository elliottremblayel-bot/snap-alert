"""
Eurostar Snap availability alert.

Checks snap.eurostar.com for the routes, weekdays and time slots set in
config.json, and sends an email when a matching slot becomes available.

Usage:
  python snap_alert.py               normal run (checks, emails, saves state)
  python snap_alert.py --dry-run     check and print only, no email, no state saved
  python snap_alert.py --test-email  send a test email and exit

Email settings come from environment variables:
  GMAIL_ADDRESS       the Gmail account that sends the alert
  GMAIL_APP_PASSWORD  a Gmail app password (not the normal password)
  ALERT_TO            where alerts go (optional, defaults to GMAIL_ADDRESS)
"""

import argparse
import json
import os
import re
import smtplib
import sys
import time
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"
DEBUG_DIR = HERE / "debug"

SEARCH_URL = (
    "https://snap.eurostar.com/uk-en/search"
    "?adult={adults}&origin={origin}&destination={destination}&outbound={day}"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
LONDON = ZoneInfo("Europe/London")
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Send one "monitor is not working" email after this many failed runs in a row
# (8 runs x 15 min = 2 hours).
FAIL_RUNS_BEFORE_WARNING = 8

SLOT_RE = re.compile(r"(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})")
PRICE_RE = re.compile(r"£\s*(\d+(?:[.,]\d{1,2})?)")
NOT_AVAILABLE_RE = re.compile(r"not available|sold out", re.I)
NO_TICKETS_RE = re.compile(r"no snap tickets are available", re.I)
BLOCKED_RE = re.compile(r"access denied|captcha|are you a robot|request blocked|cf-chl", re.I)


# ---------------------------------------------------------------- parsing

def page_text(html: str) -> str:
    """Visible text of the results part of the page, one item per line."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    # Keep only the results block: after "Train results", before "FAQs".
    start = text.find("Train results")
    end = text.find("FAQs", start if start >= 0 else 0)
    if start >= 0:
        text = text[start:end if end > start else None]
    return text


def parse_slots(html: str) -> dict:
    """
    Returns {"status": ..., "slots": [...]}
      status "ok"       slots were read (each has start, end, price or None)
      status "none"     the page says no Snap tickets for this date
      status "blocked"  looks like a bot-protection page
      status "unknown"  the page layout was not recognised
    """
    if BLOCKED_RE.search(html[:20000]) and "Train results" not in html:
        return {"status": "blocked", "slots": []}

    text = page_text(html)
    matches = list(SLOT_RE.finditer(text))
    slots = []
    for i, m in enumerate(matches):
        window_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        window = text[m.end():window_end]
        price_m = PRICE_RE.search(window)
        na_m = NOT_AVAILABLE_RE.search(window)
        price = None
        if price_m and not (na_m and na_m.start() < price_m.start()):
            price = float(price_m.group(1).replace(",", "."))
        slots.append({"start": m.group(1).zfill(5), "end": m.group(2).zfill(5), "price": price})

    if slots:
        return {"status": "ok", "slots": slots}
    if NO_TICKETS_RE.search(text):
        return {"status": "none", "slots": []}
    return {"status": "unknown", "slots": []}


# ---------------------------------------------------------------- dates

def target_dates(weekdays, days_ahead, today=None):
    """Dates from tomorrow to today + days_ahead that fall on the chosen weekdays."""
    today = today or datetime.now(LONDON).date()
    wanted = {WEEKDAYS.index(w.capitalize()) for w in weekdays}
    return [
        today + timedelta(days=n)
        for n in range(1, days_ahead + 1)
        if (today + timedelta(days=n)).weekday() in wanted
    ]


# ---------------------------------------------------------------- state

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------- email

def send_email(subject: str, body_text: str, body_html: str | None = None):
    sender = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("ALERT_TO") or sender
    if not sender or not password:
        raise RuntimeError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set.")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, password.replace(" ", ""))
        smtp.sendmail(sender, [a.strip() for a in to.split(",")], msg.as_string())


def money(p: float) -> str:
    return f"£{p:.0f}" if float(p).is_integer() else f"£{p:.2f}"


def format_alert(new_items, all_items):
    def line(it):
        d = date.fromisoformat(it["date"])
        return f"{it['route']}, {d:%a %d %b}, {it['start']}-{it['end']}: {money(it['price'])}"

    subject = "Eurostar Snap: " + "; ".join(
        f"{it['route']} {date.fromisoformat(it['date']):%a %d %b} {money(it['price'])}"
        for it in new_items[:3]
    )
    if len(new_items) > 3:
        subject += f" (+{len(new_items) - 3} more)"

    text = ["New Snap availability:", ""]
    text += [f"- {line(it)}\n  {it['url']}" for it in new_items]
    others = [it for it in all_items if it not in new_items]
    if others:
        text += ["", "Still available from before:"]
        text += [f"- {line(it)}" for it in others]
    text += ["", "Snap tickets are non-refundable. Book quickly, slots sell out."]

    html = ["<p><b>New Snap availability:</b></p><ul>"]
    html += [f'<li>{line(it)} &nbsp;<a href="{it["url"]}">Book</a></li>' for it in new_items]
    html.append("</ul>")
    if others:
        html.append("<p>Still available from before:</p><ul>")
        html += [f'<li>{line(it)} &nbsp;<a href="{it["url"]}">Book</a></li>' for it in others]
        html.append("</ul>")
    html.append("<p><small>Snap tickets are non-refundable. Book quickly, slots sell out.</small></p>")
    return subject, "\n".join(text), "\n".join(html)


# ---------------------------------------------------------------- main run

def check_all(cfg, save_debug=False):
    """Returns (results, ok_count, fail_count). results: list of dicts per route/date."""
    session = requests.Session()
    session.headers.update(HEADERS)
    earliest = cfg.get("earliest_slot_start", "00:00")
    results, ok, failed = [], 0, 0

    days = target_dates(cfg["weekdays"], cfg.get("days_ahead", 15))
    for route in cfg["routes"]:
        for day in days:
            url = SEARCH_URL.format(
                adults=cfg.get("adults", 1),
                origin=route["origin"],
                destination=route["destination"],
                day=day.isoformat(),
            )
            entry = {"route": route["name"], "date": day.isoformat(), "url": url}
            try:
                r = session.get(url, timeout=30)
                if r.status_code in (403, 429, 503):
                    parsed = {"status": "blocked", "slots": []}
                else:
                    r.raise_for_status()
                    parsed = parse_slots(r.text)
                if save_debug and parsed["status"] in ("unknown", "blocked"):
                    DEBUG_DIR.mkdir(exist_ok=True)
                    name = f"{route['origin']}-{route['destination']}-{day}.html"
                    (DEBUG_DIR / name).write_text(r.text)
            except requests.RequestException as exc:
                parsed = {"status": "error", "slots": [], "error": str(exc)}

            entry.update(parsed)
            entry["matching"] = [
                s for s in parsed["slots"] if s["price"] is not None and s["start"] >= earliest
            ]
            results.append(entry)
            if parsed["status"] in ("ok", "none"):
                ok += 1
            else:
                failed += 1

            summary = parsed["status"]
            if parsed["slots"]:
                summary = ", ".join(
                    f"{s['start']}-{s['end']} " + (money(s["price"]) if s["price"] is not None else "n/a")
                    for s in parsed["slots"]
                )
            print(f"{route['name']:<16} {day:%a %d %b}: {summary}")
            time.sleep(cfg.get("seconds_between_requests", 3))
    return results, ok, failed


def run(dry_run=False):
    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        sys.exit("config.json is missing or invalid.")
    state = load_json(STATE_PATH, {"available": {}, "fail_streak": 0, "warning_sent": False})
    previous = state.get("available", {})

    results, ok, failed = check_all(cfg, save_debug=not dry_run)

    today = datetime.now(LONDON).date().isoformat()
    current = {}
    # Keep previous values for checks that failed this run, so a temporary
    # error does not trigger a repeat alert later.
    checked = {(e["route"], e["date"]) for e in results if e["status"] in ("ok", "none")}
    for key, val in previous.items():
        if val["date"] >= today and (val["route"], val["date"]) not in checked:
            current[key] = val

    all_items = []
    for e in results:
        for s in e["matching"]:
            key = f"{e['route']}|{e['date']}|{s['start']}-{s['end']}"
            item = {"route": e["route"], "date": e["date"], "start": s["start"],
                    "end": s["end"], "price": s["price"], "url": e["url"]}
            current[key] = item
            all_items.append(item)

    new_items = [current[k] for k in current if k not in previous]
    new_items.sort(key=lambda it: (it["date"], it["route"]))
    all_items.sort(key=lambda it: (it["date"], it["route"]))

    print(f"\nChecks OK: {ok}, failed: {failed}. Matching slots now: {len(all_items)}, new: {len(new_items)}.")

    if dry_run:
        print("Dry run: no email sent, state not saved.")
        return

    if new_items:
        subject, text, html = format_alert(new_items, all_items)
        send_email(subject, text, html)
        print("Alert email sent.")

    # Health check: warn once if the site cannot be read for a while.
    if ok == 0 and failed > 0:
        state["fail_streak"] = state.get("fail_streak", 0) + 1
    else:
        state["fail_streak"] = 0
        state["warning_sent"] = False
    if state["fail_streak"] >= FAIL_RUNS_BEFORE_WARNING and not state.get("warning_sent"):
        statuses = sorted({e["status"] for e in results})
        send_email(
            "Eurostar Snap alert: cannot read the Snap website",
            "The Snap checker has failed for the last "
            f"{state['fail_streak']} runs (status: {', '.join(statuses)}).\n\n"
            "Eurostar may be blocking the checker or the page layout changed. "
            "Open the latest run in the GitHub Actions tab and download the 'debug-pages' "
            "artifact to see what the site returned.",
        )
        state["warning_sent"] = True
        print("Warning email sent.")

    state["available"] = current
    save_state(state)


def main():
    ap = argparse.ArgumentParser(description="Eurostar Snap availability alert")
    ap.add_argument("--dry-run", action="store_true", help="check and print only")
    ap.add_argument("--test-email", action="store_true", help="send a test email and exit")
    args = ap.parse_args()
    if args.test_email:
        send_email("Eurostar Snap alert: test email",
                   "Email setup works. You will get alerts at this address.")
        print("Test email sent.")
        return
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
