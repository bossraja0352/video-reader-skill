#!/usr/bin/env python3
"""Turn a video into a few image frames an AI tool can read, then clean up.

  video_frames.py extract VIDEO [--max-frames 16] [--every SEC] [--start S] [--end S] [--width 768]
                                [--scenes [N]] [--scene-threshold 0.003]
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
SCENES_DEFAULT = 8        # extra frames taken at scene changes when --scenes has no value
SCENE_THRESHOLD = 0.003   # low on purpose: a small popup on a static screen scores ~0.004
SCENE_MIN_GAP = 0.2       # seconds between two picked scene frames
SCENE_DEDUPE = 0.05       # a scene frame this close to a uniform frame would be a duplicate


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


def scenes_ffmpeg(video, start, span, threshold):
    """(time, score) for every frame whose change from the previous one exceeds threshold."""
    res = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{span:.3f}", "-i", str(video),
         "-an", "-sn", "-vf", f"scale=320:-2,select='gt(scene,{threshold})',metadata=print:file=-",
         "-f", "null", "-"],
        capture_output=True, text=True)
    if res.returncode != 0:
        return None
    found, t = [], None
    for line in res.stdout.splitlines():
        if "pts_time:" in line:
            t = start + float(line.split("pts_time:")[1].split()[0])
        elif "lavfi.scene_score=" in line and t is not None:
            found.append((t, float(line.split("=")[1])))
    return found


def scenes_cv2(video, start, end, threshold):
    import cv2
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if not fps:
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
    found, prev = [], None
    while idx / fps <= end:
        good, img = cap.read()
        if not good:
            break
        h, w = img.shape[:2]
        small = cv2.resize(img, (160, max(1, round(h * 160 / w))))
        if prev is not None:
            score = sum(cv2.mean(cv2.absdiff(small, prev))[:3]) / 3 / 255
            if score > threshold:
                found.append((idx / fps, score))
        prev = small
        idx += 1
    cap.release()
    return found


def pick_scenes(found, count, taken, room):
    """Strongest scene changes first, skipping near-duplicates of frames already chosen."""
    chosen = []
    for t, score in sorted(found, key=lambda f: -f[1]):
        if len(chosen) >= min(count, room):
            break
        if any(abs(t - u) < SCENE_DEDUPE for u in taken):
            continue
        if any(abs(t - c) < SCENE_MIN_GAP for c, _ in chosen):
            continue
        chosen.append((t, score))
    return chosen


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

    scene_note = ""
    frames = [(t, False) for t in times]
    if a.scenes:
        found = (scenes_ffmpeg(video, start, span, a.scene_threshold) if backend == "ffmpeg"
                 else scenes_cv2(video, start, end, a.scene_threshold))
        if found is None:
            scene_note = "scene detection failed, using evenly spaced frames only"
        else:
            picked = pick_scenes(found, a.scenes, times, HARD_CAP - n)
            frames = sorted(frames + [(t, True) for t, _ in picked])
            scene_note = (f"{len(picked)} added at the strongest of {len(found)} detected changes"
                          if picked else f"none above {a.scene_threshold}")

    folder = Path(tempfile.mkdtemp(prefix=PREFIX))
    times = [t for t, _ in frames]
    paths = [folder / f"f{i:02d}_t{t:07.2f}s{'_scene' if scene else ''}.jpg"
             for i, (t, scene) in enumerate(frames)]
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
    if scene_note:
        print(f"scene changes: {scene_note} (files ending _scene.jpg)")
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
    e.add_argument("--scenes", type=int, nargs="?", const=SCENES_DEFAULT, default=0, metavar="N",
                   help=f"also take up to N frames (default {SCENES_DEFAULT}) at the strongest scene changes, "
                        "to catch short events between the evenly spaced frames")
    e.add_argument("--scene-threshold", type=float, default=SCENE_THRESHOLD,
                   help=f"minimum change score for --scenes (default {SCENE_THRESHOLD}, higher = fewer)")
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
