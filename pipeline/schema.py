"""shots.json validation, defaults and safe writing."""
import copy
import json
import math
import os
import shutil
import tempfile

from . import fsutil

SHOT_DEFAULTS = {"fit": "fit", "prof": "neutral", "tin": "cut", "tdur": 0.5, "note": ""}
MAX_SHOTS = 2000

_FITS = ("fit", "cut")
_INTERPS = ("oflow", "blend", "none")
_TINS = ("cut", "fade", "fadewhite")


class SchemaError(ValueError):
    """Raised when shots.json cannot be loaded; `errors` has one message per problem."""

    def __init__(self, errors, message=None):
        self.errors = list(errors)
        super().__init__(message if message is not None else "; ".join(self.errors))


def empty_state():
    return {"version": 1, "period": 0.5, "t0": 0.0, "tail": 1.5,
            "shots": [], "bad": {}, "clipnotes": {}, "markers": []}


def _is_num(v):
    """True for int/float that is finite; bool is not a number here."""
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _validate_shot(s, path, profile_names, errors):
    if not isinstance(s, dict):
        errors.append(f"{path}: must be an object")
        return
    clip = s.get("clip")
    if (not isinstance(clip, str) or not clip or "/" in clip or "\\" in clip
            or clip in (".", "..")):
        errors.append(f"{path}.clip: must be a non-empty file name without path separators")
    elif ":" in clip or any(ord(c) < 32 or ord(c) == 127 for c in clip):
        errors.append(f"{path}.clip: must not contain ':' or control characters")
    t_in = s.get("in")
    in_ok = _is_num(t_in) and t_in >= 0
    if not in_ok:
        errors.append(f"{path}.in: must be a number >= 0")
    t_out = s.get("out")
    if not _is_num(t_out) or (in_ok and t_out <= t_in):
        errors.append(f"{path}.out: must be a number > in")
    beats = s.get("beats")
    if not _is_int(beats) or not 1 <= beats <= 512:
        errors.append(f"{path}.beats: must be an integer from 1 to 512")
    if "fit" in s and s["fit"] not in _FITS:
        errors.append(f"{path}.fit: must be one of {', '.join(_FITS)}")
    if "interp" in s and s["interp"] not in _INTERPS:
        errors.append(f"{path}.interp: must be one of {', '.join(_INTERPS)}")
    if "prof" in s and (not isinstance(s["prof"], str) or s["prof"] not in profile_names):
        errors.append(f"{path}.prof: must be one of {', '.join(sorted(profile_names))}")
    if "tin" in s and s["tin"] not in _TINS:
        errors.append(f"{path}.tin: must be one of {', '.join(_TINS)}")
    if "tdur" in s and (not _is_num(s["tdur"]) or not 0 <= s["tdur"] <= 5):
        errors.append(f"{path}.tdur: must be a number from 0 to 5")
    if "note" in s and (not isinstance(s["note"], str) or len(s["note"]) > 2000):
        errors.append(f"{path}.note: must be a string of at most 2000 characters")


def _validate_bad(bad, errors):
    if not isinstance(bad, dict):
        errors.append("bad: must be an object")
        return
    for clip, ranges in bad.items():
        base = f"bad[{clip}]"
        if not isinstance(ranges, list):
            errors.append(f"{base}: must be a list")
            continue
        for i, r in enumerate(ranges):
            p = f"{base}[{i}]"
            if not isinstance(r, dict):
                errors.append(f"{p}: must be an object")
                continue
            a, b = r.get("a"), r.get("b")
            a_ok = _is_num(a) and a >= 0
            if not a_ok:
                errors.append(f"{p}.a: must be a number >= 0")
            if not _is_num(b) or (a_ok and b <= a):
                errors.append(f"{p}.b: must be a number > a")
            if "note" in r and not isinstance(r["note"], str):
                errors.append(f"{p}.note: must be a string")


def validate_shots(data, profile_names):
    """Return a list of error messages ([] when valid). Never raises."""
    errors = []
    try:
        if not isinstance(data, dict):
            return ["$: must be an object"]
        if not _is_int(data.get("version")) or data.get("version") != 1:
            errors.append("version: must be 1")
        period = data.get("period")
        if not _is_num(period) or not 0.05 < period < 10:
            errors.append("period: must be a number > 0.05 and < 10")
        t0 = data.get("t0")
        if not _is_num(t0) or t0 < 0:
            errors.append("t0: must be a number >= 0")
        if "tail" in data and (not _is_num(data["tail"]) or not 0 <= data["tail"] <= 30):
            errors.append("tail: must be a number from 0 to 30")
        shots = data.get("shots")
        if not isinstance(shots, list):
            errors.append("shots: must be a list")
        else:
            if len(shots) > MAX_SHOTS:
                errors.append(f"shots: must have at most {MAX_SHOTS} items")
            for i, s in enumerate(shots[:MAX_SHOTS]):
                _validate_shot(s, f"shots[{i}]", profile_names, errors)
        if "bad" in data:
            _validate_bad(data["bad"], errors)
        if "clipnotes" in data:
            notes = data["clipnotes"]
            if not isinstance(notes, dict):
                errors.append("clipnotes: must be an object")
            else:
                for k, v in notes.items():
                    if not isinstance(v, str):
                        errors.append(f"clipnotes[{k}]: must be a string")
        if "markers" in data:
            markers = data["markers"]
            if not isinstance(markers, list):
                errors.append("markers: must be a list")
            else:
                for i, m in enumerate(markers):
                    if not isinstance(m, dict):
                        errors.append(f"markers[{i}]: must be an object")
                        continue
                    if not _is_int(m.get("beat")) or m["beat"] < 0:
                        errors.append(f"markers[{i}].beat: must be an integer >= 0")
                    if not isinstance(m.get("title"), str):
                        errors.append(f"markers[{i}].title: must be a string")
    except Exception as exc:  # defensive: validation must never raise
        errors.append(f"$: could not be validated ({exc})")
    return errors


def with_defaults(data):
    """Deep copy of `data` with optional top-level keys and shot defaults filled in."""
    out = copy.deepcopy(data)
    out.setdefault("tail", 1.5)
    out.setdefault("bad", {})
    out.setdefault("clipnotes", {})
    out.setdefault("markers", [])
    for s in out.get("shots", []):
        for k, v in SHOT_DEFAULTS.items():
            s.setdefault(k, v)
    return out


def load_shots(path, profile_names):
    """Read, validate and default-fill a shots.json file; raise SchemaError on any problem."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except OSError as exc:
        raise SchemaError([f"{path}: cannot read file ({exc})"])
    except ValueError as exc:
        raise SchemaError([f"{path}: invalid JSON ({exc})"])
    errors = validate_shots(data, profile_names)
    if errors:
        raise SchemaError(errors)
    return with_defaults(data)


def save_shots(path, state):
    """Atomically write `state`, keeping a .bak of the old file and its unknown top-level keys."""
    path = os.fspath(path)
    out = dict(state)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = None
        if isinstance(old, dict):
            for k, v in old.items():
                out.setdefault(k, v)
        shutil.copyfile(path, path + ".bak")
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".shots-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        fsutil.replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
