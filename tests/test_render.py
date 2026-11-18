import json
import subprocess
import sys

import pytest

from pipeline.plan import build_plan
from pipeline.render import assemble_graph, fit_graph, retime_filters, segment_graph
from util import make_state

FPS = "30000/1001"
FPSF = 30000 / 1001


def test_fit_same_aspect_is_a_plain_scale():
    g = fit_graph("[r]", "[f]", 1920, 1080, "blur", 3840, 2160)
    assert g == "[r]scale=1920:1080:flags=bicubic,setsar=1[f]"


def test_fit_cover_crops():
    g = fit_graph("[r]", "[f]", 1920, 1080, "cover", 2160, 3840)
    assert "force_original_aspect_ratio=increase" in g and "crop=1920:1080" in g
    assert g.startswith("[r]") and g.endswith("[f]") and "overlay" not in g


def test_fit_blur_overlays_on_a_blurred_copy():
    g = fit_graph("[r]", "[f]", 1920, 1080, "blur", 2160, 3840)
    assert "split" in g and "gblur" in g and "overlay=(W-w)/2:(H-h)/2" in g
    assert "force_original_aspect_ratio=decrease" in g and g.endswith("[f]")


def test_retime_normal_speed():
    assert retime_filters(1.0, "auto", FPS, FPSF, 29.97, "final") == ("setpts=PTS-STARTPTS", f"fps={FPS}")


def test_retime_speed_up_never_interpolates():
    pre, post = retime_filters(2.0, "oflow", FPS, FPSF, 29.97, "final")
    assert pre == "setpts=(PTS-STARTPTS)/2.000000" and post == f"fps={FPS}"


def test_retime_auto_skips_interpolation_when_the_source_has_enough_frames():
    assert retime_filters(0.5, "auto", FPS, FPSF, 59.94, "final")[1] == f"fps={FPS}"


def test_retime_auto_interpolates_only_when_needed():
    assert "mi_mode=blend" in retime_filters(0.5, "auto", FPS, FPSF, 29.97, "draft")[1]
    assert "mi_mode=mci" in retime_filters(0.5, "auto", FPS, FPSF, 29.97, "final")[1]


def test_retime_explicit_choice_is_respected():
    assert "mi_mode=mci" in retime_filters(0.5, "oflow", FPS, FPSF, 59.94, "draft")[1]
    assert "mi_mode=blend" in retime_filters(0.5, "blend", FPS, FPSF, 29.97, "final")[1]
    assert retime_filters(0.5, "none", FPS, FPSF, 29.97, "final")[1] == f"fps={FPS}"


def seg_graph(**over):
    args = dict(w=1920, h=1080, fill="cover", src_w=3840, src_h=2160, fps=FPS, fps_float=FPSF, src_fps=29.97,
                speed=1.0, interp="auto", mode="draft", color="", first=False)
    args.update(over)
    return segment_graph(**args)


def test_segment_graph_shape():
    g = seg_graph()
    assert g.startswith("[0:v]setpts=PTS-STARTPTS[r];[r]scale=1920:1080")
    assert g.endswith("tpad=stop_mode=clone:stop_duration=2,format=yuv420p[v]")
    assert "fade=" not in g and "gbrp" not in g


def test_segment_graph_color_and_first_fade():
    g = seg_graph(color="eq=saturation=0.9", first=True)
    assert "format=gbrp,eq=saturation=0.9,format=yuv420p" in g
    assert "fade=t=in:st=0:d=" in g
    assert g.index("eq=saturation") < g.index("fade=t=in") < g.index("tpad=")


def plan(shots, **kw):
    return build_plan(make_state(shots, **kw), 30.0)


A = {"clip": "a.mp4", "in": 5.0, "out": 7.0, "beats": 4}


def test_assemble_single_segment():
    g = assemble_graph(plan([A]), 30.0, 45)
    assert g == "[0:v]fade=t=out:st=3.000000:d=1.500000,format=yuv420p[vout]"


