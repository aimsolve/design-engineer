"""Texts in a project's own language that the tests feed the framework: part names, net labels, purchase texts and wiki
lines as a Russian project writes them, so the paths that read Russian (symbols and roles on a scheme, a fastener read off
its name, a material, the fonts of a drawing) stay covered. SAMPLES[lang][key]; a `{...}` field is filled by the test."""

SAMPLES = {
    "ru": {
        # test_schemes_drawing: electric elements and nets
        "psu": "Блок питания 24 В",
        "ctrl_card": "Контроллер ESP32",
        "ctrl_desc": "плата управления",
        "driver": "Драйвер шагового TB6600",
        "stepper": "Шаговый двигатель",
        "relay": "Реле K1",
        "relay_card": "Реле из карточки",
        "sensor": "Датчик положения",
        "p24": "+24 В",
        "p0": "0 В",
        "block": "Блок {p}",
        "line_ctrl": "Контроллер линии",
        "sensor_n": "Датчик №{i:02d}",
        "board_a": "Плата А",
        "board_b": "Плата Б",
        # test_schemes_drawing: pneumatic elements
        "frl_full": "Блок подготовки воздуха (фильтр-регулятор)",
        "frl": "Блок подготовки воздуха",
        "coil": "катушка",
        "valve_52": "Пневмораспределитель 5/2 с электромагнитом",
        "cylinder": "Пневмоцилиндр двустороннего действия",
        "silencer": "Глушитель",
        "vacuum_cup": "Вакуумная присоска",
        "valve_n": "Клапан {n}",
        "cup_n": "Присоска {n}",
        # test_schemes_model: a page link labelled in the project's language
        "link_scheme": "Схема",
        "link_scheme_2w": "Электрическая схема",
        # test_fit_pairs / test_fit_review / test_fit_static: fasteners and purchase texts
        "m_letter": "М",
        "screw_socket_m5x12": "Винт с внутренним шестигранником M5x12 DIN 912",
        "bolt_hex_m6x20": "Болт с шестигранной головкой DIN EN 24017 M6x20",
        "nut_hex_m6": "Гайка шестигранная DIN 934 M6",
        "nut_hex_m12": "Гайка шестигранная ISO 4032 M12",
        "rivet_nut_m10": "Заклепка резьбовая М10",
        "screw_socket_m5x10": "Винт с внутренним шестигранником M5x10 DIN 912",
        "tnut_m5": "Сухарь пазовый М5, паз 6, A264",
        "screw_socket_m6x10": "Винт с внутренним шестигранником DIN 912 (ISO 4762) M6×10",
        "rivet_nut_m6": "Заклёпочная гайка сталь, плоский бортик (Goebel GO-NUT) M6, отв. 9.1",
        "screw_button_m4x8": "Винт с полукруглой головкой M4x8 ISO 7380",
        "washer_m4": "Шайба M4 DIN 125",
        "screw_m5x12": "Винт M5x12 DIN 912",
        "screw": "Винт {spec}",
        "or_analogue": "{code} или аналог",
        "any_maker_analogue": "можно аналог любого производителя",
        "analogue_taken": "взят аналог, размеры те же",
        "analogue_word": "аналог",
        "male_fasteners": ["Винт M5x12 DIN 912", "Штифт M5x12", "Шуруп M5x12", "Резьбовой стержень M5x12",
                           "Крепёж нержавеющий M5x12 DIN 912"],
        "not_male_fasteners": ["Шайба M5 DIN 125", "Гайка M5 DIN 934", "Сухарь пазовый М5, паз 6", "Заклёпка резьбовая М5"],
        "screw_spec_cases": [("Винт с внутренним шестигранником M5x12 DIN 912", (5.0, 12.0)),
                             ("Винт М5х10 DIN 912", (5.0, 10.0)),
                             ("Винт M1.6x8", (1.6, 8.0)),
                             ("Винт M12x1.5x40 DIN 912", (12.0, 40.0)),          # a fine pitch, then the length
                             ("Винт М5 DIN 912, длина 30 мм", (5.0, 30.0)),
                             ("Винт M2.5x6", (2.5, 6.0)),
                             ("Кабель-канал 25x25", None)],
        "linear_bearing": "Подшипник линейный {code}",
        "pipe_50": "Труба 50",
        # test_check_gates: materials and names a kit's process is told by
        "steel_08ps": "сталь 08пс",
        "alu_amg3": "алюминий АМг3",
        "silicone_3mm": "силикон 3 мм",
        "plastic": "пластик",
        "gasket_rubber": "Прокладка резина",
        # test_derive_rollout3 / test_node_paths: a part id and a maker's product name in Cyrillic (file names stay ASCII)
        "part_id": "Мебель",
        "motor": "Двигатель",
        # test_stage_title: human titles on a drawing and in a STEP label (fonts, the X2 encoding)
        "knob": "Ручка",
        "mount_plate": "Пластина крепления",
        "stiffening_angle": "Уголок усиления",
        # test_cycle_from_mechanism: a note in motion.json keeps its own letters
        "suction_frame": "Рамка присосок",
    },
}
