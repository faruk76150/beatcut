import json
import subprocess

import pytest

from pipeline.mix import audio_graph


def test_audio_graph():
    g = audio_graph(20.0, 1.5, -15)
    assert g == ("[1:a]atrim=0:20.0000,asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.05,"
                 "afade=t=out:st=16.500:d=3.500,alimiter=limit=0.89:level=0,"
                 "loudnorm=I=-15:TP=-1.5:LRA=11[a]")


def test_audio_graph_short_video_fades_over_its_whole_length():
    g = audio_graph(2.0, 1.5, -14.0)
    assert "afade=t=out:st=0.000:d=2.000" in g and "loudnorm=I=-14.0" in g


@pytest.mark.slow
def test_mix_adds_audio_and_keeps_the_video(tmp_path):
    from pipeline import grade
    from pipeline.config import load_project
    from pipeline.mix import mix
    from pipeline.plan import build_plan, total_frames
    from pipeline.render import render
    from pipeline.schema import load_shots
    from util import count_frames, make_test_project

    root = make_test_project(tmp_path)
    project = load_project(root)
    state = load_shots(project.shots_path, grade.profile_names(project.profiles))
    video = render(project, state, "draft", log=lambda *a: None)
    frames = total_frames(build_plan(state, project.fps_float))
    out = mix(project, state, "draft", video, frames, log=lambda *a: None)
    assert out == project.out_dir / f"{project.out_name}_draft.mp4" and out.exists()
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
                        "-of", "json", str(out)], capture_output=True, text=True)
    data = json.loads(r.stdout)
    assert sorted(s["codec_type"] for s in data["streams"]) == ["audio", "video"]
    assert abs(float(data["format"]["duration"]) - frames / project.fps_float) < 0.06
    assert int(count_frames(out)["nb_read_frames"]) == frames
