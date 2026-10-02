"""adapters: deterministic voices. Each returns candidate dicts with a verbatim quote and a source_id.

foxnile: bakersfieldlive.com runs the Fox + Nile theaters. Its /event-calendar page carries one visible card per show
(title, date, door/show times, venue, tixr link). Its home-page JSON-LD declares name/date/link but the venue/address
there are WRONG for every Nile show, so that voice is registered as not vouching for venue (see config `untrusted`).
"""
import html
import json
import re

from textutil import date_headings, find_dates, find_times, html_to_text, jsonld_events, to_hhmm


def foxnile_cards(page_html, source_id, today, base_url=""):
    """One candidate per visible calendar card. Returns (candidates, source_text)."""
    page_text = html_to_text(page_html, base_url)
    out = []
    for block in re.split(r'(?=<div[^>]+class="cal-container cal")', page_html)[1:]:
        block = block.split('<div role="listitem"', 1)[0]
        text = html_to_text(block, base_url)
        title = re.search(r'class="main-title-hover-3">(.*?)</div>', block, re.S)
        venue = re.search(r'fs-cmsfilter-field="venue"[^>]*>(.*?)</div>', block, re.S)
        date_txt = re.search(r'class="b-venue">(.*?)</p>', block, re.S)
        link = re.search(r'href="(https://tixr\.com/e/\d+)"', block)
        if not (title and venue and date_txt):
            continue
        dates = find_dates(html.unescape(date_txt.group(1)), today)
        if not dates:
            continue
        show = re.search(r"SHOW STARTS:\s*([0-9: ]+[ap]m)", text, re.I)
        start = None
        if show:
            tm = find_times(show.group(1))
            start = to_hhmm(tm[0][0]) if tm else None
        out.append({"name": title.group(1), "venue": venue.group(1), "date": dates[0].date.isoformat(), "start": start,
                    "end": None, "cost": None, "link": link.group(1) if link else None, "quote": text,
                    "voice": "foxnile:card", "family": "struct:card", "source_id": source_id})
    return out, page_text


def foxnile_jsonld(page_html, source_id, today):
    """Publisher-declared name/date/link. Source text is the re-serialised JSON-LD (deterministic); the quote is the
    contiguous name..startDate span of that same serialisation."""
    evs = jsonld_events(page_html)
    pieces, out = [], []
    for e in evs:
        s = json.dumps(e, ensure_ascii=False)
        pieces.append(s)
        m = re.search(r'"name": ".*?"startDate": "[^"]*"', s)
        offers = e.get("offers") if isinstance(e.get("offers"), dict) else {}
        d = find_dates(str(e.get("startDate")), today)
        if not (m and d):
            continue
        out.append({"name": e.get("name"), "venue": None, "date": d[0].date.isoformat(), "start": None, "end": None,
                    "cost": None, "link": offers.get("url"), "quote": m.group(0),
                    "voice": "foxnile:jsonld", "family": "struct:jsonld", "source_id": source_id})
    return out, "\n".join(pieces)


_BULLET = re.compile(r"^\u2022\s*(?P<name>.+?)\s+@\s+(?P<venue>.+?)(?=\s*(?:\[https?://|\(|$))")
_STREET = re.compile(r"\b(\d{2,5}\s+[A-Z0-9][A-Za-z0-9.'\- ]{1,30}?\s(?:St|Ave|Dr|Blvd|Rd|Way|Ln|Hwy|Pkwy|Ct|Pl|Street|Avenue|Drive|Boulevard|Road|Lane)\b\.?)")


def newsletter_bullets(text, source_id, today):
    """'• NAME @ VENUE [link] (TIME) 1600 20th St. description' lines. The date is the governing weekday heading above
    the bullet (the gate re-derives and checks it independently). Quote = the bullet line itself."""
    out = []
    heads = date_headings(text, today)
    if len(heads) < 3:
        return out
    pos = 0
    for raw in text.split("\n"):
        line, here, pos = raw.strip(), pos, pos + len(raw) + 1
        m = _BULLET.match(line)
        if not m:
            continue
        prev = [h for h in heads if h[0] <= here]
        if not prev:
            continue
        link = re.search(r"\[(https?://[^\]\s]+)\]", line)
        grp = re.search(r"\(([^)]*\d[^)]*)\)", line)
        start = end = None
        if grp:
            ts = find_times(grp.group(1))
            if ts:
                start = to_hhmm(ts[0][0])
                if len(ts) > 1 and ts[1][2]:
                    end = to_hhmm(ts[1][0])
        addr = _STREET.search(line)
        out.append({"name": m.group("name"), "venue": m.group("venue"), "date": prev[-1][1].isoformat(), "start": start, "end": end, "cost": None,
                    "link": link.group(1) if link else None, "address": addr.group(1).strip() if addr else None, "quote": line,
                    "voice": "newsletter:bullets", "family": "struct:bullets", "source_id": source_id})
    return out
