# Board Bored — Codebase Audit

_Audit date: 2026-08-25 · Branch: `claude/codebase-audit-bzdjqf`_

Goal set with the maintainer for this pass:

- **Deliver:** a prioritized audit **and** fix the top risks now.
- **Direction:** multi-city franchise — one codebase, per-city `board.json` — with
  **organic growth as the top priority**.
- **Constraints:** must stay runnable from a phone; a backend is acceptable if it earns its place.

---

## 1. What this is

A **zero-backend static PWA** on GitHub Pages: a free, hand-curated map of events and
open shops for Bakersfield, built around the monthly **First Friday Art Walk**. Vanilla
HTML/CSS/JS + Leaflet. No framework, no build step.

| Layer | Files |
|---|---|
| Data (single source of truth) | `data/board.json` — featured event (7 vendors / 86 sidewalk "spots" / corridor + parking geometry), `city_events` (62 upcoming), `city_events_archive` (282 past) |
| Public app | `first-friday/index.html` (Leaflet map, two views), `index.html` (funnel), `first-friday/{calendar,about,fund,submit-event}.html`, `my-clock.html`, `renaissance.html`, `print.html`, `sponsor.html` |
| Forms | vendor-signup / submit-event / feedback / sponsor → one Formspree endpoint, mailto fallback, offline retry queue (`lib/pwa.js`) |
| Admin | `admin.html` — phone CMS that edits `board.json` and publishes via the GitHub Contents API with a PAT in `localStorage` |
| PWA / CI | `sw.js`, `manifest.webmanifest`, per-vendor QR codes, GitHub Actions deploy on push to `main` |

---

## 2. Findings (prioritized)

| # | Severity | Finding | Status |
|---|---|---|---|
| F1 | **High** | **Stored/DOM XSS** — `board.json` fields (vendor/event `name`, `desc`, `note`) were concatenated into `innerHTML`, and event `link` / vendor `website` went straight into `href`. A `javascript:` link runs on tap; markup in a submitted field runs script. | **Fixed** |
| F2 | **High** | **Reflected XSS** — `vendor-signup.html` injected the URL `?ref=` param into `innerHTML` (`vendor-signup.html:195`). A crafted share link executes script with no maintainer involvement. | **Fixed** |
| F3 | **Med-High** | **Admin token theft surface** — the publish PAT lives in `localStorage`, and `admin.html` rendered vendor/spot data via `innerHTML`. Any injected markup there runs where the repo-write token lives. | **Partially fixed** (render escaped; token model unchanged — see §4) |
| F4 | **Medium** | **No validation / no tests / no build.** The whole site rides on one 4,800-line JSON; a malformed publish silently 500s the map. `pwa.js` claimed "unit-tested in Node" but no tests existed and it couldn't even be required in Node. | **Fixed** |
| F5 | **Medium** | **CDN single point of failure** — Leaflet loaded from unpkg with no Subresource Integrity; a tampered/altered asset would execute. | **Fixed (SRI)**; self-host recommended (see §4) |
| F6 | **Low-Med** | **Silent staleness** — everything is date-driven; when data ages out the UI degrades with no operator signal. | Recommended (see §4) |
| F7 | **Low** | **License mismatch** — `README.md` says "not open source, all rights reserved" while a permissive `LICENSE` file ships in the repo. Pick one. | Recommended |
| F8 | **Low** | **Formspree free tier** — one form ID for all four forms; free tier caps submissions/month and has no spam/honeypot field. A growth bottleneck. | Recommended (see §5) |

---

## 3. What was fixed in this pass

All changes are on `claude/codebase-audit-bzdjqf`, verified with `npm test` (11 unit tests
+ `board.json` validation, all green) and `node --check` on every script.

### Security (F1, F2, F3)
- **New `lib/safe.js`** — one shared sanitizer for the whole franchise:
  - `esc()` HTML-escapes text before it enters `innerHTML`.
  - `safeUrl()` allow-lists schemes (`http(s):`, `mailto:`, `tel:`, `geo:`, site-relative),
    upgrades bare hostnames to `https`, and collapses `javascript:` / `data:` / `vbscript:` /
    `file:` (and whitespace-smuggled variants like `java\tscript:`) to `#`.
