---
name: video-reader
description: Read, watch or analyze a video file (mp4, mov, webm, WhatsApp videos, screen recordings, phone clips) by sampling a few frames into a temp folder, viewing them as images, then deleting them. Use whenever the user gives a video path and asks what happens in it, why something went wrong in a recording, or what a screen or barcode scan shows. Visual only, no audio.
---

# Video reader

You cannot open a video directly. Sample frames, look at them as images, delete them. Nothing stays on disk.

`<skill_dir>` below means the folder that contains this SKILL.md.

## Steps

1. **Extract** (frames go to a temp folder, never into the project or next to the video):

   ```
   python "<skill_dir>/scripts/video_frames.py" extract "<video path>"
   ```

   The output lists `frames_dir`, the timestamp of every frame, and the exact cleanup command.

2. **Look at every frame** with whatever image-viewing ability you have (in Claude Code: the Read tool on each `.jpg` path; run the reads in parallel). Read them in order and keep the timestamps.

3. **Zoom in if needed.** The first pass is coarse (default 16 frames over the whole video). If something important happens between, say, 4s and 9s, run a second pass on just that range:

   ```
   python "<skill_dir>/scripts/video_frames.py" extract "<video path>" --start 4 --end 9 --max-frames 12
   ```

   Clean up the first folder before or after the second pass.

4. **Clean up. Always, even if you stopped early or hit an error.** Run the `cleanup when done` command the script printed. If you lost the path: `python "<skill_dir>/scripts/video_frames.py" cleanup --all`.

5. **Answer with timestamps** ("at 0:06 the scanner shows ..."). Say plainly that you saw sampled frames, not the full video.

## Options

| Flag | Meaning |
|---|---|
| `--max-frames N` | frames to take, default 16, hard cap 60 |
| `--every SEC` | one frame per N seconds instead (wins over `--max-frames`) |
| `--start S` / `--end S` | only this part of the video, in seconds |
| `--width PX` | max frame width, default 768 (keeps image cost low; raise to 1280 to read small text) |

## Limits to state honestly

- **No audio.** The script reports whether the file has an audio track. If speech matters, say you did not hear it; only transcribe if the user asks and a speech-to-text tool is available.
- **Sampling can miss short events.** A flash shorter than the gap between frames (printed as "one every ~X s") may not be visible. Narrow the range and extract again.
- Every frame costs image tokens. Do not push past ~20 frames per pass unless the question needs it.

## Requirements

Python 3 plus either `ffmpeg` and `ffprobe` on PATH, or `pip install opencv-python-headless`. The script prefers ffmpeg and falls back to OpenCV on its own. On macOS/Linux the command is usually `python3` instead of `python`.

## Using it from other AI tools

Nothing here is specific to Claude Code. A tool with a skills folder can load this folder as is. A tool without one only needs to read this file and run the same two commands, then open the printed `.jpg` paths with its image viewer.
