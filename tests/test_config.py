import json

import pytest

from pipeline.config import ConfigError, default_project, load_project, parse_project


def test_defaults(tmp_path):
    root = tmp_path.resolve()
    p = parse_project({"music": "song.mp3"}, root)
    assert p.root == root
    assert p.clips_dir == root.parent
    assert p.extensions == (".mp4", ".mov", ".mxf")
    assert p.exclude_prefix == ()
    assert p.music == root / "song.mp3"
    assert p.fps == "30000/1001"
    assert p.draft == (1920, 1080) and p.final == (3840, 2160)
    assert (p.fill, p.encoder, p.loudness, p.profiles) == ("cover", "auto", -15.0, {})
    assert p.out_name == root.name
    assert p.shots_path == root / "shots.json"
    assert p.proxy_dir == root / "proxy"
    assert p.work_dir == root / "work"
    assert p.out_dir == root / "out"


def test_fps_float_and_size(tmp_path):
    p = parse_project({"music": "m.mp3", "fps": "25"}, tmp_path)
    assert p.fps_float == 25.0
    assert parse_project({"music": "m.mp3"}, tmp_path).fps_float == pytest.approx(29.97, abs=0.001)
    assert p.size("draft") == (1920, 1080)
    assert p.size("final") == (3840, 2160)


def test_extensions_are_lowercased(tmp_path):
    p = parse_project({"music": "m.mp3", "extensions": [".MP4", ".Mov"]}, tmp_path)
    assert p.extensions == (".mp4", ".mov")


def test_custom_values(tmp_path):
    p = parse_project({"music": "m.wav", "clips_dir": "clips", "fill": "blur", "encoder": "cpu",
                       "draft": {"width": 1080, "height": 1920}, "loudness": -14,
                       "profiles": {"teal": "eq=saturation=0.9"}, "out_name": "my_edit",
                       "exclude_prefix": ["out_"]}, tmp_path)
    assert p.clips_dir == (tmp_path / "clips").resolve()
    assert p.draft == (1080, 1920)
    assert p.profiles == {"teal": "eq=saturation=0.9"}
    assert p.out_name == "my_edit"
    assert p.exclude_prefix == ("out_",)


def test_music_is_required(tmp_path):
    with pytest.raises(ConfigError, match="music"):
        parse_project({}, tmp_path)


@pytest.mark.parametrize("patch, needle", [
    ({"music": ""}, "music"),
    ({"music": "../m.mp3"}, "music"),
    ({"music": "C:/x/m.mp3"}, "music"),
    ({"fps": "abc"}, "fps"),
    ({"fps": "30/0"}, "fps"),
    ({"draft": {"width": 1921, "height": 1080}}, "draft"),
    ({"final": {"width": 0, "height": 2160}}, "final"),
    ({"final": {"width": 3840}}, "final"),
    ({"fill": "stretch"}, "fill"),
    ({"encoder": "gpu"}, "encoder"),
    ({"loudness": 0}, "loudness"),
    ({"profiles": {"x": "eq=1;movie=a"}}, "profiles"),
    ({"profiles": {"x": "[in]eq=1"}}, "profiles"),
    ({"profiles": {"Bad Name": "eq=1"}}, "profiles"),
    ({"profiles": {"x": ""}}, "profiles"),
    ({"extensions": ["mp4"]}, "extensions"),
    ({"out_name": "a/b"}, "out_name"),
    ({"typo_key": 1}, "typo_key"),
])
def test_invalid_values(tmp_path, patch, needle):
    with pytest.raises(ConfigError) as e:
        parse_project({"music": "m.mp3", **patch}, tmp_path)
    assert needle in str(e.value)


def test_load_project(tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({"music": "m.mp3"}), encoding="utf-8")
    assert load_project(tmp_path).music == tmp_path.resolve() / "m.mp3"


def test_load_project_errors(tmp_path):
    with pytest.raises(ConfigError, match="project.json"):
        load_project(tmp_path)
    (tmp_path / "project.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="project.json"):
        load_project(tmp_path)
    (tmp_path / "project.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ConfigError, match="project.json"):
        load_project(tmp_path)


def test_default_project_round_trips(tmp_path):
    d = default_project("song.mp3")
    assert d["music"] == "song.mp3" and d["fill"] == "cover" and d["encoder"] == "auto"
    assert parse_project(d, tmp_path).fps == "30000/1001"
