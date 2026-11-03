#!/usr/bin/env python3
"""Incremental model build of a build123d machine project (machine-project layout: mechanics/cad/params.py, one folder per node
mechanics/cad/parts/<node>/ with __init__.py placing its parts and one <part>.py per part, build() -> one part in its own
coordinates). Generic: knows nothing about a particular machine.

    python $S/cad_incremental.py --model assemble.py     # from mechanics/cad: the model in build/model (one STEP per part)
    python $S/cad_incremental.py script.py [args]        # any other script: run as `python script.py args` (no caching)

1. params proxy + taint: `params` gets a recording module class; every `params.X` and dict subkey `params.X["a"]` read is
   recorded in its context (a project module import, a build() call). Numbers and strings come back as subclasses carrying
   the param names; arithmetic, math and build123d pass them on, so a value counts as read where it is used.
2. node cache (--model): every build() of a node folder or part file runs only when the values of the params it read, the
   project modules it used or the files it opened changed; its parts are read back from their STEPs otherwise. Module
   globals and params entries it wrote are replayed.
"""
import ast
import builtins
import contextlib
import enum
import functools
import hashlib
import importlib
import importlib.machinery
import importlib.util
import inspect
import io
import json
import math
import numbers
import os
import pathlib
import pickle
import re
import sys
import time
import types

VERSION = "2-862903bc2a54"   # cache format / key rules: change it whenever a change could alter a cached result
# Not bumped for #18 (values keyed by what was taken, a node's params.py): a record written by the previous rules lists MORE
# inputs than these would, so reusing it rebuilds too much rather than too little - and a bump would rebuild every live project.
# One exception, as before #18: such a record does not know that `getattr(params, "X", default)` found no X, so adding X later
# reaches that part only once it rebuilds for another reason. The old record shape the new rules cannot read (a node's params.py
# listed among its modules) is dropped where it is read (_entry_deps). Records written now carry env `|t18|` (_model_run): a
# script from before #18 rejects them, as it cannot check their takes.
EMPTY = frozenset()
MUTATORS = {"__init__", "locate", "move", "relocate"}
NO_TAINT = {"tessellate", "__repr__", "__str__", "__hash__", "__eq__", "__ne__", "__bool__", "__len__", "__iter__",
            "__getstate__", "__setstate__", "__reduce__", "__reduce_ex__", "__copy__", "__deepcopy__", "__getattr__",
            "__getattribute__", "__setattr__", "__delattr__", "__new__", "__init_subclass__", "__class_getitem__",
            "__contains__", "__format__", "__dir__", "__sizeof__", "__subclasshook__", "__instancecheck__"}
WRAP_DUNDER = {"__init__", "__add__", "__radd__", "__sub__", "__rsub__", "__mul__", "__rmul__", "__truediv__", "__rtruediv__",
               "__neg__", "__and__", "__or__", "__xor__", "__matmul__", "__mod__", "__pow__", "__getitem__", "__call__", "__abs__"}


# ============================================================ taint: numbers that remember which params they came from
class TFloat(float):
    __slots__ = ("_d",)

    def __new__(cls, v, d):
        o = float.__new__(cls, v)
        o._d = d
        return o

    def __reduce__(self):
        return (TFloat, (float(self), self._d))


class TInt(int):
    def __new__(cls, v, d):
        o = int.__new__(cls, v)
        o._d = d
        return o

    def __reduce__(self):
        return (TInt, (int(self), self._d))


class TStr(str):
    def __new__(cls, v, d):
        o = str.__new__(cls, v)
        o._d = d
        return o

    def __reduce__(self):
        return (TStr, (str(self), self._d))


def deps(x, depth=0):
    t = type(x)
    if t is TFloat or t is TInt or t is TStr:
        return x._d
    if t in (float, int, str, bool, bytes, type(None)):
        return EMPTY
    # An object's taint, if it has any, is `_pdeps` in its __dict__ - so __dict__ is read HERE, before the isinstance() below.
    # isinstance() against a type the object is not falls back to obj.__class__, and a reused part's lazy stand-in answers an
    # attribute like that by reading its STEP back. On one large machine that was 799 STEP reads and 97% of the 47 s one
    # changed parameter took - for geometry that cannot carry taint at all, since what a STEP reads back has no `_pdeps`.
    od = None
    if t not in (list, tuple, set, frozenset, dict):
        try:
            od = object.__getattribute__(x, "__dict__")
        except AttributeError:
            pass                                # no __dict__ (a namedtuple, a slotted object): the checks below decide
        else:
            if "_lz" in od:
                return od.get("_pdeps", EMPTY)
    if isinstance(x, (list, tuple, set, frozenset)):
        if depth > 3:
            return EMPTY
        acc = EMPTY
        for e in x:
            d = deps(e, depth + 1)
            if d:
                acc = acc | d
        return acc
    if isinstance(x, dict):
        return deps(list(dict.values(x)), depth) if depth <= 3 else EMPTY
    return od.get("_pdeps", EMPTY) if od is not None else EMPTY


def _tag(o, d):
    if isinstance(o, (type, types.ModuleType, enum.Enum, types.FunctionType)):
        return
    try:
        od = object.__getattribute__(o, "__dict__")
    except AttributeError:
        return
    old = od.get("_pdeps")
    od["_pdeps"] = d if old is None else (old | d)


def taint(r, d):
    if not d:
        return r
    t = type(r)
    if t is float or t is TFloat:
        return TFloat(r, (r._d | d) if t is TFloat else d)
    if (t is int or t is TInt) and t is not bool:
        return TInt(r, (r._d | d) if t is TInt else d)
    if t is tuple:
        return tuple(taint(e, d) for e in r)
    if isinstance(r, list):
        for i, e in enumerate(r):
            ne = taint(e, d)
            if ne is not e:
                r[i] = ne
        return r
    if isinstance(r, tuple) and hasattr(r, "_fields"):
        return type(r)(*(taint(e, d) for e in r))
    _tag(r, d)
    return r


def _arith(name, basecls):
    base = basecls.__dict__[name]

    def op(self, *a):
        r = base(self, *a)
        if r is NotImplemented:
            return r
        d = self._d
        for x in a:
            dx = deps(x)
            if dx:
                d = d | dx
        return taint(r, d)
    return op


for _n in ("__add__", "__radd__", "__sub__", "__rsub__", "__mul__", "__rmul__", "__truediv__", "__rtruediv__",
           "__floordiv__", "__rfloordiv__", "__mod__", "__rmod__", "__pow__", "__rpow__", "__neg__", "__pos__", "__abs__",
           "__round__", "__divmod__", "__rdivmod__", "__trunc__", "__floor__", "__ceil__"):
    if _n in float.__dict__:
        setattr(TFloat, _n, _arith(_n, float))
    if _n in int.__dict__:
        setattr(TInt, _n, _arith(_n, int))


def _use_hook(base):
    def op(self, *a, **k):
        REC.use(self._d)
        return base(self, *a, **k)
    return op


# --model: a tainted value counts as READ by a node when it is used (compared, converted, formatted, passed to build123d / math),
# not when it is merely copied by `from params import *`
_USE = {float: ("__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__", "__bool__", "__int__", "__float__", "__hash__", "__repr__",
                "__str__", "__format__", "is_integer", "as_integer_ratio", "hex", "__complex__"),
        int: ("__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__", "__bool__", "__int__", "__float__", "__index__", "__hash__",
              "__repr__", "__str__", "__format__", "bit_length", "bit_count", "to_bytes", "as_integer_ratio", "__complex__"),
        str: tuple(n for n in dir(str) if n not in ("__class__", "__doc__", "__new__", "__init__", "__init_subclass__", "__subclasshook__",
                                                   "__getattribute__", "__setattr__", "__delattr__", "__dir__", "__reduce__",
                                                   "__reduce_ex__", "__getnewargs__", "__sizeof__"))}
for _c, _base in ((TFloat, float), (TInt, int), (TStr, str)):
    for _n in _USE[_base]:
        if _n not in _c.__dict__ and hasattr(_base, _n):
            setattr(_c, _n, _use_hook(getattr(_base, _n)))


def _taint_fn(f):
    @functools.wraps(f)
    def w(*a, **k):
        r = f(*a, **k)
        d = deps(a) | deps(k) if (a or k) else EMPTY
        REC.use(d)
        return taint(r, d) if d else r
    w.__cad_taint__ = True
    return w


def _tmax(builtin):
    """max/min that keep the taint of every candidate (the winner depends on all of them)."""
    def w(*a, **k):
        if len(a) == 1:
            seq = list(a[0])
            r = builtin(seq, **k)
        else:
            seq = a
            r = builtin(*a, **k)
        d = deps(list(seq)) if REC.on else EMPTY
        REC.use(d)
        return taint(r, d) if d else r
    w.__cad_taint__, w.__wrapped__ = True, builtin
    return w


# ============================================================ the recorder: who read which param, file, module
# The one name a params module goes by: `params.py` next to assemble.py is the machine's, `parts/<node>/params.py` is a node's
#. A params module is NOT a project module: its values are tracked read by read, so a part that did not read a changed
# name is not rebuilt, and its file is deliberately not hashed into anybody's key - where a project module's is, because a
# change to code there can change anything its users build.
def _is_params(modname):
    if not isinstance(modname, str):
        return False
    if modname == REC.params_name:
        return True
    p = modname.split(".")                      # parts.<node>[.<sub>...].params, and nothing else: a params.py elsewhere
    return len(p) >= 3 and p[0] == PARTS_PKG and p[-1] == REC.params_name   # (vendor/, a library) stays a project module


# What a node params module's read paths start with, so that its `X` and the machine's `X` are two keys and never one.
_AT = "@"


class Frame:
    __slots__ = ("kind", "name", "reads", "files", "mods", "gets", "sunk")

    def __init__(self, kind, name):
        self.kind, self.name = kind, name
        self.reads, self.files, self.mods = set(), set(), set()
        self.gets = set()            # (project module, name) this frame took out of a project module
        self.sunk = None             # a node's params.py: (reads, gets) of its import, for a build() that imports it itself

    def merge(self, other):
        self.reads |= other.reads
        self.files |= other.files
        self.mods |= other.mods
        self.gets |= other.gets


class Recorder:
    def __init__(self):
        self.on = False
        self.stack = [Frame("driver", "<script>")]
        self.imports = {}            # module name -> Frame of its import (reads at import time)
        self.params = None           # the real params module (the machine's)
        self.params_name = "params"
        self.param_mods = {}         # every params module in play: module name -> module (the machine's and the nodes')
        self.cad = None              # mechanics/cad (project modules live under it)
        self.root = None             # project root (files are recorded relative to it)
        self.skip_dirs = []          # build/model, build/derived: never recorded
        self.paths = {}              # param name "A.b" -> the real path ("A", "b") (keys may be ints)
        self._vh = {}
        self._th = {}                # (project module, name) -> value hash of a taken name, this run (see _take_hash)
        self._tobjs = {}             # ... and the modules holding an object it reaches
        self.mutators = set()        # project modules whose code may change something outside their own names (_changes_others)
        self.pre = {}                # params values as they were before a build function changed them (for its cache key)

    def top(self):
        return self.stack[-1]

    def push(self, kind, name):
        f = Frame(kind, name)
        self.stack.append(f)
        return f

    def pop(self, f):
        assert self.stack[-1] is f
        self.stack.pop()
        if f.kind == "import":
            self.imports[f.name] = f
            if not hasattr(sys.modules.get(f.name), "__path__"):   # a package (node folder) runs as the parent of whatever
                self.top().mods.add(f.name)                         # submodule is imported first: _import_hook records real uses
        elif f.kind == "sink":                                      # a node's params.py running: its reads are nobody's,
            if self.top().kind != "driver":                         # but its importer uses it (_entry_deps: if its import
                self.top().mods.add(f.name)                         # changes others, its reads come back)
        elif self.top().kind != "driver":
            self.top().merge(f)

    def replay(self, reads=(), files=(), mods=(), gets=()):
        t = self.top()
        if t.kind == "driver":
            return
        t.reads.update(reads)
        t.files.update(files)
        t.mods.update(mods)
        t.gets.update(gets)

    def record(self, path):
        if self.on:
            self.top().reads.add(path)
            self.paths[pstr(path)] = path

    def file(self, p):
        if not self.on:
            return
        try:
            ap = os.path.abspath(os.fspath(p))
        except TypeError:
            return
        if not ap.startswith(self.root + os.sep) or any(ap.startswith(s) for s in self.skip_dirs) or not os.path.isfile(ap):
            return
        self.top().files.add(os.path.relpath(ap, self.root))

    def get(self, modname, name):
        """A name taken out of project module `modname`: its VALUE is an input of the taker (see _take_hash)."""
        if self.on and self.top().kind != "driver":
            self.top().gets.add((modname, name))

    def is_project(self, mod):
        f = getattr(mod, "__file__", None)
        return bool(f) and os.path.abspath(f).startswith(self.cad + os.sep) and not _is_params(mod.__name__)

    def use(self, d):
        """The tainted values `d` (param names) were used in the current frame: count them as read there."""
        if d and self.on and self.top().kind != "driver":
            t = self.top()
            for n in d:
                t.reads.add(self.paths.get(n) or tuple(n.split(".")))

    def use_module(self, mod):
        if self.on and isinstance(mod, types.ModuleType) and (self.is_project(mod) or mod.__name__ in self.param_mods
                                                              and mod.__name__ != self.params_name):
            self.top().mods.add(mod.__name__)

    # ---- params values ----
    def wrap_read(self, path, v, rec=True):
        if isinstance(v, dict):
            return TrackedDict(v, path)
        if rec:
            self.record(path)
        else:
            self.paths[pstr(path)] = path
        if type(v) is list:                 # mutable: hand out the real list (an append must reach params); no taint
            return v
        if isinstance(v, types.FunctionType) and _is_params(v.__module__):
            return _param_fn(v, frozenset([pstr(path)]))
        return deep_taint(v, frozenset([pstr(path)]))

    def is_params_globals(self, g):
        """Whether `g` is the globals of a params module: what a function defined there reads from them is its own input."""
        return any(g is vars(m) for m in self.param_mods.values())

    def lookup(self, path):
        v = self.params
        if path and isinstance(path[0], str) and path[0].startswith(_AT):
            mod = sys.modules.get(path[0][len(_AT):])
            if mod is None:
                return _UNIMPORTED              # not imported yet (a build() imports it): nothing to compare - see value_hash
            if len(path) < 2:
                return "<missing>"
            try:
                v = self.pre[path[:2]] if path[:2] in self.pre else vars(mod)[path[1]]
                for k in path[2:]:
                    v = v[k]
            except (KeyError, IndexError, TypeError):
                return "<missing>"
            return v
        try:
            v = self.pre[path[0]] if path[0] in self.pre else vars(v)[path[0]]
            for k in path[1:]:
                v = v[k]
        except (KeyError, IndexError, TypeError):
            return "<missing>"
        return v

    def value_hash(self, path):
        h = self._vh.get(path)
        if h is None:
            node = bool(path) and isinstance(path[0], str) and path[0].startswith(_AT)   # a node's params.py: keyed strictly, like
            v = self.lookup(path)                                                      # a taken value (its file is not hashed)
            if v is _UNIMPORTED:
                return _UNIMPORTED_H            # never equal to a stored hash, and not cached: the module may be imported soon
            try:
                h = hashlib.sha1(vh(v, self, set(), strict=node).encode()).hexdigest()
            except Exception:               # Unhashable, a RecursionError, an object's own __getattr__ raising
                h = "<unhashable>"          # never equal to a stored hash: always a rebuild
            self._vh[path] = h
        return h

    def closure(self, mods):
        out, todo = set(), list(mods)
        while todo:
            m = todo.pop()
            if m in out:
                continue
            out.add(m)
            fr = self.imports.get(m)
            if fr is not None:
                todo.extend(fr.mods)
        return out


REC = Recorder()


def pstr(path):
    return ".".join(str(p) for p in path)


def deep_taint(v, d):
    t = type(v)
    if t is float:
        return TFloat(v, d)
    if t is int:
        return TInt(v, d)
    if t is str:
        return TStr(v, d)
    if t is tuple or t is list:
        return t(deep_taint(e, d) for e in v)
    if isinstance(v, tuple) and hasattr(v, "_fields"):
        return t(*(deep_taint(e, d) for e in v))
    if t is dict:
        return {k: deep_taint(e, d) for k, e in v.items()}
    return v


