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
var autodeskSrc = require("./ingest/sources/autodesk.js");
t("autodesk source keeps verified events and drops anything violating the contract", function () {
  var good = { name: "A Show", venue: "Test Hall", address: "1 Fake St", lat: 35.37, lng: -119.02, date: "2030-01-05", start: "20:00", area: "downtown", verified_by: ["struct:card", "struct:jsonld"] };
  var out = autodeskSrc.parse([
    good,
    Object.assign({}, good, { name: "Past", date: "2020-01-01" }),
    Object.assign({}, good, { name: "One voice", verified_by: ["llm:qwen"] }),
    Object.assign({}, good, { name: "Pinless", address: null, lat: null, lng: null }),
    Object.assign({}, good, { name: "Bad time", start: "25:99" }),
    { venue: "X", date: "2030-01-05" },
  ], "2026-10-02");
  assert.strictEqual(out.length, 2);
  assert.strictEqual(out[0].name, "A Show");
  assert.strictEqual(out[1].name, "Bad time");
  assert.strictEqual(out[1].start, null);
});
t("autodesk source tolerates a missing or non-array file payload", function () {
  assert.deepStrictEqual(autodeskSrc.parse(null, "2026-10-02"), []);
  assert.deepStrictEqual(autodeskSrc.parse({ a: 1 }, "2026-10-02"), []);
});
t("normalize.toCityEvent passes start/end/area only when supplied (shape unchanged otherwise)", function () {
  var base = normalize.toCityEvent({ name: "S", venue: "V", date: "2030-01-05" }, "x");
  assert.ok(!("start" in base) && !("area" in base) && !("end" in base));
  var rich = normalize.toCityEvent({ name: "S", venue: "V", date: "2030-01-05", start: "20:00", area: "downtown" }, "x");
  assert.strictEqual(rich.start, "20:00");
  assert.strictEqual(rich.area, "downtown");
});

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

