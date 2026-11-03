"""Texts of the shop drawings and kit files a person reads, by language, and the words the checker recognises on a drawing.

The language is the project's: `.engineer/locale.json` {"lang": ...} in the directory the scripts run from (a machine
project runs them from its root), or the `--lang` option of the scripts. No file means Russian, so a project that never
chose a language gets what it always got; a language without its own table here gets English.

TEXT[lang][key] is one text (`t(key, lang, **fields)` formats it). RECOGNISE[lang][key] is a regex fragment the checker
accepts in any language, so a set drawn in one language (or by hand in KOMPAS / SolidWorks) is read whatever the project's
language is. MATERIAL_HINTS are substrings of a material text that tell its kind (folder word, K factor, zinc note)."""
import json
import os

DEFAULT = "ru"
FALLBACK = "en"

TEXT = {
    "ru": {
        # bend label along each bend line of the flat pattern
        "dir.up": "ВВЕРХ",
        "dir.down": "ВНИЗ",
        "bend.turn": "отгиб {angle}°",
        "bend.between": "угол между полками {between}°",
        # sheet 1
        "flat.bent": "РАЗВЁРТКА",
        "flat.plain": "ЗАГОТОВКА",
        "weld.mark": "Ш1",
        "note.ref": "*Размер для справок.",
        "note.theor": "**Размеры развёртки теоретические.",
        "note.tol": "Неуказанные предельные отклонения по ГОСТ 30893.1.",
        "note.qty": "Кол-во - {qty} шт.",
        "note.holes_sheet2": "Размеры отверстий и вырезов - лист 2.",
        "note.no_holes": "Отверстий и вырезов нет.",
        "weld.default": "сварить после гибки сплошным угловым швом катет 1 по всей высоте отгиба, снаружи",
        "weld.where": "стыки отгибов в углах ({n} шт.) ",
        "weld.note": "{mark}: {where}{text}; шов зачистить заподлицо",
        "weld.zinc": "; место сварки закрасить цинкосодержащей краской.",
        # sheet 2
        "holes.title": "РАЗМЕРЫ ОТВЕРСТИЙ И ВЫРЕЗОВ ({flat}НАЧАЛО 0,0 - левый нижний угол = 0,0 файла DXF)",
        "holes.title_flat": "развёртка; ",
        "holes.cols": ["№", "X, мм", "Y, мм", "Ø, мм"],
        "holes.tap": " (под {tap})",
        "holes.count": "{n} шт.",
        "holes.perforation": "{n} шт. (перфорация, координаты по DXF)",
        "holes.cutouts": "вырезы: {n} шт.",
        "holes.none": "отверстий и вырезов нет",
        "holes.laser": "Отверстия и вырезы - лазер по {flat}(dxf). ",
        "holes.laser_flat": "развёртке ",
        "thread": "резьба",
        # title block
        "title.scale": "Масштаб {scale}",
        "title.sheet": "Лист {no}   Листов {of}",
        "title.qty": "Кол-во: {qty} шт.",
        "mm": "мм",
        # the kit
        "group.steel": "сталь",
        "group.stainless": "нержавейка",
        "group.alu": "алюминий",
        "kit.folder": "{t} мм {group}",
        "kit.list_cols": ["папка", "деталь", "кол-во", "материал", "s, мм", "гибов", "R внутр., мм", "заготовка, мм", "файлы"],
        "kit.readme": ("Комплект металла по папкам: толщина и материал.\n"
                       "У КАЖДОЙ детали три файла с одним именем: .dxf — контур для лазера (у гнутых — развёртка), .pdf — чертёж, "
                       ".step — 3D готовой детали.\n"
                       "DXF для лазера: AutoCAD 2000, миллиметры, только замкнутые контуры реза на слое 0, без текста; "
                       "линии гибов — на чертежах PDF.\n"
                       "Список: spisok.csv (разделитель ;).\n"),
        # assembly drawings
        "assy.step_row": "Сборка «{title}»",
        "assy.step_code": "шаг {no}",
        "assy.node": "Сборка узла",
        "assy.step_head": "Шаг {no}. ",
        "assy.cols": ["№", "Деталь", "Код", "Кол-во"],
        "assy.qty": "{n} шт.",
        "assy.note_explode": "Детали разнесены вдоль оси стыковки; штриховая линия — куда деталь встаёт.",
        "assy.note_fasteners": "Крепёж: размер и количество в таблице (×N).",
        "assy.note_make": "Собрать {n} шт.",
        "assy.note_sheets": "Узел на {n} листах; сборки шагов собрать раньше, чем узел.",
        "assy.kind": "Сборочный чертёж",
        "assy.code": "СБ {no}/{of}",
        "assy.metric": "М",            # the thread prefix in a fastener size, as the documents in this language write it
        # checker (check_drawings.py, laser_dxf.check): the reasons a part fails, shown in the note that replaces its drawing
        "chk.missing": "нет файла {files} (у каждой детали dxf + pdf + step с одним именем)",
        "chk.no_text": "в PDF нет текста (надписи кривыми): проверить нельзя — перегенерировать shop_drawing.py или экспорт с текстом",
        "chk.no_material": "нет материала (марка) в штампе / примечаниях",
        "chk.no_thickness": "нет толщины металла (ТОЛЩИНА / s = N мм)",
        "chk.thickness_folder": "толщина на чертеже {found} не равна папке комплекта {t} мм",
        "chk.no_radius": "гнутая деталь без внутреннего радиуса гиба (R N внутр. / РАДИУС ГИБА)",
        "chk.no_direction": "гнутая деталь без направления гиба (ВВЕРХ / ВНИЗ)",
        "chk.no_flat": "гнутая деталь без листа РАЗВЁРТКА",
        "chk.no_theor": "гнутая деталь: на развёртке нет размеров «**» (теоретические)",
        "chk.no_blank": "нет габарита заготовки {w} x {h}",
        "chk.no_hole_sheet": "есть отверстия, но нет листа «размеры отверстий и вырезов» с таблицей X, мм / Y, мм",
        "chk.no_datum": "есть отверстия, но нет базы отсчёта (ORIGIN 0,0 / БАЗА)",
        "chk.hole_dims": "нет размеров до центров {n} из {of} отверстий от 0,0 заготовки, напр. {examples}",
        "dxf.not_ascii": "DXF не ASCII",
        "dxf.version": "DXF {version}, нужен AutoCAD 2000 (AC1015)",
        "dxf.layer": "DXF: {kind} на слое {layer}, нужен слой 0",
        "dxf.extra": "DXF: лишние объекты {kinds} (только замкнутые контуры и окружности)",
        "dxf.open": "DXF: {n} незамкнутых контуров",
        "dxf.no_outline": "DXF: нет наружного контура",
    },
    "en": {
        "dir.up": "UP",
        "dir.down": "DOWN",
        "bend.turn": "bend {angle}°",
        "bend.between": "angle between flanges {between}°",
        "flat.bent": "FLAT PATTERN",
        "flat.plain": "BLANK",
        "weld.mark": "W1",
        "note.ref": "*Reference dimension.",
        "note.theor": "**Flat pattern dimensions are theoretical.",
        "note.tol": "General tolerances to ISO 2768-1.",
        "note.qty": "Quantity - {qty} pcs.",
        "note.holes_sheet2": "Hole and cut-out positions - sheet 2.",
        "note.no_holes": "No holes or cut-outs.",
        "weld.default": "weld after bending, continuous fillet weld, leg 1, along the full flange height, outside",
        "weld.where": "flange joints at the corners ({n} pcs) ",
        "weld.note": "{mark}: {where}{text}; grind the seam flush",
        "weld.zinc": "; paint the weld area with zinc-rich paint.",
        "holes.title": "HOLES AND CUT-OUTS ({flat}ORIGIN 0,0 - lower-left corner = 0,0 of the DXF file)",
        "holes.title_flat": "flat pattern; ",
        "holes.cols": ["No.", "X, mm", "Y, mm", "Ø, mm"],
        "holes.tap": " (for {tap})",
        "holes.count": "{n} pcs",
        "holes.perforation": "{n} pcs (perforation, positions in the DXF)",
        "holes.cutouts": "cut-outs: {n} pcs",
        "holes.none": "no holes or cut-outs",
        "holes.laser": "Holes and cut-outs: laser cut from the {flat}DXF. ",
        "holes.laser_flat": "flat pattern ",
        "thread": "thread",
        "title.scale": "Scale {scale}",
        "title.sheet": "Sheet {no}   Sheets {of}",
        "title.qty": "Qty: {qty} pcs",
        "mm": "mm",
        "group.steel": "steel",
        "group.stainless": "stainless",
        "group.alu": "aluminium",
        "kit.folder": "{t} mm {group}",
        "kit.list_cols": ["folder", "part", "qty", "material", "t, mm", "bends", "inner R, mm", "blank, mm", "files"],
        "kit.readme": ("Sheet-metal kit, one folder per thickness and material.\n"
                       "EVERY part has three files with one name: .dxf - the laser contour (the flat pattern of a bent part), "
                       ".pdf - the drawing, .step - the finished part in 3D.\n"
                       "Laser DXF: AutoCAD 2000, millimetres, only closed cut contours on layer 0, no text; "
                       "bend lines are on the PDF drawings.\n"
                       "Parts list: spisok.csv (separator ;).\n"),
        "assy.step_row": "Assembly “{title}”",
        "assy.step_code": "step {no}",
        "assy.node": "Node assembly",
        "assy.step_head": "Step {no}. ",
        "assy.cols": ["No.", "Part", "Code", "Qty"],
        "assy.qty": "{n} pcs",
        "assy.note_explode": "Parts are pulled apart along their docking axis; a dashed line shows where a part goes.",
        "assy.note_fasteners": "Fasteners: size and quantity in the table (×N).",
        "assy.note_make": "Assemble {n} pcs.",
        "assy.note_sheets": "The node takes {n} sheets; assemble the step sub-assemblies before the node.",
        "assy.kind": "Assembly drawing",
        "assy.code": "ASM {no}/{of}",
        "assy.metric": "M",
        "chk.missing": "missing {files} (every part has dxf + pdf + step with one name)",
        "chk.no_text": "the PDF has no text (lettering as curves): cannot be checked - regenerate with shop_drawing.py or export with text",
        "chk.no_material": "no material (grade) in the title block / notes",
        "chk.no_thickness": "no sheet thickness (THICKNESS / t = N mm)",
        "chk.thickness_folder": "thickness on the drawing {found} differs from the kit folder {t} mm",
        "chk.no_radius": "bent part without the inner bend radius (inner R N / BEND RADIUS)",
        "chk.no_direction": "bent part without the bend direction (UP / DOWN)",
        "chk.no_flat": "bent part without a FLAT PATTERN sheet",
        "chk.no_theor": "bent part: no «**» (theoretical) dimensions on the flat pattern",
        "chk.no_blank": "no overall blank size {w} x {h}",
        "chk.no_hole_sheet": "has holes but no hole sheet with the X, mm / Y, mm table",
        "chk.no_datum": "has holes but no datum (ORIGIN 0,0)",
        "chk.hole_dims": "no dimensions to the centres of {n} of {of} holes from the blank's 0,0, e.g. {examples}",
        "dxf.not_ascii": "DXF is not ASCII",
        "dxf.version": "DXF {version}, AutoCAD 2000 (AC1015) needed",
        "dxf.layer": "DXF: {kind} on layer {layer}, layer 0 needed",
        "dxf.extra": "DXF: extra entities {kinds} (closed contours and circles only)",
        "dxf.open": "DXF: {n} open contours",
        "dxf.no_outline": "DXF: no outer contour",
    },
}

