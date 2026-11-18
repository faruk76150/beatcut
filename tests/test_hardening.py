"""Regression tests for robustness fixes (locking, retries, encodings, probing, config, CLI)."""
import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import beatcut
from pipeline import fsutil, grade, media, mix, render, schema, sheet
from pipeline.config import ConfigError, load_project, parse_project
from pipeline.lock import LockError, ProjectLock
from pipeline.media import MediaError, clip_meta, parse_probe
from util import make_state

ROOT = Path(__file__).resolve().parent.parent
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def project_in(tmp_path, **extra):
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    return parse_project({"music": "m.wav", **extra}, root)


# ---- D1: project lock -------------------------------------------------------------------------

def test_lock_is_exclusive_and_released(tmp_path):
    p = project_in(tmp_path)
    with ProjectLock(p):
        with pytest.raises(LockError, match="another render is already running for this project"):
            with ProjectLock(p):
                pass
    with ProjectLock(p):
        pass


def test_lock_is_released_when_the_holder_dies(tmp_path):
    p = project_in(tmp_path)
    code = ("import sys, time\n"
            "from pipeline.config import load_project\n"
            "from pipeline.lock import ProjectLock\n"
            "lock = ProjectLock(load_project(sys.argv[1]))\n"
            "lock.__enter__()\n"
            "print('locked', flush=True)\n"
            "time.sleep(60)\n")
    (p.root / "project.json").write_text(json.dumps({"music": "m.wav"}), encoding="utf-8")
    child = subprocess.Popen([sys.executable, "-c", code, str(p.root)], cwd=ROOT, stdout=subprocess.PIPE,
                             text=True, env={**os.environ, "PYTHONPATH": str(ROOT)})
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(LockError):
            with ProjectLock(p):
                pass
    finally:
        child.kill()
        child.wait()
        child.stdout.close()
    with ProjectLock(p):
        pass


def test_cli_reports_a_busy_project_with_exit_code_2(tmp_path, monkeypatch, capsys):
    p = project_in(tmp_path)
    monkeypatch.setattr(beatcut, "_prepare", lambda d: (p, {}, [], [], []))
    monkeypatch.setattr(render, "render", lambda *a, **k: pytest.fail("must not render"))
    with ProjectLock(p):
        assert beatcut.main(["draft", str(p.root)]) == 2
    err = capsys.readouterr().err
    assert "error: another render is already running" in err and "Traceback" not in err


def test_render_segment_temp_files_are_unique(tmp_path, monkeypatch):
    p = project_in(tmp_path, clips_dir=".")
    (p.root / "a.mp4").write_bytes(b"x")
    p.work_dir.mkdir()
    seen = []

    def fake_run(cmd, cwd=None):
        seen.append(Path(cmd[-1]))
        Path(cmd[-1]).write_bytes(b"v")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(render, "_run", fake_run)
    seg = render.build_plan(make_state([{"clip": "a.mp4", "in": 1.0, "out": 3.0, "beats": 4}]), 30.0)[0]
    meta = {"a.mp4": {"width": 640, "height": 360, "fps": 30.0}}
    render.render_segment(p, seg, "draft", "cpu", meta)
    (p.work_dir / "seg_draft_000.key").unlink()
    render.render_segment(p, seg, "draft", "cpu", meta)
    assert len(seen) == 2 and seen[0] != seen[1]
    assert all(t.suffix == ".mp4" and t.parent == p.work_dir for t in seen)
    assert not [f for f in p.work_dir.iterdir() if f.name not in ("seg_draft_000.mp4", "seg_draft_000.key")]


# ---- D2 / D11: replace with retry ---------------------------------------------------------------

def test_replace_with_retry_recovers(tmp_path, monkeypatch):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_text("x")
    real, calls = os.replace, []

    def flaky(s, d):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("busy")
        real(s, d)

    monkeypatch.setattr(fsutil.os, "replace", flaky)
    fsutil.replace_with_retry(src, dst, delay=0)
    assert dst.read_text() == "x" and len(calls) == 3


