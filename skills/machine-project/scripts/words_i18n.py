"""What the framework's checks recognise in the text people write (part names and descriptions, materials, net and port
labels, wiki pages), per language. A matcher takes the fragments of EVERY language (`alt`, `chars`), so a project in one
language is read the same way as a project in another, and a part list that mixes them is read whole.

WORDS[lang][key] is a list of regular-expression fragments (joined with `|`, case as the matcher sets it) or, for a key
used inside a character class, a string of characters. Adding a language is adding its fragments under its code; a key
a language does not have is simply absent there."""

WORDS = {
    "en": {
        # cad_gates: what a part's material says it is
        "metal": [r"steel", r"aisi", r"s235", r"alumin", r"brass", r"copper", r"metal", r"galvan", r"\bamg", r"titan",
                  r"bronze"],
        "soft": [r"plastic", r"silicone", r"rubber", r"petg", r"\bpla\b", r"\babs\b", r"\btpu\b", r"printed", r"nylon",
                 r"polyurethane", r"ptfe", r"foam"],
        # check: a number with a unit in a wiki line
        "unit": [r"mm", r"cm", r"kg", r"N", r"Nm", r"deg", r"°", r"rpm", r"V", r"mA", r"W", r"g", r"MPa", r"bar", r"%"],
        # fit_checks: fasteners and purchase texts
        "m_letter": "M",                       # the M of a thread size (M5x12)
        "x_letter": "x×",                      # the x between its numbers
        "length_word": [],
        "mm_word": [],
        "female": [r"\bnut\b", r"t-?nut", r"rivet\s*nut", r"insert"],
        "male": [r"\bscrew\b", r"\bbolt\b", r"\bstud\b", r"threaded\s+rod", r"\bdowel\b"],
        "washer": [r"washer"],
        "standard": [r"DIN(?:\s*EN)?", r"ISO", r"EN\s*ISO", r"GOST"],
        "analogue": [r"\banalogue\b", r"\banalog\b", r"\bor equivalent\b"],
        "letters": "a-z",                      # lower-case letters, as a character-class range
        # kb_lint: wiki pages
        "superseded": [r"superseded", r"~~"],
        "fix_ref": [r"\b[RCD]\d+\b", r"\]\(", r"\bgate\b", r"\bfix(ed)?\b", r"\bcommit\b", r"`[^`]+\.(py|mjs|js|json)`"],
        # scheme_draw: electric elements and nets
        "volt": [r"V"],
        "supply_net": [r"VCC", r"VDD", r"VBUS", r"VIN", r"VSYS", r"\+V"],
        "ground_net": [r"GND", r"AGND", r"DGND", r"-V"],
        "role_supply": [r"power", r"psu", r"battery", r"li-?po", r"charger", r"dc-?dc", r"lm2596", r"\bbus\b", r"breaker",
                        r"\bqf", r"mains"],
        "role_input": [r"sensor", r"button", r"tact", r"switch", r"encoder", r"\badc", r"hx711", r"ads1\d", r"load.?cell",
                       r"sonar"],
        "role_actuator": [r"motor", r"coil", r"valve", r"lamp", r"buzzer", r"heater", r"fan\b"],
        "role_module": [r"driver", r"module", r"controller", r"board", r"relay"],
        "motor": [r"motor"],
        "button": [r"button", r"tact"],
        "normally_closed": [r"\bNC\b"],
        "breaker": [r"breaker", r"\bqf\b"],
        "battery": [r"battery", r"li-?po", r"18650"],
        "phase_l": [r"L\d?"],
        "motor_phase": [r"phase", r"A[+-]", r"B[+-]"],
        # scheme_draw: pneumatic elements, ports and media
        "valve": [r"valve"],
        "glue": [r"glue"],
        "sym_frl": [r"frl", r"\baw\d", r"\bac\d"],
        "sym_filter": [r"filter"],
        "sym_regulator": [r"regulator"],
        "sym_ejector": [r"ejector"],
        "sym_cup": [r"suction", r"\bcup\b"],
        "sym_silencer": [r"silencer", r"muffler"],
        "sym_tee": [r"\btee\b"],
        "sym_fitting": [r"bulkhead"],
        "sym_tank": [r"tank", r"reservoir"],
        "sym_cylinder": [r"cylinder"],
        "sym_source": [r"compressor"],
        "vacuum": [r"vacuum"],
        "medium": [r"glue", r"oil", r"water"],
        "air_supply": [r"supply"],
        "air_source_port": [r"supply"],
        "air_exhaust_port": [r"exhaust"],
        "port_in": [r"in", r"1", r"p"],
        "port_air": [r"open", r"close", r"a", r"b", r"1", r"2", r"\+", r"-"],
    },
    "ru": {
        "metal": [r"стал", r"нерж", r"алюмин", r"латун", r"медь", r"металл", r"оцинк", r"амг", r"д16", r"титан", r"бронз"],
        "soft": [r"пластик", r"пластмасс", r"силикон", r"резин", r"печатн", r"нейлон", r"полиуретан", r"фторопласт",
                 r"капролон", r"поролон"],
        "unit": [r"мм", r"см", r"кг", r"Н", r"Н·м", r"об/мин", r"В", r"мА", r"Вт", r"г", r"МПа", r"бар"],
        "m_letter": "М",
        "x_letter": "х",
        "length_word": [r"длина"],
        "mm_word": [r"мм"],
        "female": [r"гайк", r"сухар", r"заклёпк[аи]\s+резьб", r"заклепк[аи]\s+резьб", r"заклёпочная\s+гайка",
                   r"втулка\s+резьб", r"вставк", r"резьбовая\s+втулка"],
        "male": [r"винт", r"болт", r"шпильк", r"саморез", r"штифт", r"шуруп", r"резьбовой\s+стержень"],
        "washer": [r"шайб"],
        "standard": [r"ГОСТ", r"ОСТ"],
        "analogue": [r"\bанал[оа]г\w*", r"или\s+подобн\w*"],
        "letters": "а-яё",
        "superseded": [r"отменено", r"отменён"],
        "fix_ref": [r"\bгейт"],
        "volt": [r"В"],
        "supply_net": [],
        "ground_net": [r"общий", r"земля", r"масса"],
        "role_supply": [r"питан", r"блок пит", r"аккум", r"батар", r"зарядк", r"преобраз", r"шина", r"автомат", r"сеть\b",
                        r"трансформ"],
        "role_input": [r"датчик", r"кнопк", r"выключат", r"энкодер", r"концев", r"ацп", r"тензо", r"потенциом", r"джойст",
                       r"сонар", r"ультразв"],
        "role_actuator": [r"двигател", r"мотор", r"катушк", r"клапан", r"лампа", r"сирен", r"зуммер", r"нагрев",
                          r"вентилят"],
        "role_module": [r"драйвер", r"модуль", r"контроллер", r"плата", r"реле", r"преобразоват"],
        "motor": [r"двигател", r"мотор"],
        "button": [r"кнопк", r"грибок"],
        "normally_closed": [r"НЗ", r"размык"],
        "breaker": [r"автомат"],
        "battery": [r"аккумул", r"батаре"],
        "phase_l": [r"фаза L"],
        "motor_phase": [r"фаза", r"мотор", r"обмотк"],
        "valve": [r"распредел", r"клапан"],
        "glue": [r"клей"],
        "sym_frl": [r"подготовк", r"\bаw\d", r"блок подгот"],
        "sym_filter": [r"фильтр"],
        "sym_regulator": [r"регулятор", r"редуктор"],
        "sym_ejector": [r"эжектор", r"вакуумн.*генератор"],
        "sym_cup": [r"присоск"],
        "sym_silencer": [r"глушит"],
        "sym_tee": [r"тройник", r"крестовин"],
        "sym_fitting": [r"проходн", r"фитинг", r"штуцер", r"муфт"],
        "sym_tank": [r"бачок", r"\bбак\b", r"ресивер", r"ёмкост", r"емкост"],
        "sym_cylinder": [r"цилиндр"],
        "sym_source": [r"компрессор"],
        "vacuum": [r"вакуум"],
        "medium": [r"клей", r"масл", r"вода"],
        "air_supply": [r"компрессор"],
        "air_source_port": [r"компрессор", r"сет[ьи]", r"подвод", r"от\b"],
        "air_exhaust_port": [r"выхлоп", r"сброс"],
        "port_in": [r"вход"],
        "port_air": [r"откр", r"закр"],
    },
}

