---
title: Where files live
summary: where each kind of file lives, locally and on the server
updated: YYYY-MM-DD
status: current
---

# Where files live

| What | Local | Server |
|---|---|---|
| Model (params, parts) | `cad/` | — |
| Viewer | `<page>.html` | `<host>:/var/www/<site>/index.html` |
| CAD exchange | `cad/out/<zip>` | `<host>:/var/www/<site>/<zip>` |
| Sims | `sim/<name>/` | — |
| Print pieces | `print/<piece>/` | sent in the group |
