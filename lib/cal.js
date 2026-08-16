/* Board Bored — shared calendar helpers (dates + Google Calendar / ICS builders).
 * One source for every page (map, calendar). Events carry structured start/end ("HH:MM", 24h)
 * or allday:true. Times are floating local (America/Los_Angeles) — right for local events. */
(function () {
  function pad(n){return (n<10?"0":"")+n;}
  function todayMid(){var d=new Date();d.setHours(0,0,0,0);return d;}
  function parseISO(s){var p=(s||"").split("-");return p.length===3?new Date(+p[0],+p[1]-1,+p[2]):null;}
  function nextWeekday(from,wd,weeks){
    var d=new Date(from);
    for(var i=0;i<70;i++){
      if(d.getDay()===wd){ if(!weeks)return d;
        var wom=Math.floor((d.getDate()-1)/7)+1; if(weeks.indexOf(wom)>=0)return d; }
      d.setDate(d.getDate()+1);
    }
    return null;
  }
  function effDate(ev){
    var s=ev.schedule||{};
    if(s.ongoing)return todayMid();
    if(s.date)return parseISO(s.date);
    if(typeof s.weekday==="number")return nextWeekday(todayMid(),s.weekday,s.weeks);
    return null;
  }
  function at(date,hhmm){
    var d=new Date(date);
    if(!hhmm){d.setHours(0,0,0,0);return d;}
    var p=hhmm.split(":"); d.setHours(+p[0],+p[1],0,0); return d;
  }
  function stamp(d){return d.getFullYear()+pad(d.getMonth()+1)+pad(d.getDate())+"T"+pad(d.getHours())+pad(d.getMinutes())+"00";}
  function stampDay(d){return d.getFullYear()+pad(d.getMonth()+1)+pad(d.getDate());}
  var DOW=["SU","MO","TU","WE","TH","FR","SA"];
  function rrule(ev){
    var s=ev.schedule||{};
    if(typeof s.weekday!=="number")return null;
    if(s.weeks&&s.weeks.length)return "FREQ=MONTHLY;BYDAY="+s.weeks.map(function(w){return w+DOW[s.weekday];}).join(",");
    return "FREQ=WEEKLY;BYDAY="+DOW[s.weekday];
  }
  // one calendar occurrence (its next date + resolved start/end Date objects)
  function occ(ev,onDate){
    var date=onDate||effDate(ev); if(!date)return null;
    var allDay=(ev.allday||!ev.start);
    var start,end;
    if(allDay){start=at(date,null);end=new Date(start);end.setDate(end.getDate()+1);}
    else{
      start=at(date,ev.start);
      if(ev.end){end=at(date,ev.end); if(end<=start)end.setDate(end.getDate()+1);}
      else{end=new Date(start);end.setHours(end.getHours()+2);}   // default 2h
    }
    return {start:start,end:end,allDay:allDay,rr:rrule(ev)};
  }
  function locOf(ev){return (ev.venue?ev.venue+", ":"")+(ev.address||"");}
  function gcal(ev,onDate){
    var o=occ(ev,onDate); if(!o)return "";
    var dates=o.allDay?stampDay(o.start)+"/"+stampDay(o.end):stamp(o.start)+"/"+stamp(o.end);
    var det=(ev.desc||"")+(ev.link?"\n"+ev.link:"")+"\nvia Board Bored — seekerflame.github.io/board-bored-bakersfield";
    var u="https://calendar.google.com/calendar/render?action=TEMPLATE"+
      "&text="+encodeURIComponent(ev.name||"Event")+
      "&dates="+dates+
      "&details="+encodeURIComponent(det)+
      "&location="+encodeURIComponent(locOf(ev))+
      "&ctz=America/Los_Angeles";
    if(o.rr)u+="&recur="+encodeURIComponent("RRULE:"+o.rr);
    return u;
  }
  function esc(t){return (t||"").replace(/([,;\\])/g,"\\$1").replace(/\r?\n/g,"\\n");}
  function vevent(ev,onDate){
    var o=occ(ev,onDate); if(!o)return "";
    var lines=["BEGIN:VEVENT","UID:"+(ev.id||"ev")+"-"+stampDay(o.start)+"@board-bored",
      o.allDay?"DTSTART;VALUE=DATE:"+stampDay(o.start):"DTSTART:"+stamp(o.start),
      o.allDay?"DTEND;VALUE=DATE:"+stampDay(o.end):"DTEND:"+stamp(o.end),
      "SUMMARY:"+esc(ev.name),
      "LOCATION:"+esc(locOf(ev)),
      "DESCRIPTION:"+esc((ev.desc||"")+(ev.link?"\n"+ev.link:""))];
    if(o.rr)lines.push("RRULE:"+o.rr);
    lines.push("END:VEVENT");
    return lines.join("\r\n");
  }
  function icsHref(events){
    var body=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Board Bored//Bakersfield//EN","CALSCALE:GREGORIAN"]
      .concat(events.map(function(e){return vevent(e);}).filter(Boolean))
      .concat(["END:VCALENDAR"]).join("\r\n");
    return "data:text/calendar;charset=utf-8,"+encodeURIComponent(body);
  }
  // "HH:MM" -> "6:30pm"
  function pretty(hhmm){
    if(!hhmm)return ""; var p=hhmm.split(":"),h=+p[0],m=+p[1];
    var ap=h>=12?"pm":"am"; h=h%12; if(h===0)h=12;
    return h+(m?":"+pad(m):"")+ap;
  }
  window.bbCal={todayMid:todayMid,parseISO:parseISO,nextWeekday:nextWeekday,effDate:effDate,
    occ:occ,gcal:gcal,vevent:vevent,icsHref:icsHref,rrule:rrule,pretty:pretty};
})();
