/* autodesk.js — events the local truth desk (tools/autodesk/autodesk.py) has verified.
 *
 * The desk does the hard part in Python (fetch politely, extract with local models, gate every
 * claim against a verbatim source quote, require two independent voices to agree) and writes
 * data/autodesk_events.json. This module only re-checks the minimum contract and hands the
 * events to the shared pipeline (dedupe, geocode-if-needed, validate-board, atomic write).
 * Defence in depth: a malformed or hand-edited file can never inject a pinless / dateless /
 * past event, whatever the desk did.
 */
"use strict";

const fs = require("fs");

const ISO = /^\d{4}-\d{2}-\d{2}$/;
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** @param {Array} list parsed JSON  @param {string} today YYYY-MM-DD */
function parse(list, today) {
  if (!Array.isArray(list)) return [];
  const out = [];
  for (const e of list) {
    if (!e || typeof e !== "object") continue;
    if (!e.name || !e.venue || !ISO.test(e.date || "")) continue;
    if (today && e.date < today) continue; // never ingest an already-past event
    if (!(e.address || (e.lat != null && e.lng != null))) continue; // verified location or nothing
    if (!Array.isArray(e.verified_by) || e.verified_by.length < 2) continue; // two independent voices, always
    out.push({
      name: String(e.name), venue: String(e.venue), address: e.address || null,
      lat: e.lat != null ? Number(e.lat) : null, lng: e.lng != null ? Number(e.lng) : null,
      category: e.category || "Community", when: e.when || e.date, date: e.date,
      start: HHMM.test(e.start || "") ? e.start : null, end: HHMM.test(e.end || "") ? e.end : null,
      area: e.area || null, cost: e.cost != null ? e.cost : null, link: e.link || null,
      desc: "", source: e.source || "autodesk",
    });
  }
  return out;
}

async function fetchEvents({ file, today }) {
  if (!fs.existsSync(file)) {
    console.log(`[ingest]   autodesk: ${file} not found (desk has not published yet) — skipping`);
    return [];
  }
  const t = today || new Date().toISOString().slice(0, 10);
  return parse(JSON.parse(fs.readFileSync(file, "utf8")), t);
}

module.exports = { parse, fetchEvents };
