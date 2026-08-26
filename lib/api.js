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

  return { API_BASE: API_BASE, getLiveEvents: getLiveEvents, submit: submit, planner: planner };
})();
