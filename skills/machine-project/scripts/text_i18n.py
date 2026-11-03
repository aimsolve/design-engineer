"""Texts the framework puts in front of a person, per language: purchase-list rows and units (derive.py), the note that
replaces a failed drawing, the machine schemes' sheets (scheme_draw.py, schemes.py), the scaffolded fasteners' names
(scaffold.py, FASTENERS), the materials a client-CAD import writes (model_from_steps.py).

The language is the project's: `.engineer/locale.json` {"lang": ...} in the project root (`project_lang`); no file means
Russian, so a project that never chose a language gets what it always got. A language without its own table here gets
English. `t(key, lang, **fields)` formats one text."""
import json
import os

DEFAULT = "ru"
FALLBACK = "en"

TEXT = {
    "ru": {
        # derive.py: purchase list
        "unit.pcs": "шт",
        "unit.pcs_cut": "шт.",
        "unit.m": "м",
        "bought.by_length": "{name} (по модели {mm:.0f} мм, нарезать по месту)",
        "bought.cut_piece": "{stock} — отрезок {length:g} мм, {note} ({labels})",
        "bought.cut_total": "{total} ({count} отрезков)",
        "bought.wire_stock": "Провод {spec}",
        "bought.wire_piece": "{stock} — отрезок {length:g} мм ({labels})",
        "bought.wire_total": "{stock}, всего (отрезков: {count})",
        # derive.py: a laser part whose drawing failed
        "error.suffix": "_ОШИБКА",
        "error.text": "Чертёж не сделан: {reason}. Деталь не в комплекте — сообщите инженеру.\n",
        # scheme_draw.py / schemes.py: the scheme sheets
        "scheme.machine": "Машина",
        "scheme.electric": "Схема электрическая",
        "scheme.pneumatic": "Схема пневматическая",
        "scheme.from_model": " · по модели",
        "scheme.sheet": "Лист {n} из {total}",
        "scheme.air": "сжатый воздух",
        "scheme.legend.phase_l": "~230 В, фаза L",
        "scheme.legend.neutral": "ноль N",
        "scheme.legend.earth": "PE, заземление",
        "scheme.legend.zero": "0 В",
        "scheme.legend.motor_phases": "фазы двигателей",
        "scheme.legend.signals": "сигналы",
        "scheme.legend.marks": "знаки питания и 0 В: одна цепь без линии",
        "scheme.legend.flag": "флажок — одна цепь, где бы ни стоял",
        "scheme.legend.air": "сжатый воздух",
        "scheme.legend.vacuum": "вакуум",
        "scheme.legend.medium": "клей / среда",
        "scheme.legend.hoses": "H1… — трубки, длины и марки в таблице",
        "scheme.table.no": "№",
        "scheme.table.from": "Откуда",
        "scheme.table.to": "Куда",
        "scheme.table.net": "Цепь",
        "scheme.table.spec": "Провод / шланг",
        "scheme.harness": "Жгут проводов",
        "harness.note": "Трассы проводов внутри машины по модели (3D), вид — проекция; длины участков — между точками трассы, отрезы — в таблице, мм",
        "harness.table.route": "Трасса, мм",
        "harness.table.loop": "Запас, мм",
        "harness.table.cut": "Отрез, мм",
        "harness.table.od": "Ø, мм",
        "harness.total": "Всего проводов: {n}, отрезать {m:.2f} м (запас на укладку {slack:.0%}, на концы по {ends:g} мм)",
        # model_from_steps.py: a made part whose material the client's list does not name
        "material.steel": "Сталь",
    },
    "en": {
        "unit.pcs": "pcs",
        "unit.pcs_cut": "pcs",
        "unit.m": "m",
        "bought.by_length": "{name} ({mm:.0f} mm in the model, cut to fit)",
        "bought.cut_piece": "{stock} — piece {length:g} mm, {note} ({labels})",
        "bought.cut_total": "{total} ({count} pieces)",
        "bought.wire_stock": "Wire {spec}",
        "bought.wire_piece": "{stock} — piece {length:g} mm ({labels})",
        "bought.wire_total": "{stock}, in all ({count} pieces)",
        "error.suffix": "_ERROR",
        "error.text": "The drawing was not made: {reason}. The part is not in the kit — tell the engineer.\n",
        "scheme.machine": "Machine",
        "scheme.electric": "Electric scheme",
        "scheme.pneumatic": "Pneumatic scheme",
        "scheme.from_model": " · from the model",
        "scheme.sheet": "Sheet {n} of {total}",
        "scheme.air": "compressed air",
        "scheme.legend.phase_l": "~230 V, phase L",
        "scheme.legend.neutral": "neutral N",
        "scheme.legend.earth": "PE, earth",
        "scheme.legend.zero": "0 V",
        "scheme.legend.motor_phases": "motor phases",
        "scheme.legend.signals": "signals",
        "scheme.legend.marks": "supply and 0 V marks: one net without a line",
        "scheme.legend.flag": "flag — one net wherever it stands",
        "scheme.legend.air": "compressed air",
        "scheme.legend.vacuum": "vacuum",
        "scheme.legend.medium": "glue / medium",
        "scheme.legend.hoses": "H1… — hoses, lengths and types in the table",
        "scheme.table.no": "No.",
        "scheme.table.from": "From",
        "scheme.table.to": "To",
        "scheme.table.net": "Net",
        "scheme.table.spec": "Wire / hose",
        "scheme.harness": "Wiring harness",
        "harness.note": "The wires' routes inside the machine from the model (3D), drawn as a projection; segment lengths between route points, cut lengths in the table, mm",
        "harness.table.route": "Route, mm",
        "harness.table.loop": "Loop, mm",
        "harness.table.cut": "Cut, mm",
        "harness.table.od": "Ø, mm",
        "harness.total": "Wires: {n}, cut {m:.2f} m in all (laying allowance {slack:.0%}, {ends:g} mm per end)",
        "material.steel": "Steel",
    },
}

