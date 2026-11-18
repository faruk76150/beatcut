"""Checks for the demo project that ships with the repo (examples/demo)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import beatcut
from pipeline import config, grade, plan, schema

REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "examples" / "demo"
PROJECT = DEMO / "beatcut"
GENERATED = {"proxy", "work", "out"}
MAX_TOTAL = 25 * 1024 * 1024
MAX_CLIP = 5 * 1024 * 1024
FORBIDDEN_TAGS = {"creation_time", "location", "make", "model", "artist", "comment", "copyright", "title"}


def _load():
    project = config.load_project(PROJECT)
    state = schema.load_shots(project.shots_path, grade.profile_names(project.profiles))
    return project, state


def _shipped_files():
    """Files under examples/demo that the repo ships (no proxy/work/out, no *.bak)."""
    out = []
    for p in DEMO.rglob("*"):
        rel = p.relative_to(DEMO)
        if p.is_file() and not (set(rel.parts) & GENERATED) and p.suffix != ".bak":
            out.append(p)
    return out


def _clips():
    return sorted(DEMO.glob("*.mp4"))


def test_demo_project_and_shots_load():
    project, state = _load()
    assert project.root == PROJECT
    assert state["shots"]


def test_every_shot_clip_and_the_music_exist():
    project, state = _load()
    for shot in state["shots"]:
        assert (DEMO / shot["clip"]).is_file(), shot["clip"]
    assert project.music.is_file()


def test_demo_size_budget():
    files = _shipped_files()
    assert files
    assert sum(p.stat().st_size for p in files) <= MAX_TOTAL
    for clip in _clips():
        assert clip.stat().st_size < MAX_CLIP, clip.name


def test_demo_shows_off_transitions_and_grades():
    _, state = _load()
    assert len(_clips()) >= 8
    tins = {s["tin"] for s in state["shots"]}
    assert "fade" in tins and "fadewhite" in tins
    assert len({s["prof"] for s in state["shots"]} - {"neutral"}) >= 2


@pytest.mark.slow
def test_demo_draft_renders_the_planned_frame_count(tmp_path):
    from util import count_frames

    copy = tmp_path / "demo"
    shutil.copytree(DEMO, copy, ignore=shutil.ignore_patterns("proxy", "work", "out", "*.bak"))
    root = copy / "beatcut"
    pj = json.loads((root / "project.json").read_text(encoding="utf-8"))
    pj["encoder"] = "cpu"
    pj["draft"] = {"width": 854, "height": 480}
    (root / "project.json").write_text(json.dumps(pj), encoding="utf-8")

    assert beatcut.main(["draft", str(root)]) == 0

    project = config.load_project(root)
    state = schema.load_shots(project.shots_path, grade.profile_names(project.profiles))
    expected = plan.total_frames(plan.build_plan(state, project.fps_float))
    out = project.out_dir / f"{project.out_name}_draft.mp4"
    assert out.is_file(), list(project.out_dir.glob("*"))
    info = count_frames(out)
    assert int(info["nb_read_frames"]) == expected
    assert (info["width"], info["height"]) == (854, 480)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(out)],
                           capture_output=True, text=True, check=True)
    kinds = [s["codec_type"] for s in json.loads(probe.stdout)["streams"]]
    assert kinds.count("video") == 1 and kinds.count("audio") == 1


@pytest.mark.slow
def test_demo_media_carry_no_personal_metadata():
    from util import run_ok

    media = _clips() + [config.load_project(PROJECT).music]
    for path in media:
        r = run_ok(["ffprobe", "-v", "error", "-show_entries", "format_tags:stream_tags", "-of", "json", str(path)])
        data = json.loads(r.stdout)
        tags = dict(data.get("format", {}).get("tags", {}))
        for s in data.get("streams", []):
            tags.update(s.get("tags", {}))
        bad = [k for k in tags if k.lower() in FORBIDDEN_TAGS or k.lower().startswith("com.apple.")]
        assert not bad, f"{path.name}: {bad}"