// ---- analytics: inert unless configured; privacy options locked; URLs cleaned ----
var vm = require("vm");
var fs = require("fs");
var path = require("path");
var analyticsSrc = fs.readFileSync(path.join(__dirname, "..", "lib", "analytics.js"), "utf8");
function runAnalytics(cfg, hostname, search) {
  var inserted = [];
  var tag = { parentNode: { insertBefore: function (n) { inserted.push(n); } } };
  var ctx = { BB_ANALYTICS: cfg, document: { createElement: function () { return {}; }, getElementsByTagName: function () { return [tag]; } },
    location: { hostname: hostname, search: search || "", href: "https://" + hostname + "/first-friday/" }, URL: URL, URLSearchParams: URLSearchParams, console: console };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(analyticsSrc, ctx);
  return { ctx: ctx, inserted: inserted };
}
var GOOD = "phc_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789";
var LIVE = { key: GOOD, host: "https://us.i.posthog.com", hosts: ["seekerflame.github.io"] };
t("analytics: empty key = completely off (no script, no posthog object)", function () {
  var r = runAnalytics({ key: "", host: "https://us.i.posthog.com", hosts: ["seekerflame.github.io"] }, "seekerflame.github.io");
  assert.strictEqual(r.inserted.length, 0);
  assert.strictEqual(r.ctx.posthog, undefined);
  assert.strictEqual(typeof r.ctx.bbTrack, "function"); // callers never need to guard
});
t("analytics: placeholder / malformed keys are refused", function () {
  ["phc_xxxxxxxxxxxxxxxxxxxxxxxx", "<ph_project_token>", "replace-me", "phc_short", "phx_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "phc_test_AbCdEfGhIjKlMnOpQrStUvWxYz"].forEach(function (k) {
    assert.strictEqual(runAnalytics({ key: k, hosts: ["seekerflame.github.io"] }, "seekerflame.github.io").inserted.length, 0, k);
  });
});
t("analytics: silent on hosts that are not allow-listed and on ?nt=1", function () {
  assert.strictEqual(runAnalytics(LIVE, "localhost").inserted.length, 0);
  assert.strictEqual(runAnalytics(LIVE, "seekerflame.github.io", "?nt=1").inserted.length, 0);
});
t("analytics: with a real key on the real host it loads PostHog with the locked-down config", function () {
  var r = runAnalytics(LIVE, "seekerflame.github.io");
  assert.strictEqual(r.inserted.length, 1);
  assert.ok(/^https:\/\/us-assets\.i\.posthog\.com\/static\/array\.js$/.test(r.inserted[0].src), r.inserted[0].src);
  var call = r.ctx.posthog._i[0], c = call[1];
  assert.strictEqual(call[0], GOOD);
  assert.strictEqual(c.cookieless_mode, "always");
  assert.strictEqual(c.persistence, "memory");
  assert.strictEqual(c.autocapture, false);
  assert.strictEqual(c.disable_session_recording, true);
  assert.strictEqual(c.disable_surveys, true);
  assert.strictEqual(c.respect_dnt, true);
  assert.strictEqual(c.person_profiles, "identified_only");
});
t("analytics: before_send strips the #fragment (tokens) and non-allow-listed query values from every URL", function () {
  var r = runAnalytics(LIVE, "seekerflame.github.io");
  var bs = r.ctx.posthog._i[0][1].before_send;
  var e = bs({ properties: { $current_url: "https://x.test/first-friday/dashboard.html?ref=abc&token=SECRET&shop=cafe#t=test-venue-co.o.deadbeef", $referrer: "https://x.test/a?email=a@b.com#frag", $set: { email: "a@b.com" } } });
  assert.strictEqual(e.properties.$current_url, "https://x.test/first-friday/dashboard.html?ref=abc&shop=cafe");
  assert.strictEqual(e.properties.$referrer, "https://x.test/a");
  assert.strictEqual(e.properties.$set, undefined);
  assert.ok(!JSON.stringify(e).includes("SECRET") && !JSON.stringify(e).includes("deadbeef") && !JSON.stringify(e).includes("a@b.com"));
});
t("analytics: bbTrack drops unsafe event names and personal-looking values", function () {
  var r = runAnalytics(LIVE, "seekerflame.github.io");
  var sent = [];
  r.ctx.posthog.capture = function (n, p) { sent.push([n, p]); };
  r.ctx.bbTrack("Bad Name!", { a: 1 });
  r.ctx.bbTrack("claim_done", { unlock: "follow", email: "a@b.com", code: "ABC#123", n: 3, note: "x=y", ok: true });
  r.ctx.posthog._i[0][1].loaded(r.ctx.posthog); // PostHog calls this when ready; queued events flush
  assert.strictEqual(sent.length, 1);
  assert.strictEqual(sent[0][0], "claim_done");
  assert.strictEqual(JSON.stringify(sent[0][1]), JSON.stringify({ unlock: "follow", n: 3, ok: true }));
});

