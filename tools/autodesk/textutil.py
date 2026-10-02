"""Text, date and time primitives for autodesk. Stdlib only.

Everything the truth gate decides rests on these: a claim is only accepted if
it can be located in the fetched source text by these parsers, not because a
model said so.
"""
import datetime as dt
import hashlib
import json
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urljoin

STOP = {"the", "a", "an", "at", "in", "on", "of", "and", "with", "for", "to",
        "from", "by", "presents", "present", "feat", "featuring", "w"}

_MONTH_NAMES = ("january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|"
                "august|aug|september|sept|sep|october|oct|november|nov|december|dec")
_WD_NAMES = "sunday|sun|monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat"
MONTHS = {}
for _i, _full in enumerate(["january", "february", "march", "april", "may", "june", "july",
                            "august", "september", "october", "november", "december"], 1):
    MONTHS[_full] = _i
    MONTHS[_full[:3]] = _i
MONTHS["sept"] = 9
WEEKDAYS = {}
for _i, _full in enumerate(["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]):
    WEEKDAYS[_full] = _i
    WEEKDAYS[_full[:3]] = _i
WEEKDAYS.update({"tues": 2, "thur": 4, "thurs": 4})

DATE_RE = re.compile(
    r"(?i)(?:(?P<wd>\b(?:%s)\b)\.?,?\s+)?\b(?P<mon>(?:%s))\b\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\b"
    r"(?:,?\s+(?P<year>\d{4})\b)?" % (_WD_NAMES, _MONTH_NAMES))
NUM_DATE_RE = re.compile(r"(?<![\d/])(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2,4}))?(?![\d/])")
ISO_DATE_RE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
WEEKDAY_RE = re.compile(r"(?i)\b(%s)\b" % _WD_NAMES)

_AP = r"(?:a\.?m\.?|p\.?m\.?)"
RANGE_RE = re.compile(
    r"(?i)(?<![\d:])(?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\s*(?P<ap1>%s)?\s*[-–—]\s*"
    r"(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\s*(?P<ap2>%s)(?![a-z])" % (_AP, _AP))
TIME_RE = re.compile(r"(?i)(?<![\d:])(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>%s)(?![a-z])" % _AP)
H24_RE = re.compile(r"(?<![\d:])(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)(?!\s*(?:[ap]\.?m)|\d)", re.I)


def norm(s):
    s = str(s or "").replace("\u2019", "'").replace("\u2018", "'")  # curly and straight apostrophes must normalise identically
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s):
    return [t for t in norm(s).split() if t not in STOP]


def cover(needle, hay):
    """Fraction of needle's tokens that appear in hay (1.0 when needle has none)."""
    nt = tokens(needle)
    if not nt:
        return 1.0
    ht = set(norm(hay).split())
    return sum(1 for t in nt if t in ht) / len(nt)


def sha256(s):
    return hashlib.sha256(s.encode("utf-8", "ignore")).hexdigest()


def squash(s):
    """Whitespace/punctuation/case-insensitive form used for substring grounding."""
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower())


def locate_quote(source, quote):
    """Return (start, end) offsets of quote in source, or None. Tolerant of whitespace,
    punctuation and case differences; the quote must still be a contiguous span."""
    q = squash(quote)
    if len(q) < 8:
        return None
    # Build a squashed source with an index map back to original offsets.
    chars, idx = [], []
    for i, ch in enumerate(source):
        c = squash(ch)
        if c:
            chars.append(c)
            idx.append(i)
    flat = "".join(chars)
    pos = flat.find(q)
    if pos < 0:
        return None
    return idx[pos], idx[pos + len(q) - 1] + 1


def _fuzzy_tokens(text):
    """Token set for fuzzy line matching. Times and bracketed links are dropped on both sides: models tidy them
    ('7:30PM' -> '07:30 PM', link omitted) and the gate verifies times and links separately, against the page."""
    t = re.sub(r"\[https?://[^\]]*\]", " ", text)
    t = RANGE_RE.sub(" ", t)
    t = TIME_RE.sub(" ", t)
    t = H24_RE.sub(" ", t)
    return set(norm(t).split())


