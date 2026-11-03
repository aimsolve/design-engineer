"""Pass 2: detailed visual log, video split into ~9-minute segments at 2 fps, visuals only.

This is the pass that gives trustworthy timestamps (pass 1 in transcribe.py is shallow and
its timestamps drift). Uses video_metadata start_offset/end_offset + fps=2 per segment,
called concurrently.

Usage:
  CLOUDSDK_CONFIG=<gcloud config dir> GOOGLE_VERTEX_PROJECT=<project-id> \\
    uv run --isolated --no-project --with google-genai --with google-auth \\
    deep_visual.py <youtube_url> [total_duration_seconds]

Writes visual_deep_<n>.md per segment in the current directory (or OUT_DIR env var).
Adjust PROMPT to describe what you're reverse-engineering; adjust SEGMENTS / total duration
to match your video's length (default assumes ~28 min, 3 segments of ~9.5 min).
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
MODEL = "gemini-2.5-pro"
OUT = Path(os.environ.get("OUT_DIR", "."))

SEGMENT_LEN = 570  # ~9.5 min per chunk; stay well under Gemini's per-call video budget


def make_segments(total_seconds: int) -> list[tuple[int, int]]:
    segs = []
    a = 0
    while a < total_seconds:
        b = min(a + SEGMENT_LEN, total_seconds)
        segs.append((a, b))
        a = b
    return segs


T = texts()
PROMPT = f"""You are watching the segment {{a}}–{{b}} of a video (give absolute timestamps, from the start of the whole video, as MM:SS).
The video shows a machine / mechanism similar to the one I am designing. I am an engineer; I need to seek the video by your
log and take screenshots to understand the CONSTRUCTION: what each node is made of and how it is assembled.

Write down every change of shot / angle where hardware is seen (a talking head without the machine can be one line).
For each, a block:

### MM:SS–MM:SS · {T["name"]}
- **{T["frame"]}:** angle and shot size (wide / medium / close-up), what is in the centre, what at the edges.
- **{T["node"]}:** which one.
- **{T["build"]}:** what exactly is seen: material and colour of the parts (aluminium profile, sheet metal, 3D print, milling), drives (pneumatic cylinder with its approximate bore / stroke, stepper, servo, belt, screw), guides, sensors (optical, inductive, reed), vacuum cup, pneumatic valves, tubes, cable ducts, labels and brands on components.
- **{T["motion"]}:** the sequence of actions, if the node is working (what moves after what).
- **{T["shot"]}:** the exact second of the best frame `★ MM:SS`, and why it is good.

Timestamp accuracy is critical: do not round to round numbers, look at the real second.
Write in {T["language"]}, with the labels above exactly as given, no introductions and no compliments."""


def fmt(s):
    return f"{s // 60:02d}:{s % 60:02d}"


def run(url, i, a, b):
    client = genai.Client(vertexai=True, project=PROJECT, location="global")
    part = types.Part(
        file_data=types.FileData(file_uri=url, mime_type="video/*"),
        video_metadata=types.VideoMetadata(start_offset=f"{a}s", end_offset=f"{b}s", fps=2),
    )
    resp = client.models.generate_content(
        model=MODEL,
        contents=[part, PROMPT.format(a=fmt(a), b=fmt(b))],
        config=types.GenerateContentConfig(max_output_tokens=65000, temperature=0.2),
    )
    out = OUT / f"visual_deep_{i}.md"
    out.write_text(f"<!-- model: {MODEL}, {fmt(a)}–{fmt(b)}, 2 fps -->\n\n{resp.text}\n", encoding="utf-8")
    print(i, "ok", resp.usage_metadata.total_token_count, flush=True)


if __name__ == "__main__":
    url = sys.argv[1]
    total = int(sys.argv[2]) if len(sys.argv) > 2 else 1680  # default ~28 min
    segments = make_segments(total)
    with cf.ThreadPoolExecutor(len(segments)) as ex:
        for f in [ex.submit(run, url, i, a, b) for i, (a, b) in enumerate(segments, 1)]:
            f.result()
