"""Pass 3: machine map + best frames, built text-only from the deep visual log + transcript.

Usage:
  CLOUDSDK_CONFIG=<gcloud config dir> GOOGLE_VERTEX_PROJECT=<project-id> \\
    uv run --isolated --no-project --with google-genai --with google-auth \\
    summarize.py [n_segments]

Reads visual_deep_1..N.md + speech.md from OUT_DIR (default: current directory), writes summary.md.
Adapt the PROMPT's node list to your subject if it isn't a packaging-style machine.
"""
import os
import sys
from pathlib import Path

for k in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
    os.environ.pop(k, None)
from google import genai
from google.genai import types

from video_i18n import texts

PROJECT = os.environ["GOOGLE_VERTEX_PROJECT"]  # required, no default
HERE = Path(os.environ.get("OUT_DIR", "."))
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3

deep = "\n\n".join((HERE / f"visual_deep_{i}.md").read_text() for i in range(1, N + 1))
speech = (HERE / "speech.md").read_text()

T = texts()
PROMPT = f"""Below are the visual log and the verbatim speech of a video about a machine / device.
Build ONLY from this data, inventing nothing, three markdown sections:

## {T["how_machine"]}
The sequence of operations along the product / process, 6-12 steps, each step in one or two sentences, with a timestamp where it is seen or said.

## {T["machine_map"]}
For each node (feed, dosing / glue, forming / folding, hold-down, transport, discharge, pneumatics, electrics / controller, HMI, frame / housing, sensors; keep only those really in this video):
**{T["node"]}**: what it is built from (components, brands, materials, drives), then the best timestamps.

## {T["best20"]}
A numbered list: timestamp MM:SS, what is in it and why it is useful to a designer.

Write timestamps as MM:SS (no fractions of a second, no backticks, no links). If the log and the speech disagree, take the log for the visuals and the speech for numbers and brands.
Write in {T["language"]}, with the headings above exactly as given, no introduction.

=== VISUAL LOG ===
{deep}

=== SPEECH ===
{speech}"""

client = genai.Client(vertexai=True, project=PROJECT, location="global")
r = client.models.generate_content(model="gemini-2.5-pro", contents=PROMPT,
                                   config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=30000))
(HERE / "summary.md").write_text(r.text, encoding="utf-8")
print("ok", len(r.text))
