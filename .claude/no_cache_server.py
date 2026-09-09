#!/usr/bin/env python3
"""Static server with Cache-Control: no-store so local test rounds never see
a stale-cache artifact (bare http.server sends no cache header at all, which
lets browsers heuristically cache and serve old JS/HTML across reloads)."""
import http.server
import os
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8910
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # always serve bbk-live/, regardless of launch cwd


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    # Plain SimpleHTTPRequestHandler defaults to HTTP/1.0, which Chrome's
    # ServiceWorker install fetch rejects outright ("An unknown error
    # occurred when fetching the script") — HTTP/1.1 is required for SW
    # registration to succeed locally; the real deployed site (GitHub Pages)
    # already serves HTTP/1.1 so this only matters for this local server.
    protocol_version = "HTTP/1.1"

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()


http.server.test(HandlerClass=NoCacheHandler, port=port, protocol="HTTP/1.1")  # test() defaults to protocol="HTTP/1.0" and clobbers the class attribute above if omitted
