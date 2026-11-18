"""Contact sheet: one JPEG with evenly spaced frames from a video."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pipeline import media


class SheetError(ValueError):
    """Raised when the requested contact sheet would be unsafe to write."""


def _same_file(a: Path, b: Path) -> bool:
    try:
        if a.resolve() == b.resolve():
            return True
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def make_sheet(src, out, cols=5, rows=4, cell_width=384) -> Path:
    """Write a cols x rows grid of frames sampled evenly across `src` to `out`."""
    src, out = Path(src), Path(out)
    if _same_file(src, out) and out.suffix.lower() not in (".jpg", ".jpeg"):
        raise SheetError(f"{out.name}: the output would overwrite the source video; choose a .jpg name")
    media.require_tools()
    duration = media.probe_duration(src)
    if not duration or duration <= 0:
        raise media.MediaError(f"{src.name}: cannot read the duration")
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"fps={cols * rows / duration:.6f},scale={cell_width}:-2,tile={cols}x{rows}"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", os.path.abspath(src), "-vf", vf,
           "-frames:v", "1", "-q:v", "3", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise media.MediaError(f"contact sheet failed: {(r.stderr or '').strip()[-600:]}")
    return out
