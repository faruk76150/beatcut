"""Colour handling: BT.709 output tags, YUV matrix conversion of non-BT.709 sources, HDR warnings."""
import json
import subprocess

import pytest

import beatcut
from pipeline import encoders, grade, media, render
from pipeline.config import parse_project
from pipeline.media import clip_meta, hdr_transfer, parse_probe, source_matrix
from pipeline.render import OUTPUT_COLOR_ARGS, matrix_filter, segment_graph
from util import make_state

FPS = "30000/1001"
FPSF = 30000 / 1001
COLOUR_TAGS_709 = ("bt709", "bt709", "bt709")
COLOUR_TAGS_2020 = ("bt2020nc", "bt2020", "bt709")


# ---- probing --------------------------------------------------------------------------------------

def test_parse_probe_reads_the_colour_tags():
    data = {"streams": [{"width": 2, "height": 2, "r_frame_rate": "25/1", "color_space": "bt2020nc",
                         "color_transfer": "smpte2084"}], "format": {"duration": "5"}}
    m = parse_probe(data)
    assert (m["color_space"], m["color_transfer"]) == ("bt2020nc", "smpte2084")


@pytest.mark.parametrize("value", [None, "unknown", "reserved", 7])
def test_parse_probe_colour_tags_default_to_empty_text(value):
    stream = {"width": 2, "height": 2, "r_frame_rate": "25/1"}
    if value is not None:
        stream["color_space"] = stream["color_transfer"] = value
    m = parse_probe({"streams": [stream], "format": {"duration": "5"}})
    assert (m["color_space"], m["color_transfer"]) == ("", "")


def test_probe_asks_for_the_colour_tags():
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        out = {"streams": [{"width": 2, "height": 2, "r_frame_rate": "25/1", "color_space": "bt709"}],
               "format": {"duration": "1"}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out), stderr="")

    assert media.probe("x.mp4", run=run)["color_space"] == "bt709"
    entries = seen[0][seen[0].index("-show_entries") + 1]
    assert "color_space" in entries and "color_transfer" in entries


def test_clip_meta_reprobes_entries_cached_before_the_colour_fields(tmp_path):
    root = tmp_path / "beatcut"
    root.mkdir()
    p = parse_project({"music": "m.mp3", "clips_dir": ".."}, root)
    (tmp_path / "a.mp4").write_bytes(b"x")
    p.work_dir.mkdir(parents=True)
    old = {"a.mp4": {"duration": 5.0, "width": 2, "height": 2, "fps": 25.0, "rotation": 0,
                     "mtime": (tmp_path / "a.mp4").stat().st_mtime_ns}}
    (p.work_dir / "meta.json").write_text(json.dumps(old), encoding="utf-8")
    calls = []

    def probe(clip):
        calls.append(clip.name)
        return {"duration": 5.0, "width": 2, "height": 2, "fps": 25.0, "rotation": 0,
                "color_space": "bt2020nc", "color_transfer": "bt709"}

    assert clip_meta(p, probe_fn=probe)["a.mp4"]["color_space"] == "bt2020nc"
    assert clip_meta(p, probe_fn=probe)["a.mp4"]["color_space"] == "bt2020nc"
    assert calls == ["a.mp4"]
    assert "probe" not in clip_meta(p, probe_fn=probe)["a.mp4"]


# ---- matrix mapping and HDR detection -------------------------------------------------------------

@pytest.mark.parametrize("space, want", [("bt2020nc", "bt2020"), ("bt2020c", "bt2020"),
                                         ("smpte170m", "bt601"), ("bt470bg", "bt601"),
                                         ("bt709", ""), ("", ""), ("fcc", ""), (None, "")])
def test_source_matrix(space, want):
    assert source_matrix({"color_space": space}) == want


def test_source_matrix_of_an_old_cache_entry_is_empty():
    assert source_matrix({"width": 2}) == "" and source_matrix(None) == ""


@pytest.mark.parametrize("transfer, want", [("smpte2084", "smpte2084"), ("arib-std-b67", "arib-std-b67"),
                                            ("bt709", ""), ("", ""), (None, "")])
def test_hdr_transfer(transfer, want):
    assert hdr_transfer({"color_transfer": transfer}) == want


# ---- filter graph and commands ---------------------------------------------------------------------

def seg_graph(**over):
    args = dict(w=1920, h=1080, fill="cover", src_w=3840, src_h=2160, fps=FPS, fps_float=FPSF, src_fps=29.97,
                speed=1.0, interp="auto", mode="draft", color="", first=False)
    args.update(over)
    return segment_graph(**args)


def test_segment_graph_without_a_matrix_is_unchanged():
    assert seg_graph(src_matrix="") == seg_graph()
    assert "color_matrix" not in seg_graph()


