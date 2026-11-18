"""Clip scanning, ffprobe wrappers and the clip metadata cache.

Importing this module has no side effects: nothing is read or run until a function is called.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import fsutil

PROBE_VERSION = 2       # bump when parse_probe returns new fields, so meta.json entries are re-probed

# ffprobe color_space -> the scale filter's colour matrix name; anything else is treated as BT.709
SOURCE_MATRIX = {"bt2020nc": "bt2020", "bt2020c": "bt2020", "smpte170m": "bt601", "bt470bg": "bt601"}
HDR_TRANSFERS = ("smpte2084", "arib-std-b67")


def source_matrix(meta) -> str:
    """The YUV matrix a clip must be converted from to reach BT.709; "" when no conversion is needed
    (BT.709 or untagged sources are never converted)."""
    return SOURCE_MATRIX.get((meta or {}).get("color_space") or "", "")


def hdr_transfer(meta) -> str:
    """The clip's HDR transfer curve name, or "" for SDR or untagged clips."""
    transfer = (meta or {}).get("color_transfer") or ""
    return transfer if transfer in HDR_TRANSFERS else ""


class MediaError(RuntimeError):
    """Raised when a clip folder, a media file or a required tool is unusable."""


def require_tools() -> None:
    """Raise MediaError naming the missing program if ffmpeg or ffprobe is not on PATH."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise MediaError(f"{tool} was not found on PATH; install FFmpeg and try again")


def scan_clips(project) -> list:
    """Clip files directly inside project.clips_dir, filtered by extension and prefix, sorted by name."""
    folder = Path(project.clips_dir)
    if not folder.is_dir():
        raise MediaError(f"clips folder does not exist: {folder}")
    found = []
    for entry in folder.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in project.extensions:
            continue
        if any(entry.name.startswith(prefix) for prefix in project.exclude_prefix):
            continue
        found.append(entry)
    return sorted(found, key=lambda p: p.name.lower())


def _frame_rate(text) -> float:
    try:
        num, _, den = str(text).partition("/")
        den_value = float(den) if den else 1.0
        return float(num) / den_value if den_value else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _positive_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 and number != float("inf") else None


def _source_fps(stream) -> float:
    """The smaller of the valid declared and average rates: a variable frame rate clip declares a
    high container rate but delivers frames at its average rate."""
    rates = [r for r in (_frame_rate(stream.get("r_frame_rate", "0/1")),
                         _frame_rate(stream.get("avg_frame_rate", "0/1"))) if 0 < r < float("inf")]
    return min(rates) if rates else 0.0


def _rotation(stream) -> int:
    value = None
    for item in stream.get("side_data_list") or []:
        if isinstance(item, dict) and "rotation" in item:
            value = item["rotation"]
            break
    if value is None:
        value = (stream.get("tags") or {}).get("rotate")
    if value is None:
        return 0
    try:
        return int(round(float(value))) % 360
    except (TypeError, ValueError):
        return 0


def parse_probe(data: dict) -> dict:
    """ffprobe JSON -> duration, displayed width and height, frame rate and rotation."""
    streams = data.get("streams") if isinstance(data, dict) else None
    if not streams:
        raise MediaError("no video stream found")
    stream = streams[0]
    try:
        width, height = int(stream["width"]), int(stream["height"])
    except (KeyError, TypeError, ValueError) as e:
        raise MediaError("video stream has no usable width and height") from e
    durations = [d for d in (_positive_number((data.get("format") or {}).get("duration")),
                             _positive_number(stream.get("duration"))) if d is not None]
    if not durations:
        raise MediaError("video has no readable duration")
    duration = min(durations)
    rotation = _rotation(stream)
    if rotation in (90, 270):
        width, height = height, width
    return {"duration": duration, "width": width, "height": height,
            "fps": _source_fps(stream), "rotation": rotation,
            "color_space": _color_tag(stream.get("color_space")),
            "color_transfer": _color_tag(stream.get("color_transfer"))}


def _color_tag(value) -> str:
    """An ffprobe colour tag as lower-case text; empty when missing or unknown."""
    text = str(value).strip().lower() if isinstance(value, str) else ""
    return "" if text in ("unknown", "reserved", "unspecified") else text


def _run_ffprobe(cmd, run):
    try:
        result = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as e:
        raise MediaError("ffprobe was not found on PATH; install FFmpeg and try again") from e
    if result.returncode != 0:
        message = (result.stderr or "").strip() or f"exit code {result.returncode}"
        raise MediaError(f"ffprobe failed: {message}")
    try:
        return json.loads(result.stdout)
    except ValueError as e:
        raise MediaError("ffprobe returned output that is not valid JSON") from e


def probe(path, run=subprocess.run) -> dict:
    """Probe the first video stream of `path`."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries",
           "stream=width,height,r_frame_rate,avg_frame_rate,duration,color_space,color_transfer"
           ":stream_side_data=rotation:stream_tags=rotate:format=duration",
           "-of", "json", os.path.abspath(path)]
    try:
        return parse_probe(_run_ffprobe(cmd, run))
    except MediaError as e:
        raise MediaError(f"{Path(path).name}: {e}") from e


def probe_duration(path, run=subprocess.run):
    """Format duration in seconds of any media file, or None when it cannot be read."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
           os.path.abspath(path)]
    try:
        value = float(_run_ffprobe(cmd, run)["format"]["duration"])
    except (MediaError, KeyError, TypeError, ValueError):
        return None
    return value if value == value and value not in (float("inf"), float("-inf")) else None


def _load_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(path: Path, cache: dict) -> None:
    """Best effort: a cache that cannot be written is simply not kept."""
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".meta-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(cache, ensure_ascii=False, indent=1))
        fsutil.replace_with_retry(tmp, path)
    except OSError:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def clip_meta(project, clips=None, probe_fn=probe, errors=None) -> dict:
    """{clip file name: parse_probe result}, cached in work_dir/meta.json by file name and mtime.

    A clip that cannot be probed is left out (and not cached); when `errors` is a dict it receives
    {clip name: message} for each of them."""
    if clips is None:
        clips = scan_clips(project)
    cache_path = Path(project.work_dir) / "meta.json"
    old = _load_cache(cache_path)
    new, result = {}, {}
    for clip in clips:
        mtime = clip.stat().st_mtime_ns
        entry = old.get(clip.name)
        if (isinstance(entry, dict) and entry.get("mtime") == mtime and "duration" in entry
                and entry.get("probe") == PROBE_VERSION):
            meta = {k: v for k, v in entry.items() if k not in ("mtime", "probe")}
        else:
            try:
                meta = dict(probe_fn(clip))
            except MediaError as e:
                if errors is not None:
                    errors[clip.name] = str(e)
                continue
        new[clip.name] = {**meta, "mtime": mtime, "probe": PROBE_VERSION}
        result[clip.name] = meta
    if new != old:
        _write_cache(cache_path, new)
    return result
