"""Polite, robots-aware HTTP fetching with an on-disk cache. Stdlib only.

Rules baked in (not configurable away): identify ourselves, honor robots.txt
(RFC 9309: 4xx = no rules, 5xx/unreachable = assume disallowed), at most one
request per host per `min_interval` seconds, conditional GETs so an unchanged
page costs nothing, and a hard body-size cap.
"""
import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urlparse

from textutil import sha256

def prefer_ipv4():
    """Some networks black-hole IPv6 (urllib then hangs on SYN_SENT instead of falling back).
    Sort IPv4 addresses first so an unattended daemon never stalls on a dead v6 route."""
    if getattr(socket.getaddrinfo, "_bb_v4_first", False):
        return
    orig = socket.getaddrinfo

    def v4_first(*a, **k):
        return sorted(orig(*a, **k), key=lambda r: r[0] != socket.AF_INET)
    v4_first._bb_v4_first = True
    socket.getaddrinfo = v4_first


prefer_ipv4()

DEFAULT_UA = "BoardBoredBot/1.0 (+https://seekerflame.github.io/board-bored-bakersfield/; boardquestionmark@gmail.com)"
ALLOWED_TYPES = ("text/html", "text/plain", "text/calendar", "application/json", "application/ld+json", "application/xhtml")


class FetchRefused(Exception):
    pass


class PoliteFetcher:
    def __init__(self, cache_dir, user_agent=DEFAULT_UA, min_interval=10.0, timeout=20,
                 max_bytes=2_000_000, opener=None, sleeper=time.sleep, clock=time.time):
        self.ua = user_agent
        self.token = user_agent.split("/")[0]
        self.dir = cache_dir
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._open = opener or urllib.request.urlopen
        self._sleep, self._clock = sleeper, clock
        self._last = {}
        self._robots = {}
        os.makedirs(cache_dir, exist_ok=True)
        self._index_path = os.path.join(cache_dir, "index.json")
        try:
            with open(self._index_path) as f:
                self._index = json.load(f)
        except Exception:
            self._index = {}

    # -- robots ---------------------------------------------------------
    def robots_ok(self, url):
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc:
            return False, "bad scheme"
        host = p.scheme + "://" + p.netloc
        rp = self._robots.get(host)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            status, body = self._raw_get(host + "/robots.txt", cap=500_000)
            if status == 200:
                rp.parse(body.splitlines())
            elif status is not None and 400 <= status < 500:
                rp.parse([])  # no robots.txt: everything allowed
            else:
                rp.parse(["User-agent: *", "Disallow: /"])  # unreachable: assume closed
            self._robots[host] = rp
        ok = rp.can_fetch(self.token, url)
        return ok, "" if ok else "disallowed by robots.txt"

    def _raw_get(self, url, headers=None, cap=None):
        self._throttle(url)
        req = urllib.request.Request(url, headers=dict({"User-Agent": self.ua, "Accept": "*/*"}, **(headers or {})))
        try:
            with self._open(req, timeout=self.timeout) as r:
                data = r.read((cap or self.max_bytes) + 1)
                return r.status if hasattr(r, "status") else 200, data.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, ""
        except Exception:
            return None, ""

    def _throttle(self, url):
        host = urlparse(url).netloc
        wait = self._last.get(host, -1e9) + self.min_interval - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last[host] = self._clock()

    # -- fetch ----------------------------------------------------------
    def get(self, url):
        ok, why = self.robots_ok(url)
        if not ok:
            raise FetchRefused("%s: %s" % (url, why))
        key = hashlib.sha1(url.encode()).hexdigest()
        body_path = os.path.join(self.dir, key + ".body")
        meta = self._index.get(url, {})
        headers = {}
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
        self._throttle(url)
        req = urllib.request.Request(url, headers=dict({"User-Agent": self.ua, "Accept": "text/html,text/calendar,application/json;q=0.9,*/*;q=0.5"}, **headers))
        try:
            with self._open(req, timeout=self.timeout) as r:
                status = r.status if hasattr(r, "status") else 200
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype and not ctype.startswith(ALLOWED_TYPES):
                    raise FetchRefused("%s: content-type %s not allowed" % (url, ctype))
                raw = r.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    raise FetchRefused("%s: body exceeds %d bytes" % (url, self.max_bytes))
                charset = "utf-8"
                for part in (r.headers.get("Content-Type") or "").split(";")[1:]:
                    if "charset=" in part:
                        charset = part.split("=", 1)[1].strip() or "utf-8"
                try:
                    body = raw.decode(charset, "replace")
                except LookupError:
                    body = raw.decode("utf-8", "replace")
                with open(body_path, "w", encoding="utf-8") as f:
                    f.write(body)
                self._index[url] = {"etag": r.headers.get("ETag"), "last_modified": r.headers.get("Last-Modified"),
                                    "fetched_at": self._clock(), "ctype": ctype}
                self._save_index()
                return {"url": url, "status": status, "body": body, "content_type": ctype,
                        "sha256": sha256(body), "from_cache": False}
        except urllib.error.HTTPError as e:
            if e.code == 304 and os.path.exists(body_path):
                with open(body_path, encoding="utf-8") as f:
                    body = f.read()
                return {"url": url, "status": 200, "body": body, "content_type": meta.get("ctype", ""),
                        "sha256": sha256(body), "from_cache": True}
            return {"url": url, "status": e.code, "body": "", "content_type": "", "sha256": "", "from_cache": False}
        except FetchRefused:
            raise
        except Exception as e:
            return {"url": url, "status": None, "body": "", "content_type": "", "sha256": "",
                    "from_cache": False, "error": type(e).__name__}

    def head_status(self, url):
        """Status of a link we will print on the site. 403/405 (bot walls, HEAD-hostile
        servers) are 'unverifiable', not 'dead'."""
        ok, _ = self.robots_ok(url)
        if not ok:
            return "unverifiable"
        self._throttle(url)
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": self.ua})
        try:
            with self._open(req, timeout=self.timeout) as r:
                return "ok" if 200 <= (r.status if hasattr(r, "status") else 200) < 400 else "dead"
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return "dead"
            return "unverifiable" if e.code in (401, 403, 405, 429, 999) or e.code >= 500 else "dead"
        except Exception:
            return "unverifiable"

    def _save_index(self):
        tmp = self._index_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._index, f)
        os.replace(tmp, self._index_path)
