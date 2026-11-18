"""Music mix: trim the music to the picture, fade, normalise loudness, mux."""
from __future__ import annotations

import os
import subprocess

from . import media


class MixError(RuntimeError):
    pass


def audio_graph(vlen, tail, loudness):
    """Filter graph [1:a] -> [a] for a video of `vlen` seconds."""
    fade = min(vlen, tail + 2.0)
    start = vlen - fade
    return (f"[1:a]atrim=0:{vlen:.4f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.05,"
            f"afade=t=out:st={start:.3f}:d={fade:.3f},alimiter=limit=0.89:level=0,"
            f"loudnorm=I={loudness}:TP=-1.5:LRA=11[a]")


def mix(project, state, mode, video, frames, log=print):
    """Adds the music to `video` and writes out/<out_name>_<mode>.mp4."""
    media.require_tools()
    if not project.music.is_file():
        raise MixError(f"music file not found: {project.music.name}")
    project.out_dir.mkdir(parents=True, exist_ok=True)
    out = project.out_dir / f"{project.out_name}_{mode}.mp4"
    if out.exists():
        try:
            with open(out, "ab"):
                pass
        except OSError as e:
            raise MixError(f"cannot write {out.name}; close it in any player and try again") from e
    vlen = frames / project.fps_float
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", os.path.abspath(video), "-i", os.path.abspath(project.music),
           "-filter_complex", audio_graph(vlen, state["tail"], project.loudness),
           "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
           "-t", f"{vlen:.4f}", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise MixError(r.stderr[-1500:])
    log(f"written: {out}")
    return out
