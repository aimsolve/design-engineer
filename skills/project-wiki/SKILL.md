---
name: project-wiki
description: How to write a project's wiki/ — use when filing what the client said (a rule, a decision, a fact, a question, a complaint), writing or rewriting a topic page, adding a decision row, writing handover.md before a reset, running the nightly maintenance pass, migrating an oversized wiki, fixing a `check.py --kb` FAIL, or bootstrapping the wiki of a new or existing chat ("remember this", "write it down", "this is a rule", "always do it this way"). Layers (framework / client / project / topic pages / register / raw / log), the page template (CURRENT · Decisions · In progress · History · Links), the one-line decision register, size limits, the generated index.
---

# Project wiki

The wiki is the project's memory: chat scrolls away and contexts reset, the files don't. It holds **knowledge, not a
log**: every page says what is true NOW. `PRINCIPLES.md` (always loaded) has the triage table and the limits; this
skill has the formats and the procedures.

## Layers: write once, in the widest layer where it is true

| layer | where | what | limit |
|---|---|---|---|
| L0 framework | design-engineer `PRINCIPLES.md`, skills | true for any machine and client | owner merges what projects send (`tg propose` on the hosted platform) |
| L1 client | `/clients/<tg_id>/rules.md` (mounted read-write in every project of that client) | how this person works, in any project: rows `C<n> · date · rule · source project + TG #` | ≤ 6 KB |
| L2 project rules | `wiki/client/rules.md` | true for this whole machine only | ≤ 40 lines / 6 KB |
| L2′ project state | `CLAUDE.md` «Current state», `wiki/handover.md`, `tasks.json` | what is happening now, overwritten | state ≤ 5 lines, handover ≤ 8 KB |
| L3 topic pages | `wiki/<subsystem>/*.md`, `wiki/topics/*.md` | one node or topic: CURRENT + its decisions | ≤ 12 KB, ≤ 150 lines, row ≤ 300 chars |
| L3′ register | `wiki/client/decisions.md` (→ `wiki/client/decisions/YYYY-MM.md` past 40 KB) | one line per D, links to the topic page | row ≤ 200 chars |
| L4 raw | `raw/` at the project root (outside `wiki/`), `.engineer/inbox.jsonl` | chat exports, transcripts, job logs, critic dumps; never edited | — |
| L5 log | `wiki/log.md` | append-only chronology; never read whole, `grep`/`tail` it | — |

Facts and numbers live in the model (params, parts catalogue, CAD, firmware), never in a page: a page names the field
(`DIE_CLEAR`, `parts: 03.01.07-106`) and the model wins. The wider layer gets the text; narrower layers only link to it.
An R row that repeats a C row or a principle is deleted and replaced by a link.

## Page template (every page except index.md and log.md)

```markdown
---
title: Accumulator
summary: one line a reader of the index needs — what this page answers
updated: 2026-10-16
status: current | in-progress | superseded
---
# Accumulator

## CURRENT
What it is now, rewritten in place on every change. Fields named, choices with their WHY (D anchor).

## Decisions
- <a id="d323"></a>**D323** · 15.10 · hold-down strip on the press-rod line, glued at his cross · «Where the cross is…» TG #<id>, #<id>

## In progress
- 2026-10-16 agent: what is being changed now (moves up into CURRENT once it is in the model).

## History
- v3 (2026-10-02): side clamp — died: interferes with the rod (D301).

## Links
- [cabinet](cabinet.md) · [register](../client/decisions.md) · model `ACCUM["ski_split"]`
```

- **Rewrite CURRENT, never append a new state below the old one.** The old CURRENT becomes one line in History with the reason it died.
- English for structure; client quotes stay in the original words, in «guillemets», with `TG #<id>`. A quote is a few words, not the whole message: the full text is in `inbox.jsonl`.
- Over 12 KB or 150 lines → split into sub-pages (`machine/accum.md` → `machine/accum/strip.md`), the parent keeps CURRENT and links.
- Every page links out (at least to its parent or the register) and is linked from somewhere other than the index.

## Decision register (`wiki/client/decisions.md`)

One line per decision, ≤ 200 chars, newest last:

```
| D323 | 15.10 | hold-down strip on the press-rod line | [accum](../machine/accum.md#d323) | TG #<id> |
```

The detail (quotes, reasoning, implementation) lives in the topic page's Decisions section at that anchor. A superseded
decision collapses to `| D140 | 05.10 | superseded by D188 | [press](../machine/press.md#d188) | TG #<id> |`. Past 40 KB,
move whole months to `wiki/client/decisions/YYYY-MM.md`; `decisions.md` then lists the month files.

## Other files

