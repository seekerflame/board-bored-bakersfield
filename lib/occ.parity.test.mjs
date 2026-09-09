// Loads lib/occ.js (a browser global-scope script) into a Node vm sandbox and
// runs the SAME assertions as products/board-bored-api/occurrence.mjs's tests,
// to prove the hand-ported client copy behaves identically to the server.
import vm from "node:vm";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(HERE, "occ.js"), "utf8");
const sandbox = { window: {} };
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const { occursOn, isOnNow, expandOccurrences, pacificClock } = sandbox.window.bbOcc;

let pass = 0, fail = 0;
function ok(cond, label) { cond ? (pass++, console.log("  ✓ " + label)) : (fail++, console.log("  ✗ " + label)); }

const weekly = { schedule: { weekday: 3 }, start: "18:00", end: "20:00" };
ok(occursOn(weekly, "2026-08-26") === true, "occursOn matches weekday (Wed 8/26)");
ok(occursOn(weekly, "2026-08-27") === false, "occursOn rejects wrong weekday");
ok(isOnNow(weekly, { ymd: "2026-08-26", minutes: 19 * 60, weekday: 3 }) === true, "isOnNow true inside window");
ok(isOnNow(weekly, { ymd: "2026-08-26", minutes: 21 * 60, weekday: 3 }) === false, "isOnNow false after end");

const overnight = { schedule: { date: "2026-08-22" }, start: "22:00", end: "02:00" };
ok(isOnNow(overnight, { ymd: "2026-08-22", minutes: 23 * 60, weekday: 6 }) === true, "isOnNow true late-night same day (overnight)");
ok(isOnNow(overnight, { ymd: "2026-08-22", minutes: 60, weekday: 6 }) === false, "overnight event doesn't bleed backward before it starts (the bug fixed server-side)");

const monthly = { schedule: { weekday: 1, weeks: [1, 3] } };
ok(occursOn(monthly, "2026-08-03") === true, "1st Monday matches nth-weekday rule");
ok(occursOn(monthly, "2026-08-10") === false, "2nd Monday does NOT match nth-weekday rule");

const occ = expandOccurrences({ id: "x", schedule: { weekday: 0 }, start: "09:00" }, "2026-08-23", "2026-09-06");
ok(occ.length === 3, "expandOccurrences finds 3 Sundays, got " + occ.length);

const pc = pacificClock(new Date("2026-08-27T02:30:00Z")); // 19:30 PDT Aug 26
ok(pc.ymd === "2026-08-26" && pc.weekday === 3 && pc.minutes === 19 * 60 + 30, "pacificClock resolves a UTC instant to the correct Pacific day/time, got " + JSON.stringify(pc));

console.log(`\n=== ${pass} passed, ${fail} failed ===`);
process.exit(fail ? 1 : 0);
