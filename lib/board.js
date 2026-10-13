/* Board Bored — loads data/board.json, plus the sibling data files a page asks for.
 *
 * board.json holds what every page needs (board meta + city_events). The rest lives next to it so pages that
 * don't need it never download it:
 *   first-friday.json  { featured, events }          the First Friday event: spots, vendors, parking, history
 *   archive.json       { city_events_archive }       past one-time events (timeline page only)
 *
 *   bbBoard.load({ firstFriday: true, archive: true, cache: "no-store" })  ->  one board object, siblings merged in
 *
 * board.json must load or the promise rejects (as before). A missing sibling is skipped, so the page still works,
 * and an old-style board.json that still carries those keys keeps working. Paths resolve from this script's own
 * URL, so it behaves the same from / and /first-friday/. */
window.bbBoard = (function () {
  var DATA = new URL("../data/", document.currentScript.src).href;

  function get(name, cache) { return fetch(DATA + name, cache ? { cache: cache } : undefined); }
  function sibling(name, cache) {
    return get(name, cache).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }

  function load(o) {
    o = o || {};
    return Promise.all([
      get("board.json", o.cache).then(function (r) { if (!r.ok) throw new Error("board.json " + r.status); return r.json(); }),
      o.firstFriday ? sibling("first-friday.json", o.cache) : null,
      o.archive ? sibling("archive.json", o.cache) : null
    ]).then(function (r) {
      var d = r[0];
      if (r[1]) { if (r[1].events) d.events = r[1].events; if (r[1].featured) d.featured = r[1].featured; }
      if (r[2] && r[2].city_events_archive) d.city_events_archive = r[2].city_events_archive;
      return d;
    });
  }

  // The inverse of load(): the files a merged board object is stored as (admin publishes these). archive.json is never
  // rewritten from a page, so the archive key is dropped from the board part even if an old draft still carries it.
  function split(d) {
    var board = {};
    Object.keys(d).forEach(function (k) { if (k !== "featured" && k !== "events" && k !== "city_events_archive") board[k] = d[k]; });
    return { board: board, firstFriday: { featured: d.featured, events: d.events || [] } };
  }

  return { load: load, split: split };
})();
