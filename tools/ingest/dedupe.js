/* dedupe.js — decide whether an incoming (already-normalized) event is a
 * duplicate of something already in city_events (from a prior ingest run,
 * a different source covering the same event, or hand-curated data).
 *
 * Two-tier match, cheapest/most-certain first:
 *   1. Exact id collision (two runs of the same source produced the same slug).
 *   2. Fuzzy: same calendar date + same normalized venue name + name overlap.
 *      Different sources spell "Fox Theater" / "Historic Bakersfield Fox
 *      Theater" / "The Historic Bakersfield Fox Theater" differently, so venue
 *      match is substring-based after normalization, not exact.
 */
"use strict";

function norm(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/^the\s+/, "")
    .replace(/[^a-z0-9\s]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function venueOverlap(a, b) {
  const na = norm(a), nb = norm(b);
  if (!na || !nb) return false;
  return na.includes(nb) || nb.includes(na);
}

function nameOverlap(a, b, minShare = 0.5) {
  const wa = new Set(norm(a).split(" ").filter((w) => w.length > 3));
  const wb = new Set(norm(b).split(" ").filter((w) => w.length > 3));
  if (!wa.size || !wb.size) return false;
  let shared = 0;
  for (const w of wa) if (wb.has(w)) shared++;
  return shared / Math.min(wa.size, wb.size) >= minShare;
}

// A dated event that is just one occurrence of an existing weekly regular (same venue, same weekday, and when the
// regular only runs certain weeks of the month, one of those weeks, and clearly the same name) is not a new event.
function occursOnRecurring(e, date) {
  const s = e.schedule || {};
  if (typeof s.weekday !== "number" || !date) return false;
  const p = String(date).split("-").map(Number);
  if (p.length !== 3 || p.some((n) => !Number.isFinite(n))) return false;
  const d = new Date(p[0], p[1] - 1, p[2]);
  if (d.getDay() !== s.weekday) return false;
  if (Array.isArray(s.weeks) && s.weeks.length) return s.weeks.indexOf(Math.floor((p[2] - 1) / 7) + 1) >= 0;
  return true;
}

/** Returns the matching existing event, or null if `candidate` is new. */
function findDuplicate(candidate, existing) {
  for (const e of existing) {
    if (e.id === candidate.id) return e;
  }
  for (const e of existing) {
    if (e.schedule && e.schedule.date === candidate.schedule.date &&
        venueOverlap(e.venue, candidate.venue) &&
        nameOverlap(e.name, candidate.name)) {
      return e;
    }
  }
  for (const e of existing) {
    if (occursOnRecurring(e, candidate.schedule && candidate.schedule.date) &&
        venueOverlap(e.venue, candidate.venue) &&
        nameOverlap(e.name, candidate.name, 0.67)) {
      return e;
    }
  }
  return null;
}

module.exports = { findDuplicate, norm, venueOverlap, nameOverlap };
