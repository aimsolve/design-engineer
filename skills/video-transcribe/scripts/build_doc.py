"""Glue summary.md + visual_deep_*.md + speech.md into VIDEO.md with clickable YouTube timestamps.

Usage: uv run build_doc.py <youtube_video_id> "<title>" [n_segments]
Reads/writes in OUT_DIR (default: current directory).
"""
import os
import re
import sys
from pathlib import Path

from video_i18n import TEXTS, texts

HERE = Path(os.environ.get("OUT_DIR", "."))
VID = sys.argv[1] if len(sys.argv) > 1 else "VIDEO_ID"
TITLE = sys.argv[2] if len(sys.argv) > 2 else "Reference video analysis"
N = int(sys.argv[3]) if len(sys.argv) > 3 else 3
T = texts()


def link(ts):
    parts = [float(p) for p in ts.split(":")]
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return f"[{ts}](https://youtu.be/{VID}?t={int(sec)})"


def linkify(text):
    text = re.sub(r"<!--.*?-->\n*", "", text)
    # broken links from a first pass that already tried to linkify: [12:17](https://www.youtube.com/watch?v=12:17)
    text = re.sub(r"\[(\d{1,2}:\d{2})\]\(https://www\.youtube\.com/watch\?v=[\d:]+\)", r"\1", text)
    for t in TEXTS.values():   # a model's chatty first line, in any language
        text = re.sub(t["preamble"], "", text)
    return re.sub(r"(?<![\d:.\[])(\d{1,2}:\d{2}(?::\d{2})?)(?:\.\d+)?(?![\d\]])", lambda m: link(m.group(1)), text)


def body(name):
    return linkify((HERE / name).read_text(encoding="utf-8")).strip()


deep = "\n\n".join(body(f"visual_deep_{i}.md") for i in range(1, N + 1))
summary = body("summary.md")

doc = f"""# {TITLE}

{T["doc_video"].format(vid=VID)}
{T["doc_made"]}
{T["doc_accuracy"]}

{summary}

---

# {T["doc_visual"]}

{deep}

---

# {T["doc_speech"]}

{body("speech.md")}
"""
(HERE / "VIDEO.md").write_text(doc, encoding="utf-8")
print(len(doc))
