"""The rules of check.py named for a person (the platform's daily summary of projects that break the standard), per
language. Keys are the rule names check.py prints (a rule matches the first key it starts with); the `--json` output
carries the Russian name in each fail's `ru` field, which the platform reads. A rule without an entry is shown by its own
name. Grouped by the module that owns the rules: check.py, cad_gates.py, fit_checks.py."""

RULES = {
    "ru": {
        "check": {
            "layout": "структура проекта",
            "git file list": "список файлов git",
            "no generated file types committed": "сгенерированные файлы в git",
            "no committed file over 20 MB": "файлы больше 20 МБ в git",
            "screenshots/previews only in build/ or outbox/": "скриншоты рядом с исходниками",
            "AAS": "AAS (паспорт машины)",
            "firmware": "прошивка не в PlatformIO",
            "schemes": "схема не сходится с моделью или PDF-ссылка на странице",
            "device": "виртуальное устройство не сходится с моделью",
        },
        "cad": {
            "CAD layout": "раскладка CAD: params.py + папки узлов parts/<узел>/ с build()",
            "kit.json": "kit.json (кнопки скачивания) отсутствует или неверен",
            "bought kind": "у покупных нет kind",
            "single-process kits": "в комплекте смешаны процессы или материалы",
            "kit via derive.py": "выгрузка kit.json не через derive.py",
            "model parts": "у деталей модели нет process или id повторяется",
            "joints": "детали поставлены не стыковкой (connect_to)",
            "params import *": "from params import * — кэш пересобирает лишнее",
            "export manifest": "манифест выгрузки",
            "library": "библиотека деталей: зеркало расходится с источником или нет импортируемой группы",
        },
        "fit": {
            "fastener fit": "посадка крепежа: винт не подходит отверстию",
            "docked parts clash": "пристыкованные детали пересекаются",
            "bought = drawn": "покупное не то, что нарисовано",
            "bought part verified": "покупная деталь без сверки с чертежом производителя",
            "bought fastener pairs": "в покупных у крепежа нет пары",
        },
    },
    "en": {
        "check": {
            "layout": "project layout",
            "git file list": "git file list",
            "no generated file types committed": "generated files in git",
            "no committed file over 20 MB": "files over 20 MB in git",
            "screenshots/previews only in build/ or outbox/": "screenshots next to the sources",
            "AAS": "AAS (the machine's passport)",
            "firmware": "firmware not a PlatformIO project",
            "schemes": "a scheme disagrees with the model, or a PDF link on the page",
            "device": "the virtual device disagrees with the model",
        },
        "cad": {
            "CAD layout": "CAD layout: params.py + node folders parts/<node>/ with build()",
            "kit.json": "kit.json (the download buttons) missing or wrong",
            "bought kind": "bought parts without kind",
            "single-process kits": "a kit mixes processes or materials",
            "kit via derive.py": "a kit.json export not through derive.py",
            "model parts": "model parts without process, or a repeated id",
            "joints": "parts placed without docking (connect_to)",
            "params import *": "from params import *: the cache rebuilds too much",
            "export manifest": "export manifest",
            "library": "part library: the mirror differs from its source, or an imported group is missing",
        },
        "fit": {
            "fastener fit": "fastener fit: the screw does not suit its hole",
            "docked parts clash": "docked parts overlap",
            "bought = drawn": "the bought part is not the one drawn",
            "bought part verified": "a bought part not checked against the maker's drawing",
            "bought fastener pairs": "a fastener in the purchase list without its pair",
        },
    },
}
