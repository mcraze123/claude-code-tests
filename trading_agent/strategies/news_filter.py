"""
Economic news calendar filter.

Fetches high-impact USD events from cryptocraft.com/calendar.
Used by NY Open strategy to avoid trading around red/orange news.
Results are cached for 1 hour. Falls back to "no blackout" on errors
so a dead endpoint never blocks trading.
"""

import re
import sys
import time
import requests
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import Optional
import pytz

ET = pytz.timezone("America/New_York")

_CACHE: dict = {}
_CACHE_TTL   = 3600   # 1 hour
_URL         = "https://www.cryptocraft.com/calendar"
_HEADERS     = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.cryptocraft.com/",
}


# ── HTML parser ───────────────────────────────────────────────────────────────

class _CalParser(HTMLParser):
    """
    Scrapes table rows from the cryptocraft economic calendar.
    Tracks impact level via CSS class names on <tr> or <td>/<span> children.
    """

    def __init__(self):
        super().__init__()
        self.events: list[dict] = []
        self._in_row   = False
        self._cells:   list[str] = []
        self._cur_cell = ""
        self._in_td    = False
        self._impact   = None   # None | "red" | "orange" | "yellow"

    def _detect_impact(self, cls: str) -> Optional[str]:
        cls = cls.lower()
        if any(k in cls for k in ("high", "red", "impact3", "impact-3")):
            return "red"
        if any(k in cls for k in ("medium", "orange", "impact2", "impact-2")):
            return "orange"
        if any(k in cls for k in ("low", "yellow", "impact1", "impact-1")):
            return "yellow"
        return None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls   = attrs.get("class", "")

        if tag == "tr":
            self._in_row   = True
            self._cells    = []
            self._cur_cell = ""
            self._impact   = self._detect_impact(cls)
            self._in_td    = False

        elif tag in ("td", "th") and self._in_row:
            self._in_td    = True
            self._cur_cell = ""
            imp = self._detect_impact(cls)
            if imp and (self._impact is None or
                        ["yellow","orange","red"].index(imp) >
                        ["yellow","orange","red"].index(self._impact or "yellow")):
                self._impact = imp

        elif tag in ("span", "i", "em", "div") and self._in_row:
            imp = self._detect_impact(cls)
            if imp and (self._impact is None or
                        ["yellow","orange","red"].index(imp) >
                        ["yellow","orange","red"].index(self._impact or "yellow")):
                self._impact = imp

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_td:
            self._cells.append(self._cur_cell.strip())
            self._cur_cell = ""
            self._in_td    = False

        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._cells:
                self.events.append({
                    "cells":  list(self._cells),
                    "impact": self._impact,
                })

    def handle_data(self, data):
        if self._in_td:
            self._cur_cell += data


# ── Parsing helpers ───────────────────────────────────────────────────────────

_TIME_RE = re.compile(
    r"^(\d{1,2}):?(\d{2})?\s*(am|pm)$", re.IGNORECASE
)


def _parse_time_et(s: str, d: date) -> Optional[datetime]:
    s = s.strip().lower().replace("\xa0", "").replace(" ", "")
    if not s or s in ("all\xa0day", "allday", "tentative", ""):
        return None
    m = _TIME_RE.match(s)
    if not m:
        return None
    hour   = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    ampm   = m.group(3).lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    try:
        return ET.localize(datetime(d.year, d.month, d.day, hour, minute))
    except Exception:
        return None


_USD_TOKENS = {"usd", "us", "usa", "united states", "dollar"}


def _extract_events(raw: list[dict], d: date) -> list[dict]:
    """
    Walk parsed table rows; find USD rows with red or orange impact.
    Typical column order: [time, currency, (impact_cell?), event_name, ...]
    """
    out = []
    for item in raw:
        impact = item["impact"]
        if impact not in ("red", "orange"):
            continue

        cells = item["cells"]
        if not cells:
            continue

        # Find which cell contains USD
        usd_idx = None
        for i, c in enumerate(cells):
            if c.strip().lower() in _USD_TOKENS:
                usd_idx = i
                break
        if usd_idx is None:
            # fallback: look for USD anywhere in text
            joined = " ".join(cells).lower()
            if "usd" not in joined and "u.s." not in joined:
                continue
            usd_idx = 1  # assume second cell is currency

        # Time is usually the cell just before currency (or cell 0)
        time_idx = usd_idx - 1 if usd_idx > 0 else 0
        time_str = cells[time_idx] if time_idx < len(cells) else ""
        dt = _parse_time_et(time_str, d)
        if dt is None:
            # Try cell 0 regardless
            dt = _parse_time_et(cells[0], d) if cells else None
        if dt is None:
            continue

        # Event name: first non-empty, non-numeric cell after currency
        event_name = ""
        for c in cells[usd_idx + 1:]:
            c = c.strip()
            if c and not re.match(r"^[\d.\-/]+$", c) and len(c) > 2:
                event_name = c
                break

        out.append({
            "time":   dt,
            "event":  event_name or "USD event",
            "impact": impact,
        })

    return out


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_us_news(target_date: Optional[date] = None) -> list[dict]:
    """
    Return high-impact USD events for target_date (ET).
    Each item: {time: datetime (ET-aware), event: str, impact: "red"|"orange"}.
    Falls back to [] on network/parse error (fail-open, never blocks trading).
    """
    if target_date is None:
        target_date = datetime.now(ET).date()

    key = f"cal_{target_date}"
    if key in _CACHE:
        ts, data = _CACHE[key]
        if time.time() - ts < _CACHE_TTL:
            return data

    try:
        resp = requests.get(_URL, headers=_HEADERS, timeout=12)
        resp.raise_for_status()
        parser = _CalParser()
        parser.feed(resp.text)
        events = _extract_events(parser.events, target_date)
        _CACHE[key] = (time.time(), events)
        return events
    except Exception as e:
        print(f"[news_filter] fetch failed: {e}", file=sys.stderr)
        _CACHE[key] = (time.time(), [])
        return []


def is_news_blackout(bar_et: datetime,
                     before_min: int = 30,
                     after_min:  int = 15,
                     impact:     str = "red") -> bool:
    """
    True if bar_et falls within [event-before_min, event+after_min]
    of any US news event with the given impact level (or higher).
    Only checks red by default; pass impact="orange" to include orange.
    """
    events = fetch_us_news(bar_et.date())
    rank   = {"red": 2, "orange": 1, "yellow": 0}
    min_rank = rank.get(impact, 2)

    for ev in events:
        if rank.get(ev["impact"], 0) < min_rank:
            continue
        diff = (bar_et - ev["time"]).total_seconds()
        if -before_min * 60 <= diff <= after_min * 60:
            return True
    return False


def print_todays_news() -> None:
    """CLI helper: print today's high-impact USD events."""
    events = fetch_us_news()
    if not events:
        print("No high-impact USD events found (or calendar unavailable).")
        return
    for ev in events:
        print(f"  {ev['time'].strftime('%H:%M ET')}  [{ev['impact'].upper():6s}]  {ev['event']}")
