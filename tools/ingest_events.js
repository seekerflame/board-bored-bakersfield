#!/usr/bin/env node
/* ingest_events.js — the repeatable, automated event-ingestion pipeline.
 *
 * Replaces the old workflow (a human/agent manually researches events over
 * a multi-session chat, ships them as a long-lived branch, someone hand-
 * merges it later — see the codebase-audit-bzdjqf merge for exactly how
 * that went wrong twice in one week). This script IS the repeatable version
 * of that same research step: point it at a city config, it pulls every
 * enabled source, normalizes, geocodes anything missing coordinates,
 * dedupes against what's already in board.json, and only writes the file
 * back if `validate-board.js` says the result is still valid. Designed to
 * run unattended on a schedule (see .github/workflows/ingest.yml) so there
 * is never again a human-paced research branch to lose track of.
 *
 * Usage: node tools/ingest_events.js [--city data/cities/kern-county.json] [--dry-run]
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const { geocode } = require("./ingest/geocode.js");
const { toCityEvent } = require("./ingest/normalize.js");
const { findDuplicate } = require("./ingest/dedupe.js");
const ticketmaster = require("./ingest/sources/ticketmaster.js");
const meetup = require("./ingest/sources/meetup.js");
const ics = require("./ingest/sources/ics.js");

const SOURCES = { ticketmaster, meetup, ics };

function parseArgs(argv) {
  const out = { city: "data/cities/kern-county.json", dryRun: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--city") out.city = argv[++i];
    else if (argv[i] === "--dry-run") out.dryRun = true;
  }
  return out;
}

async function runSource(name, cfg, cityConfig) {
  const mod = SOURCES[name];
  if (!mod) {
    console.log(`[ingest] unknown source "${name}", skipping`);
    return [];
  }
  if (name === "ticketmaster") {
    return mod.fetchEvents({
      apiKey: process.env.TICKETMASTER_API_KEY,
      city: cityConfig.ticketmasterCity || cityConfig.name,
      stateCode: cityConfig.stateCode,
    });
  }
  if (name === "meetup") {
    return mod.fetchEvents({ accessToken: process.env.MEETUP_ACCESS_TOKEN });
  }
  if (name === "ics") {
    const all = [];
    for (const feed of cfg.feeds || []) {
      const raw = await mod.fetchEvents({ url: feed.url });
      // ICS feeds rarely carry a real street address — the city config pins
      // each feed to the venue's known address/coords so cards still get a
      // usable pin instead of falling through to the geocoder on "null".
      for (const r of raw) {
        r.venue = r.venue || feed.venueName;
        r.address = feed.address || r.address;
        r.lat = feed.lat != null ? feed.lat : r.lat;
        r.lng = feed.lng != null ? feed.lng : r.lng;
        r.source = feed.sourceLabel || r.source;
      }
      all.push(...raw);
    }
    return all;
  }
  return [];
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const repoRoot = path.resolve(__dirname, "..");
  const cityConfig = JSON.parse(fs.readFileSync(path.resolve(repoRoot, args.city), "utf8"));
  const boardPath = path.resolve(repoRoot, cityConfig.boardJsonPath || "data/board.json");
  const board = JSON.parse(fs.readFileSync(boardPath, "utf8"));

  console.log(`[ingest] city=${cityConfig.name} sources=${cityConfig.sources.map((s) => s.type).join(",")}`);

  let rawEvents = [];
  for (const src of cityConfig.sources) {
    const events = await runSource(src.type, src, cityConfig);
    console.log(`[ingest]   ${src.type}: ${events.length} events`);
    rawEvents.push(...events.map((e) => ({ ...e, _sourceType: src.type })));
  }

  const added = [];
  const skippedDupes = [];
  const geocodeFailures = [];
  for (const raw of rawEvents) {
    const candidate = toCityEvent(raw, raw._sourceType);
    const dup = findDuplicate(candidate, [...board.city_events, ...added]);
    if (dup) { skippedDupes.push({ candidate: candidate.name, matchedExisting: dup.id }); continue; }

    if ((candidate.lat == null || candidate.lng == null) && candidate.address) {
      try {
        const geo = await geocode(candidate.address, cityConfig.bbox);
        if (geo) { candidate.lat = geo.lat; candidate.lng = geo.lng; }
        else geocodeFailures.push(candidate.name);
      } catch (e) {
        console.log(`[ingest]   geocode error for "${candidate.name}": ${e.message}`);
        geocodeFailures.push(candidate.name);
      }
    }
    added.push(candidate);
  }

  console.log(`[ingest] new=${added.length} duplicates_skipped=${skippedDupes.length} ungeocoded=${geocodeFailures.length}`);
  if (geocodeFailures.length) console.log(`[ingest]   ungeocoded: ${geocodeFailures.join(", ")}`);

  if (!added.length) {
    console.log("[ingest] nothing new — board.json unchanged");
    return;
  }

  const merged = { ...board, city_events: [...board.city_events, ...added] };
  const tmpPath = boardPath + ".ingest-tmp";
  fs.writeFileSync(tmpPath, JSON.stringify(merged, null, 2) + "\n");

  try {
    execFileSync("node", [path.join(repoRoot, "tools/validate-board.js"), tmpPath], { stdio: "inherit" });
  } catch (e) {
    fs.unlinkSync(tmpPath);
    console.error("[ingest] validation FAILED on the merged result — nothing written, board.json untouched.");
    process.exitCode = 1;
    return;
  }

  if (args.dryRun) {
    console.log(`[ingest] --dry-run: validated OK, not writing (see ${tmpPath})`);
    return;
  }

  fs.renameSync(tmpPath, boardPath);
  console.log(`[ingest] wrote ${added.length} new event(s) to ${boardPath}`);
}

main().catch((e) => { console.error("[ingest] fatal:", e); process.exitCode = 1; });
