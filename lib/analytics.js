/* Board Bored analytics: PostHog, locked down to match "hard to track you".
 *  - Inert unless lib/analytics-config.js has a real key AND the page is on an allowed host.
 *  - No cookies, no localStorage/sessionStorage (cookieless_mode + memory persistence), no autocapture,
 *    no session recording, no surveys, no person profiles, honours Do Not Track.
 *  - Every outgoing URL is cleaned: the #fragment (where shop and sign-in tokens live) and any query value
 *    outside a small allowlist are dropped before the event leaves the browser.
 *  - Never load this on dashboard / redeem / account pages (tokens, private business views).
 *  - bbTrack(name, props) is safe to call anywhere; it does nothing when analytics is off.
 * Facts the options rely on were checked against posthog.com/docs/libraries/js/config on 2026-10-03. */
(function () {
  var cfg = window.BB_ANALYTICS || {};
  var ALLOWED_QUERY = { ref: 1, src: 1, shop: 1, when: 1, view: 1 };
  var ready = false, queue = [];

  window.bbTrack = function (name, props) {
    if (typeof name !== "string" || !/^[a-z0-9_]{1,40}$/.test(name)) return;
    var safe = {};
    Object.keys(props || {}).forEach(function (k) {
      var v = props[k];
      if (/^[a-z0-9_]{1,30}$/.test(k) && (typeof v === "number" || typeof v === "boolean" || (typeof v === "string" && v.length <= 40 && !/[@#=]/.test(v)))) safe[k] = v;
    });
    if (ready && window.posthog) window.posthog.capture(name, safe); else queue.push([name, safe]);
  };

  // A real project token looks like phc_ + 30-60 url-safe chars. Anything else (empty, placeholder) = stay off.
  var PLACEHOLDER = /^(<.*>|replace-?me|your-.*-here|phc_x+|phc_test.*|phc_demo.*)$/i;
  if (!cfg.key || !/^phc_[A-Za-z0-9]{20,80}$/.test(cfg.key) || PLACEHOLDER.test(cfg.key)) return;
  if (!cfg.hosts || cfg.hosts.indexOf(location.hostname) < 0) return;
  if (/[?&]nt=1\b/.test(location.search)) return; // opt-out link: ?nt=1

  function clean(u) {
    try {
      var x = new URL(u, location.href); x.hash = "";
      Array.from(x.searchParams.keys()).forEach(function (k) { if (!ALLOWED_QUERY[k]) x.searchParams.delete(k); });
      return x.toString();
    } catch (e) { return ""; }
  }

  // Official PostHog loader (from posthog.com/docs/libraries/js, US cloud).
  !function (t, e) { var o, n, p, r; e.__SV || (window.posthog = e, e._i = [], e.init = function (i, s, a) { function g(t, e) { var o = e.split("."); 2 == o.length && (t = t[o[0]], e = o[1]), t[e] = function () { t.push([e].concat(Array.prototype.slice.call(arguments, 0))) } } (p = t.createElement("script")).type = "text/javascript", p.crossOrigin = "anonymous", p.async = !0, p.src = s.api_host.replace(".i.posthog.com", "-assets.i.posthog.com") + "/static/array.js", (r = t.getElementsByTagName("script")[0]).parentNode.insertBefore(p, r); var u = e; for (void 0 !== a ? u = e[a] = [] : a = "posthog", u.people = u.people || [], u.toString = function (t) { var e = "posthog"; return "posthog" !== a && (e += "." + a), t || (e += " (stub)"), e }, u.people.toString = function () { return u.toString(1) + ".people (stub)" }, o = "init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagResult isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug".split(" "), n = 0; n < o.length; n++) g(u, o[n]); e._i.push([i, s, a]) }, e.__SV = 1) }(document, window.posthog || []);

  window.posthog.init(cfg.key, {
    api_host: cfg.host || "https://us.i.posthog.com",
    defaults: "2026-05-30",
    cookieless_mode: "always",       // no cookies or browser storage; PostHog derives a rotating anonymous id server-side
    persistence: "memory",           // belt and braces: nothing persisted client-side either
    person_profiles: "identified_only", // and we never identify anyone
    autocapture: false,              // no automatic click/field capture
    disable_session_recording: true,
    disable_surveys: true,
    respect_dnt: true,
    capture_pageview: true,
    capture_pageleave: false,
    before_send: function (e) {
      if (!e || !e.properties) return e;
      ["$current_url", "$referrer", "$initial_referrer", "$initial_current_url"].forEach(function (k) { if (e.properties[k]) e.properties[k] = clean(e.properties[k]); });
      delete e.properties.$set; delete e.properties.$set_once; // nothing person-shaped ever leaves
      return e;
    },
    loaded: function (ph) {
      ready = true;
      queue.splice(0).forEach(function (q) { ph.capture(q[0], q[1]); });
      try { var r = new URLSearchParams(location.search).get("ref"); if (r && /^[a-z0-9_-]{1,24}$/i.test(r)) ph.capture("ref_visit", { ref: r.toLowerCase() }); } catch (e) {}
    }
  });
})();
