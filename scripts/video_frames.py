#!/usr/bin/env python3
"""Turn a video into a few image frames an AI tool can read, then clean up.

  video_frames.py extract VIDEO [--max-frames 16] [--every SEC] [--start S] [--end S] [--width 768]
  video_frames.py cleanup DIR
  video_frames.py cleanup --all

Frames go to a fresh vidframes_* folder in the system temp dir, never next to
the video or inside a project. Uses ffmpeg+ffprobe if they are on PATH,
otherwise OpenCV (pip install opencv-python-headless).
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PREFIX = "vidframes_"
HARD_CAP = 60


def die(msg):
    sys.exit(f"error: {msg}")


def probe_ffmpeg(video):
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,r_frame_rate",
         "-of", "json", str(video)],
        capture_output=True, text=True)
    if res.returncode != 0:
        die(f"ffprobe could not read the file: {res.stderr.strip()}")
    info = json.loads(res.stdout)
    streams = info.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        die("no video stream found in the file")
    try:
        num, den = video_stream["r_frame_rate"].split("/")
        fps = float(num) / float(den)
    except (KeyError, ValueError, ZeroDivisionError):
        fps = 0.0
    return {
        "duration": float(info.get("format", {}).get("duration") or 0),
        "fps": fps,
        "audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def probe_cv2(video):
    import cv2
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        die("OpenCV could not open the file")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
    cap.release()
    return {"duration": frame_count / fps if fps else 0.0, "fps": fps, "audio": None}


def grab_all_ffmpeg(video, times, paths, width):
    ok = []
    for t, path in zip(times, paths):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2",
             "-q:v", "4", str(path)],
            capture_output=True, text=True)
        ok.append(path.exists() and path.stat().st_size > 0)
    return ok


def grab_all_cv2(video, times, paths, width):
    import cv2
    cap = cv2.VideoCapture(str(video))
    ok = []
    for t, path in zip(times, paths):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        good, img = cap.read()
        if good:
            h, w = img.shape[:2]
            if w > width:
                img = cv2.resize(img, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
            good, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if good:
            path.write_bytes(buf.tobytes())
        ok.append(bool(good))
    cap.release()
    return ok


def pick_backend(choice):
    if choice in ("auto", "ffmpeg") and shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return "ffmpeg"
    if choice in ("auto", "cv2"):
        try:
            import cv2  # noqa: F401
            return "cv2"
        except ImportError:
            pass
    die("need ffmpeg + ffprobe on PATH, or run: pip install opencv-python-headless")


def cmd_extract(a):
    video = Path(a.video).expanduser()
    if not video.is_file():
        die(f"file not found: {video}")

    backend = pick_backend(a.backend)
    info = probe_ffmpeg(video) if backend == "ffmpeg" else probe_cv2(video)
    duration = info["duration"]
    if duration <= 0:
        die("could not determine the video duration")

    start = max(0.0, a.start)
    end = min(duration, a.end) if a.end is not None else duration
    span = end - start
    if span <= 0:
        die("empty time range (check --start / --end)")

    wanted = round(span / a.every) if a.every else a.max_frames
    n = max(1, min(wanted, HARD_CAP))
    note = f"capped at {HARD_CAP} frames, raise --every or narrow --start/--end" if wanted > HARD_CAP else ""

    # Midpoint of n equal slices: avoids a black first frame and a failed seek at the very end.
    step = span / n
    times = [start + (i + 0.5) * step for i in range(n)]

    folder = Path(tempfile.mkdtemp(prefix=PREFIX))
    paths = [folder / f"f{i:02d}_t{t:07.2f}s.jpg" for i, t in enumerate(times)]
    grab = grab_all_ffmpeg if backend == "ffmpeg" else grab_all_cv2
    ok = grab(video, times, paths, a.width)

    got = [(t, p) for t, p, good in zip(times, paths, ok) if good]
    if not got:
        shutil.rmtree(folder, ignore_errors=True)
        die("no frames could be decoded from this file")

    audio = {True: "present but NOT read (frames only)", False: "none", None: "unknown"}[info["audio"]]
    print(f"video: {video.name}  duration: {duration:.1f}s  fps: {info['fps']:.1f}  backend: {backend}")
    print(f"audio: {audio}")
    print(f"range: {start:.1f}s-{end:.1f}s  frames: {len(got)}  one every ~{step:.2f}s (shorter events can be missed)")
    if note:
        print(f"note: {note}")
    print(f"frames_dir: {folder}")
    for t, p in got:
        print(f"t={t:7.2f}s  {p}")
    print(f'cleanup when done: python "{Path(__file__).resolve()}" cleanup "{folder}"')


def is_ours(d):
    return d.name.startswith(PREFIX) and d.resolve().parent == Path(tempfile.gettempdir()).resolve()


def cmd_cleanup(a):
    if a.all:
        targets = [d for d in Path(tempfile.gettempdir()).glob(PREFIX + "*") if d.is_dir()]
    elif a.dir:
        targets = [Path(a.dir)]
    else:
        die("give a frames folder, or --all")

    removed = 0
    for d in targets:
        if not d.is_dir():
            continue
        if not is_ours(d):
            die(f"refusing to delete {d}: not a {PREFIX}* folder in the temp dir")
        shutil.rmtree(d)
        removed += 1
    print(f"removed {removed} folder(s)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="save evenly spaced frames to a temp folder")
    e.add_argument("video")
    e.add_argument("--max-frames", type=int, default=16, help="frames to take (default 16, hard cap 60)")
    e.add_argument("--every", type=float, help="one frame per N seconds instead of --max-frames")
    e.add_argument("--start", type=float, default=0.0, help="range start in seconds")
    e.add_argument("--end", type=float, help="range end in seconds")
    e.add_argument("--width", type=int, default=768, help="max frame width in px (default 768)")
    e.add_argument("--backend", choices=["auto", "ffmpeg", "cv2"], default="auto")
    e.set_defaults(fn=cmd_extract)

    c = sub.add_parser("cleanup", help="delete extracted frames")
    c.add_argument("dir", nargs="?")
    c.add_argument("--all", action="store_true", help="delete every vidframes_* folder in the temp dir")
    c.set_defaults(fn=cmd_cleanup)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
