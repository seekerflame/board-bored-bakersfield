#!/usr/bin/env node
/* test.js — dependency-free unit tests for the security + offline cores.
 *
 * Run: node tools/test.js   (also runs in CI on every push/PR)
 *
 * Covers the two pieces of logic the whole site trusts:
 *   1. lib/safe.js  — HTML escaping + URL scheme allow-listing (XSS defence)
 *   2. lib/pwa.js   — the offline submission queue (retry/keep/drop)
 */
"use strict";
var assert = require("assert");
var safe = require("../lib/safe.js");
var pwa = require("../lib/pwa.js");

var passed = 0;
function t(name, fn) {
  try { fn(); passed++; }
  catch (e) { console.error("✗ " + name + "\n  " + e.message); process.exitCode = 1; }
}

// ---------------------------------------------------------------- esc()
t("esc neutralises tag injection", function () {
  var out = safe.esc('<img src=x onerror="alert(1)">');
  assert(out.indexOf("<img") === -1, "left a raw tag");
  assert(out.indexOf('onerror="') === -1, "left a raw attribute");
});
t("esc escapes quotes and ampersands", function () {
  assert.strictEqual(safe.esc('a & "b" \'c\''), "a &amp; &quot;b&quot; &#39;c&#39;");
});
t("esc coerces null/undefined to empty", function () {
  assert.strictEqual(safe.esc(null), "");
  assert.strictEqual(safe.esc(undefined), "");
});

// ---------------------------------------------------------------- safeUrl()
t("safeUrl blocks javascript: (and case/whitespace tricks)", function () {
  assert.strictEqual(safe.safeUrl("javascript:alert(1)"), "#");
  assert.strictEqual(safe.safeUrl("JavaScript:alert(1)"), "#");
  assert.strictEqual(safe.safeUrl("  javascript:alert(1)"), "#");
  assert.strictEqual(safe.safeUrl("java\tscript:alert(1)"), "#");
});
t("safeUrl blocks data:, vbscript:, file:", function () {
  assert.strictEqual(safe.safeUrl("data:text/html,<script>"), "#");
  assert.strictEqual(safe.safeUrl("vbscript:msgbox(1)"), "#");
  assert.strictEqual(safe.safeUrl("file:///etc/passwd"), "#");
});
t("safeUrl passes http/https/mailto/tel and escapes them", function () {
  assert.strictEqual(safe.safeUrl("https://dagnys.com/menu"), "https://dagnys.com/menu");
  assert.strictEqual(safe.safeUrl("mailto:a@b.com"), "mailto:a@b.com");
  assert.strictEqual(safe.safeUrl("tel:+15550100"), "tel:+15550100");
});
t("safeUrl upgrades a bare hostname to https", function () {
  assert.strictEqual(safe.safeUrl("dagnys.com/menu"), "https://dagnys.com/menu");
});
t("safeUrl escapes an attribute-breakout attempt", function () {
  assert.strictEqual(safe.safeUrl('https://x.com" onmouseover="alert(1)'),
    "https://x.com&quot; onmouseover=&quot;alert(1)");
});
t("safeUrl allows site-relative and anchor links", function () {
  assert.strictEqual(safe.safeUrl("/first-friday/"), "/first-friday/");
  assert.strictEqual(safe.safeUrl("#events"), "#events");
});

// ---------------------------------------------------------------- offline queue
function memStore() {
  var m = new Map(), id = 0;
  return {
    add: function (it) { var k = ++id; m.set(k, it); return Promise.resolve(k); },
    all: function () { return Promise.resolve([].concat.apply([], [...m].map(function (e) { return [{ key: e[0], item: e[1] }]; }))); },
    del: function (k) { m.delete(k); return Promise.resolve(); },
    put: function (k, it) { m.set(k, it); return Promise.resolve(); }
  };
}
var makeQueue = pwa.BB && pwa.BB._makeQueue;

var asyncTests = [];
function at(name, fn) { asyncTests.push([name, fn]); }

