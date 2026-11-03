# <project> — agent instructions

Owner: **<name>** — decides money, deadlines, consent. Reach him: <DM chat id / voice callout tool>.
Group: «<title>», chat id `<id>`, bot <bot> (registry row in the router's `chats.json`).

The engineering principles, bought parts, drawings, knowledge filing and the skill map are in the managed CLAUDE.md
(design-engineer `PRINCIPLES.md`, loaded in every session). This file holds only what is specific to this project; keep it ≤ 8 KB.

## Client
- Name / address: <e.g. his first name, formal or informal address>; Telegram id: <tg_id> (his rules for all projects: `/clients/<tg_id>/rules.md`)
- Language: <ru / en / pl>
- Jargon tolerance: <non-programmer designer (default) / engineer who wants details / programmer>
- Flaw in a shown design: <tell right away (default) / ask owner first>
- Printer & material (if he prints): <see fdm-print "Ask the client once">

## Project rules and knowledge
- `wiki/client/rules.md` (R-n) — this machine's standing rules; `wiki/index.md` (generated) — the page map;
  `wiki/handover.md` — live state after a reset.
- Single source of truth: the model (`aas/`, `mechanics/cad/params.py`), see `machine-project`.
- Deploy: <host, path, URL>
- Owner defaults: sub-agents get an explicit model override; critics the strongest model. Never read or print `.env` or credentials.

## Current state (≤ 5 lines, rewritten, never appended; history goes to wiki/log.md)
- <what is live> · <what is in work> · <what we wait for>