def _param_fn(f, d):
    @functools.wraps(f)
    def w(*a, **k):
        r = f(*a, **k)
        dd = d | deps(a) | deps(k)
        REC.use(dd)
        return taint(r, dd)
    return w


class TrackedDict(dict):
    """A params dict: a subkey read records 'NAME.key'; iterating / len / items records the whole dict."""
    __slots__ = ("_p", "_src")

    def __init__(self, src, path):
        dict.__init__(self, src)
        self._p = path
        self._src = src                     # the real dict: writes go through (a part module may store values in params)

    def __setitem__(self, k, v):
        self._src[k] = v
        dict.__setitem__(self, k, v)
        REC._vh.clear()

    def clear(self):
        self._src.clear()
        dict.clear(self)
        REC._vh.clear()

    def popitem(self):
        k, v = self._src.popitem()
        dict.pop(self, k, None)
        REC._vh.clear()
        return k, v

    def __ior__(self, other):
        self.update(other)
        return self

    def __delitem__(self, k):
        del self._src[k]
        dict.__delitem__(self, k)
        REC._vh.clear()

    def setdefault(self, k, default=None):
        REC.record(self._p + (k,))
        if k not in self._src:
            REC._vh.clear()
        v = self._src.setdefault(k, default)
        dict.__setitem__(self, k, v)
        return TrackedDict(v, self._p + (k,)) if isinstance(v, dict) else v

    def update(self, *a, **kw):
        self._src.update(*a, **kw)
        dict.update(self, *a, **kw)
        REC._vh.clear()

    def pop(self, k, *default):
        REC.record(self._p + (k,))
        dict.pop(self, k, None)
        REC._vh.clear()
        return self._src.pop(k, *default)

    def __getitem__(self, k):
        return REC.wrap_read(self._p + (k,), dict.__getitem__(self, k))

    def get(self, k, default=None):
        if dict.__contains__(self, k):
            return self[k]
        REC.record(self._p + (k,))
        return default

    def __contains__(self, k):
        REC.record(self._p + (k,))
        return dict.__contains__(self, k)

    def _whole(self):
        REC.record(self._p)
        return frozenset([pstr(self._p)])

    def __iter__(self):
        self._whole()
        return dict.__iter__(self)

    def __len__(self):
        self._whole()
        return dict.__len__(self)

    def keys(self):
        self._whole()
        return dict.keys(self)

    def values(self):
        d = self._whole()
        return [deep_taint(v, d) for v in dict.values(self)]

    def items(self):
        d = self._whole()
        return [(k, deep_taint(v, d)) for k, v in dict.items(self)]

    def copy(self):
        d = self._whole()
        return {k: deep_taint(v, d) for k, v in dict.items(self)}

    def __eq__(self, o):
        self._whole()
        return dict.__eq__(self, o)

    def __ne__(self, o):
        return not self.__eq__(o)

    __hash__ = None

    def __repr__(self):
        self._whole()
        return dict.__repr__(self)

    def __reduce__(self):
        self._whole()
        return (dict, (dict(dict.items(self)),))


class ParamsModule(types.ModuleType):
    def __getattribute__(self, name):
        try:
            v = types.ModuleType.__getattribute__(self, name)
        except AttributeError:
            if REC.on and not name.startswith("__"):
                REC.record((name,))             # `getattr(params, "X", default)`: that X is missing is an input - add it, rebuild
            raise
        if name == "__dict__" and REC.on and _user_frame():
            _read_all(v, ())                    # vars(params): every name of it, as `from params import *` reads them
        if name.startswith("__") or not REC.on:
            return v
        return REC.wrap_read((name,), v)

    def __setattr__(self, name, v):
        types.ModuleType.__setattr__(self, name, v)
        REC._vh.clear()


class NodeParamsModule(types.ModuleType):
    """`parts/<node>/params.py`: the node's own numbers and derived values, read exactly like the machine's.

    Its paths carry the module (`@parts.shuttle.params`, `RAIL_Y`), so the node's `RAIL_Y` and the machine's are two
    cache keys and never one. A name the node does not define comes from the machine's params.py and is recorded
    THERE, where the value lives - so a part reading it through the node is rebuilt by a change to the machine's own
    number, and a node that adds a name of its own later does not quietly take over that key.
    """

    def __getattribute__(self, name):
        if name.startswith("__"):
            v = types.ModuleType.__getattribute__(self, name)
            if name == "__dict__" and REC.on and _user_frame():     # vars(node params): every name of it
                _read_all(v, (_AT + types.ModuleType.__getattribute__(self, "__name__"),))
            return v
        try:
            v = types.ModuleType.__getattribute__(self, name)
        except AttributeError:
            if REC.on:                              # ... and that the node does NOT define it, which is an input too: the
                REC.record((_AT + types.ModuleType.__getattribute__(self, "__name__"), name))   # day it does, this part rebuilds
            return getattr(REC.params, name)        # the machine's, recorded at the machine's path
        if not REC.on:
            return v
        return REC.wrap_read((_AT + types.ModuleType.__getattribute__(self, "__name__"), name), v)

    def __setattr__(self, name, v):
        types.ModuleType.__setattr__(self, name, v)
        REC._vh.clear()


def _user_frame():
    """Whether the code asking a module for its __dict__ is the project's (vars(mod)), not this script or the import system."""
    return not sys._getframe(2).f_code.co_filename.startswith((__file__, "<frozen"))


def _read_all(g, at):
    for n, v in list(g.items()):
        if not (n.startswith("__") or isinstance(v, types.ModuleType)):
            REC.record(at + (n,))


class ProjectModule(types.ModuleType):
    """A project module whose names are traced: taking `C` out of `parts/shuttle/_layout.py` makes the VALUE of `C` an input
    of whoever took it, and nothing else of that module's (see _entry_deps). Before this, a part inherited every
    parameter the module read while it was imported - on one large machine that put all 13 numbers of
    `shuttle/_layout.py` into all 60 parts of the node, so a stroke only one part uses rebuilt the lot."""

    def __getattribute__(self, name):
        try:
            v = types.ModuleType.__getattribute__(self, name)
        except AttributeError:
            if REC.on and not name.startswith("__"):    # `getattr(mod, "OPT", 1.0)`: that OPT is missing is a take (keyed "<missing>")
                REC.get(types.ModuleType.__getattribute__(self, "__name__"), name)
            raise
        if REC.on and not name.startswith("__"):
            REC.get(types.ModuleType.__getattribute__(self, "__name__"), name)
        elif REC.on and name == "__dict__" and _user_frame():
            REC.get(types.ModuleType.__getattribute__(self, "__name__"), "*")   # vars(mod): any name of it, so the module whole
        return v

    def __setattr__(self, name, v):
        types.ModuleType.__setattr__(self, name, v)
        REC._th.clear()


# ============================================================ stable value hash (cache keys)
class Unhashable(Exception):
    pass


def vh(v, rec, seen, strict=False):
    """Stable hash of a params value (a read of params.py). `strict`: a value taken out of a project module or read from a node's
    params.py - see _svh."""
    if strict:
        return _svh(v, rec, seen)
    t = type(v)
    if v is None or t in (bool, str, bytes):
        return f"{t.__name__}:{v!r}"
    if t in (float, TFloat):
        return f"f:{float(v)!r}"
    if t in (int, TInt):
        return f"i:{int(v)}"
    if isinstance(v, (tuple, list)):
        return f"{t.__name__}(" + ",".join(vh(e, rec, seen) for e in v) + ")"
    if isinstance(v, dict):
        return "{" + ",".join(f"{vh(k, rec, seen)}:{vh(e, rec, seen)}" for k, e in sorted(dict.items(v), key=lambda kv: repr(kv[0]))) + "}"
    if isinstance(v, (set, frozenset)):
        return "set(" + ",".join(sorted(vh(e, rec, seen) for e in v)) + ")"
    if isinstance(v, enum.Enum):                    # its value too, which params may compute
        return f"enum:{t.__qualname__}.{v.name}=" + vh(v.value, rec, seen)
    if isinstance(v, enum.EnumMeta):
        return f"enumcls:{v.__qualname__}(" + ",".join(f"{m.name}=" + vh(m.value, rec, seen) for m in v) + ")"
    if isinstance(v, types.FunctionType):
        if id(v) in seen:
            return f"fn:{v.__qualname__}"
        seen.add(id(v))
        parts = [f"fn:{v.__module__}.{v.__qualname__}", _code_hash(v.__code__), vh(v.__defaults__, rec, seen),
                 vh(v.__kwdefaults__ or {}, rec, seen)]
        if v.__closure__:
            for c in v.__closure__:
                try:
                    parts.append(vh(c.cell_contents, rec, seen))
                except ValueError:                  # empty cell
                    parts.append("<empty>")
        g = v.__globals__
        if rec.is_params_globals(g):                # params globals the function reads (they bypass the proxy)
            for n in _code_names(v.__code__):
                if n in g and not isinstance(g[n], types.ModuleType) and (id(g[n]) not in seen):
                    parts.append(n + "=" + vh(g[n], rec, seen))
        return "|".join(parts)
    if isinstance(v, (types.BuiltinFunctionType, type, types.ModuleType)):
        return f"obj:{getattr(v, '__module__', '')}.{getattr(v, '__qualname__', getattr(v, '__name__', ''))}"
    if isinstance(v, functools.partial):
        return "partial(" + vh(v.func, rec, seen) + vh(v.args, rec, seen) + vh(v.keywords, rec, seen) + ")"
    if getattr(v, "__wrapped__", None) is not None:
        return "wrapped(" + vh(v.__wrapped__, rec, seen) + ")"
    r = repr(v)
    if " at 0x" in r:
        raise Unhashable(r)
    return f"{t.__qualname__}:{r}"


# A value a part takes out of a project module, or reads from a node's params.py, is keyed by everything it is at the Python
# level: plain data by what it holds (a Location, a Vector, a regex too); a project function by its code, defaults, closure,
# attributes and the module globals its code names; a project class by its bases and its class dict (methods by the function rule,
# class attributes by value - `Cfg.W = ...` set by another module's import changes the key); an enum member or a namedtuple by its
# class and what it is. A library function or class by its name (library code is not the project's: it is not keyed, as before).
# Anything else - an instance, a shape, an array, a function that looks names up by string, and a list / dict / set a function or
# class keeps (a memo, a registry) - is not hashed by its state, which may be a cache filling as the run goes: Unhashable at the top (the module keyed whole), and inside a function or class the module
# holding it is noted (_held_by) - _entry_deps keys both by the imports that could have made or changed it.
_DUNDER_STATE = ("__dict__", "__weakref__", "__module__", "__doc__", "__qualname__", "__slots__")


def _svh(v, rec, seen):
    t = type(v)
    if t in (str, TStr) or isinstance(v, pathlib.PurePath) and t.__module__ == "pathlib":
        # A path into the project counts from the project root: the same project in another folder (another worker's checkout,
        # a copy) keys the same - the reason PartCAD keeps mtime out of its keys.
        sv, kind = os.fspath(v), ("str" if t in (str, TStr) else "path")
        if rec.root and (sv == rec.root or sv.startswith(rec.root + os.sep)):
            return f"{kind}:<root>{sv[len(rec.root):]!r}"
        return f"{kind}:{str.__str__(sv)!r}"
    if v is None or t in (bool, bytes):
        return f"{t.__name__}:{v!r}"
    if t in (float, TFloat):
        return f"f:{float(v)!r}"
    if t in (int, TInt):
        return f"i:{int(v)}"
    if t in (tuple, list):
        return f"{t.__name__}(" + ",".join(_svh(e, rec, seen) for e in v) + ")"
    if t is dict:                                   # in its order: stations laid out one after another follow a swap
        return "{" + ",".join(f"{_svh(k, rec, seen)}:{_svh(e, rec, seen)}" for k, e in dict.items(v)) + "}"
    if t in (set, frozenset):
        return f"{t.__name__}(" + ",".join(sorted(_svh(e, rec, seen) for e in v)) + ")"
    if isinstance(v, types.ModuleType):
        return f"mod:{v.__name__}"                  # what is taken out of a module is traced when it is taken
    if t is TrackedDict:
        return f"pview:{pstr(v._p)}"                # a params dict: every read through it is recorded where it is made
    if t is re.Pattern:
        return f"re:{v.pattern!r}/{v.flags}"
    if t.__module__ == "build123d.geometry":
        g = _geom_state(v)
        if g is not None:
            return f"b3d:{t.__qualname__}{g!r}"
    if id(v) in seen:
        return f"seen:{t.__qualname__}"             # a cycle: its content is in this hash already
    seen.add(id(v))
    if isinstance(v, type):
        return _cls_vh(v, rec, seen) if _project_type(v) else f"cls:{v.__module__}.{v.__qualname__}"
    if isinstance(v, types.FunctionType):
        return _fn_vh(v, rec, seen)
    if isinstance(v, types.MethodType):
        return "meth(" + _svh(v.__func__, rec, seen) + "," + _svh(v.__self__, rec, seen) + ")"
    if isinstance(v, types.BuiltinFunctionType):
        own = v.__self__
        if own is None or isinstance(own, types.ModuleType):
            return f"bfn:{getattr(v, '__module__', '')}.{v.__qualname__}"
        return f"bm:{v.__qualname__}(" + _svh(own, rec, seen) + ")"   # `dim = DIMS.get`: what it is bound to is the value
    if isinstance(v, functools.partial):
        return "partial(" + _svh(v.func, rec, seen) + _svh(v.args, rec, seen) + _svh(v.keywords, rec, seen) + ")"
    if isinstance(v, (staticmethod, classmethod)):
        return f"{t.__name__}(" + _svh(v.__func__, rec, seen) + ")"
    if isinstance(v, property):
        return "prop(" + ",".join(_svh(f, rec, seen) for f in (v.fget, v.fset, v.fdel)) + ")"
    if _project_type(t) and isinstance(v, (enum.Enum, tuple)):
        return _obj_vh(v, rec, seen)                # an enum member, a namedtuple: what it is, not state that may change
    if isinstance(v, enum.Enum):
        return f"enum:{t.__module__}.{t.__qualname__}.{v.name}=" + _svh(v.value, rec, seen)
    wrapped = inspect.getattr_static(v, "__wrapped__", None)
    if wrapped is not None and type(t).__module__ == "builtins" and t.__module__ == "functools":
        return "wrapped(" + _svh(wrapped, rec, seen) + ")"     # an lru_cache: what it caches
    raise Unhashable(f"{t.__qualname__}: no value to key by")


def _geom_state(v):
    """The whole state of a build123d value object (a Location, Pos, Rot, Vector, Axis, Plane), or None."""
    import build123d                            # loaded already: v is one of its objects
    if isinstance(v, build123d.Location):
        t = v.wrapped.Transformation()
        return tuple(t.Value(i, j) for i in (1, 2, 3) for j in (1, 2, 3, 4))
    if isinstance(v, build123d.Vector):
        return (v.X, v.Y, v.Z)
    if isinstance(v, build123d.Axis):
        return _geom_state(v.position) + _geom_state(v.direction)
    if isinstance(v, build123d.Plane):
        return _geom_state(v.origin) + _geom_state(v.x_dir) + _geom_state(v.z_dir)
    return None


def _project_type(t):
    try:
        return any(_is_project_globals(vars(sys.modules[c.__module__])) for c in t.__mro__ if c.__module__ in sys.modules)
    except Exception:
        return False


_OBJS = None   # while a take is hashed: the modules holding an object a function or class of it reaches (see _entry_deps)
_HOME = None   # ... and the module it is taken out of: state a library wrapper holds (a singledispatch registry) counts there


def _held_by(modname, x, rec, seen):
    """Hash of `x`, something module `modname` holds (a global, a default, a closure cell, a class attribute). An object (an
    instance, a shape, a function looking names up by string) is not hashed by its state, which may be a cache filling as the run
    goes: while a take is hashed, the module is noted instead, and the taker keyed by every import that could have set it up."""
    if _OBJS is not None and modname not in REC.imports:
        modname = _HOME                         # held by library code (a decorator's closure): no import of its own to key by
    if _OBJS is not None and type(x) in (list, dict, set, bytearray):
        _OBJS.add(modname)                      # a list / dict / set a function keeps: a memo, a registry - state, not a value
        return f"obj@{modname}:{type(x).__qualname__}"
    try:
        return _svh(x, rec, seen)
    except Unhashable:
        if _OBJS is None:
            raise
        _OBJS.add(modname)
        return f"obj@{modname}:{type(x).__qualname__}"


