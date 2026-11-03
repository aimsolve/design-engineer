---
name: video-transcribe
description: Transcribe and visually analyze a reference video (e.g. a YouTube video of a similar machine) with accurate timestamps, then extract frames at those timestamps. Use when the user shares a video link and wants a transcript, a visual/construction log of what's on screen with timestamps, or screenshots at specific moments — for example "what can we borrow for the design", reverse-engineering a competing/similar machine from footage, or building a reference doc with clickable timestamps and frame grabs.
---

# Video transcribe & visual analysis

Turns a reference video (typically a YouTube link of a similar machine/mechanism) into:
1. a full speech transcript with timestamps,
2. a timestamp-accurate visual/construction log ("what's on screen, what's it made of, how does it move"),
3. a short machine-map summary + a shortlist of the best frames to screenshot,
4. an assembled `VIDEO.md` with clickable YouTube-timestamp links,
5. actual extracted frame images at the timestamps that matter.

This method was worked out live on the first project (a serial packaging-machine video).

The user is responsible for the terms of what they download: the video's licence and the site's terms of service.

## Before writing any Gemini prompt

If a **`prompting-gemini`** skill is installed, invoke it before composing or
tuning any Gemini prompt (temperature, thinking_level, prompt structure) — don't duplicate that
guidance here. The scripts below already embed working prompts from the prior session; treat
`prompting-gemini` as the place to fix a prompt that isn't behaving, not a place to relearn from
scratch.

## Not google_media

The `google_media` MCP server only *generates* images/video; it cannot understand a video. Use the Vertex/Gemini
scripts below, which call `generate_content` with a `file_data` video part.

## Method (three Gemini passes + assembly + frame grab)

Auth: Gemini **on Vertex** with whatever gcloud config has credits (`CLOUDSDK_CONFIG`).
If a preview model returns 404 on the project — `transcribe.py` already falls back to
`gemini-2.5-pro` automatically. A YouTube URL is passed straight through as `file_data`
(`mime_type="video/*"`) — Vertex fetches it itself, no download needed for the analysis passes
(only for the screenshot pass, see below).

All scripts run with:
```
CLOUDSDK_CONFIG=<gcloud config dir> GOOGLE_VERTEX_PROJECT=<vertex-project-id> \
  uv run --isolated --no-project --with google-genai --with google-auth \
  python scripts/<script>.py <args>
```
`GOOGLE_VERTEX_PROJECT` is required (scripts fail without it). Outputs go to `OUT_DIR`, default the current
directory — run from the project and set `OUT_DIR=docs/video_<name>`; never leave it pointing into the plugin
folder (the plugin cache is wiped on update).

Language of the documents (the visual logs, the summary, `VIDEO.md`): `VIDEO_LANG=ru|en`, else the project's
`.engineer/locale.json` `lang`, else Russian. The prompts and the document headings are in `scripts/video_i18n.py`.

1. **`scripts/transcribe.py <url> [speech|visual|both]`** — pass 1, whole video at once.
   Gets a full speech transcript (`speech.md`) fast, and a *shallow* visual log (`visual.md`)
   whose timestamps are only approximate. Don't trust `visual.md`'s timestamps for screenshots —
   that's what step 2 is for. Default fps here was effectively too coarse (~1 fps over the whole
   video) to pin exact seconds.

2. **`scripts/deep_visual.py <url> [total_duration_seconds]`** — pass 2, the one that actually
   gives usable timestamps. Splits the video into ~9-minute chunks and calls Gemini per chunk
   with `video_metadata(start_offset, end_offset, fps=2)` — 2 frames/second is dense enough to
   catch scene changes and pin the right second. Runs the chunks concurrently. Writes
   `visual_deep_1.md`, `visual_deep_2.md`, ... Adjust `PROMPT` if the subject isn't a
   packaging-style machine (the node vocabulary in there is a starting point, not gospel).

3. **`scripts/summarize.py [n_segments]`** — pass 3, **text-only**, no video call. Feeds the
   deep visual log + full speech transcript back to Gemini to build: how-it-works steps, a
   machine/node map, and a shortlist of ~20 best frames to screenshot. Cheap and fast since
   there's no video attached.

4. **`scripts/build_doc.py <youtube_video_id> "<title>" [n_segments]`** — glues
   `summary.md` + `visual_deep_*.md` + `speech.md` into one `VIDEO.md`, turning every bare
   `MM:SS` timestamp into a clickable `https://youtu.be/<id>?t=<seconds>` link.

5. **Verify 2–3 timestamps by eye** before trusting the rest — open the YouTube link at that
   second in a browser and confirm what the log claims is actually there. This is cheap insurance
   against occasional off-by-a-few-seconds drift.

6. **`scripts/extract_frames.py <url> <ts1> <ts2> ...`** — actual screenshots. Gemini only gives
   you timestamps, never images, so getting real screenshots needs the video **downloaded**
   (`yt-dlp`) plus a frame-accurate cut (`ffmpeg -ss <ts> -frames:v 1`). Feed it the "best frames"
   list from `summary.md` / `VIDEO.md`. Writes to `frames/<n>_<MM-SS>.jpg`.

## Local requirements

Check before running step 6:
```
which yt-dlp
which ffmpeg
```

- **ffmpeg**: `which ffmpeg`; if missing, `brew install ffmpeg` / `apt install ffmpeg`.
- **yt-dlp**: `which yt-dlp`; if missing, install it with `uv tool install yt-dlp` (keeps it isolated from any project venv and puts it
  on PATH via uv's shim, no `pip install --user` needed). `brew install yt-dlp` also works if
  Homebrew is preferred.

## Local audio/voice transcription (not this skill's job, but adjacent)

If you ever need to transcribe local audio/voice notes instead of analyzing a YouTube reference
video, use the project's STT endpoint (a Whisper server), not Gemini. Don't route that kind of request through
this skill's Vertex scripts.

## Output layout

Keep everything for one video's analysis together, e.g. `docs/video_<short-name>/`:
`speech.md`, `visual.md`, `visual_deep_*.md`, `summary.md`, `VIDEO.md`, `frames/`. Keep that shape for every video.
