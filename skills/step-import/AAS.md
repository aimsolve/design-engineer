# Machine model = Asset Administration Shell (AAS)

The AAS (IEC 63278, IDTA metamodel v3) is the machine's single source of truth. Numbers live here, the wiki keeps decisions only.
Everything the viewer and simulator read is DERIVED from it, one way.

```
STEP -> step_import.py -> build/{model.glb, parts.json, parts/}
                              |
       aas_build.py <build dir> --machine <repo>   (+ measured geometry, designer facts: seeded once)
                              v
   <repo>/aas/<machine>.aas.json   canonical, committed, pretty, stable order + ids   <- SOURCE OF TRUTH
   <repo>/build/<machine>.aasx         package: model.glb + per-part STEP/STL embedded (source STEP only named)
                              |  aas_export.py / mechanics/kinematics/build_motion.py  (plain python3, one-way)
                              v
        build/parts.json  (viewer, byte-identical to the importer's)     mechanics/kinematics/motion.json  (simulator + viewer)
```

## Commands (basyx and the validator need Python 3.12)
```bash
R="uv run --python 3.12 --with basyx-python-sdk==2.1.0 --with aas-test-engines==1.0.3 python"
S=<plugin root>/skills/step-import/scripts
$R $S/aas_build.py build --machine .            # rebuild AAS json + aasx, validate (exit 1 on fail). --no-part-files: small package
python3 $S/aas_export.py aas/<m>.aas.json --parts build/parts.json --motion mechanics/kinematics/motion.json   # or: python3 mechanics/kinematics/build_motion.py
$R $S/aas_check.py build --machine .            # round trip + derived files + validator
python3 $S/aas_semantics.py aas/<m>.aas.json   # add new own semanticIds to semantics.csv
```
CAD changed: re-run step_import, `mechanics/kinematics/measure_motion.py` (geometry -> `mechanics/kinematics/motion.measured.json`), then `aas_build.py --reseed` only if the
Kinematics geometry must be replaced (this discards edits made in the AAS). Without `--reseed` the authored submodels are carried over as is.

## Layout (one AAS, assetKind Type)
| Submodel | Standard | Content |
|---|---|---|
| Nameplate | IDTA 02006 v3.0.1 | designation, type, article number, firmware version |
| SoftwareNameplate | IDTA 02007 v1.0.1 | firmware set (one entry per board sketch); the operator app as one more `SoftwareNameplateType_App_<name>` (version, repo, branch, commit, build command, transport), **refreshed on every aas_build from `sim/site.json` -> `app.source`**, the same record the site build checks the app build against |
| AssetInterfacesDescription | IDTA 02017 v1.1 | optional: one Interface per `electronics/interfaces/*.json` listed in `facts.interfaces` (e.g. a BLE controller). BLE is not a standard binding of the template (HTTP, Modbus, MQTT, OPC UA, BACnet, IO-Link): generic Interface structure + W3C WoT terms, `base = ble:///<service>/`, form href = characteristic UUID, own `NS/ble/*` semanticIds. Seeded once, then authored (carried over) |
| TechnicalData | IDTA 02003 v2.0.1 | general information (part parameters are in PartCatalog) |
| HierarchicalStructures | IDTA 02011 v1.1.1 | EntryNode -> assembly Entities -> leaf Entities; leaf `SameAs` -> PartCatalog part; `Placement` = glTF 4x4 (column-major, metres); `Drawing` on assemblies |
| PartCatalog | ours | `Parts`: one collection per distinct part: kind, material, module, drawing, volume, bbox (Range), Type, `Params` (value + `source`/`unit` qualifiers), ParamsCheck/Conflicts, STEP/STL File. `AssemblySummary` |
| Models3D | IDTA 02026 v1.0.1 | model.glb (embedded), source STEP (`file:` reference, not embedded) |
| Kinematics | ours | `MachineParameters` (e.g. StepsPerRev; each with `source`), `Actuators` (Controller board+pin, Drive chain, Motion), `Clones`, `Convention` |
| SimulationModels | IDTA 02005 v1.1 | summary, type, scope, doc file |

