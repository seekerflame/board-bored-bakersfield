/* normalize.js — turn a source-specific raw event into the board.json
 * city_events schema (see tools/validate-board.js for the shape it enforces).
 * Every source module returns objects already close to this shape; this file
 * is the single place that fills in the fields every source shares (id
 * slugging, emoji-by-category default, the schema.date ISO stamp) so a new
 * source plugin only has to map its own API's fields, not re-derive these.
 */
"use strict";

const CATEGORY_EMOJI = {
  Music: "🎸", Sports: "🏟️", Festival: "🎪", Art: "🎨", Food: "🍽️",
  Comedy: "🎤", Family: "👨‍👩‍👧", Fitness: "🏃", Theater: "🎭", Market: "🧺",
  Community: "🤝",
};

function slugify(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
}

/**
 * @param {object} raw - {name, venue, address, category, when, date (YYYY-MM-DD),
 *   cost, desc, link, source, lat?, lng?, downtown?}
 * @param {string} sourcePrefix - e.g. "tm" for Ticketmaster, "meetup" for Meetup
 */
function toCityEvent(raw, sourcePrefix) {
  if (!raw.name || !raw.venue || !raw.date) {
    throw new Error(`normalize: missing required field on ${JSON.stringify(raw)}`);
  }
  return {
    id: `ce_${sourcePrefix}_${slugify(raw.name)}_${raw.date.replace(/-/g, "")}`,
    name: raw.name,
    emoji: raw.emoji || CATEGORY_EMOJI[raw.category] || "📍",
    category: raw.category || "Community",
    venue: raw.venue,
    address: raw.address || null,
    lat: raw.lat != null ? raw.lat : null,
    lng: raw.lng != null ? raw.lng : null,
    when: raw.when || raw.date,
    cost: raw.cost != null ? raw.cost : null,
    desc: raw.desc || "",
    link: raw.link || null,
    source: raw.source,
    downtown: raw.downtown != null ? raw.downtown : null,
    schedule: { date: raw.date },
  };
}

module.exports = { toCityEvent, slugify, CATEGORY_EMOJI };