def _defaults_vh(v, mn, rec, seen):
    """A function's defaults one by one: `_cache={}` is a memo (state), the numbers beside it are values."""
    out = ["d:" + _held_by(mn, d, rec, seen) for d in v.__defaults__ or ()]
    return out + [f"k:{k}=" + _held_by(mn, d, rec, seen) for k, d in sorted((v.__kwdefaults__ or {}).items())]


def _fn_vh(v, rec, seen):
    if v.__code__.co_filename == __file__ and inspect.getattr_static(v, "__wrapped__", None) is not None:
        # The framework's own wrapper (a build(), an lru_cache's call log, max / min): what it wraps, not its bookkeeping, which
        # grows as the run goes. Told by where its code is, not by __module__: functools.wraps copies the wrapped one's. Keyed as
        # what it wraps itself, so a value taken before the module is instrumented (while it is imported) keys the same.
        return _svh(v.__wrapped__, rec, seen)
    g = v.__globals__
    if not _is_project_globals(g):
        # A library function: its code by name, as a library class - but what a decorator closed over or wraps (the project function
        # behind @contextmanager, a singledispatch registry, a value passed to the decorator) is part of what it does.
        mn = g.get("__name__")
        parts = [f"fn:{v.__module__}.{v.__qualname__}"] + _defaults_vh(v, mn, rec, seen)
        parts += [k + "=" + _held_by(mn, e, rec, seen) for k, e in sorted(v.__dict__.items())]   # one by one: __wrapped__ beside
        # an unhashable registry view
        for c in v.__closure__ or ():
            try:
                parts.append(_held_by(mn, c.cell_contents, rec, seen))
            except ValueError:
                parts.append("<empty>")
        return "|".join(parts)
    names, mn = _code_names(v.__code__), g.get("__name__")
    if _DYNAMIC.intersection(names):
        if _OBJS is None:
            raise Unhashable(f"{v.__qualname__} looks names up by string")     # globals()[n]: not the names its code mentions
        _OBJS.add(mn)
    parts = [f"fn:{v.__module__}.{v.__qualname__}", _code_hash(v.__code__)] + _defaults_vh(v, mn, rec, seen)
    parts += [k + "=" + _held_by(mn, e, rec, seen) for k, e in sorted(v.__dict__.items()) if k != "__wrapped__"]   # width.base
    for c in v.__closure__ or ():
        try:
            x = c.cell_contents
        except ValueError:                          # empty cell
            parts.append("<empty>")
            continue
        parts.append(_held_by(mn, x, rec, seen))
    for n in names:                                 # module globals it reads by plain name: nothing sees those reads
        if n in g:
            parts.append(n + "=" + _held_by(mn, g[n], rec, seen))
    return "|".join(parts)


def _cls_vh(c, rec, seen):
    parts = [f"cls:{c.__module__}.{c.__qualname__}"] + [_svh(b, rec, seen) for b in c.__mro__[1:]]
    if _project_type(type(c)):
        parts.append("meta=" + _svh(type(c), rec, seen))
    if isinstance(c, enum.EnumMeta):
        parts.append("order=" + repr(list(c._member_names_)))     # members swapped: `list(Rail)` comes out in the other order
    for k, e in sorted(c.__dict__.items()):
        if k in _DUNDER_STATE or isinstance(c, enum.EnumMeta) and k in _ENUM_MAPS:
            continue                                    # an enum's _member_map_ ...: its members are in the dict themselves
        if k.startswith("__") and k.endswith("__"):
            try:
                parts.append(k + "=" + _svh(e, rec, seen))
            except Unhashable:
                if callable(e) or isinstance(e, (staticmethod, classmethod, property)):
                    raise                               # __init__, __call__ ...: what it does is the class
                parts.append(k + ":" + type(e).__qualname__)   # bookkeeping (__dataclass_fields__ ...): what it holds is in __init__
        else:
            parts.append(k + "=" + _held_by(c.__module__, e, rec, seen))
    return "(" + "|".join(parts) + ")"


def _obj_vh(v, rec, seen):
    """An enum member or a namedtuple of a project class: its class, and what it is."""
    t = type(v)
    parts = ["obj=" + _svh(t, rec, seen)]
    if isinstance(v, enum.Enum):
        parts += [v.name, _svh(v.value, rec, seen)]
    else:
        for base in (int, float, str, bytes, tuple, list, dict, set, frozenset):
            if isinstance(v, base):
                parts.append(_svh(base(v), rec, seen))      # the builtin value it is (a namedtuple's fields, an IntEnum's int)
                break
    try:
        d = object.__getattribute__(v, "__dict__")
    except AttributeError:
        d = {}
    own = {k: e for k, e in d.items() if k not in _ENUM_BOOK}
    if own:                                             # set in __init__ or later (`Rail.SHORT.width = ...`): what it carries
        parts.append(_held_by(t.__module__, own, rec, seen))
    return "(" + "|".join(parts) + ")"


_ENUM_MAPS = frozenset(("_member_names_", "_member_map_", "_value2member_map_", "_unhashable_values_", "_hashable_values_",
                        "_member_type_", "_value_repr_", "_new_member_", "_use_args_", "_boundary_", "_flag_mask_",
                        "_singles_mask_", "_all_bits_", "_inverted_"))   # enum's own maps; _missing_ and the like are hashed
_ENUM_BOOK = frozenset(("_value_", "_name_", "__objclass__", "_sort_order_", "__init__"))


_DYNAMIC = frozenset(("globals", "locals", "vars", "eval", "exec", "__dict__"))


def _code_names(co):
    out = set(co.co_names)
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            out.update(_code_names(c))      # a sorted list, not a set: `|=` would raise (a function with a comprehension in it)
    return sorted(out)


def _const_repr(c):
    """repr of a code constant, a frozenset (`kind in {"laser", "bent"}`) in sorted order: its own order changes with the hash
    seed of each process, which would make the code hash differ from run to run."""
    if isinstance(c, frozenset):
        return "frozenset({" + ", ".join(sorted(_const_repr(e) for e in c)) + "})"
    if isinstance(c, tuple) and any(isinstance(e, (frozenset, tuple)) for e in c):
        return "(" + ", ".join(_const_repr(e) for e in c) + ("," if len(c) == 1 else "") + ")"
    return repr(c)


def _code_hash(co):
    h = hashlib.sha1(co.co_code)
    for c in co.co_consts:
        h.update((_code_hash(c) if isinstance(c, types.CodeType) else _const_repr(c)).encode())
    h.update(repr(co.co_names).encode())
    return h.hexdigest()


_FILE_SHA = {}


def file_sha(rel):
    p = os.path.join(REC.root, rel)
    try:
        st = os.stat(p)
    except OSError:
        return "<missing>"
    k = (p, st.st_mtime_ns, st.st_size)
    if k not in _FILE_SHA:
        with open_real(p, "rb") as fh:
            _FILE_SHA[k] = hashlib.sha1(fh.read()).hexdigest()
    return _FILE_SHA[k]


B = None                     # build123d, imported in install()


# ============================================================ wrapping build123d (taint pass-through)
class _Depth:
    n = 0


def _wrap_callable(f, name):
    @functools.wraps(f)
    def w(*a, **k):
        if _Depth.n or not REC.on:          # inside build123d: only the outermost call matters
            _Depth.n += 1
            try:
                return f(*a, **k)
            finally:
                _Depth.n -= 1
        d = deps(a) | deps(k) if name not in NO_TAINT else EMPTY
        REC.use(d)
        _Depth.n += 1
        try:
            r = f(*a, **k)
        finally:
            _Depth.n -= 1
        if name.startswith("import_") and REC.on:
            REC.file(a[0] if a else k.get("filename"))
        if d:
            if name in MUTATORS and a:
                _tag(a[0], d)
            if r is not None and r is not NotImplemented:
                r = taint(r, d)
        return r
    w.__cad_wrapped__ = True
    return w


def _wrap_class(cls, done):
    if cls in done or not cls.__module__.startswith("build123d") or issubclass(cls, enum.Enum):
        return
    done.add(cls)
    for base in cls.__mro__[1:]:
        _wrap_class(base, done)
    for name, attr in list(cls.__dict__.items()):
        if name.startswith("__") and name not in WRAP_DUNDER:
            continue
        try:
            if isinstance(attr, staticmethod):
                setattr(cls, name, staticmethod(_wrap_callable(attr.__func__, name)))
            elif isinstance(attr, classmethod):
                setattr(cls, name, classmethod(_wrap_callable(attr.__func__, name)))
            elif isinstance(attr, property) and attr.fget is not None:
                setattr(cls, name, property(_wrap_callable(attr.fget, name), attr.fset, attr.fdel, attr.__doc__))
            elif isinstance(attr, types.FunctionType) and not getattr(attr, "__cad_wrapped__", False):
                setattr(cls, name, _wrap_callable(attr, name))
        except (TypeError, AttributeError):
            pass


# ============================================================ node records: dependencies, module globals
_SKIP_GLOBAL = (types.ModuleType, type, types.FunctionType, types.BuiltinFunctionType, functools.partial)


def _restore_globals(mod, values):
    for n, v in values.items():
        cur = vars(mod).get(n)
        if type(cur) is dict and type(v) is dict:
            cur.clear(); cur.update(v)
        elif type(cur) is list and type(v) is list:
            cur[:] = v
        elif type(cur) is set and type(v) is set:
            cur.clear(); cur.update(v)
        else:
            setattr(mod, n, v)


def _entry_deps(frame, qual_mod, fresh=frozenset()):
    """What this build() depends on, as (reads, files, mods, takes):
    - what it read itself, and what the module it is defined in read while it was imported (its own top-level code);
    - the VALUE of every name it took out of another project module - `takes`, keyed like a param, see _take_hash. Not what that
      module read while it was imported: a part that took `RAIL_Y` from a node's `_layout.py` is not rebuilt because a number only
      `PLATE_Z` came from changed;
    - a module keyed whole (a value with no hash, `vars(mod)`), or one holding an object a taken value reaches (an instance, a shape:
      not hashed by its state, which may be a cache filling as the run goes): the import-time reads, and the names taken by value, of
      every module of this build() whose imports reach it - what made the object, and what may have set it up since;
    - for a project module it uses but took nothing from (imported for a side effect), what that module read while imported;
    - files: every module of the closure, and every file any of them opened while imported - a code change still rebuilds every
      user, and the router ships a job folder by this list.
    A module the build() itself imported (`fresh`: imported inside it, not at the top of a file) is not there yet when the next run
    checks this record, so nothing it holds can be compared then: it is keyed as before #18, by what it read while imported and by
    its file - a node's params.py so imported, by its file too.
    Imports are never cached - every run imports every module afresh - so what a cached build() owes to a module is exactly the
    values it took from it."""
    mods = {m for m in REC.closure(frame.mods | {qual_mod}) if not _is_params(m)}   # a node's params.py, listed as a module by a
    reads, files = set(frame.reads), set(frame.files)                               # record of the previous version: its values
    for m in mods:                                                                  # are keyed read by read now
        fr = REC.imports.get(m)
        if fr is None:
            return None                     # a module we did not see being imported: cannot vouch for it
        files |= fr.files
        f = getattr(sys.modules.get(m), "__file__", None)
        if f:
            files.add(os.path.relpath(os.path.abspath(f), REC.root))
    own = REC.imports[qual_mod]
    reads |= own.reads
    # A node's params.py is keyed value by value, never by its file - but the code it imported and the files it opened are code and
    # data like any module's: a part reading one of its values rebuilds when they change.
    nodep, byfile = set(), set(fresh)
    for p in list(reads):
        if p and isinstance(p[0], str) and p[0].startswith(_AT):
            nodep.add(p[0][len(_AT):])
            if REC.value_hash(p) == "<unhashable>":     # a Pos, an Axis: no value to compare, ever - keyed by where it came from
                reads.discard(p)
                byfile.add(p[0][len(_AT):])
    takes, whole, taken, np_gets, np_side = set(), set(), set(), set(), set()
    for m in REC.closure(nodep):
        fr = REC.imports.get(m)
        if fr is not None:
            files |= fr.files
            f = getattr(sys.modules.get(m), "__file__", None)
            if f and (not _is_params(m) or m in byfile):
                files.add(os.path.relpath(os.path.abspath(f), REC.root))
            if m in byfile and fr.sunk:
                # imported by this build() (its values cannot be compared before the next one runs), or a value with no hash: keyed
                # by what it came from - its file, the params it read and the names it took
                reads |= fr.sunk[0]
                np_gets |= fr.sunk[1]
                np_side |= fr.mods
    gets, done, reached = frame.gets | own.gets | np_gets, set(), set()
    used = REC.closure(frame.mods | own.mods | {qual_mod} | nodep)
    pending = {x for x in used if x in REC.mutators or REC.closure({x}) & REC.mutators}
    while True:
        objs = set()
        for m, n in gets - done:
            mod = sys.modules.get(m)
            if m == qual_mod or mod is None or not REC.is_project(mod):
                continue
            taken.add(m)
            if n == "*" or m in fresh:
                whole.add(m)                # read through vars(mod) / mod.__dict__, or imported by this build: the module whole
                continue
            v = vars(mod).get(n, _NONE)
            if isinstance(v, (types.ModuleType, TrackedDict)):
                continue                    # a module: what is taken out of it is a take of its own; a params view: read by path
            h = _take_hash(m, n)
            if h == _UNKEYED:
                whole.add(m)                # no value to key by (an object, a shape): the module whole
            else:
                takes.add((m, n))
                if h != "<missing>":
                    objs |= {o for o in REC._tobjs.get((m, n), ()) if o}
        done |= gets
        # A module keyed whole, or one holding an object a taken value reaches (an instance, a shape - not hashed by its state,
        # which may be a cache filling as the run goes): what it holds is what its import made of it, and what any import reaching
        # it did to it since (a sibling setting a class attribute up). So every module of this build() whose imports reach one of
        # them brings what its own import read, and the names its import took - keyed by value, or reaching further, the same way.
        whole |= objs
        reach = {d for d in REC.closure(whole) if not _is_params(d)}
        new = {x for x in REC.closure(frame.mods | own.mods | {qual_mod} | nodep | whole)
               if x not in reached and (x in reach or REC.closure({x}) & reach)} | (pending - reached)
        pending = set()
        if not new:
            break
        reached |= new
        for x in new:
            fr = REC.imports.get(x)
            if fr is None:
                continue
            files |= fr.files
            f = getattr(sys.modules.get(x), "__file__", None)
            if f:
                files.add(os.path.relpath(os.path.abspath(f), REC.root))
            if fr.sunk:                     # a node's params.py: by its file and what its import read and took
                reads |= fr.sunk[0]
                gets |= fr.sunk[1]
            else:
                reads |= fr.reads
                gets |= fr.gets
    side = {m for m in (frame.mods | own.mods | np_side) - taken - {qual_mod} if not _is_params(m)}
    for m in REC.closure(side):
        fr = REC.imports.get(m)
        if fr is not None and not _is_params(m):
            reads |= fr.reads
            files |= fr.files
    return reads, files, mods, takes


# A name taken out of a project module is keyed by its value, like a param. Strictly: a value of a kind whose content `vh` cannot
# read in full (an object with a default or abridged repr, an array) is not keyed at all - `_entry_deps` falls back to the module's
# import-time reads for it. A function is keyed by its code, its defaults and closure, and the values of the module-level names
# its code mentions (it reads those by plain name, which nothing sees at run time), so `def label(): return LABEL` follows LABEL.
_NONE = object()
_UNKEYED = "<unkeyed>"
_UNIMPORTED = object()           # a module not imported yet (a build() imports it inside): no value to compare, ever
_UNIMPORTED_H = "<unimported>"   # ... and the hash that stands for it: never stored, never equal to one that is


def _params_file(path):
    """The file of the node params module a read path names, as a record's `files` lists it (or None)."""
    if not (path and isinstance(path[0], str) and path[0].startswith(_AT)):
        return None
    parts = path[0][len(_AT):].split(".")
    return os.path.relpath(os.path.join(REC.cad, *parts) + ".py", REC.root)


def _is_project_globals(g):
    """Whether `g` is the globals of a module under mechanics/cad (a project or a params module)."""
    f = g.get("__file__")
    return isinstance(f, str) and os.path.abspath(f).startswith(REC.cad + os.sep)


