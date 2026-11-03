# catalog — the manufacturers' part catalogue

`catalog/library/<group>/<part>.py` is a part of the library's first level: a purchased part **as it is sold**, with
nobody's rework in it — bearings, cylinders, profile, motors, fasteners. It ships with the framework, so it is in every
container, and it is visible to every project of every client.

`catalog/` is the import root; `catalog/library/` is the `library` package (PEP 420, no `__init__.py`, so the levels
below merge into one namespace). A project does not import from here directly: the platform copies the groups the
project imports into `<project>/mechanics/cad/library/` (`skills/machine-project/scripts/part_library.py sync`), and the
project imports `from library.bearings import kfl004` — in a part file of its own, since a node only places parts
(`skills/machine-project` «Part library» has the two files).

## The three levels

| Level | Where | Who sees it |
|---|---|---|
| catalogue | `agent/catalog/library/` (here) | every project |
| client | `/clients/<client id>/library/` | that client's projects only (the mount that carries his `rules.md`) |
| workspace | `/workspace/library/` | the projects of that workspace only |

A part designed for a client lives in **his** library and never here: the catalogue is for what a shop sells. What
makes that hold is the mount — a project's container has no path to another client's folder, so `sync` cannot copy it
and nothing can import it. `part_library.py roots` prints the levels a container sees.

Nothing shadows anything: the same `library.<group>.<part>` at two levels is a sync error that names both files. A part
reworked for a client gets its own name and may import the catalogue part to read its sizes.

## Writing a catalogue part

It is a part file with build123d joints — the same contract as a part in a project, so read
`skills/machine-project` «Code model» first. On top of that:

- **One file, one part**; `build(**args)` takes the catalogue's own options (bore, stroke, size, length) and each set of
  arguments is its own variant with its own STEP and purchase row. A part of a different series is a different file.
- **`process="bought"`** with `kind`, `code` (what the client orders), `desc` and `title` (they go into his purchase
  list, so they come from `library/i18n/catalog_i18n.py` in the project's language: `T.t("<key>", ...)`, one entry per
  language), `unit` (`"pcs"` is a neutral key derive.py shows in the project's language; a `buy="length"` row is in metres, also in it); `buy="length"` for
  stock sold by the metre.
- **Ports are the contract.** Declare the joints a user of the part needs — the mounting face, every bolt hole, the
  shaft, the moving joint with its range — in the part's own coordinates, Z out of the part at a face. They must not
  change when the geometry does.
- **Geometry: the maker's STEP, else a dimensioned envelope.** A `VENDOR` mapping at the top of the module names the
  STEP file lying beside it; with no entry the part builds an envelope from the catalogue dimensions and sets
  `standin_reason` saying which numbers are published and which are not (PRINCIPLES §3). Both are legitimate; the
  project does not change when one replaces the other.
- **Adding a STEP means naming it in `VENDOR`** in the same commit. That edit is what rebuilds the projects that import
  the part: the incremental build hashes module code, and a file that was not there before was not a dependency.
- **Say where the numbers come from.** Every dimension table in a module carries the source and the date it was read in
  the module docstring. Never a number remembered from somewhere.
- **A STEP here is copied into every project that imports its group**, so keep it the maker's part and nothing more
  (no assembly of a machine) and under 20 MB — above that `check.py` refuses to commit it in the project.
- Fasteners (screw, nut, washer) are not here: every project scaffolds its own `parts/fasteners/` from
  `skills/machine-project/templates/`. Moving them here is a separate change.
