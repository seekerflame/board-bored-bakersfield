/* Board Bored — client-side occurrence + "ON NOW" engine.
 * Faithful port of products/board-bored-api/occurrence.mjs (same tests apply
 * conceptually) so the client and server NEVER disagree on what's on now.
 * Always evaluates in Bakersfield (Pacific) time via Intl, regardless of the
 * viewer's own timezone — a traveling operator or a visitor from elsewhere
 * must see Bakersfield's actual "right now," not their own. */
window.bbOcc = (function () {
  function clockContext(date) {
    const ymd = date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-" + String(date.getDate()).padStart(2, "0");
    return { ymd, minutes: date.getHours() * 60 + date.getMinutes(), weekday: date.getDay() };
  }
  function pacificClock(date) {
    date = date || new Date();
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/Los_Angeles", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false, weekday: "short",
    }).formatToParts(date).reduce(function (a, x) { a[x.type] = x.value; return a; }, {});
    let hour = parseInt(parts.hour, 10); if (hour === 24) hour = 0;
    const wmap = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
    return { ymd: parts.year + "-" + parts.month + "-" + parts.day, minutes: hour * 60 + parseInt(parts.minute, 10), weekday: wmap[parts.weekday] };
  }
  function toMin(hhmm) {
    if (typeof hhmm !== "string" || !/^\d{2}:\d{2}$/.test(hhmm)) return null;
    const p = hhmm.split(":"); return (+p[0]) * 60 + (+p[1]);
  }
  function weekOfMonth(ymd) { return Math.floor((Number(ymd.slice(8, 10)) - 1) / 7) + 1; }
  function dowOf(ymd) { const p = ymd.split("-").map(Number); return new Date(p[0], p[1] - 1, p[2]).getDay(); }
  function yesterdayOf(ymd) {
    const p = ymd.split("-").map(Number);
    const dt = new Date(p[0], p[1] - 1, p[2]); dt.setDate(dt.getDate() - 1);
    return dt.getFullYear() + "-" + String(dt.getMonth() + 1).padStart(2, "0") + "-" + String(dt.getDate()).padStart(2, "0");
  }
  function occursOn(ev, ymd) {
    const s = ev.schedule || {};
    if (s.ongoing) return true;
    if (s.date) return s.date === ymd;
    if (typeof s.weekday === "number") {
      if (dowOf(ymd) !== s.weekday) return false;
      if (Array.isArray(s.weeks) && s.weeks.length) return s.weeks.indexOf(weekOfMonth(ymd)) >= 0;
      return true;
    }
    return false;
  }
  function isOnNow(ev, ctx, graceMin) {
    graceMin = graceMin == null ? 180 : graceMin;
    const s = ev.schedule || {};
    if (s.ongoing) return true;
    if (ev.allday) return occursOn(ev, ctx.ymd);
    const start = toMin(ev.start);
    if (start == null) return false;
    const end = toMin(ev.end);
    const overnight = end != null && end < start;
    const nowM = ctx.minutes;
    if (occursOn(ev, ctx.ymd) && nowM >= start) {
      if (end == null) return nowM <= start + graceMin;
      if (!overnight) return nowM <= end;
      return true;
    }
    if (overnight && occursOn(ev, yesterdayOf(ctx.ymd)) && nowM <= end) return true;
    return false;
  }
  function startsInMin(ev, ctx) {
    if (!occursOn(ev, ctx.ymd)) return null;
    const start = toMin(ev.start);
    return start == null ? null : start - ctx.minutes;
  }
  function expandOccurrences(ev, fromYmd, toYmd, cap) {
    cap = cap || 60;
    const out = [];
    const s = ev.schedule || {};
    function push(ymd) { out.push({ date: ymd, start: ev.start || null, end: ev.end || null, ev: ev }); }
    if (s.date) { if (s.date >= fromYmd && s.date <= toYmd) push(s.date); return out; }
    if (s.ongoing) { push(fromYmd); return out; }
    if (typeof s.weekday === "number") {
      const p = fromYmd.split("-").map(Number);
      const cur = new Date(p[0], p[1] - 1, p[2]);
      for (let i = 0; i < 400 && out.length < cap; i++) {
        const ymd = cur.getFullYear() + "-" + String(cur.getMonth() + 1).padStart(2, "0") + "-" + String(cur.getDate()).padStart(2, "0");
        if (ymd > toYmd) break;
        if (occursOn(ev, ymd)) push(ymd);
        cur.setDate(cur.getDate() + 1);
      }
    }
    return out;
  }
  function nextOccurrence(ev, ymd) {
    const s = ev.schedule || {};
    if (s.ongoing) return ymd;
    if (s.date) return s.date >= ymd ? s.date : null;
    if (typeof s.weekday === "number") {
      const p = ymd.split("-").map(Number);
      const cur = new Date(p[0], p[1] - 1, p[2]);
      for (let i = 0; i < 400; i++) {
        const c = cur.getFullYear() + "-" + String(cur.getMonth() + 1).padStart(2, "0") + "-" + String(cur.getDate()).padStart(2, "0");
        if (occursOn(ev, c)) return c;
        cur.setDate(cur.getDate() + 1);
      }
    }
    return null;
  }
  return {
    clockContext: clockContext, pacificClock: pacificClock, occursOn: occursOn, isOnNow: isOnNow,
    startsInMin: startsInMin, expandOccurrences: expandOccurrences, nextOccurrence: nextOccurrence,
  };
})();
