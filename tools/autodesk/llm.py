"""llm: local-model extraction voices (Ollama). A voice only PROPOSES candidates with a verbatim quote;
truthgate decides. Outputs are cached by sha256(model + prompt version + chunk) so an unchanged page costs nothing.
"""
import hashlib
import json
import os
import re
import urllib.request

from textutil import find_dates, find_times, heading_date, to_hhmm

PROMPT_VERSION = "v3"
SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"}, "venue": {"type": "string"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "start": {"type": "string", "description": "HH:MM 24h, empty if not stated"},
            "end": {"type": "string"}, "cost": {"type": "string"}, "link": {"type": "string"},
            "quote": {"type": "string", "description": "copy the page lines for this event exactly as written, from the line holding its date or name through the line holding its venue, newlines included"},
        },
        "required": ["name", "venue", "date", "quote"]}}},
    "required": ["events"]}

SYSTEM = ("You extract public events from web page text. Copy facts exactly; never infer, never invent. "
          "If the page does not state a field, leave it empty. Dates as YYYY-MM-DD, times as 24h HH:MM. "
          "Each event needs a `quote`: copy ALL the page lines that describe that one event, in order, exactly as written "
          "(for a listing this is usually its date line, its title line, its time lines and its venue line together). "
          "A quote that leaves out the title, the date or the venue is wrong. Skip tickets-only items "
          "(season passes, gift cards, parking). Listings are often grouped under date headings such as 'FRIDAY, SEPTEMBER 25': "
          "an event listed below a heading happens on that date. Output JSON only.")


def chunk_text(text, size=3200, overlap=300, today=None):
    """Line-aligned chunks. When `today` is given, a chunk that starts below a date heading is re-headed with
    that heading line (verbatim from the page) so the model can see which date governs its events."""
    lines, out, cur, last_head = text.split("\n"), [], "", None
    for ln in lines:
        if len(cur) + len(ln) + 1 > size and cur:
            out.append(cur)
            cur = cur[-overlap:] if overlap else ""
            cur = cur[cur.find("\n") + 1:] if "\n" in cur else ""
            if today and last_head and not any(heading_date(x, today) for x in cur.split("\n")[:2]):
                cur = last_head + "\n\n" + cur
        if today and heading_date(ln, today):
            last_head = ln.strip()
        cur += ln + "\n"
    if cur.strip():
        out.append(cur)
    return out


def post_json(url, payload, timeout=300, opener=urllib.request.urlopen):
    req = urllib.request.Request(url, method="POST", headers={"Content-Type": "application/json"}, data=json.dumps(payload).encode())
    with opener(req, timeout=timeout) as r:
        return json.loads(r.read())


def normalize_date(v, today):
    """Models answer 'October 5, 2026' when asked for ISO. Re-express it; the gate still has to find it in the quote."""
    v = str(v or "").strip()
    if len(v) == 10 and v[4] == "-" and v[7] == "-":
        return v
    d = find_dates(v, today)
    return d[0].date.isoformat() if d else v or None


_MD_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)|\[https?://[^\]]*\]|https?://\S+")


def tidy_fields(e):
    """Formatting hygiene only (no trust): small models write 'Name @ Venue' into the name field, leave '(7PM)' or link
    residue on the venue, and keep the bullet glyph. The gate still has to ground every resulting value in the page."""
    name, venue = str(e.get("name") or ""), str(e.get("venue") or "")
    name = _MD_LINK.sub(" ", name).lstrip("\u2022*- ").strip()
    if " @ " in name:
        name, tail = name.split(" @ ", 1)
        if not venue.strip():
            venue = tail
    venue = _MD_LINK.sub(" ", venue)
    venue = re.sub(r"\s*\([^)]*\)", "", venue)  # '(7PM)', '(Check-in 3:30-4:30PM)', '(According to their website)'
    e["name"] = re.sub(r"\s+", " ", name).strip(" ,;:-")
    e["venue"] = re.sub(r"\s+", " ", venue).strip(" ,;:-") or None
    return e


def normalize_time(v):
    v = str(v or "").strip()
    if not v:
        return None
    if len(v) == 5 and v[2] == ":" and v.replace(":", "").isdigit():
        return v
    t = find_times(v)
    return to_hhmm(t[0][0]) if t else None


class OllamaVoice:
    def __init__(self, model, family, host="http://localhost:11434", cache_dir=None, opener=urllib.request.urlopen,
                 timeout=300, num_ctx=8192):
        self.model, self.family, self.host = model, family, host
        self.cache_dir, self._open, self.timeout, self.num_ctx = cache_dir, opener, timeout, num_ctx
        self.stats = {"chunks": 0, "cache_hits": 0, "errors": 0}
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def _key(self, chunk, today):
        return hashlib.sha256(("%s|%s|%s|%s" % (self.model, PROMPT_VERSION, today, chunk)).encode()).hexdigest()

    def extract_chunk(self, chunk, today, url=""):
        key = self._key(chunk, today)
        path = os.path.join(self.cache_dir, "llm_" + key + ".json") if self.cache_dir else None
        if path and os.path.exists(path):
            self.stats["cache_hits"] += 1
            with open(path) as f:
                return json.load(f)
        user = "Today is %s. Page: %s\n\nTEXT:\n%s" % (today, url, chunk)
        try:
            res = post_json(self.host + "/api/chat", {
                "model": self.model, "stream": False, "format": SCHEMA, "keep_alive": "3m",
                "options": {"temperature": 0, "num_ctx": self.num_ctx},
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]},
                timeout=self.timeout, opener=self._open)
            events = json.loads(res["message"]["content"]).get("events", [])
            if not isinstance(events, list):
                events = []
        except Exception:
            self.stats["errors"] += 1
            return []  # a failed voice contributes nothing; it can never publish anything
        if path:
            with open(path + ".tmp", "w") as f:
                json.dump(events, f)
            os.replace(path + ".tmp", path)
        return events

    def extract(self, text, today, source_id, url=""):
        out = []
        for chunk in chunk_text(text, today=today):
            self.stats["chunks"] += 1
            for e in self.extract_chunk(chunk, today.isoformat(), url):
                if isinstance(e, dict):
                    e = dict(e, voice=self.model, family=self.family, source_id=source_id)
                    tidy_fields(e)
                    e["date"], e["start"], e["end"] = (normalize_date(e.get("date"), today), normalize_time(e.get("start")), normalize_time(e.get("end")))
                    for k in ("start", "end", "cost", "link"):
                        if e.get(k) == "":
                            e[k] = None
                    out.append(e)
        return out

    def unload(self):
        try:
            post_json(self.host + "/api/generate", {"model": self.model, "keep_alive": 0}, timeout=30, opener=self._open)
        except Exception:
            pass