# export_robot: g/cm^3 by a word of the part's `material` (see export_robot.DENSITY)
DENSITY = {
    "en": {"stainless": 7.9, "aisi304": 7.9, "aisi 304": 7.9,
           "steel": 7.85, "s235": 7.85, "s355": 7.85, "iron": 7.85,
           "brass": 8.5, "bronze": 8.8, "copper": 8.96,
           "alumin": 2.70, "al5754": 2.70, "al6061": 2.70,
           "petg": 1.27, "pla": 1.24, "abs": 1.04, "pa-cf": 1.20, "nylon": 1.14, "pom": 1.41,
           "pet": 1.38, "pvc": 1.40, "acrylic": 1.18, "pmma": 1.18, "polycarb": 1.20,
           "silicone": 1.15, "rubber": 1.20, "plastic": 1.20,
           "wood": 0.65, "glass": 2.5},
    "ru": {"нержав": 7.9, "сталь": 7.85, "латунь": 8.5, "амг": 2.70, "алюмин": 2.70, "пластик": 1.20, "дерев": 0.65},
}


def alt(key):
    """The fragments of `key` in every language as one alternation, without a group around it."""
    return "|".join(f for words in WORDS.values() for f in words.get(key, ()))


def chars(key):
    """The characters of `key` in every language, for a character class."""
    return "".join(words.get(key, "") for words in WORDS.values())


def density():
    """DENSITY of every language as one mapping."""
    return {k: v for table in DENSITY.values() for k, v in table.items()}