def test_assemble_cuts_use_concat():
    g = assemble_graph(plan([A, A, A]), 30.0, 0)
    assert g == "[0:v][1:v][2:v]concat=n=3:v=1:a=0[cat];[cat]format=yuv420p[vout]"


def test_assemble_fade_uses_xfade_with_half_frame_guard():
    segs = plan([A, {**A, "tin": "fade", "tdur": 0.4}])
    g = assemble_graph(segs, 30.0, 0)
    # first segment is 96 frames, the fade 12: offset = (96 - 12 - 0.5) / 30
    assert g == ("[0:v][1:v]xfade=transition=fade:duration=0.400000:offset=2.783333[x1];"
                 "[x1]format=yuv420p[vout]")


def test_assemble_mixes_runs_of_fades_and_cuts():
    segs = plan([A, {**A, "tin": "fade", "tdur": 0.4}, A, {**A, "tin": "fadewhite", "tdur": 0.4}])
    g = assemble_graph(segs, 30.0, 0)
    assert g.count("xfade=") == 2 and "transition=fadewhite" in g
    assert "[x1][x3]concat=n=2:v=1:a=0[cat]" in g


@pytest.mark.slow
@pytest.mark.parametrize("fill", ["cover", "blur"])
def test_render_is_frame_exact(tmp_path, fill):
    from pipeline.config import load_project
    from pipeline.render import render
    from pipeline.schema import load_shots
    from pipeline import grade
    from util import count_frames, make_test_project

    root = make_test_project(tmp_path, fill=fill)
    project = load_project(root)
    state = load_shots(project.shots_path, grade.profile_names(project.profiles))
    out = render(project, state, "draft", log=lambda *a: None)
    assert out == project.work_dir / "draft_video.mp4" and out.exists()
    info = count_frames(out)
    expected = round((0.25 + 0.5 * 14) * 30000 / 1001) + round(1.0 * 30000 / 1001)
    assert int(info["nb_read_frames"]) == expected
    assert (info["width"], info["height"]) == (854, 480)


@pytest.mark.slow
def test_render_reuses_cached_segments(tmp_path):
    from pipeline.config import load_project
    from pipeline.render import render
    from pipeline.schema import load_shots
    from pipeline import grade
    from util import make_test_project

    root = make_test_project(tmp_path)
    project = load_project(root)
    state = load_shots(project.shots_path, grade.profile_names(project.profiles))
    render(project, state, "draft", log=lambda *a: None)
    seg = project.work_dir / "seg_draft_000.mp4"
    stamp = seg.stat().st_mtime_ns
    render(project, state, "draft", log=lambda *a: None)
    assert seg.stat().st_mtime_ns == stamp


def test_render_logs_completed_segment_count_in_order(tmp_path, monkeypatch):
    """Segments finish out of order under parallel rendering; the log must still count 1/n, 2/n, ..."""
    import time
    from types import SimpleNamespace

    from pipeline import encoders, media, render as render_mod

    n = 5
    state = make_state([{"clip": "a.mp4", "in": 0.0, "out": 1.0, "beats": 2} for _ in range(n)])
    project = SimpleNamespace(fps_float=FPSF, work_dir=tmp_path / "work", encoder="cpu")
    monkeypatch.setattr(media, "require_tools", lambda: None)
    monkeypatch.setattr(media, "clip_meta", lambda p: {})
    monkeypatch.setattr(encoders, "detect", lambda e: "cpu")
    monkeypatch.setattr(render_mod, "assemble", lambda *a: tmp_path / "video.mp4")
    delays = {0: 0.3, 1: 0.0, 2: 0.2, 3: 0.0, 4: 0.1}

    def fake_segment(project, seg, mode, kind, meta):
        time.sleep(delays[seg.index])
        return tmp_path / f"seg{seg.index}.mp4"

    monkeypatch.setattr(render_mod, "render_segment", fake_segment)
    lines = []
    render_mod.render(project, state, "draft", log=lines.append)
    assert [l for l in lines if l.startswith("segment")] == [f"segment {i}/{n}" for i in range(1, n + 1)]
