"""beatcut command line: cut video to the beat of a music track.

Exit codes: 0 success, 1 user input problem, 2 environment or run-time failure.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
import sys
from pathlib import Path

from pipeline import beats, config, encoders, grade, media, mix, plan, render, schema, sheet
from pipeline.config import ConfigError
from pipeline.lock import LockError, ProjectLock
from pipeline.schema import SchemaError

USER_ERRORS = (ConfigError, SchemaError)
RUN_ERRORS = (media.MediaError, encoders.EncoderError, render.RenderError, mix.MixError, beats.BeatError, LockError)


class UserError(Exception):
    """A problem with what the user supplied (exit code 1)."""


def _err(message) -> None:
    print(f"error: {message}", file=sys.stderr)


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_init(args) -> int:
    clips = Path(args.clips_dir)
    music = Path(args.music)
    if not clips.is_dir():
        raise UserError(f"clips folder does not exist: {clips}")
    if not music.is_file():
        raise UserError(f"music file not found: {music}")
    root = clips / "beatcut"
    if (root / "project.json").exists():
        raise UserError(f"{root / 'project.json'} already exists; nothing was changed")
    root.mkdir(exist_ok=True)
    target = root / music.name
    if not (target.exists() and os.path.samefile(music, target)):
        shutil.copy2(music, target)
    _write_json(root / "project.json", config.default_project(music.name))
    if not (root / "shots.json").exists():
        _write_json(root / "shots.json", schema.empty_state())
    print(f"created: {root}")
    return 0


def _load_state(project):
    return schema.load_shots(project.shots_path, grade.profile_names(project.profiles))


def cmd_beats(args) -> int:
    project = config.load_project(args.project)
    if not project.music.is_file():
        raise UserError(f"music file not found: {project.music.name}")
    try:
        result = beats.analyze(project.music)
    except ImportError as e:
        raise media.MediaError(f"beat detection needs librosa and numpy ({e})") from e
    except beats.BeatError as e:
        raise UserError(str(e)) from e  # the music gave too little to fit; not an environment fault
    if project.shots_path.exists():
        state = _load_state(project)
    else:
        state = schema.empty_state()
    state["period"], state["t0"] = result["period"], result["t0"]
    schema.save_shots(project.shots_path, state)
    print(f"period: {result['period']}")
    print(f"t0: {result['t0']}")
    print(f"bpm: {result['bpm']}")
    return 0


def _prepare(project_dir):
    """Shared by check, draft and final. Returns (project, state, segs, errors, warnings)."""
    project = config.load_project(project_dir)
    if not project.clips_dir.is_dir():
        raise UserError(f"clips folder does not exist: {project.clips_dir}")
    media.require_tools()
    state = _load_state(project)
    unreadable = {}
    meta = media.clip_meta(project, errors=unreadable)
    segs = plan.build_plan(state, project.fps_float)
    if project.music.is_file():
        music_duration = media.probe_duration(project.music)
        extra = []
    else:
        music_duration = None
        extra = [f"music file not found: {project.music.name}"]
    errors, warnings = plan.check_plan(segs, {name: m["duration"] for name, m in meta.items()},
                                       state["bad"], music_duration, project.fps_float)
    for name in sorted(unreadable):
        warnings.append(f"{name}: cannot be read ({unreadable[name]})")
    for name in sorted({s.clip for s in segs} & set(meta)):
        transfer = media.hdr_transfer(meta[name])
        if transfer:
            warnings.append(f"{name}: HDR source ({transfer}); colours will look washed out because beatcut "
                            f"does not tone-map yet")
    used = sorted({s["prof"] for s in state["shots"]} & set(project.profiles))
    for name in used:
        if grade.validate_chain(project.profiles[name]) is not None:
            extra.append(f"profile {name}: ffmpeg rejects the filter chain")
    if segs and not render.assemble_fits(segs, project.fps_float, round(state["tail"] * project.fps_float),
                                         "draft"):
        extra.append(f"too many shots to assemble in one pass ({len(segs)}); split the edit")
    return project, state, segs, extra + errors, warnings


def _report(errors, warnings) -> None:
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    for e in errors:
        _err(e)


def cmd_check(args) -> int:
    project, state, segs, errors, warnings = _prepare(args.project)
    _report(errors, warnings)
    if errors:
        return 1
    print(f"ok: {len(segs)} shots, {plan.total_frames(segs) / project.fps_float:.1f} s")
    return 0


def _build(args, mode) -> int:
    project, state, segs, errors, warnings = _prepare(args.project)
    _report(errors, warnings)
    if errors:
        return 1
    with ProjectLock(project):
        video = render.render(project, state, mode)
        out = mix.mix(project, state, mode, video, plan.total_frames(segs), log=lambda *a: None)
    print(out)
    return 0


def cmd_draft(args) -> int:
    return _build(args, "draft")


def cmd_final(args) -> int:
    return _build(args, "final")


def cmd_sheet(args) -> int:
    src = Path(args.file)
    if not src.is_file():
        raise UserError(f"file not found: {src.name}")
    out = Path(args.out) if args.out else Path.cwd() / f"{src.stem}_sheet.jpg"
    print(sheet.make_sheet(src, out))
    return 0


def cmd_pick(args) -> int:
    try:
        from picker.server import serve
    except ImportError:
        _err("the picker is not available in this build")
        return 2
    try:
        serve(args.project, args.port, not args.no_open)
    except OSError as e:
        # EADDRINUSE; Windows reports a port held exclusively as WSAEACCES (10013) or WSAEADDRINUSE (10048).
        if e.errno == errno.EADDRINUSE or getattr(e, "winerror", None) in (10013, 10048):
            _err(f"port {args.port} is already in use; pass --port to choose another")
            return 2
        raise
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="beatcut", description="Cut video to the beat of a music track.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a beatcut project inside a clips folder")
    p.add_argument("clips_dir")
    p.add_argument("--music", required=True, help="music file to copy into the project")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("beats", help="detect the beat grid and write period and t0 to shots.json")
    p.add_argument("project")
    p.set_defaults(func=cmd_beats)

    for name, func, text in (("check", cmd_check, "validate the project and the edit"),
                             ("draft", cmd_draft, "render a draft"),
                             ("final", cmd_final, "render the final video")):
        p = sub.add_parser(name, help=text)
        p.add_argument("project")
        p.set_defaults(func=func)

    p = sub.add_parser("sheet", help="write a contact sheet of a video")
    p.add_argument("file")
    p.add_argument("--out", help="output JPEG (default: <name>_sheet.jpg in the current folder)")
    p.set_defaults(func=cmd_sheet)

    p = sub.add_parser("pick", help="open the clip picker")
    p.add_argument("project")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true", help="do not open a browser window")
    p.set_defaults(func=cmd_pick)
    return parser


def _prepare_streams() -> None:
    """Never crash on characters the console or a redirect cannot encode."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        options = {"errors": "backslashreplace"}
        try:
            if not stream.isatty():
                options["encoding"] = "utf-8"
            reconfigure(**options)
        except (ValueError, OSError):
            pass


def main(argv=None) -> int:
    _prepare_streams()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SchemaError as e:
        for line in e.errors:
            _err(line)
        return 1
    except (ConfigError, UserError, sheet.SheetError) as e:
        _err(e)
        return 1
    except RUN_ERRORS as e:
        _err(e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
