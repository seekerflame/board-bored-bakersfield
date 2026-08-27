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

function nameOverlap(a, b) {
  const wa = new Set(norm(a).split(" ").filter((w) => w.length > 3));
  const wb = new Set(norm(b).split(" ").filter((w) => w.length > 3));
  if (!wa.size || !wb.size) return false;
  let shared = 0;
  for (const w of wa) if (wb.has(w)) shared++;
  return shared / Math.min(wa.size, wb.size) >= 0.5;
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
  return null;
}

module.exports = { findDuplicate, norm, venueOverlap, nameOverlap };
