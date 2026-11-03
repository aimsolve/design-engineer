# The design engineer in local mode (plain Claude Code on the user's machine)

This project's `.engineer/mode` says `local`: there is no router, no Telegram, no remote worker. The user talks to you
right here; they are owner and client at once. Where a skill says `tg …`, `parts`, `shop`, `issue …` or «the router does
it», that part is the platform's (`platform.md`) and does not exist here; do what this file says instead.

## The loop
- Change the model (`mechanics/cad/params.py`, part files under `mechanics/cad/parts/<node>/`), as `machine-project` says.
- Build and check it yourself: `de build` (the model, the viewer site in `public/`, the AAS), then `de check` (every rule
  PASS/FAIL). The Stop hook runs both again when you finish a turn with changed model sources and sends you back on a FAIL;
  do not stop with a build error or a FAIL left.
- Show it: `de serve` prints the viewer address (`http://127.0.0.1:<port>/<project>/`); give that link. The page reloads by
  itself after the next `de build`.
- Heavy or long work: `job run "<cmd>" [--timeout S]`, it runs here, in the project folder, with a time limit.

## Sub-agents
- A sub-agent (critic, research) does not get the principles by itself here: put the output of `de rules` and the
  project's `wiki/client/rules.md` into its brief.

## Talking and handing over
- The answer is plain text in this conversation, in the user's language. No `tg say`, no reactions: those are the hosted platform's.
- A file for the user is its path in the project (`build/…`) or a link on the local viewer (the page's download
  buttons, `http://127.0.0.1:<port>/kit/<project>/<step|print|laser|bought|assembly>`), never an old export.
- Notes drawn on the model page land in `.engineer/inbox/` (one `<time>-note.json` with the comment, the parts the drawing
  lies on and the camera, plus the picture `.jpg`). Read new ones at the start of a turn and when the user mentions
  they drew or marked something on the model; treat each as the user's message.
- A board on the user's desk: `de serve`, the user opens the page in Chrome/Edge and presses the connect-a-board button, then
  `hw status` / `hw flash CARD` / `hw log CARD` work through that page.

## Bought parts
- The framework catalogue (`catalog/`, skill `part-library`) first; a maker's STEP the user downloaded goes to
  `mechanics/cad/vendor/`. No online part search is connected here (that is a module, off by default): when the part is
  not in the catalogue, ask the user for the maker's STEP (one closed question with the exact part number), and model a stand-in with `standin_reason` meanwhile.

## Knowledge filing
- A general lesson: a note in `wiki/log.md` (`tg propose` is the hosted platform's, there is none here); rules for this project: `wiki/client/rules.md`.
