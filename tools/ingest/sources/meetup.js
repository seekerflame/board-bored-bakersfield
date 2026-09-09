/* meetup.js — Meetup.com event source. NOT YET LIVE — see below.
 *
 * Meetup retired its old simple-API-key REST API in 2019. The current API
 * (https://www.meetup.com/api/schema/) is GraphQL and requires a registered
 * OAuth consumer (client id + secret) plus a completed OAuth flow to get an
 * access token — not just a static key like Ticketmaster. That's a one-time,
 * few-minutes setup a human has to do (register at
 * https://www.meetup.com/api/oauth/list/, authorize it, capture the token),
 * which is why this file is a structured stub rather than a working call:
 * nobody can complete that OAuth handshake on your behalf.
 *
 * Once you have MEETUP_ACCESS_TOKEN (repo secret), fill in `graphqlQuery`
 * below per the schema and this source activates the same way the others do
 * — the orchestrator already calls fetchEvents() uniformly, nothing else
 * needs to change.
 */
"use strict";

function startupCheck(token) {
  if (!token) return { ok: false, reason: "MEETUP_ACCESS_TOKEN not set — needs OAuth setup, see comment at top of this file" };
  return { ok: true };
}

async function fetchEvents({ accessToken /*, lat, lng, radiusMiles */ }) {
  const check = startupCheck(accessToken);
  if (!check.ok) {
    console.log(`[meetup] skipped: ${check.reason}`);
    return [];
  }
  console.log("[meetup] MEETUP_ACCESS_TOKEN is set but the GraphQL query isn't wired up yet — see meetup.js");
  return [];
}

module.exports = { fetchEvents, startupCheck };
