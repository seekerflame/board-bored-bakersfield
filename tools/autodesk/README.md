# autodesk: local truth desk for Board Bored events

Takes the human (and Claude) out of event collection. Runs unattended on this Mac via a Hermes cron job
(`~/.hermes/scripts/boardbored_autodesk.sh`, every 6h, `--no-agent`, silent unless something changed).

**Local models only extract. Truth is decided by deterministic code.**

| stage | file | what it does |
|---|---|---|
| fetch | `netfetch.py` | `BoardBoredBot` UA, robots.txt (RFC 9309), 1 req/host/interval, conditional GET, size/type caps |
| voices | `adapters.py`, `llm.py` | structured parsers (Fox/Nile cards + JSON-LD, newsletter bullets) and local Ollama models; each returns candidates with a verbatim quote |
| gate | `truthgate.py` | name/date/venue must be locatable in the quote (date may come from a governing weekday heading); time/cost/link/address are stripped, not guessed; past, non-event and far-future dropped |
| consensus | `truthgate.consensus` | tier **A** needs >=2 independent voice *families* agreeing, no conflicts, nothing stripped; else **B** (one-tap review) |
| publish | `autodesk.py` | `autopublish` off by default; armed only with config flag **and** 2 clean cycles; circuit breaker pauses on collapsed/exploded/failed sources; unknown venue never tier A |
| ingest | `../ingest/sources/autodesk.js` | re-checks the contract (>=2 voices, dated, pinned) and feeds the normal dedupe/geocode/validate pipeline |

```
python3 autodesk.py run            # fetch, extract, gate, propose
python3 autodesk.py review         # tier-B events with evidence quotes
python3 autodesk.py approve --tier-a | ID..   # human sign-off, writes data/autodesk_events.json
python3 autodesk.py reject ID..
python3 autodesk.py measure --source <id> --model <ollama model>   # false-accept rate vs deterministic parse
node ../ingest_events.js --dry-run # merge published events into board.json (validated)
```
State, cache and the append-only `audit.jsonl` live in `~/.boardbored/autodesk/`. Config: copy `config.example.json` there.

`revenue_desk.py` is the money side: live payment-link check, collections aging, prospect fact sheets with drafts whose every
number/name must trace to the fact sheet. It never sends anything.

Tests (offline, no Ollama): `python3 test_autodesk.py && python3 test_revenue_desk.py`.
