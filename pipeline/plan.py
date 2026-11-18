"""shots.json -> frame-exact timeline. Pure arithmetic: no ffmpeg, no file access.

Every beat boundary is rounded to a whole frame of the output, and every segment is
rendered with an exact frame count, so the picture never drifts away from the music.

Shot i becomes the main picture at frame `start_f` and hands over at `end_f`. A fade
between two shots is split evenly around the boundary: the outgoing shot is rendered
`d/2` frames longer and the incoming shot starts `d/2` frames earlier.

The first shot starts at frame 0 on its `in` point; the intro before the first beat and
the tail after the last beat are filled by reading on past the shot's `out` point.
"""
from __future__ import annotations

from dataclasses import dataclass

SPEED_SNAP = 0.015       # speeds this close to 1.0 are treated as exactly 1.0
SRC_MARGIN = 0.10        # seconds kept unused at the end of a source clip
MIN_FADE_FRAMES = 2
MIN_SPEED, MAX_SPEED = 0.1, 8.0


@dataclass
class Segment:
    index: int
    clip: str
    prof: str
    beats: int
    k_start: int         # beat index where the shot starts
    k_end: int           # beat index where the next shot starts
    start_f: int
    end_f: int
    speed: float         # source seconds consumed per timeline second
    interp: str          # "auto", "oflow", "blend" or "none"
    tin: str = "none"    # transition into this shot: "none", "cut", "fade", "fadewhite"
    din_f: int = 0       # its length in frames (0 for none and cut)
    tout: str = "none"   # transition out of this shot (the next shot's tin)
    dout_f: int = 0
    win_s_f: int = 0     # rendered window on the timeline, transition handles included
    win_e_f: int = 0
    len_f: int = 0       # frames to render
    src_start: float = 0.0   # seconds into the source clip where rendering starts
    src_len: float = 0.0     # seconds of source consumed


def beat_time(state, k):
    return state["t0"] + state["period"] * k


def beat_frame(state, k, fps):
    return round(beat_time(state, k) * fps)


def _fade_frames(kind, seconds, fps):
    if kind == "cut":
        return 0
    n = round(seconds * fps)
    n -= n % 2
    return n if n >= MIN_FADE_FRAMES else 0


def build_plan(state, fps):
    """`state` must be defaults-filled (schema.with_defaults). `fps` is a float."""
    shots = state["shots"]
    last = len(shots) - 1
    segs, k = [], 0
    for i, d in enumerate(shots):
        ks, ke = k, k + d["beats"]
        start_f = 0 if i == 0 else beat_frame(state, ks, fps)
        end_f = beat_frame(state, ke, fps) + (round(state["tail"] * fps) if i == last else 0)
        speed = (d["out"] - d["in"]) / (d["beats"] * state["period"]) if d["fit"] == "fit" else 1.0
        if abs(speed - 1.0) < SPEED_SNAP:
            speed = 1.0
        segs.append(Segment(index=i, clip=d["clip"], prof=d["prof"], beats=d["beats"], k_start=ks, k_end=ke,
                            start_f=start_f, end_f=end_f, speed=speed, interp=d.get("interp", "auto")))
        k = ke
    for i in range(1, len(segs)):
        frames = _fade_frames(shots[i]["tin"], shots[i]["tdur"], fps)
        kind = shots[i]["tin"] if frames else "cut"
        segs[i].tin, segs[i].din_f = kind, frames
        segs[i - 1].tout, segs[i - 1].dout_f = kind, frames
    for i, s in enumerate(segs):
        s.win_s_f = s.start_f - s.din_f // 2
        s.win_e_f = s.end_f + s.dout_f // 2
        s.len_f = s.win_e_f - s.win_s_f
        s.src_start = shots[i]["in"] - (s.din_f // 2) / fps * s.speed
        s.src_len = s.len_f / fps * s.speed
    return segs


def total_frames(segs):
    return segs[-1].end_f if segs else 0


def check_plan(segs, durations, bad, music_duration, fps):
    """Returns (errors, warnings). `durations`: clip name -> seconds. `bad`: clip name -> [{a, b}].
    `music_duration` may be None when unknown."""
    if not segs:
        return ["no shots"], []
    errors, warnings = [], []
    for s in segs:
        tag = f"shot {s.index + 1} ({s.clip})"
        if s.clip not in durations:
            errors.append(f"{tag}: clip not found in the clips folder")
            continue
        end = s.src_start + s.src_len
        if s.src_start < 0:
            errors.append(f"{tag}: needs {-s.src_start:.2f} s before the start of the clip; move `in` later")
        if end > durations[s.clip] - SRC_MARGIN:
            errors.append(f"{tag}: needs source up to {end:.2f} s but the clip is {durations[s.clip]} s long")
        if s.len_f < max(1, s.din_f + s.dout_f):
            errors.append(f"{tag}: shorter than its transitions; shorten the transition or add beats")
        if not MIN_SPEED <= s.speed <= MAX_SPEED:
            errors.append(f"{tag}: speed {s.speed:.2f}x is outside {MIN_SPEED}x-{MAX_SPEED}x")
        for r in bad.get(s.clip, []):
            if s.src_start < r["b"] and end > r["a"]:
                warnings.append(f"{tag}: uses a range marked bad ({r['a']}-{r['b']} s)")
    seconds = total_frames(segs) / fps
    if music_duration is not None and seconds > music_duration + 0.05:
        errors.append(f"the edit is {seconds:.2f} s long but the music is only {music_duration:.2f} s")
    return errors, warnings