def _take_hash(m, n):
    key = (m, n)
    h = REC._th.get(key)
    if h is None:
        mod = sys.modules.get(m)
        if mod is None:
            return _UNIMPORTED_H                # never equal to a stored hash, and not cached: a build() may import it next
        v = vars(mod).get(n, _NONE)
        if v is _NONE and "__getattr__" in vars(mod):
            h = _UNKEYED                        # a module __getattr__ (PEP 562) makes it: the module whole
        elif v is _NONE:
            h = "<missing>"
        else:
            global _OBJS, _HOME
            _OBJS = objs = set()
            _HOME = m
            try:
                h = hashlib.sha1(vh(v, REC, set(), strict=True).encode()).hexdigest()
            except Exception:                   # Unhashable, a RecursionError, an object's own __getattr__ raising
                h = _UNKEYED
            finally:
                _OBJS = _HOME = None
            REC._tobjs[key] = objs
        REC._th[key] = h
    return h


def _lru_replay(fn, qual):
    """A functools.lru_cache function: the first call records its reads, every later (cached) call replays them."""
    seen = {}

    @functools.wraps(fn)
    def w(*a, **k):
        was, REC.on = REC.on, False
        try:
            key = pickle.dumps((a, sorted(k.items())))
        except Exception:
            key = None
        finally:
            REC.on = was
        if key is not None and key in seen:
            REC.replay(*seen[key])
            return fn(*a, **k)
        fr = REC.push("call", qual)
        try:
            r = fn(*a, **k)
        finally:
            REC.pop(fr)
        if key is not None:
            seen[key] = (set(fr.reads), set(fr.files), set(fr.mods), set(fr.gets))
        return r
    return w


# ============================================================ hooks: imports, files, math
open_real = builtins.open
_import_real = builtins.__import__
PARTS_PKG = "parts"
BUILD_RE = re.compile(r"build(_\w+)?")


def _open_hook(file, mode="r", *a, **k):
    if REC.on and isinstance(file, (str, bytes, os.PathLike)) and not any(c in mode for c in "wax+"):
        REC.file(file)
    return open_real(file, mode, *a, **k)


def _taintable(v):
    t = type(v)
    if t in (float, int, dict):                # a str is NOT taintable: concatenation / join / formatting would lose the taint,
        return True                            # so it is recorded as read at import time (an extra rebuild is cheap, stale output is not)
    if t is tuple or (isinstance(v, tuple) and hasattr(v, "_fields")):
        return all(_taintable(e) for e in v)
    return isinstance(v, types.FunctionType) and v.__module__ == REC.params_name


def _star_view(params):
    """--model: what `from params import *` copies. Numbers, tuples of numbers, dicts and functions come tainted with their param
    name but are NOT recorded as read: they count as read where they are used (REC.use). Anything that cannot carry a taint
    reliably (a list, an object, a string) is recorded now, as before."""
    g = vars(params)
    names = g.get("__all__") or [n for n in g if not n.startswith("_")]
    view = types.SimpleNamespace(__all__=list(names))
    for n in names:
        v = g[n]
        view.__dict__[n] = REC.wrap_read((n,), v, rec=not (_taintable(v) or isinstance(v, types.ModuleType)))
    return view


def _import_hook(name, globals=None, locals=None, fromlist=(), level=0):
    m = _import_real(name, globals, locals, fromlist, level)
    if REC.on and fromlist and "*" in fromlist and m is REC.params:
        return _star_view(m)
    if REC.on:
        try:
            absname = importlib.util.resolve_name("." * level + name, (globals or {}).get("__package__")) if level else name
        except Exception:
            absname = name
        mod = sys.modules.get(absname)
        subs = [getattr(mod, n, None) for n in fromlist or ()]
        if not (subs and hasattr(mod, "__path__") and all(isinstance(s, types.ModuleType) for s in subs)):
            REC.use_module(mod)                 # `from . import rail` uses rail, not the node's __init__ (its placements)
        for s in subs:
            REC.use_module(s)
    return m


_exec_real = importlib.machinery.SourceFileLoader.exec_module


def _exec_hook(self, module):
    if _is_params(module.__name__) and module.__name__ != REC.params_name \
            and os.path.abspath(getattr(module, "__file__", "") or "").startswith(REC.cad + os.sep):
        # A node's params.py: like the machine's, what IT reads is nobody's input - what a part reads is, and that is the value
        # this module ends up holding. Its own reads go to a frame that is thrown away; recording stays ON, so a project module
        # it imports first is still instrumented (its build() wrapped, its own reads kept) exactly as if a part had.
        fr = REC.push("sink", module.__name__)
        try:
            _exec_real(self, module)
        finally:
            REC.pop(fr)
        fr.sunk = (fr.reads, fr.gets)           # kept aside: a build() that imports it itself is keyed by them (_entry_deps)
        if _changes_others(module):
            REC.mutators.add(module.__name__)
        fr.reads, fr.gets = set(), set()        # kept: the modules it imported and the files it opened, for the parts reading it
        REC.imports[module.__name__] = fr
        REC._vh = {p: h for p, h in REC._vh.items() if p[0] != _AT + module.__name__}
        for n, v in list(vars(module).items()):    # its lru_cache functions: every caller gets the reads, as in a project module
            if _defined_in(v) == module.__name__ and _is_lru(v):
                v.cache_clear()
                module.__dict__[n] = _lru_replay(v, f"{module.__name__}.{n}")
        module.__class__ = NodeParamsModule
        REC.param_mods[module.__name__] = module
        return
    if not (REC.on and REC.is_project(module)):
        return _exec_real(self, module)
    module.__dict__["max"] = _tmax(builtins.max)
    module.__dict__["min"] = _tmax(builtins.min)
    if _changes_others(module):
        REC.mutators.add(module.__name__)
    # Traced before its code runs: a module importing it back while it is half done (`from . import SPAN` in a part the node's
    # __init__ imports) takes SPAN through the proxy like any other take.
    module.__class__ = ProjectModule
    fr = REC.push("import", module.__name__)
    try:
        _exec_real(self, module)
    finally:
        REC.pop(fr)
    _instrument(module)
    REC._th = {k: h for k, h in REC._th.items() if k[0] != module.__name__}


def _changes_others(module):
    """Whether a module's code may change something outside its own names: it sets an attribute or item of anything (a library
    class patched, another module's object set up, `self.x = ...` in a method another import calls), deletes one, or reaches for
    setattr / exec / globals(). Read anywhere in the file - a function it defines may run while some module is imported. Such a
    module, and every module whose imports reach one, keeps its import-time reads and takes in every build() using it, as before
    #18 (_entry_deps). A change made by calling a library function cannot be seen here: the contract says an import changes
    nothing but its own names (SKILL.md)."""
    try:
        with open_real(module.__file__, encoding="utf-8") as fh:   # not the hooked open(): no part opened this file
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError, ValueError, TypeError):
        return True
    for n in ast.walk(tree):
        if isinstance(n, (ast.Attribute, ast.Subscript)) and isinstance(n.ctx, (ast.Store, ast.Del)):
            return True
        if isinstance(n, ast.Name) and n.id in _CHANGERS:
            return True
    return False


_CHANGERS = frozenset(("setattr", "delattr", "exec", "eval", "compile", "globals", "vars", "__import__", "importlib"))


def _defined_in(v):
    """The module a function, class or lru_cache was defined in - without running an object's own __getattr__ (it may raise)."""
    if isinstance(v, (types.FunctionType, type)):
        return v.__module__
    m = inspect.getattr_static(v, "__module__", None)
    return m if isinstance(m, str) else None


def _is_lru(v):
    return callable(inspect.getattr_static(type(v), "cache_info", None)) and inspect.getattr_static(v, "__wrapped__", None) is not None


def _instrument(mod):
    g = vars(mod)
    in_parts = mod.__name__ == PARTS_PKG or mod.__name__.startswith(PARTS_PKG + ".")
    leaf = mod.__name__.rsplit(".", 1)[-1]
    for n, v in list(g.items()):
        if _defined_in(v) != mod.__name__:
            continue
        if _is_lru(v):
            v.cache_clear()                     # warmed while the module was imported: the first build() calling it records its reads
            g[n] = _lru_replay(v, f"{mod.__name__}.{n}")
        elif in_parts and isinstance(v, types.FunctionType) and BUILD_RE.fullmatch(n) and not leaf.startswith("_"):
            if not _in_folder(mod.__name__):
                rel = os.path.relpath(os.path.abspath(mod.__file__), REC.cad)
                sys.exit(f"[cad_incremental] {rel} is an old-layout node (a module with {n}() in parts/): a node is a folder "
                         f"parts/<node>/ with __init__.py placing its parts and one <part>.py per part; a helper module is "
                         f"parts/_<name>.py")
            if n == "build":
                g[n] = model_build(v)


def _name_joint_errors(cls):
    """A docking value outside a joint's range (build123d: "position (100) must in range of (-60, 60)") names the parts and joints."""
    f = cls.connect_to
    if getattr(f, "__cad_named__", False):
        return

    def connect_to(self, other, **kw):
        try:
            return f(self, other, **kw)
        except ValueError as e:
            who = lambda j: f"part {(MODEL.names.get(id(j.parent)) if MODEL else None) or getattr(j.parent, 'label', '') or '?'!r} joint {j.label!r}"  # noqa: E731
            why = " (out of the joint's range)" if "range" in str(e) else ""
            raise ValueError(f"docking {who(self)} to {who(other)}: {e}{why}") from None
    connect_to.__cad_named__ = True
    cls.connect_to = connect_to


def install(cad, params_name="params"):
    """Arm everything; returns the (recording) params module. Call before the project's own modules are imported."""
    global B
    import build123d
    B = build123d
    REC.cad = os.path.abspath(cad)
    REC.root = os.path.abspath(os.path.join(cad, "..", "..")) if os.path.basename(os.path.dirname(REC.cad)) == "mechanics" else REC.cad
    REC.params_name = params_name
    for n in ("sin", "cos", "tan", "asin", "acos", "atan", "atan2", "hypot", "sqrt", "radians", "degrees", "exp", "log",
              "log10", "fabs", "copysign", "fmod", "pow", "floor", "ceil", "dist"):
        if hasattr(math, n) and not getattr(getattr(math, n), "__cad_taint__", False):
            setattr(math, n, _taint_fn(getattr(math, n)))
    done = set()
    for n in dir(build123d):
        v = getattr(build123d, n)
        if isinstance(v, type):
            _wrap_class(v, done)
        elif isinstance(v, types.FunctionType) and v.__module__.startswith("build123d") and not getattr(v, "__cad_wrapped__", False):
            setattr(build123d, n, _wrap_callable(v, n))
    for n in JOINT_KINDS:
        _name_joint_errors(getattr(build123d, n))
    builtins.open = io.open = _open_hook
    builtins.__import__ = _import_hook
    importlib.machinery.SourceFileLoader.exec_module = _exec_hook
    if REC.cad not in sys.path:
        sys.path.insert(0, REC.cad)
    params = importlib.import_module(params_name)
    params.__class__ = ParamsModule
    REC.params = params
    REC.param_mods[params_name] = params
    REC.on = True
    return params


# ============================================================ --model: node-level build, one STEP per part
class ModelState:
    def __init__(self, root):
        self.root = root
        self.dir = os.path.join(root, "build", "model")
        self.old = {}                  # nodes.json of the last build: qual -> record
        self.fresh = {}                # records valid in this run
        self.order = {}                # qual -> None, in order of first call
        self.rebuilt = []              # quals whose build() ran
        self.seen = set()
        self.issued = {}               # id(part dict handed to node code) -> (qual, part id, dict)
        self.shapes = {}               # (qual, part id) -> {id(shape): shape} of every copy handed out (a placed copy keeps one)
        self.top = {}                  # quals called at depth 0 (by the entry script itself, any arguments): the model's roots
        self.depth = 0
        self.calls = []                # per running build(): the quals of the build()s it called
        self.unkeyed = {}              # base qual -> calls with a non-JSON argument so far in this run
        self.root_items = {}           # id -> part dict a root build() returned (kept alive): what the entry script may hold
        self.warned = set()
        self.names = {}                # id(shape handed out) -> its part id (an error about a joint names the part)
        self.env = VERSION
        self.env_read = (VERSION,)    # the envs whose records this run may reuse (see _model_run)


MODEL = None
_STEP_TS = re.compile(rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")
_STEP_NAUO = re.compile(rb"(NEXT_ASSEMBLY_USAGE_OCCURRENCE\(')\d+'")      # OCC numbers these with a process-wide counter


def _step_normalise(data):
    n = iter(range(1, 10**9))
    data = _STEP_TS.sub(b"2000-01-01T00:00:00", data, count=1)
    return _STEP_NAUO.sub(lambda m: m.group(1) + str(next(n)).encode() + b"'", data)


def _file_sha256(path):
    with open_real(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _part_id(name):
    return re.sub(r"_+", "_", re.sub(r"[^\w]+", "_", str(name))).strip("_")


def _step_bytes(shape):
    """The normalised STEP bytes of a shape (deterministic header and instance numbering)."""
    d = os.path.join(MODEL.dir, "step")
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".tmp-{os.getpid()}.step")
    try:
        B.export_step(shape, tmp)
        with open_real(tmp, "rb") as fh:
            return _step_normalise(fh.read())
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _same_geometry(shape, path):
    """Fresh shape vs a stored STEP: volume and bounding box within noise a different CPU / OCC build can produce."""
    try:
        old = B.import_step(path)
        a, b = shape.bounding_box(), old.bounding_box()
        pairs = [(shape.volume, old.volume), (a.min.X, b.min.X), (a.min.Y, b.min.Y), (a.min.Z, b.min.Z),
                 (a.max.X, b.max.X), (a.max.Y, b.max.Y), (a.max.Z, b.max.Z)]
        return all(math.isclose(x, y, rel_tol=1e-6, abs_tol=1e-4) for x, y in pairs)
    except Exception:
        return False


def _invalidate_derived(key):
    """Delete derived results that list this part key (their meta.json), after its STEP changed in place."""
    import shutil
    dd = os.path.join(MODEL.root, "build", "derived")
    for n in os.listdir(dd) if os.path.isdir(dd) else []:
        p = os.path.join(dd, n)
        try:
            with open(os.path.join(p, "meta.json")) as fh:
                listed = json.load(fh)["parts"]
        except (OSError, ValueError, KeyError):
            continue
        if key in {x if isinstance(x, str) else x.get("key") for x in listed}:
            shutil.rmtree(p, ignore_errors=True)


def _write_step(shape, key, short=None, data=None):
    """One STEP per part, named by the part key (hash of the producing node's inputs + part id), not by its bytes: another
    machine's STEP of the same geometry differs in the last bits and is just as valid. A file that exists is kept when the
    node just ran and produced the same geometry; if it produced different geometry, the node has an input its key cannot
    see: the file is replaced, derived results of the key are dropped and the node is named in a warning."""
    d = os.path.join(MODEL.dir, "step")
    dst = os.path.join(d, key + ".step")
    exists = os.path.exists(dst)
    if exists and _same_geometry(shape, dst):
        return
    if data is None:
        data = _step_bytes(shape)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".tmp-{os.getpid()}.step")
    with open_real(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, dst)
    if exists:
        _invalidate_derived(key)
        print(f"[cad_incremental] warning: {short or key} produced different geometry than the stored STEP of the same key: "
              f"it has an input its key cannot see (a module outside mechanics/cad, a file outside the project, ...); "
              f"the STEP was replaced and its derived results dropped", file=sys.stderr)


def _node_fp(rec):
    """Fingerprint of everything a node's parts depend on: what it read, the files it opened (its module and the project
    modules it imported, by content), the state it found, and, recursively, the build()s whose parts it returned."""
    callees = _callees(rec) or {}
    body = {"env": rec["env"], "reads": rec["reads"], "files": rec["files"], "mods": rec["mods"], "wpre": rec["wpre"],
            "callees": sorted([q, c.get("fp")] for q, c in callees.items()), "shas": rec.get("shas")}
    if rec.get("args"):                                 # a variant: the same inputs with other arguments are another part
        body["args"] = rec["args"]
    if rec.get("takes"):                                # the values it took out of project modules
        body["takes"] = rec["takes"]
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _part_key(fp, pid):
    return hashlib.sha256(f"{fp}|{pid}".encode()).hexdigest()


def _read_step(key, label, color):
    sh = B.import_step(os.path.join(MODEL.dir, "step", key + ".step"))
    if label:
        sh.label = label
    if color:
        sh.color = B.Color(*color)
    return sh


