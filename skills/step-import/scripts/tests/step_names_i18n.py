"""Words of part names in another language, for the tests: what a client's CAD writes in Cyrillic, so the tests cover the
STEP escapes of non-Latin names, transliteration and the importers' word tables (scripts/import_i18n.py) without Cyrillic
in the test code. Generic words only: no real machine, maker or client."""

NAMES = {
    "ru": {
        "code": "АБВГ",                    # a maker's project code before the drawing number
        "machine": "Станок",
        "unit": "Узел",
        "frame": "Рама",
        "plate": "Планка",
        "bent": "гнутая",
        "bracket": "Кронштейн",
        "bushing": "Втулка",
        "slab": "Плита",
        "nut": "Гайка",                    # import_i18n WORDS["ru"]["nut"]
        "screw": "Винт",                   # import_i18n WORDS["ru"]["screw"]
        "motor": "Двигатель",              # import_i18n WORDS["ru"]["motor"]
        "x": "х",                          # the Cyrillic look-alike of x in a size (M6х20)
        "printed": "печатная",             # import_i18n WORDS["ru"]["printed"]
        "polyamide": "капролон",           # import_i18n WORDS["ru"]["polyamide"]
    },
}
