# Process — how work lands on this repo

_Written 2026-08-27, after the `claude/codebase-audit-bzdjqf` branch sat for
two days while `main` grew a self-serve platform in parallel, and merging
them back together needed three hand-resolved conflicts. This file exists so
that specific failure doesn't happen a second time._

## What actually went wrong

An agent session did real, valuable work (XSS hardening, 59 researched
events) on a branch, in an environment with **no push access**, so it had to
hand the work off as a git bundle to a *different* session to push. That
handoff had no timestamp pressure — nothing forced it to land before `main`
moved again. By the time it got pushed, `main` had grown a real feature
(self-serve event submission) that touched the exact same functions the audit
branch had rewritten. Nobody's fault — but the gap between "work finished"
and "work merged" is exactly the window where two good changes collide.

## The fix: stop having that gap for routine work

**Event data is no longer a research task that produces a branch.** It's a
scheduled job (`.github/workflows/ingest.yml`, `tools/ingest_events.js`) that
runs weekly, pulls from a config-driven list of sources, dedupes against
what's already there, and — only if `tools/validate-board.js` still passes —
commits straight to `main`. There is no branch to go stale, because there is
no human-paced step in the loop at all. This is what AUDIT.md §6.5 already
called out as the "auto-ingest" growth lever; PROCESS.md is that lever
actually wired up, and the reason it had to be wired up now rather than later.

**Rule going forward, for whoever/whatever touches this repo next:**

1. **Routine, repeatable changes** (new events, geocoding, data refreshes) go
   through `tools/ingest_events.js` or a similarly scheduled script — never
   a manually-researched branch that waits on a human to notice and merge it.
2. **Structural changes** (security fixes, new features, schema changes) are
   still branch work — but keep them *short-lived*. Land them the same
   session, or if a handoff is genuinely required (no push access, like this
   one), the handoff doc must say what to do if `main` has moved: check
   `git log origin/main..<branch>` for the base commit, and if `origin/main`
   has moved past it, resolve conflicts by hand before pushing — don't push
   blind and leave the merge for later. (This file exists so that
   instruction has somewhere permanent to live instead of being re-typed
   into every handoff doc.)
3. **Before merging any old branch:** run `git merge --no-commit --no-ff` first
   and read what conflicts, if any — don't assume a branch that validated
   cleanly when it was written still merges cleanly now. `data/board.json`
   merges fine on its own (it's append-only-shaped); `first-friday/index.html`
   and `sw.js` are where two features are most likely to touch the same
   lines, because that's where all the rendering and precache logic lives.

## Why this generalizes ("Kern County is the test")

`tools/ingest_events.js` takes a city config
(`data/cities/kern-county.json` today) — city name, state, bounding box,
which sources are enabled. A second city is a second config file, not a
second codebase. The sources themselves (`tools/ingest/sources/*.js`) don't
know or care what city they're being run for. That's the actual meaning of
"replicable for anyone": someone forking this repo for their own city edits
one JSON file and gets the same weekly auto-ingest, the same dedupe, the same
validate-before-write safety net, for free.

## Sources — status and how to add one

| Source | Status | What it needs |
|---|---|---|
| `ics` (generic iCalendar feeds) | **Live**, tested against a real public feed | Nothing — free, keyless. Add feed URLs to the city config as venues are found to publish one. |
| `ticketmaster` | Built, key-gated, unit-tested against the documented schema | `TICKETMASTER_API_KEY` repo secret — free signup at developer.ticketmaster.com, no approval wait. |
| `meetup` | Scaffolded, not wired to a live call | `MEETUP_ACCESS_TOKEN` — Meetup's current API is OAuth-only (their old plain-API-key REST API was retired in 2019), so this needs a human to register an OAuth consumer and complete the auth flow once. See the comment in `tools/ingest/sources/meetup.js`. |
| Instagram | **Deliberately not built** | See below. |

Every source is missing-credential-safe: if its key/token isn't set, it logs
one line and returns zero events — it never crashes the run or blocks the
other sources. Adding a new source means writing one file with a
`fetchEvents()` function returning the shape `normalize.js` expects, plus one
line in the city config. `tools/ingest_events.js` doesn't change.

## Why there's no Instagram crawler

Scraping arbitrary Instagram accounts for event posts was requested and
deliberately not built: Meta's Terms of Service prohibit automated scraping
of Instagram, and building a crawler explicitly meant to be "replicable for
anyone" would mean handing out a tool whose primary use is a ToS violation at
scale — not something to ship, regardless of how useful the data would be.

It's also not the highest-leverage way to get the same outcome:
- Meta's **official Graph API** works for pages Board Bored (or a
  cooperating venue) actually controls/grants access to — legitimate, but
  it doesn't give blanket access to *other* businesses' accounts without
  their consent, so it can't be the bulk-discovery mechanism anyway.
- Most venues that post events to Instagram *also* post the same event to
  Facebook Events, Eventbrite, Bandsintown, or their own site — which is
  exactly what `ticketmaster` and `ics` already surface. The AUDIT.md
  research pass that added the 59 events proved this out: zero of those 61
  new leads needed Instagram as a source.
- `vendor-signup.html` / `submit-event.html` already let any venue submit
  directly, no scraping needed on either side. Growing the number of venues
  who know that form exists is a marketing problem, not an engineering one
  — see AUDIT.md §6 (per-entity share pages, JSON-LD) for the actual
  highest-leverage growth moves.