# A reused part hands node code a lazy shape: a subclass of the real class (isinstance and every method behave as for the real
# shape), but the STEP is read back on the first access of anything except label / color / joints / location, which the record
# already holds. A no-change run that never touches the shapes therefore reads no STEP at all. `sh.__class__` is answered from the
# record too (the class the STEP reads back as), so `sh.__class__ is Solid` agrees with a fresh shape and, more to the point, an
# isinstance() the shape fails costs nothing: CPython falls back to obj.__class__ there, and that used to read back the STEP of
# every reused part whose geometry nobody wanted. The one thing that cannot be made lazy is the C-level `type(sh)`: until
# the first load it is the stand-in subclass (same name), so `type(sh) is Solid` is False; dispatch on isinstance() or
# `sh.__class__` instead (after a load `type(sh)` is the real class). The record says what the first eager read found
# (p["lazy"]); a record without it is read eagerly once and upgraded.
_LAZY_CLASSES = {}


def _lz_load(self):
    d = object.__getattribute__(self, "__dict__")
    path, label, color = d["_lz"][:3]
    real = B.import_step(path)                          # a failed read leaves _lz in place: every access raises the real error
    if label:
        real.label = label
    if color:
        real.color = B.Color(*color)
    d.pop("_lz")
    joints, loc = d.pop("_lz_joints", None), d.pop("_lz_loc", None)
    d.update(real.__dict__)
    d["_cad_loc0"] = _loc_matrix(real.location)         # where it was read: a node that moves it in place is caught
    for c in d.get("_NodeMixin__children", ()):         # a STEP root with parts: its children point at the stand-in now
        c.__dict__["_NodeMixin__parent"] = self
    object.__setattr__(self, "__class__", type(real))
    if joints is not None:                              # the joints node code already holds (and may have docked) stay the same objects
        d["joints"] = joints
    if loc is not None:
        self.location = loc


def _lz_getattribute(self, name):
    d = object.__getattribute__(self, "__dict__")
    lz = d.get("_lz")
    if lz is not None and name not in ("__dict__", "_lz"):
        if name == "label":
            return lz[3]
        if name == "color":
            return lz[4]
        if name == "joints":                            # docking (connect_to) and place() need no geometry: no STEP read
            return d.setdefault("_lz_joints", {})
        if name == "location":
            return d.get("_lz_loc") or B.Location()
        if name == "locate":
            return lambda loc: _lz_setattr(self, "location", loc) or self
        if name == "__class__":                 # what the STEP reads back as, from the record: isinstance() and `sh.__class__`
            return lz[5]                        # ask about the class, not about the geometry
        _lz_load(self)
    return object.__getattribute__(self, name)


def _lz_setattr(self, name, value):
    d = object.__getattribute__(self, "__dict__")
    if "_lz" in d:
        if name in ("location", "joints"):
            d["_lz_loc" if name == "location" else "_lz_joints"] = value
            return
        _lz_load(self)
    object.__setattr__(self, name, value)


def _lazy_class(cls):
    lc = _LAZY_CLASSES.get(cls)
    if lc is None:
        lc = _LAZY_CLASSES[cls] = type(cls.__name__, (cls,), {"__getattribute__": _lz_getattribute, "__setattr__": _lz_setattr,
                                                               "__module__": cls.__module__, "__qualname__": cls.__qualname__})
    return lc


def _lazy_info(sh):
    """What a lazy stand-in needs to know about the shape a STEP reads back as."""
    c = sh.color
    return {"cls": f"{type(sh).__module__}:{type(sh).__qualname__}", "label": sh.label or "",
            "color": None if c is None else [float(x) for x in c]}


def _lazy_shape(key, label, color, info):
    mod, qual = info["cls"].split(":")
    cls = importlib.import_module(mod)
    for part in qual.split("."):
        cls = getattr(cls, part)
    sh = object.__new__(_lazy_class(cls))
    col = None if info["color"] is None else B.Color(*info["color"])
    object.__getattribute__(sh, "__dict__")["_lz"] = (os.path.join(MODEL.dir, "step", key + ".step"), label, color,
                                                      info["label"], col, cls)
    return sh


def _enc(v):
    """Type-preserving JSON form of a part field / written value (tuples and non-string dict keys survive a round trip)."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return int.__int__(v)
    if isinstance(v, float):
        return float.__float__(v)
    if isinstance(v, str):
        return str.__str__(v)
    if isinstance(v, list):
        return [_enc(e) for e in list.__iter__(v)]
    if isinstance(v, tuple) and type(v) is tuple:
        return {"__t": "tuple", "v": [_enc(e) for e in v]}
    if isinstance(v, dict):
        return {"__t": "dict", "v": [[_enc(k), _enc(e)] for k, e in dict.items(v)]}
    if type(v) in (set, frozenset):
        return {"__t": type(v).__name__, "v": sorted((_enc(e) for e in v), key=lambda e: json.dumps(e, sort_keys=True))}
    raise TypeError(f"{type(v).__name__} is not JSON-serialisable")


def _dec(e):
    if isinstance(e, list):
        return [_dec(x) for x in e]
    if isinstance(e, dict):
        if e["__t"] in ("set", "frozenset"):
            return (set if e["__t"] == "set" else frozenset)(_dec(x) for x in e["v"])
        v = [_dec(x) for x in e["v"]] if e["__t"] == "tuple" else {_dec(k): _dec(x) for k, x in e["v"]}
        return tuple(v) if e["__t"] == "tuple" else v
    return e


def _dec_fields(fields):
    return {k: _dec(e) for k, e in fields.items()}


def _fp(v):
    """Fingerprint of a global: its JSON form, or None when it has none (then only a rebinding can be seen)."""
    try:
        return json.dumps(_enc(v), sort_keys=True)
    except (TypeError, ValueError, RecursionError):
        return None


def _gscope():
    out = {REC.params_name: REC.params}
    out.update({n: m for n, m in REC.param_mods.items() if n not in out})   # a node's params.py is written to like any module
    for n, m in list(sys.modules.items()):
        if m is not None and n not in out and isinstance(m, types.ModuleType) and REC.is_project(m):
            out[n] = m
    return out


_ABSENT = (None, None, None)


def _hfp(v):
    """A global with no JSON form: its strict value hash, so a change in place is seen (None: not even that)."""
    try:
        return vh(v, REC, set(), strict=True)
    except Exception:
        return None


def _gsnap():
    snap = {}
    for mn, mod in _gscope().items():
        for n, v in list(vars(mod).items()):
            if not (n.startswith("__") or isinstance(v, _SKIP_GLOBAL) or callable(v)):
                fp = _fp(v)
                snap[(mn, n)] = (fp, id(v), None if fp is not None else _hfp(v))
    return snap


def _gdiff(before):
    """What the call wrote into params / project modules that were already loaded:
    ({module: {name: encoded value}}, {param name: value it found}, first write without a JSON form or None)."""
    writes, pre, bad = {}, {}, None
    for mn in {m for m, _ in before}:
        mod = sys.modules.get(mn)
        for n, v in list(vars(mod).items()):
            if n.startswith("__") or isinstance(v, _SKIP_GLOBAL) or callable(v):
                continue
            fp0, id0, h0 = before.get((mn, n), _ABSENT)
            fp1 = _fp(v)
            if fp1 is not None and fp1 == fp0:
                continue
            if fp1 is None and id(v) == id0:
                if h0 is not None and _hfp(v) != h0:
                    bad = bad or f"{mn}.{n}"    # changed in place, and there is no form to replay it from
                continue
            if fp1 is None:
                bad = bad or f"{mn}.{n}"
                continue
            writes.setdefault(mn, {})[n] = _enc(v)
            if mn in REC.param_mods:            # the machine's params by name; a node's by its own path (`@module`, name)
                k = n if mn == REC.params_name else (_AT + mn, n)
                if (mn, n) not in before:
                    pre[k] = "<missing>"
                elif fp0 is None:
                    bad = bad or f"{mn}.{n}"
                else:
                    pre[k] = _dec(json.loads(fp0))
    return writes, pre, bad


def _pre_hit(path, pre):
    """Whether a read path names a params value the running build overwrote (its key is the value it found, see _gdiff)."""
    return path[0] in pre or len(path) > 1 and (path[0], path[1]) in pre


def _written_names():
    names = set()
    for recs in (MODEL.old, MODEL.fresh):
        for r in recs.values():
            for mn, ws in (r.get("writes") or {}).items():
                if mn != REC.params_name:
                    names.update(f"{mn}.{n}" for n in ws)
    return names


def _replay_writes(rec):
    if rec.get("writes"):
        REC._th.clear()
    for mn, ws in (rec.get("writes") or {}).items():
        mod = sys.modules.get(mn)
        _restore_globals(mod, {n: _dec(e) for n, e in ws.items()})
        if mod is REC.params:
            REC._vh = {p: h for p, h in REC._vh.items() if p[0] not in ws}
        elif mn in REC.param_mods:              # a node's params.py, restored in place (a dict, a list): drop its cached hashes
            REC._vh = {p: h for p, h in REC._vh.items() if not (p[0] == _AT + mn and len(p) > 1 and p[1] in ws)}


def _callees(rec, acc=None):
    """Records of every build() whose parts `rec` returns (recursively), or None when one has no record."""
    acc = {} if acc is None else acc
    for r in rec["ret"]:
        if r[0] == "place" and r[1] not in acc:
            crec = MODEL.fresh.get(r[1]) or MODEL.old.get(r[1])
            if crec is None or _callees(crec, acc) is None:
                return None
            acc[r[1]] = crec
    return acc


def _steps_exist(rec):
    return all(os.path.isfile(_mesh_path(p["key"])) if p.get("mesh") else
               p.get("rowonly") or os.path.isfile(os.path.join(MODEL.dir, "step", p["key"] + ".step")) for p in rec["parts"])


def _mesh_path(key):
    return os.path.join(MODEL.dir, "mesh", key + ".json")


def _mesh_data(m, name, short):
    """A part's `mesh` {"v": [[x, y, z], ...], "f": [[i, j, k], ...]} (mm, machine coordinates) as flat lists, checked."""
    try:
        if any(len(p) != 3 for p in m["v"]) or any(len(t) != 3 for t in m["f"]):
            raise ValueError
        v = [float(c) for p in m["v"] for c in p]
        f = [int(i) for t in m["f"] for i in t]
    except (TypeError, KeyError, ValueError):
        sys.exit(f"[cad_incremental] part {name!r} of {short}: 'mesh' must be {{'v': [[x, y, z], ...], 'f': [[i, j, k], ...]}}")
    if not f or len(v) % 3 or len(f) % 3 or max(f) >= len(v) // 3 or min(f) < 0:
        sys.exit(f"[cad_incremental] part {name!r} of {short}: 'mesh' has no triangles or an index out of its vertices")
    return {"v": v, "f": f}