- **Applied everywhere board data is rendered:** `first-friday/index.html` (popups + event,
  vendor, and spot cards), `first-friday/calendar.html`, `my-clock.html`, and `admin.html`
  (vendor/spot/parking rows). Every data-derived `href` now runs through `safeUrl()`.
- **Reflected `?ref=` sink escaped** in `vendor-signup.html`.
- A repo-wide grep confirms no remaining `innerHTML`/`href` built from a raw data field.

### Integrity & tests (F4)
- **`tools/validate-board.js`** — dependency-free gate: valid JSON, required keys, real
  calendar dates, unique ids, lat/lng in range (+ a Kern-County sanity box that flags a
  swapped lat/lng), and every URL re-checked with the same `safeUrl()` the pages use.
  Point it at any city's file — the franchise can't ship a broken board.
- **`tools/test.js`** — 11 unit tests for `lib/safe.js` (the XSS defence) and the offline
  queue (offline→keep, online→send, poison-payload→drop after `MAX_TRIES`).
- **`lib/pwa.js`** made safely `require()`-able in Node (browser bootstrap guarded behind an
  `IN_BROWSER` check — identical behavior in a browser), making its "unit-tested in Node"
  comment true.
- **`package.json`** with `npm test` / `npm run validate`.
- **CI gate** (`.github/workflows/pages.yml`): a new `validate` job runs on every push, PR,
  and dispatch; **`deploy` now `needs: validate`**, so a bad `board.json` fails *before*
  Pages publishes. Concurrency is now per-ref so a PR check can't cancel a live deploy.

### Reliability (F5)
- **SRI + `crossorigin`** on the Leaflet CSS/JS tags in `first-friday/index.html` and
  `admin.html`, with hashes verified against the real 1.9.4 bytes pulled from npm
  (`sha256-p4NxAoJBhIIN…` / `sha256-20nQCchB9co0…`).
- **`sw.js` bumped `bb-v8` → `bb-v9`** and precaches `lib/safe.js` + `my-clock.html` so
  returning visitors get the hardened code offline.

---

## 4. Recommended next — security & reliability

1. **Self-host Leaflet (finish F5).** SRI stops tampering but unpkg is still an availability
   SPOF and blocks true offline. Vendor `leaflet.js` + `leaflet.css` + `images/` into
   `lib/vendor/leaflet/` (exact 1.9.4 bytes are available via `npm pack leaflet@1.9.4`),
   point the two pages at local paths, and add them to the SW precache. Removes the last
   third-party runtime dependency — the franchise-correct move. (Deferred here only because
   it needs a real browser to test the source swap.)
2. **Move publishing off the in-browser PAT (F3).** The token is fine-grained + Contents-only
   + single-repo, so blast radius is small, but a plaintext repo-write token in a phone
   browser is the biggest structural risk. Two phone-friendly options:
   - **GitHub Actions `workflow_dispatch`**: admin page calls a tiny dispatch endpoint; the
     workflow writes `board.json`. Token never touches the device.
   - A **minimal serverless function** (Cloudflare Worker / Netlify Function) that holds the
     token server-side and accepts an authenticated board update. This is also the natural
     home for form intake and moderation as you grow (§5).
3. **Staleness signal (F6).** `board.json` already carries `board.events_updated`. Show a
   quiet "updated N days ago" note, and when the featured event date is in the past, surface
   an operator-visible banner instead of silently showing a stale night.
4. **Add a Content-Security-Policy** (`<meta http-equiv="Content-Security-Policy">`) once
   Leaflet is self-hosted: restrict `script-src` to `'self'`, `img-src` to `'self'` + the
   OSM tile hosts, `connect-src` to Formspree/Nominatim. Defense-in-depth behind the escaping.
5. **Resolve the license (F7)** so contributors/partners know the terms.

---

## 5. Multi-city franchise plan

The code is already close: everything reads one `data/board.json`, and the validator now
guards its shape. To go multi-city without forking:

1. **Per-city data, one codebase.** `data/<city>/board.json` (or a branch/repo per city). The
   validator already accepts a path argument — run it per city in CI.
2. **Extract the ~3 Bakersfield-specific constants** currently inline in
   `first-friday/index.html` (map center `[35.3757,-119.0205]`, the Kern box in the validator,
   copy strings) into the `board.board` block so a new city is pure data.
3. **City picker / routing.** `?city=` or path-based; the funnel (`index.html`) becomes a
   city selector when more than one exists.
