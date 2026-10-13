/* Board Bored — frontend bridge to the owned API (products/board-bored-api).
 * Resilient by design:
 *   - reads: merge live /api/events ON TOP of baked board.json; API down = baked only.
 *   - writes: POST to our API; if the API is unreachable, fall back to the user's own
 *     email client (mailto) — no third-party form service, no single point of failure.
 *
 * To go live-dynamic: set API_BASE to the deployed Worker/Deno/Cloud Run URL.
 * Empty string = baked-board-only; the site behaves exactly as before. */
window.bbApi = (function () {
  var API_BASE = "https://board-bored-api-240816253301.us-central1.run.app"; // owned Cloud Run API (bored-board-505501)
  var INBOX = "boardquestionmark@gmail.com";
  function base() { return API_BASE.replace(/\/+$/, ""); }

  // Where this page talks to the API: the owned API, or ?api=http://localhost:8787 on localhost ONLY (local testing).
  function apiBase() {
    var b = base();
    try {
      var q = new URLSearchParams(location.search).get("api");
      if (q && /^(localhost|127\.0\.0\.1)$/.test(location.hostname)) b = q.replace(/\/+$/, "");
    } catch (e) {}
    return b;
  }

  // True when ?api= redirected this page to a local API (so links to sibling pages can carry the override along).
  function apiOverridden() { return apiBase() !== base(); }

  // The friend's share code a claim can credit (the API accepts 4-24 chars of a-z0-9_-). FIRST touch wins: an existing bb_ref is never
  // overwritten. NOTE attribution() below shares the same "bb_ref" key but is LAST touch and allows 40 chars (vendor-QR slugs for
  // metrics): two policies on one key. Kept as-is (no behaviour change); a decision for whoever owns referral credit.
  function shareRef() {
    try {
      var rq = (new URLSearchParams(location.search).get("ref") || "").toLowerCase();
      if (/^[a-z0-9_-]{4,24}$/.test(rq) && !localStorage.getItem("bb_ref")) localStorage.setItem("bb_ref", rq);
      return localStorage.getItem("bb_ref") || "";
    } catch (e) { return ""; }
  }

  // Referral attribution: ?ref= / ?src= are sanitised and remembered for the visit. ONE sanitiser for every page: codes handed
  // out must always match codes recorded, or attribution breaks with no error anywhere.
  function attribution() {
    var q = new URLSearchParams(location.search);
    var clean = function (v) { return (v || "").trim().toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, 40); };
    var ref = clean(q.get("ref")), src = clean(q.get("src"));
    try {
      if (ref) localStorage.setItem("bb_ref", ref);
      else ref = localStorage.getItem("bb_ref") || "";
      if (src) localStorage.setItem("bb_src", src);
      else src = localStorage.getItem("bb_src") || "";
    } catch (e) {}
    return { ref: ref || "direct", src: src || "direct" };
  }

  // Live community events (approved). Resolves [] when no API or on any error.
  function getLiveEvents() {
    if (!API_BASE) return Promise.resolve([]);
    return fetch(base() + "/api/events", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { return (j && j.ok && j.events) || []; })
      .catch(function () { return []; });
  }

  // Submit an event. Returns {ok, status, message, via}. Falls back to mailto.
  function submit(payload) {
    if (API_BASE) {
      return fetch(base() + "/api/submit", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      }).then(function (r) { return r.json(); })
        .then(function (j) { j.via = "api"; return j; })
        .catch(function () { return mailtoFallback(payload); });
    }
    return Promise.resolve(mailtoFallback(payload));
  }

  function planner(payload) {
    if (API_BASE) {
      return fetch(base() + "/api/planner", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      }).then(function (r) { return r.json(); })
        .then(function (j) { j.via = "api"; return j; })
        .catch(function () { return mailtoFallback(payload, "Planner partner request"); });
    }
    return Promise.resolve(mailtoFallback(payload, "Planner partner request"));
  }

  // Privacy-first metric hit. Aggregate-only on the server (no PII, no 3rd
  // party) — session is an opaque per-browser id, never an identity. Always
  // fire-and-forget: tracking must never block or break the page.
  function session() {
    try {
      var s = localStorage.getItem("bb_session");
      if (!s) { s = Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem("bb_session", s); }
      return s;
    } catch (e) { return "anon"; }
  }
  function track(type, extra) {
    if (!API_BASE) return;
    var body = Object.assign({ type: type, session: session() }, extra || {});
    try {
      fetch(base() + "/api/track", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        .catch(function () {});
    } catch (e) {}
  }

  // Ultimate fallback: open the visitor's email client, prefilled. Zero dependency.
  function mailtoFallback(payload, subjectPrefix) {
    var subj = (subjectPrefix || "Board Bored event") + ": " + (payload.name || payload.org || "");
    var lines = Object.keys(payload).filter(function (k) { return k[0] !== "_" && payload[k]; })
      .map(function (k) { return k + ": " + payload[k]; });
    var href = "mailto:" + INBOX + "?subject=" + encodeURIComponent(subj) +
      "&body=" + encodeURIComponent(lines.join("\n"));
    try { window.location.href = href; } catch (e) {}
    return { ok: true, via: "mailto", status: "emailed",
      message: "Opening your email app so you can send it to us — hit send and you're on the list." };
  }

  return { API_BASE: API_BASE, CONTACT_EMAIL: INBOX, apiBase: apiBase, apiOverridden: apiOverridden, attribution: attribution, shareRef: shareRef,
    getLiveEvents: getLiveEvents, submit: submit, planner: planner, track: track };
})();