def _write_mesh(md, key, short=None):
    """The triangles of a mesh part under its key. A view-only mesh is keyed by its triangles, so an existing file is that one; a
    solid mesh part is keyed like a STEP part, by its node's inputs, and a file that exists with other triangles is
    replaced the way _write_step replaces a STEP (its derived results dropped, the node named)."""
    dst = _mesh_path(key)
    if os.path.isfile(dst) and (short is None or _same_mesh(md, dst)):
        return
    data = json.dumps(md, separators=(",", ":"))
    exists = os.path.isfile(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = f"{dst}.tmp-{os.getpid()}"
    with open_real(tmp, "w") as fh:
        fh.write(data)
    os.replace(tmp, dst)
    if exists:
        _invalidate_derived(key)
        print(f"[cad_incremental] warning: {short} produced other triangles than the stored mesh of the same key: it has an input "
              f"its key cannot see; the mesh was replaced and its derived results dropped", file=sys.stderr)


def _is_mesh_shape(sh):
    """A meshpart.Mesh: a part whose geometry is its closed triangles (an empty Compound carrying them). Duck-typed: a
    Mesh of a meshpart imported under another name is one too, never an empty STEP."""
    return isinstance(sh, B.Compound) and all(hasattr(sh, a) for a in ("mesh_vertices", "mesh_triangles", "problem"))


def _solid_mesh_data(sh, name, short):
    """The triangles as the part stands in its own coordinates: moved by the shape's location, as export_step bakes it into a
    STEP part's geometry (its joints are recorded in that same frame)."""
    bad = sh.problem()
    if bad:
        sys.exit(f"[cad_incremental] part {name!r} of {short}: a meshpart.Mesh must be a closed solid, and {bad}")
    import numpy as np
    import meshpart
    v = meshpart.moved(sh.mesh_vertices, sh.location)
    return {"v": np.round(v, 5).reshape(-1).tolist(), "f": sh.mesh_triangles.reshape(-1).tolist()}


def _same_mesh(md, path):
    """A stored mesh against fresh triangles: volume and box within the noise another CPU or manifold3d build can give (the
    STEP rule of _same_geometry), not byte for byte."""
    try:
        import numpy as np
        with open_real(path) as fh:
            old = json.load(fh)
        vols, boxes = [], []
        for m in (md, old):
            v, f = np.array(m["v"], float).reshape(-1, 3), np.array(m["f"], np.int64).reshape(-1, 3)
            vols.append(np.einsum("ij,ij->i", v[f[:, 0]], np.cross(v[f[:, 1]], v[f[:, 2]])).sum() / 6)
            boxes.append(np.concatenate([v.min(0), v.max(0)]))
        return math.isclose(vols[0], vols[1], rel_tol=1e-6, abs_tol=1e-3) and np.allclose(boxes[0], boxes[1], atol=1e-4)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _read_solid_mesh(key, label, color):
    import meshpart
    v, f = meshpart.read(_mesh_path(key))
    sh = meshpart.Mesh(vertices=v, triangles=f)
    if label:
        sh.label = label
    if color:
        sh.color = B.Color(*color)
    return sh


@contextlib.contextmanager
def _writes_applied(rec):
    """While a reused record's takes are checked: the params and module globals its build wrote (its own and its callees'), as it
    left them - the state its takes were hashed in, at the end of that build. Restored after."""
    saved = []
    try:
        for mn, ws in (rec.get("writes") or {}).items():
            mod = sys.modules.get(mn)
            if mod is None:
                continue
            g = vars(mod)
            saved.append((mod, {n: _held(g.get(n, _NONE)) for n in ws}))
            _restore_globals(mod, {n: _dec(e) for n, e in ws.items()})
        if saved:
            REC._th.clear()
            REC._vh.clear()
        yield
    finally:
        for mod, old in reversed(saved):
            for n, (v, items) in old.items():
                if v is _NONE:
                    vars(mod).pop(n, None)
                    continue
                if type(v) is list:
                    v[:] = items
                elif items is not None:
                    v.clear(); v.update(items)
                if vars(mod).get(n, _NONE) is not v:
                    setattr(mod, n, v)
        if saved:
            REC._th.clear()
            REC._vh.clear()


def _held(v):
    """A global as it is now - the object, and the items of a dict / list / set, which _restore_globals refills in place."""
    return v, (type(v)(v) if type(v) in (dict, list, set) else None)


def _node_valid(rec, _seen=None, _written=frozenset(), _above=()):
    """Whether a record may be reused. `_written`: (module, name) a node above writes - checked by THAT node's key (its wpre and
    the reads behind its writes; its writes are deterministic), so not here, where they would be compared in the wrong state."""
    if not rec.get("cacheable") or rec.get("env") not in MODEL.env_read:
        return False
    files = {r for r, _h in rec["files"]}
    own = {(mn, n) for mn, ws in (rec.get("writes") or {}).items() for n in ws}
    if any(r[0] not in ("own", "place", "row") for r in rec["ret"]) or \
            rec.get("kind") != "part" and not all(p.get("rowonly") for p in rec["parts"]):
        return False                                    # recorded before #30 (a node that built a shaped part): run it again
    callees = _callees(rec)
    if callees is None or not all(_steps_exist(c) and c.get("cacheable") for c in callees.values()):
        return False
    for p, h in rec["reads"]:
        p = tuple(p)
        if _written and _written_path(p, _written):
            continue
        cur = REC.value_hash(p)
        if cur == _UNIMPORTED_H and _params_file(p) in files:
            continue                                    # a node's params.py this build imports itself: its file is the key
        if h == "<unhashable>" or cur != h:
            return False                                # a value with no stable hash (default repr) cannot prove "unchanged"
    if any(file_sha(r) != h for r, h in rec["files"]):
        return False
    if any(mn not in sys.modules for mn in rec.get("writes", {})):
        return False
    for key, fp in rec.get("wpre", {}).items():
        mn, n = key.rsplit(".", 1)
        if (mn, n) in _written:
            continue
        mod = sys.modules.get(mn)
        if mod is None or _fp(vars(mod).get(n)) != fp:
            return False
    with _writes_applied(rec):                          # its takes were hashed when its build was over, writes and all
        bad = [(m, n, h) for m, n, h in rec.get("takes", ()) if (m, n) not in _written and _take_hash(m, n) != h]
    if bad and _above:
        # Hashed in the state the nodes above left for it (a SCALE one set before calling it, reached through a function it took):
        # once more with their writes applied too, outermost first. A match there is that state, as their keys vouch for it.
        with contextlib.ExitStack() as st:
            for a in _above + (rec,):
                st.enter_context(_writes_applied(a))
            bad = [t for t in bad if _take_hash(t[0], t[1]) != t[2]]
    if bad:
        return False                                    # a value it took out of a project module changed
    return _callees_valid(rec, callees, _seen, _written | own, _above + (rec,)) and _steps_exist(rec)


def _written_path(path, written):
    """Whether a read path names a value in `written` ((module, name) pairs)."""
    if path and isinstance(path[0], str) and path[0].startswith(_AT):
        return len(path) > 1 and (path[0][len(_AT):], path[1]) in written
    return (REC.params_name, path[0]) in written


def _callees_valid(rec, callees, _seen, written, above=()):
    # Reused, a node is not run, so neither is any build() it places or calls: each of those must still hold on its own.
    # A node's key no longer covers everything its parts' modules read at import time - only the values it took itself - so a
    # number that reaches one of its parts through that part's own module would otherwise leave the whole node reused, stale.
    seen = set() if _seen is None else _seen
    for q in list(callees) + list(rec.get("calls", ())):
        if q in seen:
            continue
        seen.add(q)
        crec = MODEL.fresh.get(q) or MODEL.old.get(q)
        if crec is None or not _node_valid(crec, seen, written, above):
            return False
    return True


_PLACE_KEYS = ("__ref", "__via", "__joint", "loc", "pname", "pnode")      # what place() and the framework add to a handed-out part dict: never stored


def _make_item(qual, p):
    it = _dec_fields(p["fields"])
    if p.get("mesh_solid"):
        it["shape"] = _read_solid_mesh(p["key"], p["label"], p["color"])
        it["shape"]._cad_loc0 = _loc_matrix(it["shape"].location)
        _attach_joints(it["shape"], p.get("joints"))
        MODEL.names[id(it["shape"])] = p["id"]
    elif not p.get("rowonly"):
        info = p.get("lazy")
        try:
            it["shape"] = _lazy_shape(p["key"], p["label"], p["color"], info) if info else None
        except (ImportError, AttributeError, ValueError):
            it["shape"] = None
        if it["shape"] is None:
            it["shape"] = _read_step(p["key"], p["label"], p["color"])
            it["shape"]._cad_loc0 = _loc_matrix(it["shape"].location)
            info = _lazy_info(it["shape"])
            if info:
                p["lazy"] = info
        _attach_joints(it["shape"], p.get("joints"))
        MODEL.names[id(it["shape"])] = p["id"]
    MODEL.issued[id(it)] = (qual, p["id"], it)         # the dict is kept so its id stays unique
    it["__ref"] = (qual, p["id"])                      # survives place()'s copy: the placed dict still names its variant
    MODEL.shapes.setdefault(it["__ref"], {})[id(it.get("shape"))] = it.get("shape")
    return it


JOINT_KINDS = {"RigidJoint": "rigid", "RevoluteJoint": "revolute", "LinearJoint": "linear", "CylindricalJoint": "cylindrical",
               "BallJoint": "ball"}


def _floats(v):
    return [float(x) for x in v]


def _joints_json(sh, short, pid):
    """The build123d joints of a part's shape, in the part's own coordinates (the frame its STEP is written in): {label: {type, loc |
    axis {origin, direction}, range, angular_range, ref}}. A STEP keeps no joints: a reused part gets them back from this."""
    out = {}
    for label, j in (getattr(sh, "joints", None) or {}).items():
        kind = JOINT_KINDS.get(type(j).__name__)
        if kind is None:
            sys.exit(f"[cad_incremental] part {pid!r} of {short}: joint {label!r} is a {type(j).__name__}; use RigidJoint, RevoluteJoint, "
                     f"LinearJoint, CylindricalJoint or BallJoint")
        e = {"type": kind}
        if kind in ("rigid", "ball"):
            e["loc"] = _loc_matrix(sh.location * j.relative_location)
        else:
            a = (sh.location * j.relative_axis.location)
            e["loc"] = _loc_matrix(a)
            e["axis"] = {"origin": _floats(a.position), "direction": _floats(a.z_axis.direction)}
        if kind in ("linear", "cylindrical") and not all(math.isfinite(x) for x in j.linear_range):
            sys.exit(f"[cad_incremental] part {pid!r} of {short}: joint {label!r} has no finite linear_range {tuple(j.linear_range)} "
                     f"(build123d's default (0, inf) sends a docked part to infinity): give it its real travel, linear_range=(lo, hi)")
        if kind == "linear":
            e["range"] = _floats(j.linear_range)
        elif kind in ("revolute", "cylindrical"):
            e["range" if kind == "revolute" else "angular_range"] = _floats(j.angular_range)
            if kind == "cylindrical":
                e["range"] = _floats(j.linear_range)
            if getattr(j, "_angle_reference_provided", True) and j.angle_reference is not None:
                r = sh.location.wrapped.Transformation()
                v = B.Vector(j.angle_reference)
                e["ref"] = [float(sum(r.Value(i, c) * v.to_tuple()[c - 1] for c in (1, 2, 3))) for i in (1, 2, 3)]
        elif kind == "ball":
            e["range"] = [_floats(x) for x in j.angular_range]
        out[str(label)] = e
    return out


def _attach_joints(sh, js):
    """Give a part read back from its STEP the joints its build had (a lazy one too: nothing here reads the STEP)."""
    for label, e in (js or {}).items():
        if e["type"] in ("rigid", "ball"):
            loc = _matrix_loc(e["loc"])
            if e["type"] == "rigid":
                B.RigidJoint(label, sh, loc)
            else:
                B.BallJoint(label, sh, loc, angular_range=tuple(tuple(x) for x in e["range"]))
            continue
        ax = B.Axis(tuple(e["axis"]["origin"]), tuple(e["axis"]["direction"]))
        ref = tuple(e["ref"]) if e.get("ref") else None
        if e["type"] == "linear":
            B.LinearJoint(label, sh, axis=ax, linear_range=tuple(e["range"]))
        elif e["type"] == "revolute":
            B.RevoluteJoint(label, sh, axis=ax, angle_reference=ref, angular_range=tuple(e["range"]))
        else:
            B.CylindricalJoint(label, sh, axis=ax, angle_reference=ref, linear_range=tuple(e["range"]),
                               angular_range=tuple(e["angular_range"]))


def _joint_value(j):
    """What a docking set on the moving joint (linear position, revolute angle; cylindrical both), or None."""
    kind = JOINT_KINDS.get(type(j).__name__)
    if kind == "linear":
        return None if j.position is None else float(j.position)
    if kind == "revolute":
        return None if j._angle is None else float(j._angle)
    if kind == "cylindrical":
        return None if j.position is None else [float(j.position), float(j.angle)]
    return None


def _edges(listed, short):
    """{index in `listed`: {to: index of the part it is docked to, at: that part's joint, by: its own joint, type, value?}} for the
    parts a node returns: the dockings its code made (build123d sets `connected_to` on the joint whose connect_to moved the other
    part) and those a sub-node's list brought along (`__joint`, by the shape of the part it is docked to)."""
    by_shape, all_shapes, out = {}, {}, {}
    for i, it in enumerate(listed):
        if isinstance(it, dict) and it.get("shape") is not None:
            by_shape.setdefault(id(it["shape"]), i)
            all_shapes.setdefault(id(it["shape"]), []).append(i)
    name = lambda i: listed[i].get("name") if isinstance(listed[i], dict) else i      # noqa: E731
    for i, it in enumerate(listed):
        e = isinstance(it, dict) and it.get("__joint")
        if not e:
            continue
        to = all_shapes.get(id(e["to"]), []) if e["to"] is not None else []
        if len(to) != 1 or len(all_shapes.get(id(it.get("shape")), [])) != 1:
            sys.exit(f"[cad_incremental] {short}: part {name(i)!r} is docked to a part " +
                     ("this node does not return with it" if not to else "returned more than once") +
                     ": call build() once per copy and return a sub-node's list whole")
        out[i] = dict(e, to=to[0])
    for i, it in enumerate(listed):
        sh = it.get("shape") if isinstance(it, dict) else None
        for label, j in (getattr(sh, "joints", None) or {}).items() if sh is not None else ():
            c = getattr(j, "connected_to", None)
            k = by_shape.get(id(c.parent)) if c is not None else None
            if k is None or k == i:
                continue
            if k in out:
                sys.exit(f"[cad_incremental] {short}: part {name(k)!r} is docked twice ({name(out[k]['to'])!r} {out[k]['at']!r} and "
                         f"{name(i)!r} {label!r}): a part is placed by one docking; check the rest with a second joint, not connect_to")
            kinds = [JOINT_KINDS.get(type(x).__name__) for x in (j, c)]
            moving = j if kinds[0] != "rigid" else c
            e = {"to": i, "at": str(label), "by": str(c.label), "type": next((x for x in kinds if x != "rigid"), "rigid")}
            v = _joint_value(moving)
            if v is not None:
                e["value"] = v
            out[k] = e
    return out


def _check_docked(listed, edges, short):
    """A docking is a one-shot move (build123d connect_to), not a constraint: the parts must still stand where it put them when the
    node returns them. A part placed with a Location after something was docked to it, or docked in the wrong order (x -> y, then
    a -> x leaves y behind), fails here naming both parts and joints."""
    for k, e in edges.items():
        child, parent = listed[k].get("shape"), listed[e["to"]].get("shape")
        if child is None or parent is None:
            continue
        j, c = parent.joints.get(e["at"]), child.joints.get(e["by"])
        if j is None or c is None or e["type"] == "ball":
            continue
        v = e.get("value")
        kw = {"linear": {"position": v}, "revolute": {"angle": v}}.get(e["type"], {})
        if e["type"] == "cylindrical" and v is not None:
            kw = {"position": v[0], "angle": v[1]}
        want = _loc_matrix(parent.location * j.relative_to(c, **{a: b for a, b in kw.items() if b is not None}))
        if not all(math.isclose(a_, b_, abs_tol=1e-6) for a_, b_ in zip(want, _loc_matrix(child.location))):
            name = lambda i: listed[i].get("pname") or listed[i].get("name")      # noqa: E731
            sys.exit(f"[cad_incremental] {short}: part {name(k)!r} is docked by its joint {e['by']!r} to {name(e['to'])!r} joint "
                     f"{e['at']!r} but does not stand there any more: {name(e['to'])!r} was moved after the docking (placed with a "
                     f"Location, or docked to something later). Dock top-down: place / dock a part first, then dock what sits on it")


def _loc_matrix(loc):
    """A build123d Location as a row-major 4x4 matrix (16 floats)."""
    t = loc.wrapped.Transformation()
    return [float(t.Value(r, c)) for r in (1, 2, 3) for c in (1, 2, 3, 4)] + [0.0, 0.0, 0.0, 1.0]


def _matrix_loc(m):
    from OCP.gp import gp_Trsf
    t = gp_Trsf()
    t.SetValues(*m[:12])
    return B.Location(t)


def _part_rec(qual):
    return MODEL.fresh.get(qual) or MODEL.old[qual]


def _node_items(rec):
    out = _node_items0(rec)
    via = [rec["qual"], *rec.get("via", ())]           # the records whose returned parts these are (overlapping roots)
    for it in out:
        it["__via"] = via
    return out


def _node_items0(rec):
    own = [_make_item(rec["qual"], p) for p in rec["parts"]]
    out = []
    for r in rec["ret"]:
        if r[0] == "own":
            out.append(own[r[1]])
            continue
        crec = _part_rec(r[1])                          # a part of a build() this node called and returned (and placed)
        it = _make_item(r[1], next(p for p in crec["parts"] if p["id"] == r[2]))
        if r[0] == "place":
            it.update(loc=_matrix_loc(r[3]), pnode=r[5], **({"pname": r[4]} if r[4] is not None else {}))
            if it.get("shape") is not None and it["shape"].joints:
                it["shape"].location = it["loc"]       # where it stands: what the caller docks to it lands there (a part
                                                       # without joints keeps its own coordinates, as before #16)
        out.append(it)
    for it, r in zip(out, rec["ret"]):                  # who it is docked to: the shape of the copy handed out in this same list
        if r[0] == "place" and len(r) > 6 and r[6]:    # (each copy has its own shape, also after place()'s dict copy)
            it["__joint"] = dict(r[6], to=out[r[6]["to"]].get("shape"))
    return out


def _keep_called(rec, seen=None):
    """A reused record keeps the records of every build() it called (a part read only for a size) in the model's records, so
    their STEPs survive the GC and the next run that does call them finds them."""
    seen = set() if seen is None else seen
    for q in rec.get("calls", ()):
        crec = MODEL.fresh.get(q) or MODEL.old.get(q)
        if crec is not None and q not in seen:
            seen.add(q)
            MODEL.fresh.setdefault(q, crec)
            MODEL.order.setdefault(q, None)
            _keep_called(crec, seen)


def sha_json(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest()


def _hex(c):
    return "#%02x%02x%02x" % tuple(round(max(0.0, min(1.0, float(x))) * 255) for x in list(c)[:3])


def _node_path(modname):
    """parts.tower.carriage (a node folder) and parts.tower.carriage.slider (a part file in it) -> "tower/carriage"."""
    if modname == PARTS_PKG:
        return ""                                       # the machine root (parts/__init__.py) is no group of its own
    segs = modname.split(".")[1:] if modname.startswith(PARTS_PKG + ".") else modname.split(".")[-1:]
    if not hasattr(sys.modules.get(modname), "__path__") and len(segs) > 1:
        segs = segs[:-1]
    return "/".join(segs) or modname


def _in_folder(modname):
    """A module of the folder layout: a node folder's __init__ or a file inside a node folder (not a module parts/<x>.py)."""
    mod = sys.modules.get(modname)
    return modname == PARTS_PKG or modname.startswith(PARTS_PKG + ".") and (hasattr(mod, "__path__") or modname.count(".") >= 2)


def _canon_arg(v):
    """A build() argument in the variant key: 600 and 600.0 are one variant; anything without a JSON form raises TypeError."""
    if v is None or isinstance(v, bool):
        return {"__bool": bool(v)} if isinstance(v, bool) else v   # True is not the default 1
    if isinstance(v, numbers.Integral):
        return int(v)
    if isinstance(v, numbers.Real):
        x = float(v)
        return int(x) if x.is_integer() else x
    if isinstance(v, str):
        return str.__str__(v)
    if isinstance(v, list):
        return [_canon_arg(e) for e in v]
    if isinstance(v, tuple):
        return {"__t": "tuple", "v": [_canon_arg(e) for e in v]}
    if isinstance(v, dict):
        items = sorted(([_canon_arg(k), _canon_arg(e)] for k, e in dict.items(v)), key=lambda kv: json.dumps(kv[0], sort_keys=True))
        return {"__t": "dict", "v": items}
    raise TypeError(type(v).__name__)


def _variant(f, a, k):
    """(suffix, call, keyed) of a build(*a, **k): suffix "" when every argument equals its default (that call IS the plain build()),
    else "(name=value,...)" of the arguments that differ; call = the readable call with every argument; keyed False when an
    argument has no JSON form (the caller then numbers the call, the call always runs and its key is new each run). None: the arguments
    do not bind."""
    try:
        sig = inspect.signature(f)
        b = sig.bind(*a, **k)
    except (TypeError, ValueError):
        return None
    b.apply_defaults()
    defaults = {n: p.default for n, p in sig.parameters.items() if p.default is not inspect.Parameter.empty}
    given = {n: v for n, v in b.arguments.items()   # a non-JSON default object itself is fixed by the (hashed) code
             if not (n in defaults and v is defaults[n] and _canon_jsonable(v) is _NOJSON)}
    try:
        canon = {n: _canon_arg(v) for n, v in given.items()}
        diff = {n: c for n, c in canon.items() if n not in defaults or _canon_jsonable(defaults[n]) != c}
    except TypeError:
        txt = ", ".join(f"{n}={v!r}" for n, v in b.arguments.items())
        return "(~)", f"({txt})", False             # no JSON form: the caller numbers it, it always runs
    def plain(c):
        if isinstance(c, dict) and set(c) == {"__bool"}:
            return c["__bool"]
        if isinstance(c, dict) and c.get("__t") == "tuple":
            return [plain(e) for e in c["v"]]
        if isinstance(c, dict) and c.get("__t") == "dict":
            return {json.dumps(plain(k)) if not isinstance(plain(k), str) else plain(k): plain(e) for k, e in c["v"]}
        return [plain(e) for e in c] if isinstance(c, list) else c
    js = lambda c: json.dumps(plain(c), sort_keys=True, ensure_ascii=False)
    suffix = "(" + ",".join(f"{n}={js(c)}" for n, c in diff.items()) + ")" if diff else ""
    return suffix, "(" + ", ".join(f"{n}={js(canon[n]) if n in canon else '<default>'}" for n in b.arguments) + ")", True


_NOJSON = object()


def _canon_jsonable(v):
    try:
        return _canon_arg(v)
    except TypeError:
        return _NOJSON                                  # a non-JSON default never equals a given value


def model_build(f):
    """A parts/ build function in --model mode: it runs only when the values of the params it read, the project modules it
    used or the files it opened changed (or it has no record). What a build() it called depends on is part of its own record,
    because that call's frame is merged into the caller's. Its parts live on as one STEP each. What it wrote into params or other
    project modules is recorded (JSON) and replayed on reuse; a write that has no JSON form makes the node always run.
    Folder layout: a part file's build(**args) returns ONE part dict in its own coordinates; each distinct set of arguments
    (defaults applied, 600 == 600.0) is its own record, a variant. A node returns placed copies (place(): `loc`); the record keeps
    [variant, part id, matrix, name, placing node] per placement. A node builds no shaped part itself: it places part files' parts."""
    base = f"{f.__module__}.{f.__name__}"
    short0 = base[len(PARTS_PKG) + 1:] if base.startswith(PARTS_PKG + ".") else ".".join(base.split(".")[-2:])

    @functools.wraps(f)
    def w(*a, **k):
        v = _variant(f, a, k)
        if v is None:
            return f(*a, **k)                           # the arguments do not fit: let Python raise its own TypeError
        suffix, call, keyed = v
        if not keyed:                                   # one record per such call in this run: two calls never share one
            MODEL.unkeyed[base] = MODEL.unkeyed.get(base, 0) + 1
            suffix = f"(~{MODEL.unkeyed[base]})"
        qual, short = base + suffix, short0 + suffix
        again = qual in MODEL.seen
        MODEL.seen.add(qual)
        MODEL.order.setdefault(qual, None)
        if MODEL.calls:
            MODEL.calls[-1].add(qual)
        if MODEL.depth == 0:
            MODEL.top[qual] = None
        MODEL.depth += 1
        try:
            out = _run(qual, short, suffix, call, keyed, again, a, k)
        finally:
            MODEL.depth -= 1
        if MODEL.depth == 0:
            for it in out if isinstance(out, list) else [out]:
                MODEL.root_items[id(it)] = it
        return out

    def _run(qual, short, suffix, call, keyed, again, a, k):
        REC._th.clear()                                 # a node may have changed a taken list in place since: hash afresh
        was, REC.on = REC.on, False
        try:
            rec = (MODEL.fresh.get(qual) or MODEL.old.get(qual)) if keyed else None
            if rec is not None and not (again and rec.get("writes")) and _node_valid(rec):
                _replay_writes(rec)
                items = _node_items(rec)
                for cq, crec in _callees(rec).items():      # the build()s it returned parts of are part of the model too
                    MODEL.fresh.setdefault(cq, crec)
                    MODEL.order.setdefault(cq, None)
                _keep_called(rec)
            else:
                rec = None
            before = None if rec is not None else _gsnap()
        finally:
            REC.on = was
        if rec is not None:
            MODEL.fresh[qual] = rec
            REC.replay([tuple(p) for p, _ in rec["reads"]], [r for r, _ in rec["files"]], rec["mods"],
                       [(m, n) for m, n, _h in rec.get("takes", ())])
            return items[0] if rec.get("kind") == "part" else items
        mod = sys.modules[f.__module__]
        fr = REC.push("call", qual)
        fr.mods.add(mod.__name__)
        MODEL.calls.append(set())
        before_mods = set(sys.modules)
        try:
            items = f(*a, **k)
            REC.use(deps(items))                    # whatever the node hands back (a thickness field...) depends on its params
        finally:
            REC.pop(fr)
            called = MODEL.calls.pop()
        MODEL.rebuilt.append(qual)
        got = _entry_deps(fr, mod.__name__, set(sys.modules) - before_mods)
        was, REC.on = REC.on, False
        try:
            node_default = _node_path(f.__module__)
            single = isinstance(items, dict)
            listed = [items] if single else items
            parts, ret = [], []
            via = set()
            edges = _edges(listed, short) if isinstance(listed, list) else {}
            _check_docked(listed, edges, short)
            if isinstance(listed, list):
                for i_, it in enumerate(listed):
                    if isinstance(it, dict) and isinstance(it.get("__via"), list):
                        via.update(it["__via"])
                    ref = it.get("__ref") if isinstance(it, dict) else None
                    if ref is not None and _part_rec(tuple(ref)[0]).get("kind") != "part" and (id(it) in MODEL.issued or "loc" in it):
                        if it.get("shape") is not None:
                            sys.exit(f"[cad_incremental] node {_part_rec(tuple(ref)[0])['func']} built part {ref[1]!r} itself: a node "
                                     f"only places parts; move it to its own part file and place() it")
                        rp = next((x for x in _part_rec(tuple(ref)[0])["parts"] if x["id"] == ref[1]), {})
                        if rp.get("mesh") and "loc" in it and not all(math.isclose(a_, b_, abs_tol=1e-9) for a_, b_ in
                                                                      zip(_loc_matrix(it["loc"]), _loc_matrix(B.Location()))):
                            sys.exit(f"[cad_incremental] {short} moved mesh part {ref[1]!r} (place() with a Location): a mesh part is "
                                     f"drawn where it stands; return its node's list as it is")
                        ret.append(["row", *tuple(ref)])    # a row of a sub-node (a node builds no shaped part itself)
                        continue
                    if ref is not None and "loc" in it:     # a placed copy of a variant this node (or a sub-node) built
                        ref = tuple(ref)
                        sh0 = it.get("shape")
                        if id(sh0) not in MODEL.shapes.get(ref, {}):
                            sys.exit(f"[cad_incremental] {short} changed the 'shape' of a placed copy of part {ref[1]!r}: a placed "
                                     f"part is the shape its part file built; change the part file (or add a variant) instead")
                        at = _loc_matrix(sh0.location) if sh0 is not None else None
                        near = lambda m: all(math.isclose(a_, b_, abs_tol=1e-6) for a_, b_ in zip(at, m))   # noqa: E731
                        if sh0 is not None and not near(_loc_matrix(it["loc"])) and not near(
                                object.__getattribute__(sh0, "__dict__").get("_cad_loc0") or _loc_matrix(B.Location())):
                            sys.exit(f"[cad_incremental] {short}: part {ref[1]!r} was moved (docked by connect_to?) after place(); "
                                     f"dock it first, then place() it (and place() a sub-node's list after docking one of its parts)")
                        crec = _part_rec(ref[0])
                        cp = next(p for p in crec["parts"] if p["id"] == ref[1])
                        for kk, vv in it.items():
                            if kk in _PLACE_KEYS or kk == "shape":
                                continue
                            try:
                                same = kk in cp["fields"] and json.dumps(_enc(vv), sort_keys=True) == json.dumps(cp["fields"][kk], sort_keys=True)
                            except (TypeError, ValueError, RecursionError):
                                same = False
                            if not same:
                                sys.exit(f"[cad_incremental] node {short} changed field {kk!r} of a placed copy of part {ref[1]!r}: "
                                         f"fields come from its part file ({crec.get('call') or crec['func']})"
                                         + ("; qty is the number of placements: place it again instead" if kk == "qty" else ""))
                        it.setdefault("pnode", node_default)    # the innermost node that placed it
                        ret.append(["place", ref[0], ref[1], _loc_matrix(it["loc"]), it.get("pname"), it["pnode"], edges.get(i_)])
                        continue
                    if id(it) in MODEL.issued:          # a part file's part returned as is: where its joints (or its build) put it
                        q, pid = MODEL.issued[id(it)][:2]
                        it.update(loc=it["shape"].location if it.get("shape") is not None else B.Location(), pnode=node_default)
                        ret.append(["place", q, pid, _loc_matrix(it["loc"]), None, node_default, edges.get(i_)])
                        continue
                    if not (isinstance(it, dict) and "name" in it):
                        sys.exit(f"[cad_incremental] {short} returned an item that is not a dict with a 'name': "
                                 f"{type(it).__name__} {repr(it)[:80]}; every item must be a part dict")
                    sh = it.get("shape")
                    mesh = None
                    if _is_mesh_shape(sh):              # a solid mesh part: placed and docked like a STEP part
                        if it.get("process") in ("laser", "bent"):
                            sys.exit(f"[cad_incremental] part {it['name']!r} of {short} is a meshpart.Mesh with process "
                                     f"{it['process']!r}: a sheet part needs a B-rep for its flat pattern and drawing; draw it in "
                                     f"build123d (a mesh part is printed, or other)")
                        rowonly, mesh = False, _solid_mesh_data(sh, it["name"], short)
                    elif isinstance(sh, B.Shape) and sh.wrapped is not None:
                        rowonly = False
                    elif sh is None and it.get("mesh") is not None:
                        rowonly, mesh = True, _mesh_data(it["mesh"], it["name"], short)   # view-only triangles (a vendor unit): a row
                    elif sh is None and it.get("process") == "bought":                    # with a mesh, where it stands
                        rowonly = True                  # a bought part may be a row (qty, unit) with no 3D
                    else:
                        sys.exit(f"[cad_incremental] part {it['name']!r} of {short} has no usable 'shape' "
                                 f"(process {it.get('process', 'other')!r}); only a bought part may be a row without a shape")
                    is_node = hasattr(sys.modules[f.__module__], "__path__")
                    if not rowonly and not single and not is_node:
                        sys.exit(f"[cad_incremental] part file {short} returned a list: a part file returns one part (geom.P); "
                                 f"a node (the folder's __init__.py) places several")
                    if not rowonly and (not single or is_node):
                        sys.exit(f"[cad_incremental] node {short} built part {it['name']!r} itself: a node only places parts; move "
                                 f"it to its own file parts/{node_default}/{_part_id(it['name'])}.py and place() it")
                    raw = {kk: vv for kk, vv in it.items() if kk not in ("shape", "mesh", "__ref", "__via")}   # a loc here: not a placed variant
                    if rowonly:                         # a row has no position: place() of a whole sub-node list moves it along
                        raw = {kk: vv for kk, vv in raw.items() if kk not in _PLACE_KEYS}
                    if raw.get("node") is None:
                        raw["node"] = node_default
                    raw.setdefault("process", "other")
                    c = None if rowonly else sh.color
                    color = [float(x) for x in c] if c is not None else None
                    if raw.get("color") is None:
                        raw["color"] = _hex(c) if c is not None else None
                    fields = {}
                    for kk, vv in raw.items():
                        try:
                            fields[kk] = _enc(vv)
                        except (TypeError, ValueError, RecursionError):
                            sys.exit(f"[cad_incremental] part {it['name']!r} of {short}: field {kk!r} ({type(vv).__name__}) cannot be "
                                     f"stored in the model; only 'shape' may be a non-JSON value")
                    if not rowonly and sh.parent is not None:   # a child of a Compound cannot be exported on its own
                        import copy
                        sh = copy.copy(sh)
                        sh.parent = None
                    ret.append(["own", len(parts)])
                    js = {} if rowonly else _joints_json(sh, short, it["name"])
                    parts.append({"id": _part_id(it["name"]), "fields": json.loads(json.dumps(fields)), "shape": None if rowonly else sh,
                                  **({"joints": js} if js else {}),
                                  "label": None if rowonly else (sh.label or None), "color": color, **({"rowonly": True} if rowonly else {}),
                                  **({"mesh": True, "mesh_data": mesh} if mesh else {}),
                                  **({"mesh_solid": True} if mesh and not rowonly else {})})
            if single and not (len(ret) == 1 and ret[0][0] == "own"):
                sys.exit(f"[cad_incremental] {short} returned a part of another build(); a part file returns its own part")
            writes, pre, bad = _gdiff(before)
            if writes:
                REC._th.clear()                         # a value it took may be one it changed
            why = ("it returned something other than a list" if not isinstance(listed, list) else
                   "it used a module that was not imported while recording" if got is None else
                   f"it wrote a value to {bad} that has no JSON form" if bad else
                   f"an argument has no JSON form {call}" if not keyed else None)
            if not keyed:                               # its argument's value is not in the key: never reuse a key (or a STEP,
                suffix = f"{suffix}#{time.time_ns()}"   # or a derived kit) across runs
            rec = {"qual": qual, "func": short, "call": short0 + call, "env": MODEL.env, "parts": parts, "ret": ret,
                   **({"dnode": node_default} if single else {}),
                   **({"via": sorted(via - {qual})} if via - {qual} else {}),
                   "cacheable": why is None, **({"kind": "part"} if single else {}), **({"args": suffix} if suffix else {}),
                   **({"calls": sorted(called)} if called else {})}
            if why and qual not in MODEL.warned:
                MODEL.warned.add(qual)
                print(f"[cad_incremental] warning: {short0} is rebuilt on every run: {why}", file=sys.stderr)
            if got is not None:
                reads, files, mods, takes = got
                REC.pre = pre                           # a param the node overwrote is keyed by the value it found
                REC._vh = {p: h for p, h in REC._vh.items() if not _pre_hit(p, pre)}
                rec.update(reads=sorted([list(p), REC.value_hash(p)] for p in reads),
                           files=sorted([r, file_sha(r)] for r in files), mods=sorted(mods))
                takes = {(m, n) for m, n in takes if n not in (writes.get(m) or {})}    # one it wrote: wpre has the value it found
                if takes:
                    rec["takes"] = sorted([m, n, _take_hash(m, n)] for m, n in takes)
                REC.pre = {}
                REC._vh = {p: h for p, h in REC._vh.items() if not _pre_hit(p, pre)}
            else:
                rec.update(reads=[], files=[], mods=[])
            rec["writes"] = writes
            known = _written_names() | {f"{mn}.{n}" for mn, ws in writes.items() for n in ws}
            rec["wpre"] = {f"{mn}.{n}": fp for (mn, n), (fp, *_r) in before.items() if f"{mn}.{n}" in known and fp is not None}
            datas = None
            if got is None:                             # inputs unknown: the geometry itself is part of the key
                datas = [None if p["shape"] is None else json.dumps(p["mesh_data"]).encode() if p.get("mesh_solid")
                         else _step_bytes(p["shape"]) for p in parts]
                rec["shas"] = [x and hashlib.sha256(x).hexdigest() for x in datas]
            rec["fp"] = _node_fp(rec)
            for i, p in enumerate(parts):               # the STEP is written last: its name needs the node's inputs
                md = p.pop("mesh_data", None)
                p["key"] = _part_key(rec["fp"] if p["shape"] is not None else sha_json([p["fields"], md]) if md else sha_json(p["fields"]),
                                     p["id"])                    # a row: its fields (and a mesh part's triangles)
                sh = p.pop("shape")
                if sh is not None and not p.get("mesh_solid"):
                    _write_step(sh, p["key"], short, datas[i] if datas else None)
                if md:
                    _write_mesh(md, p["key"], short if p.get("mesh_solid") else None)
            MODEL.fresh[qual] = rec
            if isinstance(listed, list):                # go on with what is on disk, exactly what a reuse would return
                listed = _node_items(rec)
                items = listed[0] if single else listed
        finally:
            REC.on = was
        MODEL.fresh[qual] = rec
        return items
    w.__cad_wrapped__ = True
    return w


def fresh_since(path, start):
    """True when the path or anything under it was modified at or after `start`."""
    try:
        if os.path.getmtime(path) >= start:
            return True
        if os.path.isdir(path):
            for dp, dns, fns in os.walk(path):
                if any(os.path.getmtime(os.path.join(dp, n)) >= start for n in dns + fns):
                    return True
    except OSError:
        return True
    return False


def kit_referenced_derived(root):
    """Names of build/derived/<k>/ dirs that any JSON manifest in build/kit/ points at (any string value, recursively):
    those files are served to clients, so GC must keep them even when their part keys are no longer current."""
    names = set()
    pat = re.compile(r"build/derived/([^/\\]+)")

    def walk(o):
        if isinstance(o, str):
            names.update(pat.findall(o.replace("\\", "/")))
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    kit = os.path.join(str(root), "build", "kit")
    for n in (os.listdir(kit) if os.path.isdir(kit) else []):
        if n.endswith(".json"):
            try:
                with open(os.path.join(kit, n), encoding="utf-8") as fh:
                    walk(json.load(fh))
            except (OSError, ValueError):
                pass
    return names


def _model_gc(keep, start):
    """Delete what the merged model does not reference, except anything written at or after `start` (a rebuild running next to us)."""
    root, step = MODEL.root, os.path.join(MODEL.dir, "step")
    mdir = os.path.join(MODEL.dir, "mesh")
    for n in os.listdir(mdir) if os.path.isdir(mdir) else ():
        if (n[:-5] not in keep or not n.endswith(".json")) and not fresh_since(os.path.join(mdir, n), start):
            try:
                os.remove(os.path.join(mdir, n))
            except OSError:
                pass
    if os.path.isdir(step):
        for n in os.listdir(step):
            p = os.path.join(step, n)
            if (n[:-5] not in keep or not n.endswith(".step")) and not fresh_since(p, start):
                try:
                    os.remove(p)
                except OSError:
                    pass
    import shutil
    shutil.rmtree(os.path.join(root, "build", "cad_cache"), ignore_errors=True)     # leftovers of the retired op cache / params map
    for n in ("params_map.json", "params_map.keys.json", "params_map.parts.json"):
        try:
            os.remove(os.path.join(root, "build", n))
        except OSError:
            pass
    dd = os.path.join(root, "build", "derived")
    kit_used = kit_referenced_derived(root)
    if os.path.isdir(dd):
        for n in os.listdir(dd):
            p = os.path.join(dd, n)
            if not os.path.isdir(p) or fresh_since(p, start) or n in kit_used:
                continue
            try:
                with open_real(os.path.join(p, "meta.json")) as fh:
                    used = set(json.load(fh)["parts"])
            except (OSError, ValueError, KeyError, TypeError):
                used = None
            if used is None or not used <= keep:
                shutil.rmtree(p, ignore_errors=True)
    if os.path.isdir(MODEL.dir):                        # leftovers of a crashed run: <file>.tmp-<pid>
        for n in os.listdir(MODEL.dir):
            p = os.path.join(MODEL.dir, n)
            if ".tmp-" in n and os.path.isfile(p) and not fresh_since(p, start):
                try:
                    os.remove(p)
                except OSError:
                    pass


def _module_file_exists(cad, qual):
    mod = qual.split("(", 1)[0].rsplit(".", 1)[0].replace(".", os.sep)
    return os.path.isfile(os.path.join(cad, mod + ".py")) or os.path.isfile(os.path.join(cad, mod, "__init__.py"))


@contextlib.contextmanager
def project_lock(root):
    """Exclusive per-project lock (build/model/.lock) for the whole of a --model run or a derive: two writers never overlap.
    A child started by a lock holder (derive -> cad_incremental) inherits CAD_MODEL_LOCK_HELD = the locked root's real path and
    does not lock that same root again; a run on any other root still takes its own lock.
    Scope: flock serialises writers on ONE machine only. Cross-machine jobs rely on content-addressed STEPs and the GC mtime
    guard (fresh_since); cross-machine locking is out of scope."""
    if os.environ.get("CAD_MODEL_LOCK_HELD") == os.path.realpath(root):
        yield
        return
    import fcntl
    d = os.path.join(root, "build", "model")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ".lock"), "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def model_run(o, cad, root):
    with project_lock(root):
        return _model_run(o, cad, root)


def _model_run(o, cad, root):
    """The entry run (assemble.py) is authoritative for the model. A partial run (another script) merges: records of nodes it did
    not call are kept, so it can only refine the model; the next entry run settles what really belongs to it."""
    global MODEL
    import runpy
    t0 = time.time()
    MODEL = ModelState(root)
    bd = importlib.import_module('build123d').__version__
    MODEL.env = f"{VERSION}|model4|t18|{bd}"       # a record keyed by the values it took; a script from before #18 cannot
    MODEL.env_read = (MODEL.env, f"{VERSION}|model4|{bd}")   # check those, so it must not accept it - while this one keeps
                                                            # reusing that script's records, which list MORE inputs
    try:
        with open(os.path.join(MODEL.dir, "nodes.json")) as fh:
            MODEL.old = json.load(fh)
    except (OSError, ValueError):
        MODEL.old = {}
    install(cad, o.params)
    for d in ("model", "derived"):
        REC.skip_dirs.append(os.path.join(root, "build", d) + os.sep)
    sys.argv = [o.script] + o.args
    status, g = 0, {}
    try:
        g = runpy.run_path(o.script, run_name="__main__")
    except SystemExit as e:
        status = e.code or 0
    finally:
        REC.on = False
    if status:
        return status
    def _held(v, depth=0):
        if depth > 4:
            return
        if isinstance(v, dict) and "__ref" in v:
            yield v
        elif isinstance(v, dict):
            for e in list(v.values()):
                yield from _held(e, depth + 1)
        elif isinstance(v, (list, tuple)):
            for e in v:
                yield from _held(e, depth + 1)
    for n, v in g.items():                              # a placement only counts inside a node: the script's own place() is lost
        if n.startswith("__"):
            continue
        for it in _held(v):
            if "loc" in it and id(it) not in MODEL.root_items:
                sys.exit(f"[cad_incremental] {os.path.basename(o.script)} placed part {it['__ref'][1]!r} itself ({n}): a "
                         f"placement made outside a node is not in the model; place it in a node's build()")
    # a script that calls only some nodes refines the model, never shrinks it: records of nodes it did not call stay (unless their
    # module is gone); the entry script assemble.py is authoritative and drops them
    entry = os.path.abspath(o.script) == os.path.join(os.path.abspath(cad), "assemble.py")   # the full-model script is authoritative
    nodes = {q: MODEL.fresh[q] for q in MODEL.order}
    for q, r in MODEL.old.items():
        if q not in nodes and not entry and _module_file_exists(cad, q):
            nodes[q] = r
    for q, r in nodes.items():                          # the model's roots: what the entry script itself called; another script
        top = q in MODEL.top if entry else bool(MODEL.old.get(q, {}).get("top"))   # (a check) never adds or drops one
        if top:
            r["top"] = True
        else:
            r.pop("top", None)
    tops = [q for q, r in nodes.items() if r.get("top")]
    inner = {v for q in tops for v in nodes[q].get("via", ())}
    tops = [q for q in tops if q not in inner]          # a root whose parts another root returns (assemble.py also calling a
                                                        # sub-node or reading a part's size) is counted through that root only
    for q in tops:                                      # a kept root's variants stay in the model with it
        for cq, crec in (_callees(nodes[q]) or {}).items():
            nodes.setdefault(cq, crec)
    parts, owner = [], {}
    placed, rows, seq, via = {}, {}, {}, set()          # (variant, part id) -> its placements under the roots; rows -> count
    ret_of, name_of, docked = {}, {}, []                # a placement's root list; its name; (placement, its docking) to resolve
    for q in tops:
        if nodes[q].get("kind") == "part":              # a part file the entry script called itself: where it was built
            pid = nodes[q]["parts"][0]["id"]
            placed.setdefault((q, pid), []).append(["place", q, pid, _loc_matrix(B.Location()), None, nodes[q].get("dnode")])
            seq.setdefault(pid, len(seq))
            continue
        for r in nodes[q]["ret"]:
            if r[0] == "place":
                placed.setdefault((r[1], r[2]), []).append(r)
                ret_of[id(r)] = nodes[q]["ret"]
                seq.setdefault(r[2], len(seq))
            elif r[0] == "row" or r[0] == "own" and nodes[q]["parts"][r[1]].get("rowonly"):
                k = (r[1], r[2]) if r[0] == "row" else (q, nodes[q]["parts"][r[1]]["id"])
                if r[0] == "row":
                    via.add(k)
                rows[k] = rows.get(k, 0) + 1
                seq.setdefault(k[1], len(seq))
    rowfrom, base_of = {}, lambda q: q.split("(", 1)[0]
    for (q, pid), n in rows.items():                    # a row of a sub-assembly placed n times: its qty n times
        rec = nodes.get(q) or _part_rec(q)
        nodes.setdefault(q, rec)
        p = next(x for x in rec["parts"] if x["id"] == pid)
        if p.get("mesh") and n > 1:                     # its triangles stand where the node drew them: no place() can move them
            sys.exit(f"[cad_incremental] mesh part {pid!r} of {rec['func']} is returned several times; a mesh part is drawn where it "
                     f"stands: return it once, unmoved (a node passing its node's list on as it is, e.g. the machine root, is fine)")
        fields = _dec_fields(p["fields"])
        same = lambda e: {k: v for k, v in e.items() if k not in ("qty", "id", "func", "key")} == \
            {k: v for k, v in dict(fields, name=fields.get("name", pid)).items() if k != "qty"}
        prev = next((e for e in parts if e["id"] == pid), None)
        if prev is not None and pid in rowfrom and rowfrom[pid] == base_of(q) and same(prev):
            prev["qty"] += fields.get("qty", 1) * n     # the same row from another variant of the same node: one purchase line
            continue
        if pid in owner:
            sys.exit(f"[cad_incremental] duplicate part id {pid!r}: produced by {owner[pid]} and {rec['func']}")
        owner[pid], rowfrom[pid] = rec["func"], base_of(q)
        parts.append(dict(fields, id=pid, func=rec["func"], key=p["key"], qty=fields.get("qty", 1) * n, name=fields.get("name", pid),
                          **({"mesh": f"build/model/mesh/{p['key']}.json",
                              "placements": [{"name": pid, "node": fields.get("node"), "loc": _loc_matrix(B.Location())}]} if p.get("mesh") else {})))
    variants = []
    for (vq, pid), pls in placed.items():
        crec = nodes[vq]
        who = crec.get("call") or crec["func"]
        p = next(x for x in crec["parts"] if x["id"] == pid)
        fields = _dec_fields(p["fields"])
        units = p.get("rowonly") or fields.get("process") == "bought"   # a row, or bought stock drawn once (5 x 1 m of pipe):
        if "qty" in fields and not units:                                # qty = units per placement
            sys.exit(f"[cad_incremental] part {pid!r} of part file {who} sets qty={fields['qty']!r}: a part with a shape counts its "
                     f"placements; drop qty there and place it as many times as it is used (only a bought part may give units per placement)")
        if pid in owner:
            sys.exit(f"[cad_incremental] duplicate part id {pid!r}: produced by {owner[pid]} and {who}; give each part (and each "
                     f"variant of a part file) its own name")
        owner[pid] = who
        e = dict(fields, id=pid, func=crec["func"], key=p["key"], qty=fields.get("qty", 1) * len(pls) if units else len(pls))
        if p.get("mesh"):
            e["mesh"] = f"build/model/mesh/{p['key']}.json"
            if p.get("mesh_solid"):
                e["mesh_solid"] = True          # closed triangles of its own: printed, drawn and checked like a solid
        elif not p.get("rowonly"):
            e.update(sha256=_file_sha256(os.path.join(MODEL.dir, "step", p["key"] + ".step")), step=f"build/model/step/{p['key']}.step")
        if p.get("joints"):
            e["joints"] = p["joints"]
        e.setdefault("name", pid)
        parts.append(e)
        variants.append((e, pls))
    parts.sort(key=lambda e: seq.get(e["id"], len(seq)))   # in the order the nodes return them
    ids = {e["id"] for e in parts}
    used = {}                                           # placement name (the viewer's id) -> its part id: unique in the model
    for e, pls in variants:
        for r in pls:
            if r[4] is not None:
                if r[4] in used or r[4] in ids and r[4] != e["id"]:
                    sys.exit(f"[cad_incremental] placement name {r[4]!r} of part {e['id']!r} is already used by part "
                             f"{used.get(r[4], r[4])!r}: placement names are unique in the model")
                used[r[4]] = e["id"]
    for e, pls in variants:
        out, n = [], 0
        for r in pls:
            name = r[4]
            while name is None:
                n += 1
                c = e["id"] if n == 1 else f"{e['id']}_{n}"
                if c not in used and (c not in ids or c == e["id"]):
                    name = c
            used[name] = e["id"]
            name_of[id(r)] = name
            pnode = r[5] if e.get("node") == nodes[pls[0][1]].get("dnode", e.get("node")) else e["node"]   # a node the part names
            pnode = pnode or e.get("node")                  # placed by the machine root itself: the part's own folder groups it
            out.append({"name": name, "node": pnode, "loc": r[3]})                                        # itself groups it
            if len(r) > 6 and r[6] and id(r) in ret_of:
                docked.append((out[-1], r[6], ret_of[id(r)]))
        e["placements"] = out
    for pl, j, rets in docked:                          # the joint graph: to = the placement it is docked to
        pl["joint"] = dict(j, to=name_of[id(rets[j["to"]])])
    os.makedirs(MODEL.dir, exist_ok=True)
    for fn, data in (("parts.json", {"parts": parts}), ("nodes.json", nodes)):
        tmp = os.path.join(MODEL.dir, f"{fn}.tmp-{os.getpid()}")
        with open_real(tmp, "w") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, os.path.join(MODEL.dir, fn))
    _write_device(os.path.dirname(os.path.dirname(MODEL.dir)))
    _model_gc({p["key"] for r in nodes.values() for p in r["parts"] if p.get("key")} | {p["key"] for p in parts}, t0)
    rebuilt = list(dict.fromkeys(MODEL.rebuilt))
    print(f"[cad_incremental] {time.time() - t0:.1f} s | nodes rebuilt {len(rebuilt)}, reused {len(nodes) - len(rebuilt)}"
          f" | parts {len(parts)}", file=sys.stderr)
    if rebuilt:
        print(f"[cad_incremental] rebuilt: {', '.join(MODEL.fresh[q]['func'] for q in rebuilt)}", file=sys.stderr)
    return 0


