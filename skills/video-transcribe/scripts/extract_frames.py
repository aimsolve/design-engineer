"""Download a reference video and extract frames at specific timestamps.

Needed because Vertex/Gemini only gives you timestamps from the YouTube URL — it does not hand
back images. To turn a timestamp into an actual screenshot you need the video file locally
(yt-dlp) and a frame-accurate cut (ffmpeg).

Usage:
  uv run extract_frames.py <youtube_url> 00:00 00:23 12:03 17:30 ...

Downloads the video once to ./_downloaded.mp4 (skipped if it already exists), then writes one
JPEG per timestamp to ./frames/<n>_<MM-SS>.jpg (or OUT_DIR env var for both).

Requires `yt-dlp` and `ffmpeg` on PATH. Install yt-dlp with `uv tool install yt-dlp` if missing.
"""
import os
import subprocess
import sys
from pathlib import Path

OUT = Path(os.environ.get("OUT_DIR", "."))
FRAMES = OUT / "frames"
VIDEO_FILE = OUT / "_downloaded.mp4"


def ensure_tool(name: str):
    if subprocess.run(["which", name], capture_output=True).returncode != 0:
        sys.exit(f"{name} not found on PATH. Install it first (see SKILL.md).")


def download(url: str) -> Path:
    if VIDEO_FILE.exists():
        print("already downloaded:", VIDEO_FILE)
        return VIDEO_FILE
    subprocess.run(
        ["yt-dlp", "-f", "bv*[height<=1080]+ba/b[height<=1080]", "-o", str(VIDEO_FILE), url],
        check=True,
    )
    return VIDEO_FILE


def extract(video: Path, timestamps: list[str]):
    FRAMES.mkdir(exist_ok=True)
    for i, ts in enumerate(timestamps, 1):
        safe = ts.replace(":", "-")
        out = FRAMES / f"{i:02d}_{safe}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", ts, "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)],
            check=True,
            capture_output=True,
        )
        print("wrote", out)


if __name__ == "__main__":
    ensure_tool("yt-dlp")
    ensure_tool("ffmpeg")
    url = sys.argv[1]
    timestamps = sys.argv[2:]
    if not timestamps:
        sys.exit("pass at least one MM:SS timestamp")
    video = download(url)
    extract(video, timestamps)
