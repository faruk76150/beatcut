import json

import pytest

from pipeline.config import parse_project
from pipeline.media import MediaError, clip_meta, parse_probe, scan_clips


def project(tmp_path, **extra):
    root = tmp_path / "beatcut"
    root.mkdir()
    return parse_project({"music": "m.mp3", **extra}, root)


def touch(folder, *names):
    for n in names:
        (folder / n).write_bytes(b"x")


def test_scan_filters_and_sorts(tmp_path):
    touch(tmp_path, "b.mov", "A.MP4", "notes.txt", "out_final.mp4")
    (tmp_path / "folder.mp4").mkdir()
    p = project(tmp_path, exclude_prefix=["out_"])
    assert [c.name for c in scan_clips(p)] == ["A.MP4", "b.mov"]


def test_scan_missing_folder(tmp_path):
    p = project(tmp_path, clips_dir="nope")
    with pytest.raises(MediaError, match="nope"):
        scan_clips(p)


def test_parse_probe_plain():
    data = {"streams": [{"width": 3840, "height": 2160, "r_frame_rate": "30000/1001"}],
            "format": {"duration": "12.5"}}
    m = parse_probe(data)
    assert m == {"duration": 12.5, "width": 3840, "height": 2160,
                 "fps": pytest.approx(29.97, abs=0.001), "rotation": 0,
                 "color_space": "", "color_transfer": ""}


def test_parse_probe_rotation_from_side_data():
    data = {"streams": [{"width": 3840, "height": 2160, "r_frame_rate": "60/1",
                         "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]}],
            "format": {"duration": "4"}}
    m = parse_probe(data)
    assert (m["width"], m["height"], m["rotation"]) == (2160, 3840, 270)


def test_parse_probe_rotation_from_tag():
    data = {"streams": [{"width": 1920, "height": 1080, "r_frame_rate": "25/1", "tags": {"rotate": "90"}}],
            "format": {"duration": "4"}}
    m = parse_probe(data)
    assert (m["width"], m["height"], m["rotation"]) == (1080, 1920, 90)


def test_parse_probe_without_video_stream():
    with pytest.raises(MediaError):
        parse_probe({"streams": [], "format": {"duration": "4"}})


def test_clip_meta_caches_by_mtime(tmp_path):
    touch(tmp_path, "a.mp4", "b.mp4")
    p = project(tmp_path)
    calls = []

    def fake_probe(path):
        calls.append(path.name)
        return {"duration": 5.0, "width": 1920, "height": 1080, "fps": 30.0, "rotation": 0}

    first = clip_meta(p, probe_fn=fake_probe)
    assert sorted(first) == ["a.mp4", "b.mp4"] and first["a.mp4"]["duration"] == 5.0
    assert sorted(calls) == ["a.mp4", "b.mp4"]
    clip_meta(p, probe_fn=fake_probe)
    assert len(calls) == 2                       # served from the cache
    assert json.loads((p.work_dir / "meta.json").read_text(encoding="utf-8"))["a.mp4"]["width"] == 1920
