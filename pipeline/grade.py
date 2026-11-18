"""Colour profiles. A profile is one plain ffmpeg filter chain applied to RGB frames.

Custom profiles come from project.json only; shots.json carries profile names, never filters.
"""
from __future__ import annotations

import subprocess

BUILTIN = {
    "neutral": "",
    "warm": "colorbalance=rs=.03:bs=-.03:rm=.03:bm=-.035:rh=.02:bh=-.04,eq=saturation=1.03",
    "cool": "colorbalance=rs=-.03:bs=.03:rm=-.02:bm=.03:rh=-.02:bh=.03,eq=saturation=0.97",
    "contrast": "curves=all='0/0 0.25/0.2 0.5/0.5 0.75/0.8 1/1',eq=saturation=1.05",
    "golden": ("curves=all='0/0.02 0.25/0.2 0.5/0.45 0.75/0.7 0.9/0.82 1/0.92',"
               "colorbalance=rs=.02:gs=-.02:bs=-.02:rm=.03:gm=-.02:bm=-.03:rh=.02:bh=-.03,"
               "eq=saturation=0.9"),
}


class GradeError(ValueError):
    pass


def profile_names(custom):
    return set(BUILTIN) | set(custom)


def chain(name, custom):
    """Filter chain for a profile name; custom profiles override built-in ones."""
    if name in custom:
        return custom[name]
    if name in BUILTIN:
        return BUILTIN[name]
    raise GradeError(f"unknown colour profile: {name}")


def validate_chain(chain, run=subprocess.run):
    """Try a filter chain on a tiny generated frame. Returns None when ffmpeg accepts it,
    otherwise a short description of the failure."""
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:d=0.04",
           "-vf", f"format=gbrp,{chain},format=yuv420p", "-frames:v", "1", "-f", "null", "-"]
    try:
        r = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        return str(e)
    if r.returncode != 0:
        return (r.stderr or "").strip()[-300:] or f"exit code {r.returncode}"
    return None
