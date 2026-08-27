/* geocode.js — free, keyless geocoding via OpenStreetMap Nominatim.
 *
 * Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/)
 * requires: max 1 req/sec, a real identifying User-Agent, and no heavy bulk use.
 * This ingester runs on a weekly schedule over a few dozen venues at most, well
 * inside that policy — but the rate limit + User-Agent below are load-bearing,
 * not decoration. Do not remove them to "speed this up".
 */
"use strict";

const APP_UA = "board-bored-bakersfield-ingest/1.0 (https://github.com/seekerflame/board-bored-bakersfield)";
let lastCall = 0;

async function rateLimit() {
  const elapsed = Date.now() - lastCall;
  const wait = Math.max(0, 1100 - elapsed);
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  lastCall = Date.now();
}

/**
 * Geocode a free-text address/venue query, constrained to a bounding box.
 * Returns {lat, lng, display_name} or null if nothing matched.
 * bbox: {minLat, minLng, maxLat, maxLng} — the city's sanity box (same shape
 * validate-board.js already uses), passed as Nominatim's viewbox + bounded=1
 * so a same-named place in another state/county can't win.
 */
async function geocode(query, bbox) {
  await rateLimit();
  const params = new URLSearchParams({
    q: query,
    format: "jsonv2",
    limit: "1",
  });
  if (bbox) {
    params.set("viewbox", `${bbox.minLng},${bbox.maxLat},${bbox.maxLng},${bbox.minLat}`);
    params.set("bounded", "1");
  }
  const url = `https://nominatim.openstreetmap.org/search?${params.toString()}`;
  const res = await fetch(url, { headers: { "User-Agent": APP_UA } });
  if (!res.ok) throw new Error(`Nominatim ${res.status} for "${query}"`);
  const rows = await res.json();
  if (!rows.length) return null;
  const r = rows[0];
  return { lat: Number(r.lat), lng: Number(r.lon), display_name: r.display_name };
}

module.exports = { geocode, APP_UA };
