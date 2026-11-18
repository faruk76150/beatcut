import subprocess

import pytest

from pipeline.grade import BUILTIN, GradeError, chain, profile_names


def test_builtin_names():
    assert set(BUILTIN) == {"neutral", "warm", "cool", "contrast", "golden"}
    assert BUILTIN["neutral"] == ""


def test_profile_names_include_custom():
    assert profile_names({"teal": "eq=saturation=0.9"}) == set(BUILTIN) | {"teal"}
    assert profile_names({}) == set(BUILTIN)


def test_chain_lookup():
    assert chain("neutral", {}) == ""
    assert chain("warm", {}) == BUILTIN["warm"]
    assert chain("teal", {"teal": "eq=saturation=0.9"}) == "eq=saturation=0.9"
    assert chain("warm", {"warm": "eq=gamma=1.1"}) == "eq=gamma=1.1"      # custom overrides built-in


def test_unknown_profile():
    with pytest.raises(GradeError, match="nope"):
        chain("nope", {})


def test_builtin_chains_are_single_chains():
    for text in BUILTIN.values():
        assert ";" not in text and "[" not in text and "]" not in text


@pytest.mark.slow
@pytest.mark.parametrize("name", ["warm", "cool", "contrast", "golden"])
def test_builtin_chains_run_in_ffmpeg(name):
    vf = "format=gbrp," + BUILTIN[name] + ",format=yuv420p"
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:d=0.1",
                        "-vf", vf, "-frames:v", "1", "-f", "null", "-"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
