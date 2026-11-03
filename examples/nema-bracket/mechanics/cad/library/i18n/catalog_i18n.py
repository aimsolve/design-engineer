"""The purchase-list texts of the catalogue's parts (desc, title, the stand-in note), per language: they reach the client's
purchase list and model page, so they come out in the project's language. A part imports this module
(`from library.i18n import catalog_i18n as T`) and the library sync copies the `i18n` group into the project with it.

The language is the project's `.engineer/locale.json` {"lang"}, found by walking up from this file (in a project it lies
in <project>/mechanics/cad/library/i18n/); no file is Russian, a language without a table here is English. A model build
records the file it opened, so changing the project's language rebuilds the parts that read it."""
import json
import os

DEFAULT = "ru"
FALLBACK = "en"

TEXT = {
    "ru": {
        "kfl.desc": "Подшипниковый узел {code}, фланцевый на 2 болта, вал Ø{bore:g} мм, крепёж M8",
        "kfl.title": "Подшипник {code}",
        "kfl.standin": ("габарит по каталожным размерам (d{bore:g}, межосевое {bolts:g}, {length:g}x{width:g}, ступица "
                        "{hub:g}); толщина фланца и Ø ступицы производителем не публикуются — заменяется STEP производителя"),
        "sc.desc": "Пневмоцилиндр Airtac {code}, Ø{bore:g} мм, ход {stroke:g} мм, резьба штока {thread}, порт {port}",
        "sc.title": "Пневмоцилиндр {code}",
        "sc.standin": ("габарит по каталожной таблице SC (корпус □{body:g}, длина {length:g}+ход, штанга Ø{rod:g}); "
                       "посадочные резьбы крышек и расположение портов не смоделированы — заменяется STEP производителя"),
        "profile.code": "Профиль {size:g}x{size:g}",
        "profile.desc": "Профиль алюминиевый конструкционный {size:g}x{size:g}, паз {slot:g}",
        "profile.title": "Профиль {size:g}x{size:g}",
        "profile.standin": ("упрощённый профиль {size:g}x{size:g} (габарит, пазы {slot:g} и торцы по каталогу; форма "
                            "внутренних перемычек не моделируется) — заменяется STEP производителя"),
    },
    "en": {
        "kfl.desc": "Bearing unit {code}, two-bolt flange, shaft Ø{bore:g} mm, M8 fasteners",
        "kfl.title": "Bearing {code}",
        "kfl.standin": ("envelope from the catalogue dimensions (d{bore:g}, bolt centres {bolts:g}, {length:g}x{width:g}, hub "
                        "{hub:g}); the flange thickness and the hub Ø are not published by the maker — replaced by the maker's STEP"),
        "sc.desc": "Pneumatic cylinder Airtac {code}, Ø{bore:g} mm, stroke {stroke:g} mm, rod thread {thread}, port {port}",
        "sc.title": "Pneumatic cylinder {code}",
        "sc.standin": ("envelope from the SC catalogue table (body □{body:g}, length {length:g}+stroke, rod Ø{rod:g}); the cap "
                       "mounting threads and the port positions are not modelled — replaced by the maker's STEP"),
        "profile.code": "Profile {size:g}x{size:g}",
        "profile.desc": "Aluminium construction profile {size:g}x{size:g}, slot {slot:g}",
        "profile.title": "Profile {size:g}x{size:g}",
        "profile.standin": ("simplified profile {size:g}x{size:g} (envelope, slots {slot:g} and end faces from the catalogue; "
                            "the inner web shape is not modelled) — replaced by the maker's STEP"),
    },
}


def _read_lang(path):
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > 65536:
            return DEFAULT
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return DEFAULT
    code = d.get("lang") if isinstance(d, dict) else None
    return code if code in TEXT else FALLBACK if isinstance(code, str) and code else DEFAULT


def lang():
    """The language of the project this file lies in (see the module docstring)."""
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(d, ".engineer")) or os.path.isdir(os.path.join(d, "mechanics")):
            return _read_lang(os.path.join(d, ".engineer", "locale.json"))
        up = os.path.dirname(d)
        if up == d:
            return DEFAULT
        d = up


def t(key, **fields):
    return TEXT[lang()][key].format(**fields)
