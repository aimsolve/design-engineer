"""Helpers shared by the nodes of player-demo: `P()` describes a part, `place()` puts it into its node."""


def P(name, shape, color, node=None, process="other", **fields):
    """One part as a part file's build() returns it (cad_incremental --model contract), built in its OWN coordinates, with
    build123d joints on `shape` where it docks and where other parts dock to it (RigidJoint, RevoluteJoint, LinearJoint, ...).
    node: leave it out, it is the part file's folder (parts/<node>/<part>.py).
    process: print | laser | bent | mill | turn | other | bought; it decides which derive.py scope picks the part up.
    Fields the derivers read (all JSON): material, thickness (laser/bent), finish, R, K, dim_d, note, code (the cipher printed in
    the kit name), weld, unit, and for bought parts desc, supplier, link, kind (fasteners|electrical|motors|pneumatic|bearings|
    profile|other), class. No qty: it is the number of placements; only a bought part may give qty = units per placement (5 for a
    pipe drawn once and bought as 5 × 1 m). A bought part without a 3D model: shape=None, with qty (a row of the purchase list,
    returned by the node without place())."""
    p = {"name": name, "shape": shape, "color": color, "process": process, **fields}
    if node is not None:
        p["node"] = node
    return p


def place(part, loc=None, name=None):
    """`part` (what a part file's build() returned) placed in the node. Without `loc`: where its joints put it (build123d
    `a["shape"].joints["x"].connect_to(b["shape"].joints["y"])` moves b; dock first, then place). With `loc`: at that Location (the
    machine's root part). A list of placed parts (what a sub-node's build() returned) is a sub-assembly: every part moves with it,
    locations compose; without `loc` each part of it
    moves with the part the parent docked (or the part it is docked to inside the sub-assembly). name: the placement's id in the viewer (default:
    the part's name, then <name>_2, _3, ...). Change the part in its file, never in the placed copy; call build() once per copy."""
    from build123d import Location
    if isinstance(part, list):
        if loc is not None:
            return [place(p, loc) for p in part]
        return [place(p, m) for p, m in zip(part, _moves(part))]
    sh = part.get("shape")
    if loc is None:
        new = sh.location if sh is not None else part.get("loc", Location())
    elif "loc" in part:
        new = loc * part["loc"]
    else:                                       # a fresh part: on top of where its build put it (identity once read from its STEP)
        new = loc * sh.location if sh is not None and sh.joints else loc
    out = dict(part, loc=new)
    if sh is not None and sh.joints:
        sh.location = new                       # what is docked to it next is docked where it stands (a part without joints
                                                # keeps its own coordinates)
    if name is not None:
        out["pname"] = name
    return out


def _moves(part):
    """How each part of a sub-assembly moves when the parent docked some of its parts: a docked part by its own move, any other part
    by the move of the part it is docked to inside the sub-assembly (`__joint`), else by the one move all docked parts share."""
    from build123d import Location
    own = [p["shape"].location * p["loc"].inverse() if p.get("shape") is not None and p["shape"].joints and "loc" in p
           and not _same(p["shape"].location, p["loc"]) else None for p in part]
    tok = {id(p["shape"]): i for i, p in enumerate(part) if p.get("shape") is not None}
    parent = {}                                 # docked inside the sub-assembly: by the framework's record, else (a plain script run)
    for i, p in enumerate(part):                # by the joint whose connect_to moved it
        if (p.get("__joint") or {}).get("to") is not None and id(p["__joint"]["to"]) in tok:
            parent[i] = tok[id(p["__joint"]["to"])]
    for i, p in enumerate(part):
        for j in (p["shape"].joints.values() if p.get("shape") is not None else ()):
            c = getattr(j, "connected_to", None)
            if c is not None and id(c.parent) in tok and tok[id(c.parent)] != i:
                parent.setdefault(tok[id(c.parent)], i)
    distinct = []
    for m in own:
        if m is not None and not any(_same(m, d) for d in distinct):
            distinct.append(m)
    out = []
    for i, p in enumerate(part):
        k, seen = i, set()
        while own[k] is None and k not in seen and k in parent:
            seen.add(k)
            k = parent[k]
        m = own[k] if own[k] is not None else distinct[0] if len(distinct) == 1 else Location() if not distinct else None
        if m is None and p.get("shape") is not None:
            raise ValueError(f"place(): parts of this sub-assembly were docked to different places and {p.get('name')!r} is docked "
                             f"to none of them; dock it inside its node to one of them")
        out.append(m or Location())
    return out


def _same(a, b, tol=1e-9):
    ta, tb = a.wrapped.Transformation(), b.wrapped.Transformation()
    return all(abs(ta.Value(r, c) - tb.Value(r, c)) <= tol for r in (1, 2, 3) for c in (1, 2, 3, 4))