# What the checker accepts on a drawing, per language (regex fragments; every language's are accepted at once).
RECOGNISE = {
    "ru": {
        "material": r"Ст\s?\d|12Х18Н10Т|08Х18Н10|08пс|09Г2С|Д16|АМг|оцинк|сталь|нерж|алюм|латун|медь",
        "thickness": r"толщин\w*(?:\s+металла)?(?:\s*s)?\s*=?",
        "bend_label": r"(?:ВВЕРХ|ВНИЗ),\s*отгиб\s*\d+\s*°",
        "bend_word": r"радиус\w*\s+гиба|гиб\w*|внутр\.?",
        "direction": r"ВВЕРХ|ВНИЗ",
        "flat": r"РАЗВ[ЁЕ]РТК",
        "table": r"X,\s*мм[\s\S]{0,40}Y,\s*мм",
        "datum": r"\bБАЗА\b",
        "perforation": r"шт\.\s*\(перфорац",
        "mm": r"мм",
    },
    "en": {
        "material": r"AISI|AlMg|DC0\d|S235|S275|S355|1\.43\d\d|1\.44\d\d|\b(?:5754|5083|6061|6082|7075)\b|steel|stainless|"
                    r"alumin\w*|galvan\w*|zinc|brass|copper",
        "thickness": r"thickness\s*=?|\bTHK\.?",
        "bend_label": r"(?:UP|DOWN),\s*bend\s*\d+\s*°",
        "bend_word": r"bend\w*|inner",
        "direction": r"\bUP\b|\bDOWN\b",
        "flat": r"FLAT PATTERN",
        "table": r"X,\s*mm[\s\S]{0,40}Y,\s*mm",
        "datum": r"ORIGIN|DATUM",
        "perforation": r"pcs\s*\(perforation",
        "mm": r"mm",
    },
}

