/* ticketmaster.js — Ticketmaster Discovery API event source.
 *
 * Free tier: 5,000 req/day, get a key at https://developer.ticketmaster.com/
 * (self-serve signup, no approval wait). Set TICKETMASTER_API_KEY as a repo
 * secret (Settings -> Secrets and variables -> Actions) to enable this
 * source; the ingester skips it cleanly (one log line, not a crash) if unset.
 *
 * Schema reference: https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/
 * (stable, documented, unchanged for years — the parsing below is written
 * against that spec, unit-tested against a realistic mock in tools/test.js
 * since we don't have a live key to test against here.)
 */
"use strict";

function startupCheck(key) {
  if (!key) return { ok: false, reason: "TICKETMASTER_API_KEY not set" };
  if (/^(<.*>|%[a-z]|replace-me|your-.*-here|sk-replace.*)$/i.test(key)) {
    return { ok: false, reason: "TICKETMASTER_API_KEY looks like a placeholder, not a real key" };
  }
  return { ok: true };
}

/** Parse one Discovery API `_embedded.events[]` entry into our raw-event shape. */
function parseEvent(ev) {
  const venue = (ev._embedded && ev._embedded.venues && ev._embedded.venues[0]) || {};
  const addressParts = [venue.address && venue.address.line1, venue.city && venue.city.name,
    venue.state && venue.state.stateCode, venue.postalCode].filter(Boolean);
  const priceRange = (ev.priceRanges && ev.priceRanges[0]) || null;
  const dt = ev.dates && ev.dates.start;
  const date = dt && (dt.localDate || (dt.dateTime && dt.dateTime.slice(0, 10)));
  if (!date) return null; // TBD-dated events aren't useful on a "what's on" board
  const classification = (ev.classifications && ev.classifications[0]) || {};
  return {
    name: ev.name,
    venue: venue.name || "TBD venue",
    address: addressParts.length ? addressParts.join(", ") : null,
    lat: venue.location ? Number(venue.location.latitude) : null,
    lng: venue.location ? Number(venue.location.longitude) : null,
    category: (classification.segment && classification.segment.name) || "Community",
    when: dt.localTime ? `${date} ${dt.localTime}` : date,
    date,
    cost: priceRange ? `$${priceRange.min}–$${priceRange.max}` : null,
    desc: ev.info || ev.pleaseNote || `${ev.name} at ${venue.name || "TBD"}.`,
    link: ev.url || null,
    source: "ticketmaster",
    downtown: null,
  };
}

/** @returns {Promise<Array>} raw events (see normalize.js for the shape) */
async function fetchEvents({ apiKey, city, stateCode, radiusMiles = 25 }) {
  const check = startupCheck(apiKey);
  if (!check.ok) {
    console.log(`[ticketmaster] skipped: ${check.reason}`);
    return [];
  }
  const params = new URLSearchParams({
    apikey: apiKey, city, stateCode, radius: String(radiusMiles), unit: "miles",
    size: "100", sort: "date,asc",
  });
  const res = await fetch(`https://app.ticketmaster.com/discovery/v2/events.json?${params}`);
  if (!res.ok) {
    console.log(`[ticketmaster] API returned ${res.status}, skipping this run`);
    return [];
  }
  const body = await res.json();
  const events = (body._embedded && body._embedded.events) || [];
  return events.map(parseEvent).filter(Boolean);
}

module.exports = { fetchEvents, parseEvent, startupCheck };
