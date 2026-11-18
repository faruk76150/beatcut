import pytest

from pipeline.plan import beat_frame, build_plan, check_plan, total_frames
from util import make_state

FPS = 30.0


def two(second=None, first=None):
    a = {"clip": "a.mp4", "in": 5.0, "out": 7.0, "beats": 4}
    b = {"clip": "b.mp4", "in": 5.0, "out": 7.0, "beats": 4}
    a.update(first or {})
    b.update(second or {})
    return make_state([a, b])


def test_speed_formula():
    segs = build_plan(make_state([{"clip": "a.mp4", "in": 2.0, "out": 6.0, "beats": 4}]), FPS)
    assert segs[0].speed == pytest.approx(2.0)      # 4 s of source over 4 beats x 0.5 s


def test_cut_mode_runs_at_normal_speed():
    segs = build_plan(make_state([{"clip": "a.mp4", "in": 2.0, "out": 9.0, "beats": 4, "fit": "cut"}]), FPS)
    assert segs[0].speed == 1.0


def test_speed_close_to_one_snaps():
    segs = build_plan(make_state([{"clip": "a.mp4", "in": 0.0, "out": 2.01, "beats": 4}]), FPS)
    assert segs[0].speed == 1.0


def test_frame_positions_with_cuts():
    a, b = build_plan(two(), FPS)
    assert (a.start_f, a.end_f) == (0, 90)          # t0 1.0 s + 4 x 0.5 s = 3.0 s -> frame 90
    assert (b.start_f, b.end_f) == (90, 195)        # beat 8 at 5.0 s -> 150, plus 1.5 s tail
    assert (a.len_f, b.len_f) == (90, 105)
    assert (a.tin, a.tout, b.tin, b.tout) == ("none", "cut", "cut", "none")
    assert a.src_start == 5.0 and b.src_start == 5.0
    assert a.src_len == pytest.approx(3.0)          # reads 1 s past `out` to cover the intro
    assert total_frames([a, b]) == 195


def test_fade_adds_handles_on_both_sides():
    a, b = build_plan(two({"tin": "fade", "tdur": 0.4}), FPS)
    assert (b.tin, b.din_f, a.tout, a.dout_f) == ("fade", 12, "fade", 12)
    assert (a.win_s_f, a.win_e_f) == (0, 96)
    assert (b.win_s_f, b.win_e_f) == (84, 195)
    assert (a.len_f, b.len_f) == (96, 111)
    assert b.src_start == pytest.approx(5.0 - 6 / 30)
    assert a.len_f + b.len_f - b.din_f == total_frames([a, b])


def test_odd_fade_is_rounded_down_to_even():
    a, b = build_plan(two({"tin": "fadewhite", "tdur": 0.5}), FPS)     # 15 frames -> 14
    assert (b.tin, b.din_f) == ("fadewhite", 14)


def test_tiny_fade_becomes_a_cut():
    a, b = build_plan(two({"tin": "fade", "tdur": 0.03}), FPS)
    assert (b.tin, b.din_f, a.tout, a.dout_f) == ("cut", 0, "cut", 0)


def test_interp_defaults_to_auto():
    a, b = build_plan(two({"interp": "blend"}), FPS)
    assert (a.interp, b.interp) == ("auto", "blend")


def test_no_drift_over_many_beats():
    fps = 30000 / 1001
    st = make_state([{"clip": "a.mp4", "in": 0, "out": 1, "beats": 1} for _ in range(200)],
                    period=0.5108, t0=0.337, tail=0)
    segs = build_plan(st, fps)
    for s in segs[1:]:
        assert s.start_f == beat_frame(st, s.k_start, fps)
    assert sum(s.len_f for s in segs) == total_frames(segs) == round((0.337 + 0.5108 * 200) * fps)


def durations(**over):
    d = {"a.mp4": 20.0, "b.mp4": 20.0}
    d.update(over)
    return d


def test_check_accepts_a_valid_plan():
    st = two()
    assert check_plan(build_plan(st, FPS), durations(), st["bad"], 60.0, FPS) == ([], [])


def test_check_empty_plan():
    errors, _ = check_plan([], {}, {}, None, FPS)
    assert errors == ["no shots"]


def test_check_missing_clip():
    st = two()
    errors, _ = check_plan(build_plan(st, FPS), {"a.mp4": 20.0}, {}, None, FPS)
    assert len(errors) == 1 and "shot 2" in errors[0] and "b.mp4" in errors[0]


def test_check_source_overrun():
    st = two()
    errors, _ = check_plan(build_plan(st, FPS), durations(**{"a.mp4": 7.5}), {}, None, FPS)
    assert len(errors) == 1 and "a.mp4" in errors[0] and "7.5" in errors[0]


def test_check_handle_before_clip_start():
    st = two({"in": 0.0, "out": 2.0, "tin": "fade", "tdur": 0.4})
    errors, _ = check_plan(build_plan(st, FPS), durations(), {}, None, FPS)
    assert len(errors) == 1 and "shot 2" in errors[0]


def test_check_shot_shorter_than_its_transitions():
    st = make_state([{"clip": "a.mp4", "in": 5, "out": 7, "beats": 4},
                     {"clip": "b.mp4", "in": 5, "out": 5.5, "beats": 1, "tin": "fade", "tdur": 1.0},
                     {"clip": "a.mp4", "in": 9, "out": 11, "beats": 4, "tin": "fade", "tdur": 1.0}])
    errors, _ = check_plan(build_plan(st, FPS), durations(), {}, None, FPS)
    assert any("shot 2" in e and "transition" in e for e in errors)


def test_check_speed_out_of_range():
    st = make_state([{"clip": "a.mp4", "in": 0, "out": 19, "beats": 1}], tail=0)
    errors, _ = check_plan(build_plan(st, FPS), durations(**{"a.mp4": 90.0}), {}, None, FPS)
    assert any("speed" in e for e in errors)


def test_check_warns_about_bad_ranges():
    st = two()
    errors, warnings = check_plan(build_plan(st, FPS), durations(), {"a.mp4": [{"a": 6.0, "b": 6.5}]}, None, FPS)
    assert errors == [] and len(warnings) == 1 and "shot 1" in warnings[0]


def test_check_music_too_short():
    st = two()
    errors, _ = check_plan(build_plan(st, FPS), durations(), {}, 5.0, FPS)
    assert len(errors) == 1 and "music" in errors[0]