# Substrings (lower case) of a material text that tell its kind; a material matching none is steel.
MATERIAL_HINTS = {
    "stainless": ["нерж", "12х18", "08х18", "aisi", "1.4301", "1.4404", "stainless", "inox"],
    "alu": ["алюм", "амг", "д16", "alu", "6061", "6082", "5754", "5083"],
    "zinc": ["оцинк", "galvan", "zinc"],
}

# Characters a fastener code may use for "times" between two numbers (M5x12): the Latin x and its look-alikes.
TIMES_LOOKALIKES = "xх"


def lang_of(code):
    """The language whose texts are used for a language code: its own table, else English; none given -> the default."""
    return code if code in TEXT else FALLBACK if isinstance(code, str) and code else DEFAULT


def project_lang(root="."):
    """The project's language from `<root>/.engineer/locale.json`; no readable file holding an object -> the default."""
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


def recognise(key):
    """One regex alternation of `key` over every language."""
    return "|".join(f"(?:{v[key]})" for v in RECOGNISE.values())


def material_kind(text):
    """"stainless" / "alu" / "steel" from a material text in any language."""
    m = str(text or "").lower()
    return next((k for k in ("stainless", "alu") if any(x in m for x in MATERIAL_HINTS[k])), "steel")


def is_zinc(text):
    m = str(text or "").lower()
    return any(x in m for x in MATERIAL_HINTS["zinc"])


def group_kind(word):
    """The kind whose folder word (in any language) is `word`, else None."""
    for table in TEXT.values():
        for k in ("steel", "stainless", "alu"):
            if table[f"group.{k}"] == word:
                return k
    return None