def locate_quote_fuzzy(source, quote, threshold=0.85, max_lines=3):
    """Models paraphrase quotes (drop a link, tidy '6:00' to '06:00', skip a line). Find the 1-3 consecutive real source
    lines the quote is overwhelmingly made of. Returns (start, end, window_text) or None. The caller then judges the claim
    against that REAL text; the model's wording is never used as evidence."""
    q = _fuzzy_tokens(quote)
    if len(q) < 4:
        return None
    lines, pos = [], 0
    for ln in source.split("\n"):
        if ln.strip():
            lines.append((pos, pos + len(ln), ln))
        pos += len(ln) + 1
    # Tightest window first: a 1-line match beats a 2- or 3-line one even if the longer one scores a hair higher. Extra
    # lines would let a neighbouring record's date/venue/time leak into the evidence.
    for w in range(1, max_lines + 1):
        best = None
        for i in range(len(lines) - w + 1):
            chunk = lines[i:i + w]
            text = "\n".join(c[2] for c in chunk)
            t = _fuzzy_tokens(text)
            if not t or len(t) > 3 * len(q):
                continue
            score = len(q & t) / len(q)
            if score >= threshold and (best is None or score > best[0]):
                best = (score, chunk[0][0], chunk[-1][1], text)
        if best:
            return (best[1], best[2], best[3])
    return None


class DateTok:
    __slots__ = ("date", "start", "end", "has_year", "wd")

    def __init__(self, date, start, end, has_year, wd):
        self.date, self.start, self.end, self.has_year, self.wd = date, start, end, has_year, wd

    def __repr__(self):
        return "DateTok(%s@%d)" % (self.date, self.start)


def resolve_year(month, day, today, tolerance_days=60):
    best = None
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            d = dt.date(y, month, day)
        except ValueError:
            continue
        delta = (d - today).days
        key = (0 if delta >= -tolerance_days else 1, abs(delta))
        if best is None or key < best[0]:
            best = (key, d)
    return best[1] if best else None


def find_dates(text, today):
    out = []
    for m in DATE_RE.finditer(text):
        mon = MONTHS[m.group("mon").lower()]
        day = int(m.group("day"))
        if m.group("year"):
            try:
                d = dt.date(int(m.group("year")), mon, day)
            except ValueError:
                continue
            has_year = True
        else:
            d = resolve_year(mon, day, today)
            has_year = False
        if d is None:
            continue
        wd = WEEKDAYS[m.group("wd").lower()] if m.group("wd") else None
        if wd is not None and (d.weekday() + 1) % 7 != wd:
            continue  # "Fri, Oct 9" where Oct 9 is not a Friday: not a coherent date token
        out.append(DateTok(d, m.start(), m.end(), has_year, wd))
    for m in ISO_DATE_RE.finditer(text):
        try:
            d = dt.date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        except ValueError:
            continue
        out.append(DateTok(d, m.start(), m.end(), True, None))
    for m in NUM_DATE_RE.finditer(text):
        mon, day = int(m.group("m")), int(m.group("d"))
        if not (1 <= mon <= 12 and 1 <= day <= 31):
            continue
        if m.group("y"):
            y = int(m.group("y"))
            y += 2000 if y < 100 else 0
            try:
                d = dt.date(y, mon, day)
            except ValueError:
                continue
            has_year = True
        else:
            d = resolve_year(mon, day, today)
            has_year = False
        if d is not None:
            out.append(DateTok(d, m.start(), m.end(), has_year, None))
    out.sort(key=lambda t: t.start)
    return out


def heading_date(line, today):
    """Date of a PURE date heading ('FRIDAY, SEPTEMBER 25', 'THURSDAY \u2022 AUGUST 20'); None for anything else.
    Rejects ranges ('September 24\u201330'), bylines with extra words, and weekday/date mismatches."""
    s = line.strip()
    if not s or len(s) > 45:
        return None
    ds = find_dates(s, today)
    if len(ds) != 1:
        return None
    t = ds[0]
    rest = WEEKDAY_RE.sub("", s[:t.start] + " " + s[t.end:])
    if re.sub(r"[^A-Za-z0-9]", "", rest):
        return None
    wd = WEEKDAY_RE.search(s)
    if not wd or WEEKDAYS[wd.group(1).lower()] != (t.date.weekday() + 1) % 7:
        return None  # a heading names its weekday and the weekday must be right; a byline ('Sep 25, 2026') never qualifies
    return t.date