@pytest.mark.parametrize("matrix", ["bt2020", "bt601"])
def test_segment_graph_converts_the_matrix_before_anything_else(matrix):
    g = seg_graph(src_matrix=matrix, color="eq=saturation=0.9")
    assert g.startswith(f"[0:v]{matrix_filter(matrix)},setpts=PTS-STARTPTS[r];")
    assert f"in_color_matrix={matrix}:out_color_matrix=bt709" in g
    assert g.replace(matrix_filter(matrix) + ",", "") == seg_graph(color="eq=saturation=0.9")


def test_matrix_filter_keeps_the_frame_size():
    f = matrix_filter("bt2020")
    assert f.startswith("scale=in_color_matrix=bt2020:out_color_matrix=bt709")
    assert "w=" not in f and "h=" not in f and ":-" not in f


def test_assemble_command_tags_the_output_as_bt709():
    segs = render.build_plan(make_state([{"clip": "a.mp4", "in": 1.0, "out": 3.0, "beats": 4}]), 30.0)
    for kind in ("cpu", "nvenc"):
        cmd = render.assemble_command(["seg_draft_000.mp4"], segs, FPS, FPSF, 0, "draft", kind)
        text = " ".join(cmd)
        assert " ".join(OUTPUT_COLOR_ARGS) in text and text.index("-color_trc") < text.index("draft_video.mp4")
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert graph.endswith(f"format=yuv420p,{render.TAG_FILTER}[vout]")
    assert OUTPUT_COLOR_ARGS == ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]


def test_tag_bt709_appends_to_the_output_chain():
    assert render.tag_bt709("[0:v]null[v]", "[v]") == f"[0:v]null,{render.TAG_FILTER}[v]"
    with pytest.raises(ValueError):
        render.tag_bt709("[0:v]null[x]", "[v]")


def _render_one(tmp_path, monkeypatch, info):
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    p = parse_project({"music": "m.wav", "clips_dir": "."}, root)
    (p.root / "a.mp4").write_bytes(b"x")
    p.work_dir.mkdir(exist_ok=True)
    seen = []

    def fake_run(cmd, cwd=None):
        seen.append(cmd)
        from pathlib import Path
        Path(cmd[-1]).write_bytes(b"v")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(render, "_run", fake_run)
    seg = render.build_plan(make_state([{"clip": "a.mp4", "in": 1.0, "out": 3.0, "beats": 4}]), 30.0)[0]
    render.render_segment(p, seg, "draft", "cpu", {"a.mp4": info})
    key = (p.work_dir / "seg_draft_000.key").read_text(encoding="utf-8")
    return (seen[0] if seen else None), key


def test_render_segment_converts_and_tags(tmp_path, monkeypatch):
    base = {"width": 640, "height": 360, "fps": 30.0}
    cmd, key709 = _render_one(tmp_path, monkeypatch, {**base, "color_space": "bt709"})
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "color_matrix" not in graph
    assert " ".join(OUTPUT_COLOR_ARGS) in " ".join(cmd)
    cmd, key2020 = _render_one(tmp_path, monkeypatch, {**base, "color_space": "bt2020nc"})
    assert cmd is not None, "a changed source matrix must invalidate the cached segment"
    assert matrix_filter("bt2020") in cmd[cmd.index("-filter_complex") + 1]
    assert key709 != key2020 and json.loads(key2020)[0] == render.CACHE_VERSION == 3


# ---- HDR warning -----------------------------------------------------------------------------------

