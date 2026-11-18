"""Video encoder detection and ffmpeg encoder arguments."""

import subprocess


class EncoderError(RuntimeError):
    """Raised when a requested encoder is not available."""


_TRIAL_CMD = [
    "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
    "-frames:v", "1", "-c:v", "hevc_nvenc", "-f", "null", "-",
]

_SEGMENT = {
    ("cpu", "draft"): "-c:v libx264 -preset veryfast -crf 15",
    ("cpu", "final"): "-c:v libx265 -preset medium -crf 16 -tag:v hvc1",
    ("nvenc", "draft"): "-c:v h264_nvenc -preset p4 -rc vbr -cq 16 -b:v 0",
    ("nvenc", "final"): "-c:v hevc_nvenc -preset p5 -rc vbr -cq 15 -b:v 0 -tag:v hvc1",
}

_ASSEMBLE = {
    ("cpu", "draft"): "-c:v libx264 -preset medium -crf 18",
    ("cpu", "final"): "-c:v libx265 -preset medium -crf 19 -tag:v hvc1",
    ("nvenc", "draft"): "-c:v h264_nvenc -preset p5 -rc vbr -cq 19 -b:v 0",
    ("nvenc", "final"): "-c:v hevc_nvenc -preset p7 -rc vbr -cq 19 -b:v 0 -tag:v hvc1",
}


def detect(preference: str, run=subprocess.run) -> str:
    """Return "nvenc" or "cpu" for the given preference ("auto", "nvenc", "cpu")."""
    if preference == "cpu":
        return "cpu"
    if preference not in ("auto", "nvenc"):
        raise EncoderError(f"unknown encoder preference: {preference!r}")
    forced = preference == "nvenc"

    def fail(message):
        if forced:
            raise EncoderError(message)
        return "cpu"

    try:
        listing = run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True,
                      encoding="utf-8", errors="replace")
        if "h264_nvenc" not in listing.stdout or "hevc_nvenc" not in listing.stdout:
            return fail("ffmpeg does not list h264_nvenc and hevc_nvenc")
        trial = run(_TRIAL_CMD, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return fail("ffmpeg was not found")
    if trial.returncode != 0:
        return fail("NVENC trial encode failed")
    return "nvenc"


def _check(kind: str, mode: str) -> None:
    if kind not in ("cpu", "nvenc"):
        raise EncoderError(f"unknown encoder kind: {kind!r}")
    if mode not in ("draft", "final"):
        raise EncoderError(f"unknown mode: {mode!r}")


def input_args(kind: str) -> list[str]:
    """Arguments placed before -i."""
    return ["-hwaccel", "cuda"] if kind == "nvenc" else []


def segment_args(kind: str, mode: str) -> list[str]:
    _check(kind, mode)
    return _SEGMENT[(kind, mode)].split()


def assemble_args(kind: str, mode: str) -> list[str]:
    _check(kind, mode)
    return _ASSEMBLE[(kind, mode)].split()
