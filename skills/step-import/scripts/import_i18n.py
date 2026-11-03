"""Language tables of step-import.

WORDS: what a client's CAD calls a part, per language: regex fragments the importers read part names with (step_import.py
kind and material, params.py types, step_to_project.py fasteners and processes). A CAD's names are in its designer's
language, not in the project's, so every language is read at once: `words(key)` joins the fragments of all languages into
one alternation. The English words live in the code's own regexes; a table here adds a language. A new language: a table
with the same keys (a missing key adds nothing).

LOOKALIKES: letters of another script that look like Latin ones and turn up inside sizes and codes (`M6х20` typed with a
Cyrillic `х`): `latinise` maps them to Latin, `like(c)` gives every look-alike of one Latin letter, for a character class.

TRANSLIT: letters -> Latin, for the Python identifiers step_to_project.py makes from part names.

TEXT: what the import writes for a person (the material and unit of a purchase-list row), in the project's language:
`.engineer/locale.json` {"lang": ...} (`project_lang`); no file means Russian, so a project that never chose a language
gets what it always got. A language without its own table gets English."""
import json
import os

WORDS = {
    "ru": {
        # step_import.py: standard parts (kind) and the material classes
        "standard": r"ГОСТ",
        "fastener": r"шайба|гайка|винт|болт|заклепка|шпилька|ножка",
        "bearing": r"подшипник",
        "glass": r"стекл",
        "rubber": r"лент[аы]|ремень",
        "din_rail": r"din рейка",
        "electronics": r"клемм|датчик|индикатор|ацп|радиатор",
        "motor": r"шагов|двигател",
        "pneumatic": r"пневмо|присоск|тройник|фитинг|цилиндр",
        "printed": r"печатн",
        # params.py: part types and their params
        "pulley": r"шкив",
        "belt": r"ремень",
        "conveyor_belt": r"лента",
        "foot": r"ножка",
        "stud": r"шпилька",
        "rivet_nut": r"заклепка резьбовая",
        "screw": r"винт",
        "press_in": r"запрессов",
        "tee": r"тройник",
        "flow_control": r"регулятор расхода",
        "pneumatic_fitting": r"тройник qb|пневмофитинг",
        "suction_cup": r"присоск",
        "terminal_block": r"клеммный терминал",
        "indicator_led": r"индикатор",
        "heatsink": r"радиатор",
        "adc_board": r"ацп",
        # step_to_project.py: the fastener templates and the processes
        "washer": r"шайба",
        "nut": r"гайка",
        "screw_or_bolt": r"винт|болт",
        "polyamide": r"капр[ао]лон|полиамид",
    },
}

LOOKALIKES = {
    "ru": ("хХМТАВСЕНКОРмтасекор", "xXMTABCEHKOPmtacekop"),
}

TRANSLIT = {
    "ru": dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                   "a b v g d e e zh z i y k l m n o p r s t u f kh ts ch sh shch _ y _ e yu ya".split())),
}

DEFAULT = "ru"
FALLBACK = "en"

TEXT = {
    "ru": {
        "material.printed": "пластик (печать)",
        "material.polyamide": "капролон",
        "material.steel": "сталь",
        "unit.pcs": "шт",
    },
    "en": {
        "material.printed": "plastic (printed)",
        "material.polyamide": "polyamide",
        "material.steel": "steel",
        "unit.pcs": "pcs",
    },
}

_NEVER = r"(?!x)x"


def words(key):
    """The fragments every language has for `key`, as one group `(?:a|b)`; a group that never matches when none has it."""
    alts = [t[key] for t in WORDS.values() if key in t]
    return "(?:" + "|".join(alts) + ")" if alts else _NEVER


_LOOK = str.maketrans("".join(a for a, _ in LOOKALIKES.values()), "".join(b for _, b in LOOKALIKES.values()))


def latinise(s):
    """`s` with every look-alike letter replaced by its Latin twin."""
    return (s or "").translate(_LOOK)


def like(c):
    """Every look-alike of the Latin letter `c`, for a regex character class (the letter itself not included)."""
    return "".join(a for src, dst in LOOKALIKES.values() for a, b in zip(src, dst) if b == c)


def translit(c):
    """One lower-case letter in Latin: its transliteration, or the letter itself."""
    for t in TRANSLIT.values():
        if c in t:
            return t[c]
    return c


def lang_of(code):
    """The language whose texts are used for a project language code."""
    return code if code in TEXT else FALLBACK if isinstance(code, str) and code else DEFAULT


def project_lang(root="."):
    """The project's language from `<root>/.engineer/locale.json`; no file (or anything but a small regular file holding
    an object) is the default."""
    p = os.path.join(str(root), ".engineer", "locale.json")
    try:
        if not os.path.isfile(p) or os.path.getsize(p) > 65536:
            return DEFAULT
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return DEFAULT
    return lang_of(d.get("lang")) if isinstance(d, dict) else DEFAULT


def t(key, lang=DEFAULT):
    """One text in `lang` (English when that language lacks it)."""
    return TEXT.get(lang_of(lang), {}).get(key) or TEXT[FALLBACK][key]