def test_replace_with_retry_raises_on_the_last_attempt(tmp_path, monkeypatch):
    calls = []

    def always(s, d):
        calls.append(1)
        raise PermissionError("busy")

    monkeypatch.setattr(fsutil.os, "replace", always)
    with pytest.raises(PermissionError):
        fsutil.replace_with_retry(tmp_path / "a", tmp_path / "b", tries=4, delay=0)
    assert len(calls) == 4


def test_save_shots_survives_a_reader_holding_the_file(tmp_path, monkeypatch):
    path = tmp_path / "shots.json"
    state = schema.empty_state()
    real, calls = os.replace, []

    def flaky(s, d):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("reader")
        real(s, d)

    monkeypatch.setattr(fsutil.os, "replace", flaky)
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    schema.save_shots(path, state)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1 and len(calls) == 2


def test_clip_meta_cache_write_failure_is_not_fatal(tmp_path, monkeypatch):
    p = project_in(tmp_path, clips_dir=".")
    (p.root / "a.mp4").write_bytes(b"x")

    def boom(s, d, **k):
        raise PermissionError("locked")

    monkeypatch.setattr(media.fsutil, "replace_with_retry", boom)
    meta = clip_meta(p, probe_fn=lambda c: {"duration": 5.0, "width": 2, "height": 2, "fps": 25.0, "rotation": 0})
    assert meta["a.mp4"]["duration"] == 5.0
    assert not list(p.work_dir.glob("*.tmp")) and not list(p.work_dir.glob("meta*"))


# ---- D3: sheet -----------------------------------------------------------------------------------

