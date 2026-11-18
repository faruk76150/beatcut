import json
import os

import pytest

import beatcut


def test_init_creates_a_project(tmp_path, capsys):
    clips = tmp_path / "clips"
    clips.mkdir()
    music = tmp_path / "song.mp3"
    music.write_bytes(b"ID3")
    assert beatcut.main(["init", str(clips), "--music", str(music)]) == 0
    root = clips / "beatcut"
    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert project["music"] == "song.mp3" and project["clips_dir"] == ".."
    assert (root / "song.mp3").read_bytes() == b"ID3"
    assert json.loads((root / "shots.json").read_text(encoding="utf-8"))["version"] == 1
    assert music.exists()                                   # the original is copied, not moved


def test_init_refuses_to_overwrite(tmp_path, capsys):
    clips = tmp_path / "clips"
    clips.mkdir()
    music = tmp_path / "song.mp3"
    music.write_bytes(b"ID3")
    assert beatcut.main(["init", str(clips), "--music", str(music)]) == 0
    (clips / "beatcut" / "project.json").write_text('{"music": "kept.mp3"}', encoding="utf-8")
    assert beatcut.main(["init", str(clips), "--music", str(music)]) == 1
    assert "kept.mp3" in (clips / "beatcut" / "project.json").read_text(encoding="utf-8")
    assert "error:" in capsys.readouterr().err


def test_init_missing_inputs(tmp_path, capsys):
    assert beatcut.main(["init", str(tmp_path / "nope"), "--music", str(tmp_path / "x.mp3")]) == 1


def test_check_without_project_json(tmp_path, capsys):
    assert beatcut.main(["check", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "error:" in err and "project.json" in err and "Traceback" not in err


@pytest.mark.slow
def test_check_reports_schema_errors_line_by_line(tmp_path, capsys):
    from util import make_test_project

    root = make_test_project(tmp_path, shots=[{"clip": "wide30.mp4", "in": 3.0, "out": 1.0, "beats": 0}])
    assert beatcut.main(["check", str(root)]) == 1
    err = capsys.readouterr().err
    assert "error: shots[0].out" in err and "error: shots[0].beats" in err


@pytest.mark.slow
def test_check_reports_plan_errors(tmp_path, capsys):
    from util import make_test_project

    root = make_test_project(tmp_path, shots=[{"clip": "missing.mp4", "in": 0.0, "out": 1.0, "beats": 2}])
    assert beatcut.main(["check", str(root)]) == 1
    assert "missing.mp4" in capsys.readouterr().err


@pytest.mark.slow
def test_check_and_draft_end_to_end(tmp_path, capsys):
    from util import count_frames, make_test_project

    root = make_test_project(tmp_path)
    assert beatcut.main(["check", str(root)]) == 0
    assert "ok: 4 shots" in capsys.readouterr().out
    assert beatcut.main(["draft", str(root)]) == 0
    out = root / "out" / "clips_draft.mp4"
    assert out.exists()
    expected = round((0.25 + 0.5 * 14) * 30000 / 1001) + round(1.0 * 30000 / 1001)
    assert int(count_frames(out)["nb_read_frames"]) == expected


@pytest.mark.slow
def test_pick_is_reported_as_unavailable_until_plan_b(tmp_path, capsys):
    from util import make_test_project

    root = make_test_project(tmp_path)
    try:
        import picker.server  # noqa: F401
    except ImportError:
        assert beatcut.main(["pick", str(root), "--no-open"]) == 2


def test_pick_passes_the_project_folder_to_serve(tmp_path, monkeypatch):
    import picker.server

    (tmp_path / "project.json").write_text(json.dumps({"music": "m.mp3"}), encoding="utf-8")
    seen = []
    monkeypatch.setattr(picker.server, "serve", lambda *a, **k: seen.append((a, k)))
    assert beatcut.main(["pick", str(tmp_path), "--no-open", "--port", "8799"]) == 0
    (root, port, open_browser), kwargs = seen[0][0], seen[0][1]
    assert kwargs == {} and port == 8799 and open_browser is False
    assert isinstance(root, (str, os.PathLike)) and os.path.samefile(root, tmp_path)


def test_beats_with_too_few_beats_is_an_input_problem(tmp_path, monkeypatch, capsys):
    from pipeline import beats

    (tmp_path / "project.json").write_text(json.dumps({"music": "m.mp3"}), encoding="utf-8")
    (tmp_path / "m.mp3").write_bytes(b"ID3")

    def too_few(path):
        raise beats.BeatError("fewer than 4 beats were detected; set period and t0 by hand")

    monkeypatch.setattr(beats, "analyze", too_few)
    assert beatcut.main(["beats", str(tmp_path)]) == 1
    assert "error: fewer than 4 beats were detected; set period and t0 by hand" in capsys.readouterr().err


def test_beats_without_librosa_is_still_an_environment_problem(tmp_path, monkeypatch, capsys):
    from pipeline import beats

    (tmp_path / "project.json").write_text(json.dumps({"music": "m.mp3"}), encoding="utf-8")
    (tmp_path / "m.mp3").write_bytes(b"ID3")

    def missing(path):
        raise ImportError("No module named 'librosa'")

    monkeypatch.setattr(beats, "analyze", missing)
    assert beatcut.main(["beats", str(tmp_path)]) == 2
    assert "librosa" in capsys.readouterr().err


def test_pick_on_a_port_in_use_exits_2_with_a_hint(tmp_path, monkeypatch, capsys):
    import socket

    from pipeline import media

    monkeypatch.setattr(media, "require_tools", lambda: None)  # the bind, not ffmpeg, is under test

    (tmp_path / "project.json").write_text(json.dumps({"music": "m.mp3"}), encoding="utf-8")
    (tmp_path / "m.mp3").write_bytes(b"ID3")
    with socket.socket() as busy:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            busy.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert beatcut.main(["pick", str(tmp_path), "--no-open", "--port", str(port)]) == 2
    err = capsys.readouterr().err
    assert f"error: port {port} is already in use; pass --port to choose another" in err
    assert "Traceback" not in err


def test_check_with_missing_clips_folder_is_an_input_problem(tmp_path, capsys, monkeypatch):
    from pipeline import media
    from pipeline.schema import empty_state

    monkeypatch.setattr(media, "require_tools", lambda: None)   # the folder check must not need ffmpeg
    root = tmp_path / "proj"
    root.mkdir()
    (root / "music.wav").write_bytes(b"x")
    (root / "project.json").write_text(json.dumps({"music": "music.wav", "clips_dir": "no_such_folder"}),
                                       encoding="utf-8")
    (root / "shots.json").write_text(json.dumps(empty_state()), encoding="utf-8")
    assert beatcut.main(["check", str(root)]) == 1
    err = capsys.readouterr().err
    assert "clips folder does not exist" in err and "Traceback" not in err
