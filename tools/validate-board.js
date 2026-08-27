#!/usr/bin/env node
/* validate-board.js — the gate that stops a bad publish from breaking the site.
 *
 * data/board.json is the whole product: one hand/admin-edited file that every
 * page fetches at runtime. A trailing comma, a swapped lat/lng, or a
 * "javascript:" link pasted from a submission takes the live map down with only
 * a "Couldn't load" message. This script parses the file and asserts the shape
 * the pages rely on, so CI (and `npm test`) fail BEFORE GitHub Pages deploys.
 *
 * No dependencies — plain Node. Usage:
 *   node tools/validate-board.js [path/to/board.json]
 * Exit 0 = valid, exit 1 = errors printed to stderr.
 *
 * MULTI-CITY: point it at any city's board.json. Every town's data passes the
 * same gate, so the franchise can't ship a broken board.
 */
"use strict";
var fs = require("fs");
var path = require("path");
var safe = require("../lib/safe.js");

var file = process.argv[2] || path.join(__dirname, "..", "data", "board.json");
var errors = [];
var warnings = [];
function err(msg) { errors.push(msg); }
function warn(msg) { warnings.push(msg); }

// ---- coordinate sanity: keep pins on Earth, and roughly in Kern County ----
// A swapped lat/lng is the classic map bug; this catches it instead of the map
// silently centering in the ocean. The box is generous (all of Kern + margin).
var LAT = [34.7, 36.1], LNG = [-120.3, -118.0];
function checkLatLng(where, lat, lng) {
  if (typeof lat !== "number" || typeof lng !== "number") return; // optional
  if (lat < -90 || lat > 90) err(where + ": lat " + lat + " is out of range");
  if (lng < -180 || lng > 180) err(where + ": lng " + lng + " is out of range");
  if (lat >= -90 && lat <= 90 && (lat < LAT[0] || lat > LAT[1] || lng < LNG[0] || lng > LNG[1])) {
    warn(where + ": (" + lat + "," + lng + ") is outside the expected Kern County area — swapped lat/lng?");
  }
}

// ---- URL safety: the same rule the pages render with ----
// safeUrl() returns "#" for anything it would refuse to put in an href. If a
// non-empty link collapses to "#", it's a dangerous scheme (javascript:, data:)
// and must never reach the live site.
function checkUrl(where, u) {
  if (u == null || u === "") return;
  if (safe.safeUrl(u) === "#") err(where + ": unsafe/blocked URL " + JSON.stringify(u));
}

var ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
function checkDate(where, d) {
  if (!ISO_DATE.test(d)) { err(where + ": date " + JSON.stringify(d) + " is not YYYY-MM-DD"); return; }
  var p = d.split("-").map(Number);
  var dt = new Date(p[0], p[1] - 1, p[2]);
  if (dt.getFullYear() !== p[0] || dt.getMonth() !== p[1] - 1 || dt.getDate() !== p[2]) {
    err(where + ": date " + JSON.stringify(d) + " is not a real calendar day");
  }
}

function uniqueIds(where, arr) {
  var seen = Object.create(null);
  (arr || []).forEach(function (item, i) {
    if (!item || item.id == null) { err(where + "[" + i + "]: missing id"); return; }
    if (seen[item.id]) err(where + ": duplicate id " + JSON.stringify(item.id));
    seen[item.id] = true;
  });
}

// ---------------------------------------------------------------- run
var raw;
try {
  raw = fs.readFileSync(file, "utf8");
} catch (e) {
  console.error("Cannot read " + file + ": " + e.message);
  process.exit(1);
}

var d;
try {
  d = JSON.parse(raw);
} catch (e) {
  console.error("❌ board.json is not valid JSON: " + e.message);
  process.exit(1);
}

