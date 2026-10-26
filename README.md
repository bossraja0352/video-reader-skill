# video-reader

An agent skill that lets an AI coding assistant "watch" a video. AI tools can't open `.mp4` files, but they can read images. This skill samples a handful of frames, hands them to the assistant as images, and deletes them afterwards. Nothing is left on disk.

Works with Claude Code out of the box. Any other tool that supports `SKILL.md` skills, or can simply run a Python script and open image files, can use it too.

## What it does

1. `extract` saves evenly spaced frames from a video into a temporary folder (`vidframes_*` in your system temp directory, never next to the video or inside your project).
2. The assistant opens each frame as an image and reasons about what happens, with timestamps.
3. `cleanup` deletes the temporary folder.

## Requirements

- Python 3.8+
- One of:
  - `ffmpeg` and `ffprobe` on your `PATH` (preferred), or
  - OpenCV: `pip install opencv-python-headless`

The script picks ffmpeg when available and falls back to OpenCV by itself. No other Python packages are needed.

## Install

The repository root is the skill folder, so cloning it into your skills directory is the whole install.

**Claude Code, macOS / Linux**

```bash
git clone https://github.com/bossraja0352/video-reader-skill.git ~/.claude/skills/video-reader
```

**Claude Code, Windows (PowerShell)**

```powershell
git clone https://github.com/bossraja0352/video-reader-skill.git "$env:USERPROFILE\.claude\skills\video-reader"
```

To use it in one project only, clone into `<project>/.claude/skills/video-reader` instead.

**Other AI tools**

- If the tool has a skills folder that uses `SKILL.md` files, copy this folder there.
- If it doesn't, tell the assistant to read `SKILL.md` in this folder and follow it. It only needs to run two commands and open the `.jpg` files they print.

## Use

Ask your assistant something like:

> Look at `C:\Users\me\Downloads\screen-recording.mp4` and tell me what goes wrong at the end.

The skill's description tells the assistant to pick it up whenever you hand it a video path. You can also run the script yourself:

```bash
python scripts/video_frames.py extract "path/to/video.mp4"
```

Example output (values are illustrative):

```
video: clip.mp4  duration: 9.9s  fps: 30.0  backend: ffmpeg
audio: present but NOT read (frames only)
range: 0.0s-9.9s  frames: 16  one every ~0.62s (shorter events can be missed)
frames_dir: /tmp/vidframes_ab12cd34
t=   0.31s  /tmp/vidframes_ab12cd34/f00_t0000.31s.jpg
t=   0.93s  /tmp/vidframes_ab12cd34/f01_t0000.93s.jpg
...
cleanup when done: python ".../scripts/video_frames.py" cleanup "/tmp/vidframes_ab12cd34"
```

Then delete the frames:

```bash
python scripts/video_frames.py cleanup "/tmp/vidframes_ab12cd34"
python scripts/video_frames.py cleanup --all     # remove every leftover vidframes_* folder
```

Use `python3` instead of `python` where that is the name on your system.

### Options for `extract`

| Flag | Meaning | Default |
|---|---|---|
| `--max-frames N` | Number of frames to take (hard cap 60) | 16 |
| `--every SEC` | One frame per N seconds instead of a fixed count | off |
| `--start S`, `--end S` | Only sample this part of the video, in seconds | whole video |
| `--width PX` | Maximum frame width; smaller frames cost fewer image tokens | 768 |
| `--scenes [N]` | Also take up to N frames (8 if no number) at the strongest scene changes, to catch short events. Put the video path before this flag. Files end in `_scene.jpg` | off |
| `--scene-threshold X` | Minimum change score for `--scenes`. Raise it for shaky or noisy footage | 0.003 |
| `--backend` | `auto`, `ffmpeg` or `cv2` | `auto` |

### Catching short events with `--scenes`

Evenly spaced frames can fall on both sides of a popup or error flash that lasts a fraction of a second. With `--scenes` the script also scans the range for frames that differ most from the one before, and adds those on top of the even sampling:

```bash
python scripts/video_frames.py extract "screen-recording.mp4" --scenes
```

The threshold is deliberately low, because a small popup on a static screen scores only about 0.004, while a popup covering a big part of the screen scores 0.2 or more. Changes are ranked by size and the top N are kept, at least 0.2 s apart. On a long or busy video a small event can still lose out to bigger ones, so zoom in with `--start` / `--end`. The scan decodes the whole range, so it is slower than plain sampling on long videos.

### Recommended workflow: coarse, then fine

Take the default 16 frames first. If the interesting part is, say, between 4 s and 9 s, extract again with `--start 4 --end 9 --max-frames 12` to get a much denser look at just that range. Clean up after each pass.

## How it works

- Frames are taken at the midpoint of `N` equal slices of the chosen range. That avoids a black first frame and seeks that fail at the very end of the file.
- ffmpeg seeks straight to each timestamp, so long videos don't have to be decoded from start to finish.
- Frames are scaled down to `--width` and saved as JPEG.
- `cleanup` refuses to delete anything that is not a `vidframes_*` folder directly inside the system temp directory.

## Limits

- **No audio.** The script tells you whether the file has an audio track, but it does not transcribe it.
- **Sampling can miss short events.** Anything shorter than the gap between frames may not appear in any frame. `--scenes` improves the odds a lot but does not guarantee it.
- **Frames are sent to your AI provider.** Everything runs locally, but once the assistant opens a frame as an image, that image goes to whichever model your tool uses. Don't run it on videos you wouldn't be willing to show that provider.
- Each frame costs image tokens. More than about 20 frames per pass is rarely needed.

## Uninstall

Delete the `video-reader` folder from your skills directory.

## Layout

```
video-reader/
├── SKILL.md                 # instructions the assistant follows
├── README.md
├── .gitignore
└── scripts/
    └── video_frames.py      # extract / cleanup
```