- `wiki/client/rules.md`: `| R<n> | rule in machine terms | «his words» | scope | overrides | TG # |`, ≤ 40 lines. A row that is true for the client in any project moves to `/clients/<id>/rules.md` as a C row; R keeps only a link.
- `wiki/client/open_questions.md`: asked and not answered (as sent, why it matters, chase by, TG #). Answered → a D row, the Q row keeps a link.
- `wiki/client/feedback.md`: mood and frustration, one short entry: date, quote, cause, and **it ends with the rule, gate or fix it produced** (`→ R12`, `→ C4`, `→ gate ack`, `→ fix stage1_drawings.py`). «I have been waiting for a long time» ends in an ETA/promise fix, not in more wiki.
- `wiki/findings/*.md`: sim and critic results with a trust verdict; built on old geometry → `superseded`.
- `wiki/gotchas.md`: traps by tool. A trap that is not project-specific also goes to `tg propose` on the hosted platform (locally: `wiki/log.md`).
- `wiki/handover.md`: overwritten before every reset, ≤ 8 KB: backlog (never drop a row), waits, promises, running jobs, next steps. No rules and no history: those have their own homes.
- `CLAUDE.md` «Current state»: ≤ 5 lines, rewritten at handover / nightly maintenance only (each edit re-sends CLAUDE.md into the context), pointing to handover. No dated bullets.
- `wiki/index.md`: **generated** by `check.py` from each page's frontmatter (or its first heading + first line). Do not edit it; fix the page's `title` / `summary` instead.

## Log line

```
## [2026-10-16 08:38] ingest | TG #<id> | pages: rules R72, machine/drawings; tg propose
```

Kinds: `ingest`, `decision`, `maintenance`, `migration`, `handover`. One line per event; the log is the only place a chronology lives.

## Ingest: every client message, handover, roast, sim result

Triage by `PRINCIPLES.md` §5, widest layer first. For a node decision: change the model → rewrite the node page's CURRENT
+ add its D row there → one register line → log line. A new rule: C row or R row (mark the D rows it overrides). Our
mistake: the rule AND the fix of the generator or gate. Then `check.py --kb` and fix what it FAILs.

## Maintenance pass (nightly turn without the client; also on retire)

1. `python3 ${DE_ROOT:-/opt/design-engineer}/skills/machine-project/scripts/check.py . --kb` — fix every FAIL.
2. Each WARN «page older than the latest decision»: rewrite that page's CURRENT from the model params (read `params.py` / the parts catalogue, not the old page text).
3. Collapse superseded register and R rows to one short line; merge duplicate pages.
4. Rules true for this client in any project → `/clients/<id>/rules.md` (C row with source project + TG #), the R row becomes a link. Framework lessons → `tg propose "…" --tg <msg id>` in Russian (kept by the platform, sent to the owner).
5. Every feedback entry ends in a rule, gate or fix; write the missing one.
6. `CLAUDE.md`: «Current state» ≤ 5 lines; remove everything `PRINCIPLES.md` now covers and anything that contradicts the stack (listener / `bot.py wait` instructions, voice-callout tools, desktop leftovers). Keep only this project: client profile, chat, deploy URL, project principles.
7. `wiki/handover.md` ≤ 8 KB: backlog and waits only.
8. Run `check.py --kb` again; one `maintenance` log line with what changed. Never message the client from this turn.

## One-time migration of an oversized wiki

For a wiki that grew as a log (e.g. a 190 KB `decisions.md`, a 30 KB `CLAUDE.md`):

1. Read `wiki/` and the model; list the machine's real nodes today (params / assembly), not the old page set.
2. Create the missing topic pages (template above), one per node the decisions talk about; write CURRENT from the model.
3. Split `decisions.md`: for each D row, the detail (quote, reasoning) goes into its topic page's Decisions section at `<a id="dN">`; the register keeps one ≤ 200-char line linking there. Month files past 40 KB.
4. Superseded rows collapse; the R rows true for the client everywhere move to `/clients/<id>/rules.md`.
5. Chat exports, `*.txt`, transcripts, `.obsidian/` → `raw/` (`mv`); fix links.
6. Delete stale schema copies (`wiki/WIKI.md`, `wiki/owner_rules.md` and the like): the schema is this skill, the rules are L0–L2. Move any still-true rule from them to its layer first.
7. `CLAUDE.md` «Current state» changelog → `wiki/log.md` (dated lines), state → 5 lines; `handover.md` → ≤ 8 KB.
8. `check.py --kb` until no FAIL. One `migration` log line. Do it in packages of ≤ 25 min (a turn dies at 45).

## Bootstrap a wiki

**New empty chat.** The platform scaffolds the project (`machine-project`) and copies [templates/](templates/). After the
client describes the project: register rows D1… for what he said, open questions for the unknowns, topic pages for
the nodes he named. Ask the first question only after the wiki exists — one question, with a picture if it is geometry.

**Existing chat, bot added late.** Get the history (`group-chat`: the Bot API cannot read it), put the export in `raw/`, then:
1. Read it oldest first; fetch photos/documents/voice the important messages refer to.
2. Pass 1 — facts and decisions → model params and register rows with topic pages.
3. Pass 2 — open threads: unanswered questions, promises, tests → open_questions, handover.
4. Pass 3 — one topic page per node discussed; unknowns marked as unknown.
5. A short hello in the group: who the bot is, what it understood (3–5 lines), ONE question about the most blocking unknown.

## Briefing agents from the wiki

"Read first: `/clients/<id>/rules.md`, `wiki/client/rules.md`, `wiki/index.md`, the topic page(s)" + the decisions it must
not break. Template in `engineer-loop/briefing.md`.