def date_headings(source, today):
    """[(offset, date)] of pure date-heading lines."""
    out, pos = [], 0
    for ln in source.split("\n"):
        d = heading_date(ln, today)
        if d:
            out.append((pos, d, ln.strip()))
        pos += len(ln) + 1
    return out


def governing_heading(source, pos, today, min_headings=3):
    """Nearest date heading above `pos`, but only when the document is genuinely organised by date headings
    (>= min_headings of them). A lone byline date must never be attached to a dateless event."""
    hs = date_headings(source, today)
    if len(hs) < min_headings:
        return None
    prev = [h for h in hs if h[0] <= pos]
    return prev[-1] if prev else None


def _hm(h, m, ap):
    h, m = int(h), int(m or 0)
    if m > 59 or h > 12 or h < 1:
        return None
    ap = (ap or "").lower().replace(".", "")
    if ap.startswith("p") and h != 12:
        h += 12
    if ap.startswith("a") and h == 12:
        h = 0
    return h * 60 + m


def find_times(text):
    """List of (minutes_from_midnight, start_offset, is_range_end) for every time token."""
    out, covered = [], []
    for m in RANGE_RE.finditer(text):
        ap2 = m.group("ap2")
        ap1 = m.group("ap1") or ap2
        a = _hm(m.group("h1"), m.group("m1"), ap1)
        b = _hm(m.group("h2"), m.group("m2"), ap2)
        if a is None or b is None:
            continue
        if not m.group("ap1") and a > b:  # "11-2PM" means 11AM-2PM
            a = _hm(m.group("h1"), m.group("m1"), "am")
        out.append((a, m.start(), False))
        out.append((b, m.start("h2"), True))
        covered.append((m.start(), m.end()))
    for m in TIME_RE.finditer(text):
        if any(s <= m.start() < e for s, e in covered):
            continue
        v = _hm(m.group("h"), m.group("m"), m.group("ap"))
        if v is not None:
            out.append((v, m.start(), False))
    for m in H24_RE.finditer(text):
        if any(s <= m.start() < e for s, e in covered):
            continue
        out.append((int(m.group("h")) * 60 + int(m.group("m")), m.start(), False))
    for m in re.finditer(r"(?i)\b(noon|midnight)\b", text):
        out.append((720 if m.group(1).lower() == "noon" else 0, m.start(), False))
    out.sort(key=lambda t: t[1])
    return out


def to_hhmm(minutes):
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def hhmm_to_min(s):
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(s or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


class _TextParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe"}
    BLOCK = {"p", "div", "li", "br", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
             "section", "article", "ul", "ol", "table", "header", "footer", "main", "time", "dt", "dd"}

    def __init__(self, base_url=""):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.base = [], 0, base_url
        self._href = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag == "time" and a.get("datetime"):
            self.out.append(" [datetime=%s] " % a["datetime"])
        if tag == "a" and a.get("href"):
            href = urljoin(self.base, a["href"].strip())
            self._href = href if href.startswith(("http://", "https://")) else None

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "a" and self._href:
            self.out.append(" [%s] " % self._href)
            self._href = None
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html, base_url=""):
    p = _TextParser(base_url)
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    text = "".join(p.out)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.split("\n")]
    out, blank = [], 0
    for ln in lines:
        if ln:
            out.append(ln)
            blank = 0
        elif blank == 0 and out:
            out.append("")
            blank = 1
    return "\n".join(out).strip()


def jsonld_events(html):
    """Schema.org Event objects embedded as JSON-LD: the publisher's own machine-readable
    declaration, truthful by construction (no model involved)."""
    found = []
    for m in re.finditer(r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                t = node.get("@type")
                types = t if isinstance(t, list) else [t]
                if any(isinstance(x, str) and x.endswith("Event") for x in types) and node.get("startDate"):
                    found.append(node)
                for k in ("@graph", "itemListElement", "subEvent", "event"):
                    if k in node:
                        stack.append(node[k])
                if "item" in node:
                    stack.append(node["item"])
    return found
