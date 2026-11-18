"""Shared helpers for the test suite."""
import copy
import json
import subprocess
from pathlib import Path

SHOT_DEFAULTS = {"fit": "fit", "prof": "neutral", "tin": "cut", "tdur": 0.5, "note": ""}


def make_state(shots, period=0.5, t0=1.0, tail=1.5):
    """A complete, defaults-filled shots.json state."""
    full = []
    for s in shots:
        d = dict(SHOT_DEFAULTS)
        d.update(s)
        full.append(d)
    return {"version": 1, "period": period, "t0": t0, "tail": tail, "shots": full,
            "bad": {}, "clipnotes": {}, "markers": []}


def patched(base, fn):
    """Deep copy of `base` with `fn` applied to it."""
    d = copy.deepcopy(base)
    fn(d)
    return d


def run_ok(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    return r


def make_test_project(tmp_path, *, fill="cover", size=(854, 480), shots=None):
    """A small synthetic project: three generated clips, a generated tone as music.
    Returns the project folder. Needs ffmpeg on PATH."""
    clips = Path(tmp_path) / "clips"
    root = clips / "beatcut"
    root.mkdir(parents=True)
    specs = [("wide30.mp4", "640x360", "30000/1001"),
             ("wide60.mp4", "640x360", "60000/1001"),
             ("tall30.mp4", "360x640", "30000/1001")]
    for name, sz, rate in specs:
        run_ok(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={sz}:r={rate}:d=8",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clips / name)])
    run_ok(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=20",
            str(root / "music.wav")])
    project = {"music": "music.wav", "encoder": "cpu", "fill": fill,
               "draft": {"width": size[0], "height": size[1]},
               "final": {"width": size[0], "height": size[1]}}
    (root / "project.json").write_text(json.dumps(project), encoding="utf-8")
    if shots is None:
        shots = [
            {"clip": "wide30.mp4", "in": 1.0, "out": 3.0, "beats": 4},
            {"clip": "wide60.mp4", "in": 1.0, "out": 2.0, "beats": 4, "tin": "fade", "tdur": 0.4},
            {"clip": "tall30.mp4", "in": 1.0, "out": 2.0, "beats": 2},
            {"clip": "wide30.mp4", "in": 4.0, "out": 5.0, "beats": 4, "prof": "warm",
             "tin": "fadewhite", "tdur": 0.4},
        ]
    state = {"version": 1, "period": 0.5, "t0": 0.25, "tail": 1.0, "shots": shots}
    (root / "shots.json").write_text(json.dumps(state), encoding="utf-8")
    return root


def colour_tags(space, primaries, trc):
    """A filter stamping colour tags on frames. ffmpeg 7.1+ ignores -color_primaries/-color_trc output
    options and takes them from the frames, so tags must be set inside the filter graph."""
    return f"setparams=colorspace={space}:color_primaries={primaries}:color_trc={trc}"


def make_color_clip(path, matrix, tags, *, colour="0xE04020", size="320x180", rate="30000/1001", seconds=6):
    """A solid-colour H.264 clip whose RGB colour is encoded with the YUV `matrix` ("bt709", "bt2020",
    "bt601") and tagged with `tags` = (colorspace, primaries, transfer)."""
    run_ok(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c={colour}:s={size}:r={rate}:d={seconds},format=rgb24,"
            f"scale=out_color_matrix={matrix}:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p,"
            f"{colour_tags(*tags)}",
            "-c:v", "libx264", "-preset", "ultrafast", str(path)])
    return path


def probe_color(path):
    """(color_space, color_primaries, color_transfer) of the first video stream."""
    r = run_ok(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=color_space,color_primaries,color_transfer", "-of", "json", str(path)])
    s = json.loads(r.stdout)["streams"][0]
    return s.get("color_space"), s.get("color_primaries"), s.get("color_transfer")


def frame_means(path, index, matrix="bt709"):
    """Mean R, G, B (0-255) of frame `index`, decoded with the given YUV matrix."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                        f"select=eq(n\\,{index}),scale=in_color_matrix={matrix}:flags=accurate_rnd+full_chroma_int,"
                        "format=rgb24", "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True)
    assert r.returncode == 0 and r.stdout, r.stderr[-800:]
    data, n = r.stdout, len(r.stdout) // 3
    return [sum(data[c::3]) / n for c in range(3)]


def count_frames(path):
    r = run_ok(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                "-show_entries", "stream=nb_read_frames,width,height", "-of", "json", str(path)])
    return json.loads(r.stdout)["streams"][0]
