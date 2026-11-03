"""Texts of fea_static.py that a person reads (the picture, material names), by language. Russian is the default."""

TEXTS = {
    "ru": {
        "materials": {
            "S235": "сталь S235", "S355": "сталь S355", "AISI304": "нержавейка AISI 304",
            "AL5754": "алюминий АМг3 (5754 H22)", "AL6061T6": "алюминий 6061-T6",
            "PETG": "PETG, печать (вдоль слоёв)", "PLA": "PLA, печать (вдоль слоёв)", "PA-CF": "PA-CF, печать (вдоль слоёв)",
            "POM": "POM-C",
        },
        "colorbar": "напряжение по Мизесу, МПа",
        "summary": ("наибольшее напряжение {vm:.1f} МПа при пределе текучести {yld} МПа (запас {sf:.1f}); "
                    "наибольшее смещение {u:.3f} мм, на картинке ×{scale:.0f}; серое — закреплено"),
        "force": "{n:.3g} Н",
    },
    "en": {
        "materials": {
            "S235": "steel S235", "S355": "steel S355", "AISI304": "stainless AISI 304",
            "AL5754": "aluminium 5754 H22", "AL6061T6": "aluminium 6061-T6",
            "PETG": "PETG, printed (along the layers)", "PLA": "PLA, printed (along the layers)",
            "PA-CF": "PA-CF, printed (along the layers)", "POM": "POM-C",
        },
        "colorbar": "von Mises stress, MPa",
        "summary": ("peak stress {vm:.1f} MPa at a yield of {yld} MPa (safety {sf:.1f}); "
                    "peak deflection {u:.3f} mm, ×{scale:.0f} in the picture; grey = fixed"),
        "force": "{n:.3g} N",
    },
}