t("analytics: private (token) pages never load it; public pages do", function () {
  var root = path.join(__dirname, "..");
  ["first-friday/dashboard.html", "first-friday/redeem.html", "first-friday/account.html", "admin.html", "admin-queue.html"].forEach(function (f) {
    var p = path.join(root, f);
    if (fs.existsSync(p)) assert.ok(!/analytics/.test(fs.readFileSync(p, "utf8")), f + " must not load analytics (private pages never load it)");
  });
  ["index.html", "first-friday/index.html", "first-friday/deals.html", "first-friday/biz.html"].forEach(function (f) {
    assert.ok(/lib\/analytics\.js/.test(fs.readFileSync(path.join(root, f), "utf8")), f + " should load analytics");
  });
});
t("dedupe: a dated occurrence of an existing weekly regular is a duplicate; a different night or venue is not", function () {
  var weekly = [{ id: "w1", name: "Pub Trivia at Temblor Brewing", venue: "Temblor Brewing Company", schedule: { weekday: 2 } },
                { id: "w2", name: "Open Mic", venue: "Test Hall", schedule: { weekday: 5, weeks: [1, 3] } }];
  var mk = function (name, venue, date) { return { id: "x_" + name + date, name: name, venue: venue, schedule: { date: date } }; };
  assert.ok(dedupe.findDuplicate(mk("Pub Trivia", "Temblor Brewing Company", "2026-10-06"), weekly), "Tuesday trivia is the Tuesday regular");
  assert.ok(!dedupe.findDuplicate(mk("Pub Trivia", "Temblor Brewing Company", "2026-10-07"), weekly), "Wednesday is not Tuesday");
  assert.ok(!dedupe.findDuplicate(mk("Pub Trivia", "Another Pub", "2026-10-06"), weekly), "another venue is not the same regular");
  assert.ok(!dedupe.findDuplicate(mk("Karaoke Night", "Temblor Brewing Company", "2026-10-06"), weekly), "a different event the same night is new");
  assert.ok(dedupe.findDuplicate(mk("Open Mic", "Test Hall", "2026-10-02"), weekly), "1st Friday of the month is in the regular's weeks");
  assert.ok(!dedupe.findDuplicate(mk("Open Mic", "Test Hall", "2026-10-09"), weekly), "2nd Friday is not one of its weeks (1st and 3rd)");
});
// ---------------------------------------------------------------- validate-board.js + archive.json + first-friday.json
(function () {
  var fs = require("fs"), os = require("os"), path = require("path"), cp = require("child_process");
  var realBoard = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "data", "board.json"), "utf8"));
  var realFF = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "data", "first-friday.json"), "utf8"));
  var real = Object.assign({}, realBoard, realFF);   // the merged shape the pages see
  function write(dir, name, v) { if (v !== undefined) fs.writeFileSync(path.join(dir, name), typeof v === "string" ? v : JSON.stringify(v)); }
  function run(board, archive, firstFriday) {
    var dir = fs.mkdtempSync(path.join(os.tmpdir(), "bbv-"));
    write(dir, "board.json", board); write(dir, "archive.json", archive); write(dir, "first-friday.json", firstFriday);
    var r = cp.spawnSync(process.execPath, [path.join(__dirname, "validate-board.js"), path.join(dir, "board.json")], { encoding: "utf8" });
    fs.rmSync(dir, { recursive: true, force: true });
    return r;
  }
  var base = JSON.parse(JSON.stringify(real)); delete base.city_events_archive;
  var slim = JSON.parse(JSON.stringify(base)); delete slim.events; delete slim.featured;   // board.json as shipped now
  var good = { id: "x1", name: "Old show", schedule: { date: "2026-01-02" }, lat: 35.37, lng: -119.02 };
  t("validator checks archive.json beside the board and counts it", function () {
    var r = run(base, { city_events_archive: [good] });
    assert.strictEqual(r.status, 0, r.stderr);
    assert(/1 archived/.test(r.stdout), r.stdout);
  });
  t("validator rejects an unsafe link inside archive.json", function () {
    var r = run(base, { city_events_archive: [Object.assign({}, good, { link: "javascript:alert(1)" })] });
    assert.strictEqual(r.status, 1);
    assert(/city_events_archive\[0\]\.link/.test(r.stderr), r.stderr);
  });
  t("validator rejects an archive.json without the array, and broken JSON", function () {
    assert.strictEqual(run(base, { nope: [] }).status, 1);
    assert.strictEqual(run(base, "{broken").status, 1);
  });
  t("an old-style board.json that still carries the archive keeps validating", function () {
    var legacy = Object.assign({}, base, { city_events_archive: [good] });
    var r = run(legacy);
    assert.strictEqual(r.status, 0, r.stderr);
    assert(/1 archived/.test(r.stdout), r.stdout);
  });
  t("both places holding the archive warns instead of silently doubling it", function () {
    var r = run(Object.assign({}, base, { city_events_archive: [good] }), { city_events_archive: [good, Object.assign({}, good, { id: "x2" })] });
    assert.strictEqual(r.status, 0, r.stderr);
    assert(/both board\.json and archive\.json/.test(r.stderr), r.stderr);
    assert(/2 archived/.test(r.stdout), "archive.json wins: " + r.stdout);
  });
  t("first-friday.json beside a slim board.json is validated and counted", function () {
    var r = run(slim, undefined, { featured: realFF.featured, events: realFF.events });
    assert.strictEqual(r.status, 0, r.stderr);
    assert(/1 event\(s\), 8 vendors, 86 spots/.test(r.stdout), r.stdout);
  });
  t("validator rejects bad data inside first-friday.json (coordinates, missing array, broken JSON, dangling featured)", function () {
    var ev = JSON.parse(JSON.stringify(realFF.events));
    ev[0].spots[0].lat = 95;
    var r = run(slim, undefined, { featured: realFF.featured, events: ev });
    assert.strictEqual(r.status, 1);
    assert(/spots\[0\]/.test(r.stderr) && /out of range/.test(r.stderr), r.stderr);
    assert.strictEqual(run(slim, undefined, { featured: "x" }).status, 1, "no events array");
    assert.strictEqual(run(slim, undefined, "{broken").status, 1);
    assert.strictEqual(run(slim, undefined, { featured: "no-such-event", events: realFF.events }).status, 1, "featured must name a real event");
  });
  t("a slim board.json with no First Friday data anywhere fails (the pages need an event)", function () {
    assert.strictEqual(run(slim).status, 1);
  });
  t("an old-style board.json that still carries events + featured keeps validating; both places warns", function () {
    assert.strictEqual(run(base).status, 0);
    var r = run(base, undefined, { featured: realFF.featured, events: realFF.events });
    assert.strictEqual(r.status, 0, r.stderr);
    assert(/both board\.json and first-friday\.json/.test(r.stderr), r.stderr);
  });
  t("the shipped board.json carries neither the archive nor the First Friday block; their files do", function () {
    assert.strictEqual(realBoard.events, undefined);
    assert.strictEqual(realBoard.featured, undefined);
    assert(Array.isArray(realFF.events) && realFF.events.length > 0 && realFF.featured);
    assert.strictEqual(real.city_events_archive, undefined);
    var a = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "data", "archive.json"), "utf8"));
    assert(Array.isArray(a.city_events_archive) && a.city_events_archive.length > 0);
  });
})();