4. **Shared shell, per-city content.** The HTML/JS/CSS is the franchise product; each city
   ships only a `board.json` + share image + QR set. `tools/` can generate the QR codes and
   share cards per city (the generators already exist in `lib/qrcode.js` / the `qr/` tooling).

**Added 2026-09-10 — the other two axes are already built, only "per city" is new work:**
"Per art district" is already the `area` field every event carries (`downtown` / `citywide` /
`southwest`, etc.) — it drives the legend and can filter today without any schema change.
"Per function" (concert vs. market vs. class vs. fitness) is already `category`. Any business
signing up through `submit-event.html` already picks both. The only genuinely missing axis
of the four the roadmap asks for ("per city, per art district, per function-event") is city —
everything else is a filter on data that already exists, not new infrastructure.

### 5b. "Any business, anywhere" — the account layer this actually needs

Today, self-serve is real but stateless: a business fills out `submit-event.html` once,
it goes to the review queue, it goes live — and that's the whole relationship. There's no
"my listings" a business can come back and manage, which is the actual blocker on "let any
business sign up and be part of the community" at scale (one-off submission works; ongoing
membership doesn't exist yet). Scoped, not started:

1. **Magic-link auth, not passwords.** This audience (small local businesses, once) doesn't
   want an account system. Reuse the pattern this repo already has twice — the trusted-planner
   HMAC token (`issuePlannerToken` in `products/board-bored-api/core.mjs`) and the signed
   check-in QR token — for a `business_token = businessId + "." + HMAC(businessId, ADMIN_SECRET)`
   emailed as a link. No password DB, no session store, consistent with "stateless where
   possible" already threaded through this whole backend.
2. **One new field, not a new table.** Add `businessId` to the event/deal schema
   (`schema.mjs`). A business's "dashboard" is just `GET /api/events?businessId=X` filtered
   client-side — reuses `listAll()`, no new storage shape.
3. **City + district + function are already the filters a business picks at signup** (see 5a)
   — the account layer doesn't add new categorization, it just lets the SAME categorized
   listing be edited/renewed instead of resubmitted from scratch.
4. **Renewal, not re-submission**, is the actual value unlock: a recurring business (a bar
   with a weekly night) re-upping their listing without filling out the form again, and
   seeing their own check-in / deal-redemption counts (the `bb_checkins` collection already
   exists — nothing there is being surfaced to the business that earned them).

This is real design work, not a config flag — flagged here so it's ready to scope precisely
next time someone's actually building it, instead of getting re-derived from scratch.

---

## 6. Organic-growth plan (top priority)

Growth here is **share-loop + local SEO**, both of which the static model supports well:

1. **Per-entity share pages for real link previews.** Today every share points at the same
   `share.png`. Give each event/vendor an OG image + title/description so a shared link shows
   *that* event. Static approach: a build step that stamps a `<meta og:*>` page per slug
   (`e/<id>.html` → redirects into the map with `?highlight=`). This is the single highest-
   leverage growth change — every share becomes a rich, clickable card.
2. **Structured data for search.** Emit `schema.org/Event` JSON-LD per event so Google shows
   them in the Events rich result and Maps. For a local "what's on in Bakersfield" board this
   is where most organic discovery will come from.
3. **A real sitemap + per-day / per-category landing URLs** (`/tonight`, `/this-weekend`,
   `/<category>`) that render server-static from `board.json` at deploy time — each is an
   indexable page targeting a real local query.
4. **Make the share loop measurable.** The `?ref=`/`?src=` tracking already exists (now
   XSS-safe); wire a privacy-light, first-party counter (or the leaderboard that
   `data/leaderboard.json` is already stubbed for) so you can see which vendors/walkers drive
   signups and lean into them.
5. **Auto-ingest with the validator as the safety net.** You already back-populate from _The
   Bakersfield Guy_. A scripted ingest (issue → `city_events`) multiplies coverage; the new
   validator + escaping mean ingested third-party text can't break or attack the site.

**Recommended first three for growth:** per-entity OG pages (#1), Event JSON-LD (#2), and the
ingest pipeline (#5) — each compounds, and all three stay fully static + free.

---

## 7. How to run the checks

```bash
npm test          # 11 unit tests + board.json validation
npm run validate  # validate the live board.json only
node tools/validate-board.js data/<city>/board.json   # any city
```

CI runs the same on every push/PR and blocks deploy on failure.
