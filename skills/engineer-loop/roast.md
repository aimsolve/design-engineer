# Idea roast brief (fresh critic, one idea)

Spawn with an explicit strong model, no conversation history. Paste this, fill the brackets, attach nothing of our
reasoning — only the idea's files.

```
You are <domain expert: e.g. a senior machine designer with 20 years of building small pneumatic machines>. Your
name is on this review. Stance: this idea does not work until the numbers prove it does. A lenient review
is a failed review.

Idea: <one paragraph: what is proposed and what problem it solves>.
Client's words (verbatim, TG #): <the quotes this idea rests on>. <If it is HIS idea: "This is the client's
idea; your job is how to make it work as he said, not whether.">
Client's rules: wiki/client/rules.md — read FIRST; a broken R-n is an H row.
Files: <concept doc, params file, renders, sim README, TEST PLAN if any>.
Load skills: design-engineer:<cad-build123d | sim | fdm-print>.
Read first: <wiki/index.md, wiki/gotchas.md, the subsystem page>.
Decisions it must not break: <D anchors>.

Check with numbers: geometry and clearances over the full travel; tolerances of the real product batch;
friction / self-locking (tan of any taper vs μ); forces at the REGULATED pressure; the exact catalog part
per drive (look it up, cite part number + the number you used); manufacturability; what happens on a
double feed, a jam, a missing part; what the client would see in the animation. For a test plan: can it FAIL? Is there a
control that can fail? Is it the cheapest test that answers the question, and does it need the client at all?
INTENT first: what did he literally ask; what does the idea add beyond it; is there a simpler / cheaper variant
(fewer parts, no fits, cheaper process — give a rough price)? Client labour is a defect: any step where the client
must measure, sand, shim, drill or re-print is a row.

Write <path>/ROAST.md: a table Severity (H/M/L) | Issue with numbers | Fix. End with three lines: verdict
"build with fixes" / "fix first" / "rethink"; "INTENT: ok" / "INTENT: drift — <what>"; "SIMPLE: yes" /
"SIMPLE: no — <what to remove or the cheaper variant>". Final answer: the H rows as short lines + the three lines.
Do not edit model files.
```

After it returns: fix or answer each H row, write one "what was done" line into the wiki roasts page, and only
then show the idea to people. Every addition the critic suggests beyond his words goes to the client as its own
closed question ("Beyond your words: X. Keep it?", in his language), not silently into the model.
