---
name: datasheets
description: Numbers about electronic parts come only from the manufacturer's datasheet or app note, cited as [doc-id rev p.N §section] — where to fetch (the makers' direct PDFs from TI / ADI / ST / Microchip, web.archive.org, the StackExchange API for "has anyone seen this"; official sources only, no bot bypass), what never to use, caching in the project's datasheets/ with the revision, and a grep script that prints hits ready to cite. Use whenever a part number, a register value, a noise figure, a frequency or a limit is about to reach a client, a review or a sim input.
---

# Datasheets and sources

## The rule

**Every number shown to a client carries `[doc-id rev p.N §section]`** — e.g. `[SBAS501D p.43 §9.2.3]`. A number
from memory is written "(unverified)", in the client's language, and is not used for a decision. Forum posts are leads, never
the citation: find the same number in the datasheet or say it comes from a forum.

## Fetch order

Official sources only: the maker's PDFs and documented public APIs. No scraping around bot protection; a reader proxy
or any other third-party fetcher only if the user chooses one (and then public URLs only).

| # | Where | How |
|---|---|---|
| 1 | TI | `curl -sfLo datasheets/<part>.pdf https://www.ti.com/lit/ds/symlink/<part>.pdf`; app notes `https://www.ti.com/lit/an/<id>/<id>.pdf` (e.g. sbaa532a) |
| 2 | ADI (MT-xxx tutorials, Trinamic TMC datasheets), ST, Microchip, other makers | direct PDF link from the maker's product page |
| 3 | The datasheet link on a distributor's product page | only when it serves the maker's own PDF (check the doc id and revision on the first page) |
| 4 | web.archive.org | `https://web.archive.org/web/2026/<url>` — an archived copy of the maker's URL |
| 5 | The user | when a page does not answer: ask the user for the PDF, naming the part and the doc id |

Community: electronics.stackexchange via its public API (keyless, 300 req/day/IP):
`curl -s "https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=votes&q=ADS1220%20stepper&site=electronics&filter=withbody" | gunzip -f`
(content is CC BY-SA — attribute with the link). Maker forum threads with the maker's engineers' answers are the best
forum source when they open directly. Klipper docs (load cells next to steppers) are readable directly.

**Never:** alldatasheet and similar mirrors (stale copies, unclear licensing); sites behind bot challenges (don't
bypass — use search snippets as leads only); a number from an LLM answer or a blog without a datasheet behind it.
If a fetch fails, try the next row, don't loop on one site.

## Cache and cite

- Save into the project's `datasheets/<part>_<doc-id>.pdf` (e.g. `ads1220_SBAS501D.pdf`); add a row to
  `datasheets/INDEX.md`: part, doc id, revision + date, URL, fetched date. A newer revision → new file, keep the old
  one if findings cite it.
- Find the number and its citation:

```bash
python ${DE_ROOT:-/opt/design-engineer}/skills/datasheets/scripts/ds_grep.py datasheets/ads1220_SBAS501D.pdf "µVRMS|noise" --context 1
python ${DE_ROOT:-/opt/design-engineer}/skills/datasheets/scripts/ds_grep.py datasheets/ads1220_SBAS501D.pdf --toc
```

Hits print as `[SBAS501D p.18 §7.1 Noise Performance] …`. Tables in PDFs extract as broken lines: open the page
(render it) and read the table before quoting a value from it. Say which conditions the number holds for (gain, data
rate, temperature, typ vs max) — "0.09 µVrms" without "gain 128, 20 SPS, typ" is a wrong number.