at("queue keeps items while offline, sends when back online", function () {
  var q = makeQueue(memStore());
  return q.enqueue("https://x", "a")
    .then(function () { return q.enqueue("https://x", "b"); })
    .then(function () { return q.pending(); })
    .then(function (n) { assert.strictEqual(n, 2, "should hold 2"); })
    .then(function () { return q.flush(function () { return Promise.reject(new Error("offline")); }); })
    .then(function (r) { assert.strictEqual(r.kept, 2); assert.strictEqual(r.sent, 0); })
    .then(function () { return q.flush(function () { return Promise.resolve({ ok: true }); }); })
    .then(function (r) { assert.strictEqual(r.sent, 2); })
    .then(function () { return q.pending(); })
    .then(function (n) { assert.strictEqual(n, 0, "queue should be empty"); });
});

at("queue drops a permanently-rejected (4xx) payload after MAX_TRIES", function () {
  var q = makeQueue(memStore());
  var chain = q.enqueue("https://x", "poison");
  // 10 flushes where the server keeps answering "not ok" (4xx)
  for (var i = 0; i < 10; i++) {
    chain = chain.then(function () { return q.flush(function () { return Promise.resolve({ ok: false }); }); });
  }
  return chain
    .then(function () { return q.pending(); })
    .then(function (n) { assert.strictEqual(n, 0, "poison payload should be dropped, not retried forever"); });
});


// ---------------------------------------------------------------- ingest/normalize.js
var normalize = require("./ingest/normalize.js");
t("normalize.toCityEvent fills defaults and builds a stable id", function () {
  var e = normalize.toCityEvent({
    name: "Trivia Night", venue: "Some Bar", date: "2026-11-20",
    category: "Community", source: "test",
  }, "ics");
  assert.strictEqual(e.id, "ce_ics_trivia_night_20261120");
  assert.strictEqual(e.emoji, "🤝");
  assert.strictEqual(e.schedule.date, "2026-11-20");
});
t("normalize.toCityEvent throws on missing required fields", function () {
  assert.throws(function () { normalize.toCityEvent({ name: "X" }, "ics"); });
});
t("normalize picks the category emoji when none is given", function () {
  var e = normalize.toCityEvent({ name: "Show", venue: "V", date: "2026-01-01", category: "Music" }, "tm");
  assert.strictEqual(e.emoji, "🎸");
});

// ---------------------------------------------------------------- ingest/dedupe.js
var dedupe = require("./ingest/dedupe.js");
t("dedupe catches an exact id collision", function () {
  var existing = [{ id: "ce_x", name: "A", venue: "V", schedule: { date: "2026-01-01" } }];
  var dup = dedupe.findDuplicate({ id: "ce_x", name: "different name", venue: "different venue", schedule: { date: "2020-01-01" } }, existing);
  assert(dup, "same id must be treated as a duplicate regardless of other fields");
});
t("dedupe catches the same event worded differently by two sources", function () {
  var existing = [{ id: "ce_fox_mariachi", name: "Leyendas del Mariachi USA — Mi Historia Tour",
    venue: "Historic Bakersfield Fox Theater", schedule: { date: "2026-09-04" } }];
  var dup = dedupe.findDuplicate({ id: "ce_tm_x", name: "Leyendas del Mariachi — Mi Historia Tour",
    venue: "The Historic Bakersfield Fox Theater", schedule: { date: "2026-09-04" } }, existing);
  assert(dup, "differently-worded duplicate should still be caught");
});
t("dedupe does not false-flag a genuinely different event", function () {
  var existing = [{ id: "ce_a", name: "Leyendas del Mariachi USA", venue: "Fox Theater", schedule: { date: "2026-09-04" } }];
  var dup = dedupe.findDuplicate({ id: "ce_b", name: "Totally Different Touring Act",
    venue: "Rabobank Theater", schedule: { date: "2026-11-12" } }, existing);
  assert.strictEqual(dup, null, "different name+venue+date must not be flagged");
});