def test_sheet_refuses_to_overwrite_its_source(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    with pytest.raises(sheet.SheetError):
        sheet.make_sheet(src, src)
    with pytest.raises(sheet.SheetError):
        sheet.make_sheet(src, tmp_path / "." / "clip.mp4")
    assert issubclass(sheet.SheetError, ValueError)
    assert src.read_bytes() == b"x"


def test_cli_sheet_overwrite_and_missing_file(tmp_path, capsys):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    assert beatcut.main(["sheet", str(src), "--out", str(src)]) == 1
    assert "error:" in capsys.readouterr().err and src.read_bytes() == b"x"
    assert beatcut.main(["sheet", str(tmp_path / "nope.mp4")]) == 1
    assert "error: file not found: nope.mp4" in capsys.readouterr().err


def test_cli_sheet_default_goes_to_the_working_folder(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src" / "clip.mp4"
    src.parent.mkdir()
    src.write_bytes(b"x")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    seen = []
    monkeypatch.setattr(sheet, "make_sheet", lambda s, o: seen.append((Path(s), Path(o))) or Path(o))
    assert beatcut.main(["sheet", str(src)]) == 0
    assert seen[0][1].resolve() == (cwd / "clip_sheet.jpg").resolve()


# ---- D4: output encoding ---------------------------------------------------------------------

class FakeStream:
    def __init__(self, tty):
        self.tty, self.calls = tty, []

    def isatty(self):
        return self.tty

    def reconfigure(self, **kw):
        self.calls.append(kw)

    def write(self, s):
        return len(s)

    def flush(self):
        pass


def test_main_reconfigures_the_standard_streams(tmp_path, monkeypatch):
    out, err = FakeStream(False), FakeStream(True)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    beatcut.main(["check", str(tmp_path)])
    assert out.calls == [{"encoding": "utf-8", "errors": "backslashreplace"}]
    assert err.calls == [{"errors": "backslashreplace"}]


def test_init_and_check_survive_redirected_output_with_non_ascii_names(tmp_path):
    folder = tmp_path / "çekim şubat"
    folder.mkdir()
    music = tmp_path / "şarkı.mp3"
    music.write_bytes(b"ID3")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    for args in (["init", str(folder), "--music", str(music)], ["check", str(folder / "beatcut")]):
        if args[0] == "check" and shutil.which("ffmpeg") is None:
            pytest.skip("check needs ffmpeg on PATH")
        r = subprocess.run([sys.executable, str(ROOT / "beatcut.py"), *args], capture_output=True, env=env)
        assert b"Traceback" not in r.stderr and b"UnicodeEncodeError" not in r.stderr, r.stderr
        assert r.returncode in (0, 1)


# ---- D5: subprocess text decoding ------------------------------------------------------------------

def test_every_text_subprocess_call_in_the_pipeline_sets_an_encoding():
    bad = []
    for path in (ROOT / "pipeline").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                kw = {k.arg for k in node.keywords}
                if "text" in kw and not {"encoding", "errors"} <= kw:
                    bad.append(f"{path.name}:{node.lineno}")
    assert not bad


# ---- D6: unreadable clips ----------------------------------------------------------------------

def test_clip_meta_skips_unreadable_clips(tmp_path):
    p = project_in(tmp_path, clips_dir=".")
    (p.root / "good.mp4").write_bytes(b"x")
    (p.root / "bad.mp4").write_bytes(b"x")

    def probe(clip):
        if clip.name == "bad.mp4":
            raise MediaError("bad.mp4: broken")
        return {"duration": 5.0, "width": 2, "height": 2, "fps": 25.0, "rotation": 0}

    errors = {}
    meta = clip_meta(p, probe_fn=probe, errors=errors)
    assert list(meta) == ["good.mp4"] and errors == {"bad.mp4": "bad.mp4: broken"}
    assert "bad.mp4" not in json.loads((p.work_dir / "meta.json").read_text(encoding="utf-8"))
    assert list(clip_meta(p, probe_fn=probe)) == ["good.mp4"]      # errors is optional


# ---- D8: init -------------------------------------------------------------------------------------

def test_init_keeps_an_existing_shots_file(tmp_path):
    clips = tmp_path / "clips"
    (clips / "beatcut").mkdir(parents=True)
    (clips / "beatcut" / "shots.json").write_text('{"keep": true}', encoding="utf-8")
    music = tmp_path / "song.mp3"
    music.write_bytes(b"ID3")
    assert beatcut.main(["init", str(clips), "--music", str(music)]) == 0
    assert (clips / "beatcut" / "shots.json").read_text(encoding="utf-8") == '{"keep": true}'
    assert (clips / "beatcut" / "project.json").exists()


def test_init_with_music_already_inside_the_project(tmp_path):
    clips = tmp_path / "clips"
    (clips / "beatcut").mkdir(parents=True)
    music = clips / "beatcut" / "song.mp3"
    music.write_bytes(b"ID3")
    assert beatcut.main(["init", str(clips), "--music", str(music)]) == 0
    assert music.read_bytes() == b"ID3"


# ---- D9 / D10: probe ------------------------------------------------------------------------------

def test_parse_probe_variable_frame_rate_uses_the_smaller_rate():
    data = {"streams": [{"width": 2, "height": 2, "r_frame_rate": "240/1", "avg_frame_rate": "30000/1001"}],
            "format": {"duration": "5"}}
    assert parse_probe(data)["fps"] == pytest.approx(29.97, abs=0.001)


@pytest.mark.parametrize("r, avg, want", [("0/0", "25/1", 25.0), ("25/1", "0/0", 25.0), ("30/1", "60/1", 30.0)])
def test_parse_probe_ignores_invalid_rates(r, avg, want):
    data = {"streams": [{"width": 2, "height": 2, "r_frame_rate": r, "avg_frame_rate": avg}],
            "format": {"duration": "5"}}
    assert parse_probe(data)["fps"] == want


def test_parse_probe_duration_is_the_shorter_of_stream_and_format():
    data = {"streams": [{"width": 2, "height": 2, "r_frame_rate": "25/1", "duration": "4.0"}],
            "format": {"duration": "10.0"}}
    assert parse_probe(data)["duration"] == 4.0
    data["streams"][0]["duration"] = "N/A"
    assert parse_probe(data)["duration"] == 10.0
    data = {"streams": [{"width": 2, "height": 2, "r_frame_rate": "25/1", "duration": "7"}], "format": {}}
    assert parse_probe(data)["duration"] == 7.0
    assert set(parse_probe(data)) == {"duration", "width", "height", "fps", "rotation", "color_space",
                                      "color_transfer"}


def test_probe_asks_for_the_extra_fields():
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        out = {"streams": [{"width": 2, "height": 2, "r_frame_rate": "25/1"}], "format": {"duration": "1"}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out), stderr="")

    media.probe("x.mp4", run=run)
    entries = seen[0][seen[0].index("-show_entries") + 1]
    assert "avg_frame_rate" in entries and "duration" in entries.split(":format=")[0]


# ---- D12: out_name -------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["a<b", "a>b", "a:b", 'a"b', "a|b", "a?b", "a*b", "a..b", "a\\b"])
def test_out_name_rejects_forbidden_characters(tmp_path, name):
    with pytest.raises(ConfigError, match="out_name"):
        parse_project({"music": "m.mp3", "out_name": name}, tmp_path)


def test_default_out_name_inside_a_beatcut_folder_uses_the_parent_name(tmp_path):
    root = tmp_path / "my_clips" / "beatcut"
    root.mkdir(parents=True)
    assert parse_project({"music": "m.mp3"}, root).out_name == "my_clips"
    other = tmp_path / "x" / "edit"
    other.mkdir(parents=True)
    assert parse_project({"music": "m.mp3"}, other).out_name == "edit"


def test_default_out_name_replaces_forbidden_characters_of_the_parent():
    from pipeline.config import _default_out_name

    assert _default_out_name(Path("/some/my:clips?/beatcut")) == "my_clips_"


# ---- D13: filter chain validation and assembly size ---------------------------------------------

def test_validate_chain_uses_the_trial_command():
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No such filter")

    assert grade.validate_chain("eq=saturation=0.9", run=run) is not None
    cmd, kw = seen[0]
    assert cmd == ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:d=0.04", "-vf",
                   "format=gbrp,eq=saturation=0.9,format=yuv420p", "-frames:v", "1", "-f", "null", "-"]
    assert kw["encoding"] == "utf-8" and kw["errors"] == "replace"

    def ok(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    assert grade.validate_chain("eq=saturation=0.9", run=ok) is None


@needs_ffmpeg
def test_validate_chain_with_real_ffmpeg():
    assert grade.validate_chain("eq=saturation=0.9") is None
    assert grade.validate_chain("nosuchfilter=1") is not None


def _segs(n, tin="cut"):
    shot = {"clip": "a.mp4", "in": 5.0, "out": 7.0, "beats": 4, "tin": tin, "tdur": 0.4}
    return render.build_plan(make_state([shot] * n), 30.0)


def test_assemble_fits():
    assert render.assemble_fits(_segs(5), 30.0, 45, "draft") is True
    assert render.assemble_fits(_segs(1500, "fade"), 30.0, 45, "draft") is False
    assert callable(render.assemble_command)


def test_assemble_refuses_an_oversized_command(tmp_path):
    p = project_in(tmp_path)
    p.work_dir.mkdir()
    segs = _segs(1500, "fade")
    with pytest.raises(render.RenderError, match="too many shots"):
        render.assemble(p, segs, [p.work_dir / f"seg_draft_{i:03d}.mp4" for i in range(len(segs))], "draft",
                        "cpu", 45)


# ---- D14: stale key ------------------------------------------------------------------------------

def test_render_segment_removes_the_old_key_before_rendering(tmp_path, monkeypatch):
    p = project_in(tmp_path, clips_dir=".")
    (p.root / "a.mp4").write_bytes(b"x")
    p.work_dir.mkdir()
    key = p.work_dir / "seg_draft_000.key"
    key.write_text("stale", encoding="utf-8")
    (p.work_dir / "seg_draft_000.mp4").write_bytes(b"old")

    def failing(cmd, cwd=None):
        assert not key.exists()
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(render, "_run", failing)
    seg = render.build_plan(make_state([{"clip": "a.mp4", "in": 1.0, "out": 3.0, "beats": 4}]), 30.0)[0]
    with pytest.raises(render.RenderError):
        render.render_segment(p, seg, "draft", "cpu", {"a.mp4": {"width": 2, "height": 2, "fps": 25.0}})
    assert not key.exists()


# ---- D15: BOM and short first segment ---------------------------------------------------------------

def test_json_files_with_a_byte_order_mark_are_read(tmp_path):
    (tmp_path / "project.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"music": "m.mp3"}).encode())
    assert load_project(tmp_path).music.name == "m.mp3"
    state = schema.empty_state()
    (tmp_path / "shots.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(state).encode())
    assert schema.load_shots(tmp_path / "shots.json", set(grade.BUILTIN))["version"] == 1
    schema.save_shots(tmp_path / "shots.json", state)            # an old BOM file is readable by save too


def test_opening_fade_never_exceeds_the_first_segment():
    base = dict(w=1920, h=1080, fill="cover", src_w=3840, src_h=2160, fps="30", fps_float=30.0, src_fps=30.0,
                speed=1.0, interp="auto", mode="draft", color="", first=True)
    short = render.segment_graph(**base, len_f=10)
    assert f"fade=t=in:st=0:d={10 / 30.0:.6f}" in short
    long = render.segment_graph(**base, len_f=100)
    assert f"fade=t=in:st=0:d={render.FADE_IN_FRAMES / 30.0:.6f}" in long


# ---- K2 / K3 ----------------------------------------------------------------------------------------

def test_missing_project_json_message(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_project(tmp_path)
    assert str(e.value) == f"no project.json in {tmp_path}"
    (tmp_path / "beatcut").mkdir()
    (tmp_path / "beatcut" / "project.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_project(tmp_path)
    assert str(e.value) == f"no project.json in {tmp_path} (did you mean {tmp_path / 'beatcut'}?)"


@needs_ffmpeg
def test_mix_reports_an_unwritable_output(tmp_path):
    p = project_in(tmp_path)
    p.music.write_bytes(b"x")
    blocker = p.out_dir / f"{p.out_name}_draft.mp4"
    blocker.mkdir(parents=True)                       # opening it for writing fails like a locked file
    with pytest.raises(mix.MixError, match=r"cannot write .*_draft\.mp4; close it in any player and try again"):
        mix.mix(p, {"tail": 1.0}, "draft", tmp_path / "v.mp4", 30, log=lambda *a: None)


# ---- slow end-to-end ------------------------------------------------------------------------------

@pytest.mark.slow
def test_check_warns_about_an_unreadable_unused_clip(tmp_path, capsys):
    from util import make_test_project

    root = make_test_project(tmp_path)
    (root.parent / "broken.mp4").write_bytes(b"not a video")
    assert beatcut.main(["check", str(root)]) == 0
    err = capsys.readouterr().err
    assert "warning: broken.mp4: cannot be read (" in err


@pytest.mark.slow
def test_check_rejects_an_invalid_custom_profile(tmp_path, capsys):
    from util import make_test_project

    shots = [{"clip": "wide30.mp4", "in": 1.0, "out": 3.0, "beats": 4, "prof": "bad"}]
    root = make_test_project(tmp_path, shots=shots)
    cfg = json.loads((root / "project.json").read_text(encoding="utf-8"))
    cfg["profiles"] = {"bad": "nosuchfilter=1", "unused": "alsonotafilter=1"}
    (root / "project.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert beatcut.main(["check", str(root)]) == 1
    err = capsys.readouterr().err
    assert "error: profile bad: ffmpeg rejects the filter chain" in err and "unused" not in err


@pytest.mark.slow
def test_check_rejects_an_edit_too_long_to_assemble(tmp_path, capsys, monkeypatch):
    from util import make_test_project

    root = make_test_project(tmp_path)
    monkeypatch.setattr(render, "assemble_fits", lambda *a, **k: False)
    assert beatcut.main(["check", str(root)]) == 1
    assert "error: too many shots to assemble in one pass (4); split the edit" in capsys.readouterr().err


@pytest.mark.slow
def test_draft_prints_the_output_path_once(tmp_path, capsys):
    from util import make_test_project

    root = make_test_project(tmp_path)
    assert beatcut.main(["draft", str(root)]) == 0
    assert capsys.readouterr().out.count("clips_draft.mp4") == 1


@pytest.mark.slow
def test_short_video_stream_gives_the_video_duration(tmp_path):
    from util import run_ok

    src = tmp_path / "short.mp4"
    run_ok(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=64x64:r=25:d=1",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=3", "-c:v", "libx264", "-c:a", "aac", str(src)])
    assert media.probe(src)["duration"] < 1.5


# ---- security review follow-ups: H1 (clip name characters), H2 (render containment), H3 (absolute paths) ----

def _shot_errors(clip):
    state = make_state([{"clip": "a.mp4", "in": 1.0, "out": 2.0, "beats": 4}])
    state["shots"][0]["clip"] = clip
    return schema.validate_shots(state, {"neutral"})


@pytest.mark.parametrize("clip", ["D:x.mp4", "a:b.mp4", "x.mp4:stream", "a\x00.mp4", "a\n.mp4", "a\x1f.mp4",
                                  "a\x7f.mp4"])
def test_clip_names_with_colons_or_control_characters_are_refused(clip):
    errors = _shot_errors(clip)
    assert any(e.startswith("shots[0].clip: ") for e in errors), errors


def test_colons_and_control_characters_stay_legal_in_bad_and_clipnotes_keys():
    state = make_state([{"clip": "a.mp4", "in": 1.0, "out": 2.0, "beats": 4}])
    state["bad"] = {"D:x.mp4": [{"a": 0.0, "b": 1.0}], "q\x01.mp4": []}
    state["clipnotes"] = {"D:x.mp4": "note", "q\x01.mp4": "note"}
    assert schema.validate_shots(state, {"neutral"}) == []


def test_render_segment_refuses_a_clip_outside_the_clips_folder(tmp_path, monkeypatch):
    p = project_in(tmp_path, clips_dir="clips")
    (p.root / "clips").mkdir()
    (p.root / "outside.mp4").write_bytes(b"x")
    p.work_dir.mkdir()
    monkeypatch.setattr(render, "_run", lambda *a, **k: pytest.fail("ffmpeg must not run"))
    for clip in ("../outside.mp4", "sub/../../outside.mp4"):
        seg = render.build_plan(make_state([{"clip": clip, "in": 1.0, "out": 3.0, "beats": 4}]), 30.0)[0]
        with pytest.raises(render.RenderError, match=r"shot 1 \(.*\): clip is outside the clips folder"):
            render.render_segment(p, seg, "draft", "cpu", {clip: {"width": 640, "height": 360, "fps": 30.0}})


def test_probe_and_probe_duration_pass_an_absolute_path():
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        out = {"streams": [{"width": 2, "height": 2, "r_frame_rate": "25/1"}], "format": {"duration": "1"}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out), stderr="")

    media.probe("-x.mp4", run=run)
    media.probe_duration("concat:a.mp4|b.mp4", run=run)
    assert seen[0][-1] == os.path.abspath("-x.mp4")
    assert seen[1][-1] == os.path.abspath("concat:a.mp4|b.mp4")
    assert all(os.path.isabs(c[-1]) for c in seen)


def test_sheet_and_mix_pass_absolute_input_paths(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(media, "require_tools", lambda: None)
    monkeypatch.setattr(media, "probe_duration", lambda p, run=None: 10.0)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)
    sheet.make_sheet("-v.mp4", tmp_path / "s.jpg")
    cmd = seen[0]
    assert cmd[cmd.index("-i") + 1] == os.path.abspath("-v.mp4")
    p = project_in(tmp_path)
    p.music.write_bytes(b"x")
    mix.mix(p, {"tail": 1.0}, "draft", "-v.mp4", 30, log=lambda *a: None)
    cmd = seen[1]
    inputs = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-i"]
    assert inputs[0] == os.path.abspath("-v.mp4") and all(os.path.isabs(i) for i in inputs)
