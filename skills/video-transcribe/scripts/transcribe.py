"""Pass 1: full-video transcript + shallow visual log through Gemini on Vertex.

A public YouTube URL is passed directly as file_data (mime video/*) — no download needed.
This pass is fast but its visual timestamps are shallow/approximate (see deep_visual.py for pass 2).

Usage:
  CLOUDSDK_CONFIG=<gcloud config dir> GOOGLE_VERTEX_PROJECT=<project-id> \\
    uv run --isolated --no-project --with google-genai --with google-auth \\
    transcribe.py <youtube_url> [speech|visual|both]

Writes speech.md / visual.md in the current directory (or set OUT_DIR env var).
Adapt PROMPTS["visual"] to describe the machine/subject you're actually analyzing.
"""
import os
import sys
import concurrent.futures as cf
from pathlib import Path

for k in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
    os.environ.pop(k, None)

from google import genai
from google.genai import types

from video_i18n import texts

PROJECT = os.environ["GOOGLE_VERTEX_PROJECT"]  # required, no default
# gemini-3-pro-preview 404s on some trial projects; falls back to 2.5 pro automatically.
MODELS = ["gemini-3-pro-preview", "gemini-2.5-pro"]
OUT = Path(os.environ.get("OUT_DIR", "."))

T = texts()
PROMPTS = {
    "speech": f"""Make a FULL verbatim transcript of the speech in this video, in the original language.
Format: markdown. Every paragraph starts with the timestamp [MM:SS] (or [H:MM:SS]) of the moment the phrase starts.
A new paragraph at least every 20-40 seconds and at every change of topic.
Do not shorten, do not retell, do not skip parts. Filler words may be dropped.
Write technical terms, part names and numbers (sizes, speeds, prices, models of motors / sensors / controllers) exactly.
If important text appears on screen (labels, a display, a drawing), put it on its own line `> {T["on_screen"]}: ...`.""",
    "visual": f"""This video is about a machine / device similar to the one I am designing. I need a VISUAL LOG, to take screenshots by its timestamps later and to understand the construction.
Go through the video from start to end and write down every significant shot / scene. Format markdown, one block per scene:

### [MM:SS–MM:SS] {T["scene"]}
- **{T["in_frame"]}:** what exactly is seen (which node, from which angle, wide shot / close-up, the machine running or standing).
- **{T["node"]}:** which part of the machine / device it belongs to.
- **{T["details"]}:** materials, profiles, drives (steppers / servos / pneumatic cylinders), sensors, belts, shafts, bearings, fasteners, sizes if seen or named.
- **{T["how"]}:** what happens in motion, if shown.
- **{T["shot"]}:** `★` if this is a good frame for a screenshot (sharp, the node well visible), with the exact second of the best moment, for example `★ 03:17`.

At the end add a section "## {T["machine_map"]}": every node in the order of the product flow, for each the best timestamps where it is seen.
And a section "## {T["best15"]}": a list of timestamps with one line on what is in each.
Write in {T["language"]}, with the headings and labels above exactly as given. Timestamps must be exact: I will seek by them.

NOTE: this is the first (fast) pass over the whole video: the timestamp accuracy here is APPROXIMATE.
For exact timestamps use the second pass over segments (deep_visual.py).""",
}


def run(url: str, kind: str) -> Path:
    client = genai.Client(vertexai=True, project=PROJECT, location="global")
    contents = [types.Part(file_data=types.FileData(file_uri=url, mime_type="video/*")), PROMPTS[kind]]
    last = None
    for model in MODELS:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(max_output_tokens=65000, temperature=0.2),
            )
            out = OUT / f"{kind}.md"
            out.write_text(f"<!-- model: {model}, source: {url} -->\n\n{resp.text}\n", encoding="utf-8")
            print(kind, model, "ok", resp.usage_metadata.total_token_count, "tokens", flush=True)
            return out
        except Exception as e:  # try the next model
            last = e
            print(kind, model, "failed:", str(e)[:300], flush=True)
    raise last


if __name__ == "__main__":
    url = sys.argv[1]
    kinds = ["speech", "visual"] if len(sys.argv) < 3 or sys.argv[2] == "both" else [sys.argv[2]]
    with cf.ThreadPoolExecutor(len(kinds)) as ex:
        for f in [ex.submit(run, url, k) for k in kinds]:
            f.result()
