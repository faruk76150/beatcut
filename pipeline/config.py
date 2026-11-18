"""project.json -> validated, immutable Project object.

Importing this module has no side effects: files are read only inside load_project().
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

DEFAULT_EXTENSIONS = (".mp4", ".mov", ".mxf")
DEFAULT_FPS = "30000/1001"
DEFAULT_DRAFT = (1920, 1080)
DEFAULT_FINAL = (3840, 2160)
DEFAULT_LOUDNESS = -15.0

_FPS_RE = re.compile(r"^\d+(/\d+)?$")
_PROFILE_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
_KNOWN_KEYS = {"clips_dir", "extensions", "exclude_prefix", "music", "fps", "draft", "final",
               "fill", "encoder", "loudness", "profiles", "out_name"}


class ConfigError(ValueError):
    """Raised when project.json is missing, unreadable or invalid."""


@dataclass(frozen=True)
class Project:
    root: Path
    clips_dir: Path
    extensions: tuple
    exclude_prefix: tuple
    music: Path
    fps: str
    draft: tuple
    final: tuple
    fill: str
    encoder: str
    loudness: float
    profiles: dict
    out_name: str

    @property
    def shots_path(self) -> Path:
        return self.root / "shots.json"

    @property
    def proxy_dir(self) -> Path:
        return self.root / "proxy"

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @property
    def fps_float(self) -> float:
        num, _, den = self.fps.partition("/")
        return int(num) / int(den or 1)

    def size(self, mode: str) -> tuple:
        return self.draft if mode == "draft" else self.final


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_text(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _has_parent_ref(text: str) -> bool:
    return ".." in re.split(r"[/\\]", text)


def _parse_music(v) -> str:
    if not _is_text(v):
        raise ConfigError("music: must be a non-empty file name relative to the project folder")
    if (PurePosixPath(v).is_absolute() or PureWindowsPath(v).is_absolute()
            or PureWindowsPath(v).drive or v.startswith(("/", "\\"))):
        raise ConfigError("music: must be a relative path, not an absolute one")
    if _has_parent_ref(v):
        raise ConfigError("music: must not contain '..'")
    return v


def _parse_fps(v) -> str:
    if not isinstance(v, str) or not _FPS_RE.match(v):
        raise ConfigError("fps: must be a string like '25' or '30000/1001'")
    num, _, den = v.partition("/")
    if int(num) == 0 or (den != "" and int(den) == 0):
        raise ConfigError("fps: numerator and denominator must be greater than zero")
    return v


def _parse_size(key, v, default) -> tuple:
    if v is None:
        return default
    if not isinstance(v, dict) or set(v) != {"width", "height"}:
        raise ConfigError(f"{key}: must be an object with exactly 'width' and 'height'")
    for dim in ("width", "height"):
        n = v[dim]
        if isinstance(n, bool) or not isinstance(n, int) or n <= 0 or n % 2:
            raise ConfigError(f"{key}.{dim}: must be a positive even integer")
    return (v["width"], v["height"])


def _parse_str_list(key, v) -> list:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ConfigError(f"{key}: must be a list of strings")
    return v


def _parse_extensions(v) -> tuple:
    items = _parse_str_list("extensions", v)
    if not items:
        raise ConfigError("extensions: must not be empty")
    for x in items:
        if not x.startswith(".") or len(x) < 2:
            raise ConfigError(f"extensions: {x!r} must start with '.'")
    return tuple(x.lower() for x in items)


def _parse_profiles(v) -> dict:
    if not isinstance(v, dict):
        raise ConfigError("profiles: must be an object mapping profile names to filter chains")
    out = {}
    for name, chain in v.items():
        if not isinstance(name, str) or not _PROFILE_NAME_RE.match(name):
            raise ConfigError(f"profiles: name {name!r} must match [a-z0-9_-]{{1,32}}")
        if not _is_text(chain):
            raise ConfigError(f"profiles.{name}: filter chain must be a non-empty string")
        if any(ch in chain for ch in ";[]"):
            raise ConfigError(f"profiles.{name}: filter chain must not contain ';', '[' or ']'")
        out[name] = chain
    return out


def _parse_choice(key, v, allowed) -> str:
    if v not in allowed:
        raise ConfigError(f"{key}: must be one of {', '.join(allowed)}")
    return v


_FORBIDDEN_NAME_CHARS = '<>:"/\\|?*'


def _parse_out_name(v) -> str:
    if not _is_text(v) or ".." in v or any(ch in _FORBIDDEN_NAME_CHARS for ch in v):
        raise ConfigError("out_name: must be a non-empty file name without any of < > : \" / \\ | ? * or '..'")
    return v


def _default_out_name(root: Path) -> str:
    """The project folder's name; for the conventional `<clips>/beatcut` folder, the clips folder's name."""
    if root.name == "beatcut":
        parent = "".join("_" if ch in _FORBIDDEN_NAME_CHARS else ch for ch in root.parent.name).strip(" .")
        if parent and ".." not in parent:
            return parent
    return root.name


def parse_project(data: dict, root) -> Project:
    """Validate `data` (the parsed project.json) and build a Project rooted at `root`."""
    if not isinstance(data, dict):
        raise ConfigError("project.json: top level must be an object")
    unknown = sorted(set(data) - _KNOWN_KEYS)
    if unknown:
        raise ConfigError(f"unknown key(s): {', '.join(unknown)}")
    if "music" not in data:
        raise ConfigError("music: required")

    root = Path(root).resolve()
    music = _parse_music(data["music"])

    if "clips_dir" in data:
        if not _is_text(data["clips_dir"]):
            raise ConfigError("clips_dir: must be a non-empty string")
        clips_dir = (root / data["clips_dir"]).resolve()
    else:
        clips_dir = root.parent

    loudness = data.get("loudness", DEFAULT_LOUDNESS)
    if not _is_number(loudness) or not -40 <= loudness <= -5:
        raise ConfigError("loudness: must be a number between -40 and -5")

    return Project(
        root=root,
        clips_dir=clips_dir,
        extensions=_parse_extensions(data["extensions"]) if "extensions" in data else DEFAULT_EXTENSIONS,
        exclude_prefix=tuple(_parse_str_list("exclude_prefix", data.get("exclude_prefix", []))),
        music=root / music,
        fps=_parse_fps(data.get("fps", DEFAULT_FPS)),
        draft=_parse_size("draft", data.get("draft"), DEFAULT_DRAFT),
        final=_parse_size("final", data.get("final"), DEFAULT_FINAL),
        fill=_parse_choice("fill", data.get("fill", "cover"), ("cover", "blur")),
        encoder=_parse_choice("encoder", data.get("encoder", "auto"), ("auto", "cpu", "nvenc")),
        loudness=float(loudness),
        profiles=_parse_profiles(data.get("profiles", {})),
        out_name=_parse_out_name(data["out_name"]) if "out_name" in data else _default_out_name(root),
    )


def load_project(root) -> Project:
    """Read root/project.json and return the validated Project."""
    root = Path(root)
    path = root / "project.json"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as e:
        hint = ""
        if (root / "beatcut" / "project.json").is_file():
            hint = f" (did you mean {root / 'beatcut'}?)"
        raise ConfigError(f"no project.json in {root}{hint}") from e
    except OSError as e:
        raise ConfigError(f"project.json: cannot read {path}: {e}") from e
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ConfigError(f"project.json: invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError("project.json: top level must be an object")
    return parse_project(data, root)


def default_project(music_name: str) -> dict:
    """The dict `init` writes: every default spelled out, plus the music file name."""
    return {
        "clips_dir": "..",
        "extensions": list(DEFAULT_EXTENSIONS),
        "exclude_prefix": [],
        "music": music_name,
        "fps": DEFAULT_FPS,
        "draft": {"width": DEFAULT_DRAFT[0], "height": DEFAULT_DRAFT[1]},
        "final": {"width": DEFAULT_FINAL[0], "height": DEFAULT_FINAL[1]},
        "fill": "cover",
        "encoder": "auto",
        "loudness": DEFAULT_LOUDNESS,
        "profiles": {},
    }
