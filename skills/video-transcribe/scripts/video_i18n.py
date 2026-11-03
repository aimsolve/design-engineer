"""Texts of the video-transcribe scripts that end up in the documents a person reads, by language.

The language: env VIDEO_LANG, else the project's .engineer/locale.json "lang" (run from the project), else ru.
The prompts themselves are in English (only the model reads them); they ask for the answer in TEXTS[lang]["language"]
and use these headings and labels verbatim, so the documents keep one shape per language."""
import json
import os
from pathlib import Path

TEXTS = {
    "ru": {
        "language": "Russian",
        "on_screen": "в кадре",
        "scene": "Короткое название сцены", "in_frame": "В кадре", "node": "Узел", "details": "Детали конструкции",
        "how": "Как работает", "shot": "Скрин", "machine_map": "Карта машины", "best15": "Лучшие 15 кадров",
        "name": "название", "frame": "Кадр", "build": "Конструкция", "motion": "Движение",
        "how_machine": "Как работает станок", "best20": "Лучшие 20 кадров для скриншотов",
        "doc_video": "Видео: https://www.youtube.com/watch?v={vid}. Все тайм-теги кликабельны и открывают видео с этой секунды.",
        "doc_made": "Сделано Gemini на Vertex: речь целиком, визуал отдельным проходом по 2 кадра в секунду.",
        "doc_accuracy": "Тайм-теги визуала машинные, точность около секунды, перед скриншотом проверять глазами.",
        "doc_visual": "Визуальный лог: что в кадре",
        "doc_speech": "Речь, дословно",
        "preamble": r"(?m)^Отличный выбор!.*\n",
    },
    "en": {
        "language": "English",
        "on_screen": "on screen",
        "scene": "Short scene name", "in_frame": "In frame", "node": "Node", "details": "Construction details",
        "how": "How it works", "shot": "Screenshot", "machine_map": "Machine map", "best15": "Best 15 frames",
        "name": "name", "frame": "Frame", "build": "Construction", "motion": "Motion",
        "how_machine": "How the machine works", "best20": "Best 20 frames for screenshots",
        "doc_video": "Video: https://www.youtube.com/watch?v={vid}. Every timestamp is a link that opens the video at that second.",
        "doc_made": "Made by Gemini on Vertex: the whole speech, the visuals in a separate pass at 2 frames a second.",
        "doc_accuracy": "Visual timestamps are machine-made, accurate to about a second: check by eye before a screenshot.",
        "doc_visual": "Visual log: what is on screen",
        "doc_speech": "Speech, verbatim",
        "preamble": r"(?m)^(Great choice|Excellent choice)!.*\n",
    },
}


def lang():
    v = os.environ.get("VIDEO_LANG")
    if not v:
        try:
            v = json.loads(Path(".engineer/locale.json").read_text(encoding="utf-8")).get("lang")
        except (OSError, ValueError, AttributeError):
            v = None
    return v if v in TEXTS else "ru"


def texts():
    return TEXTS[lang()]