// ---------------------------------------------------------------- lib/api.js shared page helpers
(function () {
  var fs = require("fs"), path = require("path");
  var root = path.join(__dirname, "..");
  function loadApi(search, hostname, store) {
    var key = require.resolve("../lib/api.js");
    delete require.cache[key];
    global.window = {};
    global.location = { search: search, hostname: hostname };
    global.localStorage = { getItem: function (k) { return k in store ? store[k] : null; }, setItem: function (k, v) { store[k] = String(v); } };
    require(key);
    return global.window.bbApi;
  }
  function tb(name, fn) {  // like t(), but always removes the fake browser globals afterwards
    t(name, function () {
      try { fn(); }
      finally { delete global.window; delete global.location; delete global.localStorage; }
    });
  }
  tb("attribution sanitises ?ref= and ?src=, remembers them, and defaults to direct", function () {
    var store = {};
    var a = loadApi("?ref=Bad%20Code!!&src=IG_story", "x.test", store).attribution();
    assert.deepStrictEqual(a, { ref: "badcode", src: "ig_story" });
    assert.strictEqual(store.bb_ref, "badcode");
    assert.deepStrictEqual(loadApi("", "x.test", store).attribution(), { ref: "badcode", src: "ig_story" }, "remembered on the next page");
    assert.deepStrictEqual(loadApi("", "x.test", {}).attribution(), { ref: "direct", src: "direct" });
    assert.strictEqual(loadApi("?ref=" + "a".repeat(80), "x.test", {}).attribution().ref.length, 40);
  });
  tb("apiBase honours ?api= on localhost only", function () {
    var api = loadApi("", "seekerflame.github.io", {});
    assert(/^https:\/\/[^/]+$/.test(api.apiBase()), "owned API, no trailing slash: " + api.apiBase());
    assert.strictEqual(loadApi("?api=http://localhost:8787/", "localhost", {}).apiBase(), "http://localhost:8787");
    assert.strictEqual(loadApi("?api=http://127.0.0.1:9/", "127.0.0.1", {}).apiBase(), "http://127.0.0.1:9");
    assert.strictEqual(loadApi("?api=https://evil.example", "seekerflame.github.io", {}).apiBase(), api.apiBase(), "never honoured off localhost");
    assert.strictEqual(loadApi("?api=http://localhost:8787", "localhost", {}).apiOverridden(), true);
    assert.strictEqual(loadApi("", "localhost", {}).apiOverridden(), false);
  });
  tb("shareRef keeps the first valid share code and refuses malformed ones", function () {
    var store = {};
    assert.strictEqual(loadApi("?ref=Friend-1", "x.test", store).shareRef(), "friend-1");
    assert.strictEqual(loadApi("?ref=someone-else", "x.test", store).shareRef(), "friend-1", "first touch wins");
    assert.strictEqual(loadApi("?ref=ab", "x.test", {}).shareRef(), "", "shorter than 4 is not a share code");
    assert.strictEqual(loadApi("?ref=" + "a".repeat(25), "x.test", {}).shareRef(), "", "longer than 24 is not a share code");
    assert.strictEqual(loadApi("", "x.test", {}).shareRef(), "");
  });
  tb("the contact email in lib/api.js is the one board.json declares", function () {
    var board = JSON.parse(fs.readFileSync(path.join(root, "data", "board.json"), "utf8"));
    assert.strictEqual(loadApi("", "x.test", {}).CONTACT_EMAIL, board.board.contact);
  });
  var pages = [];
  (function walk(dir) {
    fs.readdirSync(dir, { withFileTypes: true }).forEach(function (e) {
      if (e.name === "node_modules" || e.name === ".git" || e.name === "tools") return;
      var p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p); else if (/\.html$/.test(e.name)) pages.push(p);
    });
  })(root);
  t("no page re-implements what lib/api.js provides (these copies used to drift)", function () {
    pages.forEach(function (p) {
      var s = fs.readFileSync(p, "utf8"), n = path.relative(root, p);
      assert(s.indexOf('localStorage.setItem("bb_ref"') === -1, n + " has its own referral sanitiser");
      assert(!/var CONTACT_EMAIL ?= ?"/.test(s), n + " hardcodes CONTACT_EMAIL");
      assert(s.indexOf('get("api")') === -1, n + " has its own ?api= override");
      assert(s.indexOf('bbApi && window.bbApi.API_BASE') === -1, n + " resolves the API base itself");
    });
  });
  t("every page that uses the shared helpers loads lib/api.js before it uses them", function () {
    var used = 0;
    pages.forEach(function (p) {
      var s = fs.readFileSync(p, "utf8"), n = path.relative(root, p);
      var m = /bbApi\.(apiBase|attribution|CONTACT_EMAIL)/.exec(s);
      if (!m) return;
      used++;
      var load = s.indexOf("lib/api.js");
      assert(load !== -1 && load < m.index, n + " uses bbApi." + m[1] + " without loading lib/api.js first");
    });
    assert(used >= 10, "expected the shared helpers to be in use on >= 10 pages, found " + used);
  });
})();

