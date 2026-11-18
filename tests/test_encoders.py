import subprocess

import pytest

from pipeline.encoders import EncoderError, assemble_args, detect, input_args, segment_args

LISTED = " V....D h264_nvenc   NVIDIA NVENC H.264 encoder\n V....D hevc_nvenc   NVIDIA NVENC hevc encoder\n"


class FakeRun:
    def __init__(self, listing="", trial_rc=0, missing=False):
        self.listing, self.trial_rc, self.missing, self.calls = listing, trial_rc, missing, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if self.missing:
            raise FileNotFoundError("ffmpeg")
        if "-encoders" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=self.listing, stderr="")
        return subprocess.CompletedProcess(cmd, self.trial_rc, stdout="", stderr="")


def test_cpu_preference_runs_nothing():
    run = FakeRun(LISTED)
    assert detect("cpu", run) == "cpu" and run.calls == []


def test_auto_picks_nvenc_when_the_trial_encode_works():
    run = FakeRun(LISTED, 0)
    assert detect("auto", run) == "nvenc"
    assert len(run.calls) == 2 and "hevc_nvenc" in run.calls[1]


def test_auto_falls_back_when_the_trial_encode_fails():
    assert detect("auto", FakeRun(LISTED, 1)) == "cpu"


def test_auto_skips_the_trial_when_nvenc_is_not_listed():
    run = FakeRun(" V....D libx264\n")
    assert detect("auto", run) == "cpu" and len(run.calls) == 1


def test_auto_without_ffmpeg():
    assert detect("auto", FakeRun(missing=True)) == "cpu"


def test_forced_nvenc_raises_when_unavailable():
    with pytest.raises(EncoderError):
        detect("nvenc", FakeRun(LISTED, 1))
    with pytest.raises(EncoderError):
        detect("nvenc", FakeRun(""))
    assert detect("nvenc", FakeRun(LISTED, 0)) == "nvenc"


@pytest.mark.parametrize("fn", [segment_args, assemble_args])
def test_codec_per_kind_and_mode(fn):
    assert "libx264" in fn("cpu", "draft")
    assert "libx265" in fn("cpu", "final")
    assert "h264_nvenc" in fn("nvenc", "draft")
    assert "hevc_nvenc" in fn("nvenc", "final")
    for kind in ("cpu", "nvenc"):
        args = fn(kind, "final")
        assert args[args.index("-tag:v") + 1] == "hvc1"
        assert "-tag:v" not in fn(kind, "draft")


def test_input_args():
    assert input_args("nvenc") == ["-hwaccel", "cuda"]
    assert input_args("cpu") == []