# A purchase-list unit a part may give as a language-neutral key: derive.py shows it in the project's language. Any other
# unit (one a project spelled itself, in any language) goes into the list as it is written.
UNITS = {"pcs": "unit.pcs"}

# scaffold.py: the bought fasteners every build123d project starts with (templates/screw.py, nut.py, washer.py): their
# purchase-list names, written into the project's own copies when it is scaffolded. The templates carry the English ones.
FASTENERS = {
    "ru": {
        "screw": {"WORD": "Винт",
                  "NAMES": {"DIN 912": "Винт с внутренним шестигранником", "ISO 7380": "Винт с полукруглой головкой",
                            "ISO 10642": "Винт с потайной головкой", "DIN 7985": "Винт с полукруглой головкой под крест",
                            "DIN 7984": "Винт с низкой головкой и внутренним шестигранником",
                            "DIN 933": "Болт с шестигранной головкой"}},
        "nut": {"WORD": "Гайка",
                "NAMES": {"DIN 934": "Гайка", "DIN 985": "Гайка самоконтрящаяся", "ISO 4032": "Гайка шестигранная",
                          "ISO 4034": "Гайка шестигранная (класс C)"}},
        "washer": {"WORD": "Шайба"},
    },
}

# derive.py: Cyrillic letters spelled in Latin, for file names that must stay ASCII (a failed drawing's note)
TRANSLIT = {
    "ru": {**dict(zip("абвгдезийклмнопрстуфыэ", "a b v g d e z i i k l m n o p r s t u f y e".split())),
           "ж": "zh", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ю": "yu", "я": "ya", "ё": "e", "ъ": "",
           "ь": ""},
}


def lang_of(code):
    """The language whose texts are used for a project language code."""
    return code if code in TEXT else FALLBACK if isinstance(code, str) and code else DEFAULT


def project_lang(root="."):
    """The project's language from `<root>/.engineer/locale.json`; no file (or anything but a small regular file holding
    an object) is the Russian default."""
    p = os.path.join(str(root), ".engineer", "locale.json")
    try:
        if not os.path.isfile(p) or os.path.getsize(p) > 65536:
            return DEFAULT
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return DEFAULT
    return lang_of(d.get("lang")) if isinstance(d, dict) else DEFAULT


def t(key, lang=DEFAULT, **fields):
    s = TEXT[lang_of(lang)][key]
    return s.format(**fields) if fields else s


def unit(u, lang=DEFAULT):
    """A part's purchase-list unit as the list shows it: a neutral key (UNITS) in the project's language, else as written."""
    return t(UNITS[u], lang) if u in UNITS else u


def translit():
    """Every language's letters spelled in Latin, upper case included."""
    out = {c: v for table in TRANSLIT.values() for c, v in table.items()}
    out.update({c.upper(): v for c, v in list(out.items())})
    return out
