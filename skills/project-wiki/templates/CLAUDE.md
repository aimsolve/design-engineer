# {pid}

This is a project of an AI engineering team that designs physical machines and devices for clients: 3D
models, calculations and simulations, electronics and firmware, parts for 3D printing and for the shop. The client
works with us in the Telegram group «{title}». His model and files are on the project page {url} (the standard
viewer; members of the chat sign in with Telegram).

## Who works here

Two kinds of agent share this folder, each in its own Claude session; your role comes with your session.

- The **manager** is the only one who talks to the Telegram chat. It hears the client, opens a GitHub issue of this
  project for each piece of work with the client's words quoted verbatim, and delivers the results.
- An **executor** works exactly one issue, in a fresh session, and writes back only in that issue. Several executors
  may run at once on independent issues.

Issues are in English; the client only ever reads his own language.

## Confidentiality: a hard rule

{conf_rule}

## Where things are

- The model is the single source of truth: `mechanics/cad/params.py`, `aas/` (skill `machine-project`).
- `wiki/index.md` is the map of the project's knowledge; `wiki/client/rules.md` holds the client's standing rules,
  `wiki/client/feedback.md` what he told us we did wrong. Both agents write the wiki (skill `project-wiki`).
- `.engineer/inbox.jsonl` is the whole chat history as the bot saw it; media are in `inbox/`.
- `public/` is served at {url}; its root is the standard viewer, never a hand-made page of the machine.
- The platform commits and pushes this folder after every turn: never commit. `git log` / `git show` read the history,
  for reference only: an old version is rebuilt in the current model, never restored as files (`machine-project` «Git»).
- Never read or print `.env` files or credentials.

This file describes the project and does not change from turn to turn; what is happening now lives in the issues and
in the wiki.
