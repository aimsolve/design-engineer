# Briefing a fresh agent from the wiki

Minimal prompt. The wiki and the plugin skills carry the context; the prompt carries only the task and the fences.

```
<One paragraph: the task, why, what done looks like.>

Load skills first: design-engineer:engineer-loop, design-engineer:<cad-build123d | sim | fdm-print — whatever the task touches>.
Client's words (verbatim, TG #): <the quotes the task rests on>.
Read first: wiki/client/rules.md (the client's rules — they beat everything else), wiki/index.md, wiki/gotchas.md, wiki/process.md, wiki/<subsystem page>,
<params file: header + the constants you touch>, <the specific source files>.

Do not break: <decision anchors, e.g. D6 (orientation), D12 (tapers)>.
You may edit: <own module>, <own review folder>. You must not edit: the params file (say so if it must
change), other modules, the viewer, wiki/.
Check: <build of your module>, <clearance script>, 2+ renders you have LOOKED at.

Default to the simplest buildable design; anything beyond the client's words is listed, not built silently.
Return: 6 lines max — what changed, the numbers that matter, anything added beyond his words, anything unsure or
conflicting, file paths. Any time estimate in agent minutes/hours, never human days.
```

- Explicit model on every spawn. Critics: the strongest available.
- Every brief carries `wiki/client/rules.md` and the client's verbatim quotes — no exceptions.
- A critic gets the same + the project `CRITIC.md` + round number + "stance: prove it does not work". Name its
  output file. Never paste our conclusions into it.
- When it returns: ingest (see `project-wiki`).