// ---------------------------------------------------------------- lib/board.js (shared board loader)
(function () {
  var fs = require("fs"), path = require("path");
  var root = path.join(__dirname, "..");
  function loadBoardLib(files, calls, scriptSrc) {
    var key = require.resolve("../lib/board.js");
    delete require.cache[key];
    global.window = {};
    global.document = { currentScript: { src: scriptSrc || "https://x.test/site/lib/board.js" } };
    global.fetch = function (url, opt) {
      calls.push({ url: String(url), opt: opt });
      var name = String(url).split("/data/")[1];
      if (!(name in files)) return Promise.resolve({ ok: false, status: 404, json: function () { return Promise.resolve({}); } });
      if (files[name] === "boom") return Promise.reject(new Error("offline"));
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(JSON.parse(JSON.stringify(files[name]))); } });
    };
    require(key);
    return global.window.bbBoard;
  }
  function tbl(name, fn) {   // async test with fake browser globals removed afterwards
    asyncTests.push([name, function () {
      return Promise.resolve().then(fn).then(
        function (v) { cleanup(); return v; }, function (e) { cleanup(); throw e; });
    }]);
    function cleanup() { delete global.window; delete global.document; delete global.fetch; }
  }
  var FF = { featured: "first-friday", events: [{ slug: "first-friday", name: "FF" }] };
  var ARCH = { city_events_archive: [{ id: "a1", name: "old" }] };
  var BOARD = { board: { contact: "a@b.co" }, city_events: [{ id: "c1" }] };

  tbl("bbBoard.load fetches only board.json unless a page asks for more, resolving paths from its own URL", function () {
    var calls = [], b = loadBoardLib({ "board.json": BOARD, "first-friday.json": FF, "archive.json": ARCH }, calls);
    return b.load().then(function (d) {
      assert.deepStrictEqual(calls.map(function (c) { return c.url; }), ["https://x.test/site/data/board.json"]);
      assert.strictEqual(d.events, undefined);
      assert.strictEqual(d.city_events.length, 1);
    });
  });
  tbl("bbBoard.load merges the First Friday and archive files in when asked", function () {
    var calls = [], b = loadBoardLib({ "board.json": BOARD, "first-friday.json": FF, "archive.json": ARCH }, calls);
    return b.load({ firstFriday: true, archive: true }).then(function (d) {
      assert.strictEqual(d.featured, "first-friday");
      assert.strictEqual(d.events[0].name, "FF");
      assert.strictEqual(d.city_events_archive[0].id, "a1");
      assert.strictEqual(calls.length, 3);
    });
  });
  tbl("a missing or failing sibling never breaks the page; a failing board.json does", function () {
    var b = loadBoardLib({ "board.json": BOARD, "first-friday.json": "boom" }, []);
    return b.load({ firstFriday: true, archive: true }).then(function (d) {
      assert.strictEqual(d.events, undefined);
      assert.strictEqual(d.city_events.length, 1);
      var b2 = loadBoardLib({}, []);
      return b2.load().then(function () { throw new Error("should have rejected"); }, function (e) { assert(/board\.json 404/.test(e.message)); });
    });
  });
  tbl("an old-style board.json that still carries events keeps working when the sibling is missing", function () {
    var legacy = Object.assign({}, BOARD, FF);
    var b = loadBoardLib({ "board.json": legacy }, []);
    return b.load({ firstFriday: true }).then(function (d) { assert.strictEqual(d.events[0].slug, "first-friday"); assert.strictEqual(d.featured, "first-friday"); });
  });
  tbl("the cache option reaches every fetch", function () {
    var calls = [], b = loadBoardLib({ "board.json": BOARD, "first-friday.json": FF }, calls);
    return b.load({ firstFriday: true, cache: "no-store" }).then(function () {
      assert(calls.length === 2 && calls.every(function (c) { return c.opt && c.opt.cache === "no-store"; }));
    });
  });
  tbl("bbBoard.split is the inverse of load: the files a merged board is stored as", function () {
    var b = loadBoardLib({ "board.json": BOARD, "first-friday.json": FF, "archive.json": ARCH }, []);
    return b.load({ firstFriday: true, archive: true }).then(function (d) {
      var parts = b.split(d);
      assert.deepStrictEqual(parts.board, BOARD, "archive and First Friday keys are not written back into board.json");
      assert.deepStrictEqual(parts.firstFriday, FF);
      assert.deepStrictEqual(b.split({ board: {}, city_events: [] }).firstFriday, { featured: undefined, events: [] });
    });
  });
  t("pages that read the First Friday block ask for it; pages that do not never download it", function () {
    var pages = [];
    (function walk(dir) {
      fs.readdirSync(dir, { withFileTypes: true }).forEach(function (e) {
        if (e.name === "node_modules" || e.name === ".git" || e.name === "tools") return;
        var p = path.join(dir, e.name);
        if (e.isDirectory()) walk(p); else if (/\.html$/.test(e.name)) pages.push(p);
      });
    })(root);
    var asks = 0;
    pages.forEach(function (p) {
      var s = fs.readFileSync(p, "utf8"), n = path.relative(root, p);
      var reads = /\b(?:d|bd|DATA)\.(?:events|featured)\b/.test(s);
      var asksFF = /bbBoard\.load\(\{[^}]*firstFriday:true/.test(s);
      if (reads) { assert(asksFF, n + " reads the First Friday block without asking bbBoard for it"); asks++; assert(s.indexOf("lib/board.js") !== -1, n + " must load lib/board.js"); }
      else assert(!asksFF, n + " downloads first-friday.json but never reads it");
      assert(!/fetch\([^)]*first-friday\.json/.test(s), n + " fetches first-friday.json by hand instead of through bbBoard");
    });
    assert(asks >= 5, "expected >= 5 pages to use the First Friday block, found " + asks);
  });
})();

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
