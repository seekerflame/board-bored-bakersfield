# ● Board Bored

**The free local board.** Events, open shops, and the people behind them — on one map.
No apps. No ads. No cuts. No accounts. Open source (MIT) — **remix it for your city.**

Built for Bakersfield. First board: **First Friday Art Walk** (Downtown Arts District).

---

## How it works (for humans)

- **Visitors** scan ONE QR code (or pick up the paper map) → see tonight's open spots → walk.
- **Vendors** send one email (`boardquestionmark@gmail.com`) or just say "add me" in person → they're on the map in a minute. Free, forever. Cash-only shops welcome — we never touch payments.
- **The organizer** runs the whole board from a phone (`admin.html`).

**The ONE QR trick:** the printed QR encodes the site's own address, and the site always
shows the *current* event. Next month you change the data, not the code — the same
printed QR keeps working forever.

## The pages

| Page | Who it's for | What it does |
|---|---|---|
| `index.html` | everyone | Board Bored home — features the current event |
| `first-friday/` | visitors | Live map + vendor cards + share button |
| `print.html` | paper people | Printable walking list + the ONE QR (self-targeting) |
| `admin.html` | the organizer | Add/edit vendors + event info from a phone, publish live |
| `data/board.json` | the whole site | ONE data file — everything reads from here |

## Run the board from your phone (organizer)

1. **One-time, at home:** create a GitHub *fine-grained token* → only this repo →
   Repository permissions → **Contents: Read and write**. Open `admin.html` on your
   phone → ⚙️ → paste repo (`owner/board-bored`) + token → Save. It stays in your
   phone's browser only.
2. **At the event:** a vendor says "add me" → tap **Add a vendor** → name, pin emoji,
   address (→ Find drops the pin, or tap the map), hours → **Save** → **🚀 Publish live**.
   Everyone's map updates in about a minute.
3. **No token / no signal?** Everything saves as a draft on your phone.
   **⬇ Download JSON** anytime and publish when you're back online — or edit
   `data/board.json` right on github.com from any browser.

## Deploy (pick one, both free)

- **GitHub Pages:** push this folder to a public repo → Settings → Pages → deploy from
  `main` / root. Done. (This also enables phone publishing above.)
- **Netlify Drop:** drag this folder onto [netlify.com/drop](https://app.netlify.com/drop).
  (Phone publishing needs the GitHub path; Drop is view-only hosting.)

Then print `print.html` (the QR builds itself from wherever the site lives) and tape it everywhere.

## Update anything, anytime

Everything is `data/board.json`. Change the event date, add vendors, fix a typo —
from `admin.html`, from github.com in a browser, or in any text editor. No build step.
Retroactive edits are the *point*.

## Remix for your city

Fork it. Change `data/board.json` (city, event, vendors). That's the whole setup.
If you make your town's board, tell us: `boardquestionmark@gmail.com`.

## Credits & licenses

- Map: [Leaflet](https://leafletjs.com) (BSD-2) + [OpenStreetMap](https://www.openstreetmap.org/copyright) tiles (ODbL — attribution kept, be gentle with the free tile server)
- Geocoding: [Nominatim](https://nominatim.org) (usage policy applies)
- QR: [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) (MIT, vendored in `lib/`)
- Everything else: vanilla HTML/CSS/JS, MIT. No frameworks, no build, no tracking.
