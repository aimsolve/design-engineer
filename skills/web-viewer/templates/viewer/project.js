// Project hook: everything that is specific to THIS model. build.py inlines this file into page.tpl.html.
// Contract: define `function viewerProject(V)` and return a config object. Every key is optional.
// V gives you: THREE, P (params.json), scene, groups, byName, pickables, G(group), find(sub), findAll(sub), mat, box, at,
// k/ease/there (motion ramps), mm/mm2/num (decimal-comma formatting), springLen, setSpring, rotateAbout, tube, addTag, L.
// Rule: no CAD number typed here -- read it from V.P (put viewer-only numbers in a VIEWER dict in the part module).
function viewerProject(V) {
  return {
    title: "Model",                        // panel title + page title, in the client's language
    // lang: "en",                         // UI language of the page (page_i18n.js: ru | en; default ru)
    subtitle: "",                          // HTML under the title (what the model is, what is approximate)
    axesText: "",                          // HTML, e.g. axes in words from the operator's position
    views: [],                             // [{name, yaw, pitch, dist, target:[x,y,z]}]; first = home / "reset"; none = fit all
    // axes: { at: [x, y, z], size: 180, minDist: 600 },   // axis gizmo, tags hidden when zoomed closer than minDist
    // near: 20, zoom: [60, 3500], pitchRange: [0.05, 1.45], edges: true, noPick: ["tabletop"],
    cycle: 0,                              // seconds; 0 = static model (no play controls)
    ops: [],                               // [{t0, t1, name, d}] operation windows shown in the panel
    groupText: {},                         // {group: "what this group does, stroke"} for the card's motion row
    parts: [],                             // [[name substring, title, description]] -- fallback when meshes.json has no title/desc
    // labels: { reset: "Reset view" },   // override any UI text key of L
    // material: function (m, geo) { return null; },   // custom material for a mesh record, null = default
    setup: function () {},                 // build procedural objects, hide CAD meshes you redraw live
    pose: function (t, cycles) {}          // set every moving thing from t alone (pure function of time)
    // meta: function (t, cycles) { return "t = " + t.toFixed(2); }
  };
}
