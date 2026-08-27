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
