/* ics.js — generic iCalendar (.ics) feed source. Free, keyless, works with
 * whatever venue happens to publish one (WordPress "The Events Calendar",
 * Squarespace `?format=ical`, Google Calendar public exports, etc.).
 *
 * A minimal parser on purpose — this repo ships zero npm dependencies
 * (AUDIT.md: "no framework, no build step") and RFC 5545 is simple enough
 * for the handful of fields a "what's on" board needs (SUMMARY, DTSTART,
 * LOCATION, DESCRIPTION, URL). It does NOT handle recurrence rules (RRULE)
 * or multi-day folding beyond basic line unfolding — venues whose calendars
 * lean on those can still be covered by hand-entering the recurring pattern
 * once (as the existing "Every Tuesday" trivia-night entries already do).
 */
"use strict";

function unfold(text) {
  // RFC 5545: a line starting with a space/tab continues the previous line.
  return text.replace(/\r\n/g, "\n").replace(/\n[ \t]/g, "");
}

function parseICSDate(v) {
  // "20260904T200000Z" or "20260904T200000" or "20260904" (all-day)
  const m = /^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2}))?/.exec(v);
  if (!m) return null;
  const [, y, mo, d, h, mi] = m;
  const date = `${y}-${mo}-${d}`;
  const time = h ? `${h}:${mi}` : null;
  return { date, time };
}

function unescapeICS(s) {
  return String(s || "").replace(/\\n/gi, " ").replace(/\\,/g, ",").replace(/\;/g, ";").replace(/\\\\/g, "\\");
}

/** @param {string} icsText raw .ics file contents
 *  @returns {Array<{name,venue,address,when,date,desc,link}>} */
function parseICS(icsText) {
  const lines = unfold(icsText).split("\n");
  const out = [];
  let cur = null;
  for (const raw of lines) {
    const line = raw.trim();
    if (line === "BEGIN:VEVENT") { cur = {}; continue; }
    if (line === "END:VEVENT") { if (cur) out.push(cur); cur = null; continue; }
    if (!cur) continue;
    const idx = line.indexOf(":");
    if (idx < 0) continue;
    let key = line.slice(0, idx);
    const value = line.slice(idx + 1);
    key = key.split(";")[0].toUpperCase(); // strip params like ;TZID=...
    if (key === "SUMMARY") cur.name = unescapeICS(value);
    else if (key === "LOCATION") cur.venue = unescapeICS(value);
    else if (key === "DESCRIPTION") cur.desc = unescapeICS(value);
    else if (key === "URL") cur.link = value.trim();
    else if (key === "DTSTART") cur._dt = parseICSDate(value);
  }
  return out
    .filter((e) => e.name && e._dt)
    .map((e) => ({
      name: e.name,
      venue: e.venue || null,
      address: e.venue || null, // callers should override with a known venue address when they have one
      when: e._dt.time ? `${e._dt.date} ${e._dt.time}` : e._dt.date,
      date: e._dt.date,
      desc: e.desc || "",
      link: e.link || null,
      source: "ics",
      category: "Community",
      downtown: null,
    }));
}

/** @param {{url: string}} feed */
async function fetchEvents({ url }) {
  const res = await fetch(url);
  if (!res.ok) {
    console.log(`[ics] ${url} returned ${res.status}, skipping`);
    return [];
  }
  return parseICS(await res.text());
}

module.exports = { fetchEvents, parseICS };