Sub-element semanticIds of the IDTA templates were copied from the published template JSON; the templates are not fully populated (only elements we have data for).
HierarchicalStructures follows the nested EntryNode/Node form; entities are CoManaged (no separate AAS per part).

## Ids (never random)
- Base: `aas_lib.BASE`, `https://design-engineer.dev/` unless the environment sets `DE_AAS_BASE` (a deployment that publishes
  AAS files under its own domain sets it). An existing AAS file keeps the base it was written with: `aas_build.py` (and
  any writer) calls `aas_lib.adopt(path)` first, since the carried submodels reference the rebuilt ones by id; readers
  (`aas_lib.own_semantic`) take a file's own semanticIds under its base and the configured one.
- AAS `<base>ids/<machine>/aas`, asset `.../asset`, submodels `.../sm/<Name>`.
- Part collection = `P_<slug(part id)>` (non-ASCII ids get an 8-char sha1 suffix). Package file names: `/files/parts/<slug(id) lower>.step|stl` (OPC forbids non-ASCII).
- BoM entity = `p_<slug(part id)>_<k>` (leaf) / `a_<slug(drawing)>_<k>` (assembly), k = index among same-keyed siblings, so inserting an unrelated node does not rename others. Root = `EntryNode`.
- Node path (`name[k]/name[k]/...`, as in motion.json) is computed from entity display names; Kinematics references nodes by ModelReference, never by path text.

## Kinematics: what is data
Actuator = Controller (`Board`, `StepPin`, `DirPin`/`ValvePin`) + Drive (Motor part, optional Driver, MotorPulley/DrivenPulley -> Part ref; teeth come from the
part's `teeth` param, or `TeethOverride` with a `source` when the printed roller has no catalogue part, roller radii measured) + Motion (nodes as references,
axis, pivot, rest pose, ranges). Numbers that are computed carry a qualifier `derived` and no value:
`per_step:belt_surface` = 2*pi*r/steps*Tm/Td (mm), `per_step:output` = 360/steps*Tm/Td (deg), `output_mirrored`, `motor_shaft` = 360/steps,
`driven_roller`, `steps_per_rev`, `ratio_text`, `roller_radius_mm`, `range_from_steps`. Change StepsPerRev or a tooth count in the AAS and motion.json follows.
Designer-confirmed facts carry `source` "designer <date>". Clone actuators (mechanisms missing in the CAD) reference the SOURCE nodes plus `clone_id`.

## Standard vs ours
Standard: metamodel, the submodel templates above (semanticIds of template elements), AASX packaging, validated by aas-test-engines.
Ours: PartCatalog, Kinematics, all `<base>semantics/<type>/<property>` ids, the `source`/`unit`/`derived`/`shape` qualifiers,
Placement string. `semantics.csv` lists each own id by its `<type>/<property>` key with its unit and a note; its
`eclass_irdi` column is empty on purpose: ECLASS is free to view, but using its IRDIs needs a licence, so a user who has
one fills it in (the notes name the properties that have an ECLASS counterpart).

## Known SDK quirks handled in aas_build.py
basyx 2.1.0 writes `specificAssetIds: []` (schema rejects it) -> stripped; it lowercases supplementary names -> we lowercase them; unordered sets -> canonical sort.

## Machines without a STEP (build123d)
`machine-project/scripts/scaffold.py` writes a minimal valid AAS (same ids and shapes as above); `aas_sync.py` fills TechnicalData.ModelParameters from `mechanics/cad/params.py`
and PartCatalog + a BoM from `build/model/parts.json`: EntryNode -> `a_<slug(node)>_0` per `placements[].node` -> `p_<slug(id)>_0` with `BulkCount`
(its placements in that node; a row without placements: under its `node`, BulkCount = qty), flat when no part has placements. `check.py` there validates it. The machine name is the stem of `aas/<name>.aas.json` (`aas_lib.machine_name`), not necessarily the folder name.