// board block
if (!d.board || typeof d.board !== "object") err("missing top-level 'board' object");
else {
  ["city", "name", "contact"].forEach(function (k) {
    if (!d.board[k]) err("board." + k + " is required");
  });
  if (d.board.contact && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(d.board.contact)) {
    err("board.contact " + JSON.stringify(d.board.contact) + " is not an email address");
  }
}

// events
if (!Array.isArray(d.events) || !d.events.length) err("'events' must be a non-empty array");
uniqueIds("events (slug)", (d.events || []).map(function (e) { return { id: e.slug }; }));

(d.events || []).forEach(function (ev, i) {
  var w = "events[" + i + "]";
  if (!ev.slug) err(w + ": missing slug");
  if (!ev.name) err(w + ": missing name");
  if (ev.date) checkDate(w, ev.date);

  if (ev.boundary && Array.isArray(ev.boundary.polygon)) {
    ev.boundary.polygon.forEach(function (pt, j) {
      if (!Array.isArray(pt) || pt.length < 2) err(w + ".boundary.polygon[" + j + "]: expected [lat,lng]");
      else checkLatLng(w + ".boundary.polygon[" + j + "]", pt[0], pt[1]);
    });
  }
  (ev.corridor && ev.corridor.path || []).forEach(function (pt, j) {
    if (Array.isArray(pt)) checkLatLng(w + ".corridor.path[" + j + "]", pt[0], pt[1]);
  });

  uniqueIds(w + ".vendors", ev.vendors);
  (ev.vendors || []).forEach(function (v, j) {
    var vw = w + ".vendors[" + j + "]";
    if (!v.name) err(vw + ": missing name");
    checkLatLng(vw, v.lat, v.lng);
    checkUrl(vw + ".website", v.website);
    if (v.instagram && v.instagram.indexOf("http") === 0) checkUrl(vw + ".instagram", v.instagram);
  });

  uniqueIds(w + ".spots", ev.spots);
  (ev.spots || []).forEach(function (s, j) {
    checkLatLng(w + ".spots[" + j + "]", s.lat, s.lng);
  });

  uniqueIds(w + ".parking", ev.parking);
  (ev.parking || []).forEach(function (p, j) {
    checkLatLng(w + ".parking[" + j + "]", p.lat, p.lng);
  });
});

// featured must point at a real event
if (d.featured && !(d.events || []).some(function (e) { return e.slug === d.featured; })) {
  err("featured " + JSON.stringify(d.featured) + " does not match any event slug");
}

// city events (current + archive)
["city_events", "city_events_archive"].forEach(function (key) {
  if (d[key] == null) return;
  if (!Array.isArray(d[key])) { err("'" + key + "' must be an array"); return; }
  uniqueIds(key, d[key]);
  d[key].forEach(function (e, i) {
    var w = key + "[" + i + "]";
    if (!e.name) err(w + ": missing name");
    var s = e.schedule || {};
    var hasWhen = s.ongoing || s.date || typeof s.weekday === "number";
    if (!hasWhen) err(w + ": schedule needs one of ongoing / date / weekday");
    if (s.date) checkDate(w + ".schedule", s.date);
    checkLatLng(w, e.lat, e.lng);
    checkUrl(w + ".link", e.link);
  });
});

// ---------------------------------------------------------------- report
warnings.forEach(function (m) { console.error("⚠️  " + m); });
if (errors.length) {
  errors.forEach(function (m) { console.error("❌ " + m); });
  console.error("\nboard.json FAILED validation: " + errors.length + " error(s).");
  process.exit(1);
}
var counts = (d.events || []).reduce(function (a, e) {
  a.vendors += (e.vendors || []).length; a.spots += (e.spots || []).length; return a;
}, { vendors: 0, spots: 0 });
console.log("✅ board.json valid — " + (d.events || []).length + " event(s), " +
  counts.vendors + " vendors, " + counts.spots + " spots, " +
  (d.city_events || []).length + " city events, " +
  (d.city_events_archive || []).length + " archived" +
  (warnings.length ? " (" + warnings.length + " warning(s))" : "") + ".");