def test_hdr_warning_only_for_clips_used_in_the_edit(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project.json").write_text(json.dumps({"music": "m.wav", "clips_dir": "."}), encoding="utf-8")
    (root / "m.wav").write_bytes(b"x")
    state = make_state([{"clip": "pq.mp4", "in": 1.0, "out": 3.0, "beats": 4},
                        {"clip": "sdr.mp4", "in": 1.0, "out": 3.0, "beats": 4}], t0=0.0)
    (root / "shots.json").write_text(json.dumps(state), encoding="utf-8")
    base = {"duration": 10.0, "width": 640, "height": 360, "fps": 30.0, "rotation": 0, "color_space": "bt2020nc"}
    meta = {"pq.mp4": {**base, "color_transfer": "smpte2084"},
            "sdr.mp4": {**base, "color_transfer": "bt709"},
            "hlg_unused.mp4": {**base, "color_transfer": "arib-std-b67"}}
    monkeypatch.setattr(media, "require_tools", lambda: None)
    monkeypatch.setattr(media, "clip_meta", lambda project, errors=None: meta)
    monkeypatch.setattr(media, "probe_duration", lambda path: 60.0)
    monkeypatch.setattr(grade, "validate_chain", lambda chain: None)
    assert beatcut.main(["check", str(root)]) == 0
    err = capsys.readouterr().err
    assert ("warning: pq.mp4: HDR source (smpte2084); colours will look washed out because beatcut "
            "does not tone-map yet") in err
    assert "sdr.mp4" not in err and "hlg_unused.mp4" not in err
    assert err.count("HDR source") == 1


# ---- slow end-to-end -------------------------------------------------------------------------------

def _single_shot_render(tmp_path, clip_maker, *, encoder="cpu", mode="draft", shots=None):
    from pipeline.config import load_project
    from pipeline.schema import load_shots
    from util import make_test_project

    root = make_test_project(tmp_path, size=(320, 180),
                             shots=shots or [{"clip": "c.mp4", "in": 0.5, "out": 3.5, "beats": 6}])
    clip_maker(root.parent)
    cfg = json.loads((root / "project.json").read_text(encoding="utf-8"))
    cfg["encoder"] = encoder
    (root / "project.json").write_text(json.dumps(cfg), encoding="utf-8")
    project = load_project(root)
    state = load_shots(project.shots_path, grade.profile_names(project.profiles))
    return root, render.render(project, state, mode, log=lambda *a: None)


FRAME = 50          # after the 30-frame opening fade, before the closing fade


@pytest.mark.slow
def test_bt2020_matrix_source_matches_the_bt709_source(tmp_path, monkeypatch):
    from util import frame_means, make_color_clip

    _, out709 = _single_shot_render(tmp_path / "a", lambda d: make_color_clip(d / "c.mp4", "bt709",
                                                                                COLOUR_TAGS_709))
    _, out2020 = _single_shot_render(tmp_path / "b", lambda d: make_color_clip(d / "c.mp4", "bt2020",
                                                                                 COLOUR_TAGS_2020))
    ref, got = frame_means(out709, FRAME), frame_means(out2020, FRAME)
    diff = max(abs(a - b) for a, b in zip(ref, got))
    print(f"bt709 source {ref}, bt2020 source {got}, max channel difference {diff:.2f}")
    assert diff <= 4, (ref, got)

    # Without the conversion the same clip is clearly off: proves the comparison catches the bug.
    monkeypatch.setattr(media, "source_matrix", lambda meta: "")
    _, raw = _single_shot_render(tmp_path / "c", lambda d: make_color_clip(d / "c.mp4", "bt2020",
                                                                             COLOUR_TAGS_2020))
    uncorrected = frame_means(raw, FRAME)
    raw_diff = max(abs(a - b) for a, b in zip(ref, uncorrected))
    print(f"uncorrected bt2020 source {uncorrected}, max channel difference {raw_diff:.2f}")
    assert raw_diff > 6, (ref, uncorrected)


def _nvenc_available():
    try:
        return encoders.detect("auto") == "nvenc"
    except Exception:
        return False


@pytest.mark.slow
@pytest.mark.parametrize("encoder, mode", [("cpu", "draft"), ("cpu", "final"),
                                           ("nvenc", "draft"), ("nvenc", "final")])
def test_output_is_tagged_bt709(tmp_path, encoder, mode):
    from util import make_color_clip, probe_color

    if encoder == "nvenc" and not _nvenc_available():
        pytest.skip("NVENC is not available on this machine")
    shots = [{"clip": "c.mp4", "in": 0.5, "out": 2.5, "beats": 4},
             {"clip": "wide30.mp4", "in": 1.0, "out": 2.0, "beats": 2, "tin": "fade", "tdur": 0.4}]
    root, video = _single_shot_render(
        tmp_path, lambda d: make_color_clip(d / "c.mp4", "bt2020", COLOUR_TAGS_2020),
        encoder=encoder, mode=mode, shots=shots)
    work = root / "work"
    assert probe_color(work / f"seg_{mode}_000.mp4") == ("bt709", "bt709", "bt709")
    assert probe_color(work / f"seg_{mode}_001.mp4") == ("bt709", "bt709", "bt709")
    assert probe_color(video) == ("bt709", "bt709", "bt709")


@pytest.mark.slow
def test_hdr_clip_warns_and_the_draft_is_tagged_bt709(tmp_path, capsys):
    from util import colour_tags, make_test_project, probe_color, run_ok

    shots = [{"clip": "hdr.mp4", "in": 0.5, "out": 2.5, "beats": 4},
             {"clip": "wide30.mp4", "in": 1.0, "out": 2.0, "beats": 2}]
    root = make_test_project(tmp_path, size=(320, 180), shots=shots)
    hdr = root.parent / "hdr.mp4"
    run_ok(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "testsrc2=s=320x180:r=30000/1001:d=4," + colour_tags("bt2020nc", "bt2020", "smpte2084"),
            "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
            "-pix_fmt", "yuv420p10le", str(hdr)])
    assert probe_color(hdr) == ("bt2020nc", "bt2020", "smpte2084")
    assert beatcut.main(["check", str(root)]) == 0
    assert ("warning: hdr.mp4: HDR source (smpte2084); colours will look washed out because beatcut "
            "does not tone-map yet") in capsys.readouterr().err
    assert beatcut.main(["draft", str(root)]) == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert probe_color(out) == ("bt709", "bt709", "bt709")
