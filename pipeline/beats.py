"""Beat grid detection. librosa is imported only inside analyze()."""
from __future__ import annotations

import numpy as np


class BeatError(RuntimeError):
    pass


def fit_grid(times):
    """Least-squares beat grid through detected beat times. Returns (period, t0).

    Beats are numbered from the gaps between neighbours, so a detector that rounds every
    beat to its analysis hop, or misses a few beats, does not bend the fit.
    t0 is the first grid point at or after zero."""
    t = np.asarray(times, dtype=float)
    if t.size < 4:
        raise BeatError("fewer than 4 beats were detected; set period and t0 by hand")
    gaps = np.diff(t)
    step = float(np.median(gaps))
    k = np.concatenate([[0.0], np.cumsum(np.maximum(np.round(gaps / step), 1.0))])
    period, first = np.polyfit(k, t, 1)
    return float(period), float(first % period)


def analyze(path):
    """Returns {"period", "t0", "bpm", "beats_found"} for a music file."""
    import librosa

    y, sr = librosa.load(str(path), sr=22050, mono=True)
    _, frames = librosa.beat.beat_track(y=y, sr=sr)
    times = librosa.frames_to_time(frames, sr=sr)
    period, t0 = fit_grid(times)
    return {"period": round(period, 5), "t0": round(t0, 4), "bpm": round(60.0 / period, 2),
            "beats_found": int(len(times))}