def _write_device(root):
    """build/model/device.json, the virtual device (device.py): derived after every model build; a problem is reported, never fatal
    (check.py rule «device» fails on it)."""
    try:
        import importlib.util                   # by path: a project module named device.py is already imported in this process
        spec = importlib.util.spec_from_file_location("_mp_device", os.path.join(os.path.dirname(os.path.abspath(__file__)), "device.py"))
        device = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(device)
        for p in device.write(root, sys.executable):
            print(f"[cad_incremental] device: {p}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - the model is built; the device is a projection of it
        print(f"[cad_incremental] device.json not written: {type(e).__name__}: {e}", file=sys.stderr)


# ============================================================ CLI
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--model", action="store_true",
                    help="node-level build: each node / part file build() runs only when what it read changed; its parts are stored as "
                         "one STEP each in <root>/build/model (parts.json, nodes.json)")
    ap.add_argument("--no-cache", "--full", action="store_true", help="ignored: kept so that older project scripts still run")
    ap.add_argument("--params", default="params")
    ap.add_argument("script", nargs="?")
    ap.add_argument("args", nargs=argparse.REMAINDER)
    o = ap.parse_args(argv)
    if not o.script:
        ap.error("script required")
    cad = os.getcwd()
    if o.model:
        if not os.path.exists(os.path.join(cad, o.params + ".py")):     # run from elsewhere: the script's own folder
            cad = os.path.dirname(os.path.abspath(o.script))
        if not os.path.exists(os.path.join(cad, o.params + ".py")):
            ap.error("--model needs params.py next to the script (mechanics/cad)")
        root = os.path.abspath(os.path.join(cad, "..", "..")) if os.path.basename(os.path.dirname(cad)) == "mechanics" else cad
        return model_run(o, cad, root)
    if os.path.basename(o.script) == "assemble.py":
        ap.error("assemble.py builds the model: run it with --model (a plain run stores nothing)")
    import runpy                                        # any other script: run as `python script.py args` would
    sys.argv = [o.script] + o.args
    for d in dict.fromkeys([cad, os.path.dirname(os.path.abspath(o.script))]):
        if d not in sys.path:
            sys.path.insert(0, d)
    try:
        runpy.run_path(o.script, run_name="__main__")
    except SystemExit as e:
        return e.code or 0
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cad_incremental                       # one module: the hooks and MODEL live in cad_incremental, not __main__
    sys.exit(cad_incremental.main())