// ---------------------------------------------------------------- ingest/sources/ics.js
var ics = require("./ingest/sources/ics.js");
t("ics parser reads SUMMARY/DTSTART/DESCRIPTION/URL from a real-shaped VEVENT", function () {
  var text = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Trivia Night\r\nDTSTART:20261120T190000\r\nDESCRIPTION:Weekly trivia\\, no cover\r\nURL:https://example.com/trivia\r\nEND:VEVENT\r\nEND:VCALENDAR";
  var events = ics.parseICS(text);
  assert.strictEqual(events.length, 1);
  assert.strictEqual(events[0].name, "Trivia Night");
  assert.strictEqual(events[0].date, "2026-11-20");
  assert.strictEqual(events[0].desc, "Weekly trivia, no cover");
  assert.strictEqual(events[0].link, "https://example.com/trivia");
});
t("ics parser handles line folding per RFC 5545", function () {
  var text = "BEGIN:VEVENT\r\nSUMMARY:A long name that got \r\n folded onto a second line\r\nDTSTART:20260101\r\nEND:VEVENT";
  var events = ics.parseICS(text);
  assert.strictEqual(events[0].name, "A long name that got folded onto a second line");
});
t("ics parser skips VEVENTs with no DTSTART rather than crashing", function () {
  var text = "BEGIN:VEVENT\r\nSUMMARY:No date\r\nEND:VEVENT";
  assert.strictEqual(ics.parseICS(text).length, 0);
});

// ---------------------------------------------------------------- ingest/sources/ticketmaster.js
var ticketmaster = require("./ingest/sources/ticketmaster.js");
t("ticketmaster.startupCheck rejects unset and placeholder keys", function () {
  assert.strictEqual(ticketmaster.startupCheck(undefined).ok, false);
  assert.strictEqual(ticketmaster.startupCheck("<your-key-here>").ok, false);
  assert.strictEqual(ticketmaster.startupCheck("replace-me").ok, false);
  assert.strictEqual(ticketmaster.startupCheck("a1b2c3realkey").ok, true);
});
t("ticketmaster.parseEvent maps a realistic Discovery API response", function () {
  var raw = {
    name: "Example Show", url: "https://ticketmaster.com/x",
    dates: { start: { localDate: "2026-11-20", localTime: "19:00:00" } },
    priceRanges: [{ min: 25, max: 95 }],
    classifications: [{ segment: { name: "Music" } }],
    _embedded: { venues: [{ name: "Rabobank Theater",
      address: { line1: "1001 Truxtun Ave" }, city: { name: "Bakersfield" }, state: { stateCode: "CA" }, postalCode: "93301",
      location: { latitude: "35.373", longitude: "-119.019" } }] },
  };
  var e = ticketmaster.parseEvent(raw);
  assert.strictEqual(e.name, "Example Show");
  assert.strictEqual(e.venue, "Rabobank Theater");
  assert.strictEqual(e.date, "2026-11-20");
  assert.strictEqual(e.cost, "$25–$95");
  assert.strictEqual(e.lat, 35.373);
});
t("ticketmaster.parseEvent drops events with no resolvable date", function () {
  var raw = { name: "TBD Show", dates: { start: {} }, _embedded: { venues: [{ name: "V" }] } };
  assert.strictEqual(ticketmaster.parseEvent(raw), null);
});

(function run() {
  if (!makeQueue) { console.error("✗ pwa.js did not export BB._makeQueue"); process.exitCode = 1; }
  var chain = Promise.resolve();
  asyncTests.forEach(function (pair) {
    chain = chain.then(function () {
      return Promise.resolve().then(pair[1]).then(
        function () { passed++; },
        function (e) { console.error("✗ " + pair[0] + "\n  " + e.message); process.exitCode = 1; }
      );
    });
  });
  chain.then(function () {
    console.log((process.exitCode ? "✗ FAILURES above — " : "✓ all ") + passed + " tests passed");
  });
})();
