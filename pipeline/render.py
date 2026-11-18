"""Segment rendering and assembly with ffmpeg. The timeline comes from plan.py."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import encoders, fsutil, grade, media
from .plan import build_plan, total_frames

FADE_IN_FRAMES = 30
CACHE_VERSION = 3
MAX_COMMAND_CHARS = 30000      # Windows limits a command line to 32767 characters
# Every segment and every assembled video is BT.709. ffmpeg 7.1+ takes the encoder's primaries and
# transfer from the frames, not from -color_primaries/-color_trc, so the graph's last step stamps the
# frames (TAG_FILTER) and the output options request the same values from the encoder.
OUTPUT_COLOR_ARGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
TAG_FILTER = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709"


def tag_bt709(graph, out_label):
    """`graph` with TAG_FILTER appended to the chain that ends in `out_label`."""
    if not graph.endswith(out_label):
        raise ValueError(f"filter graph does not end in {out_label}")
    return f"{graph[:-len(out_label)]},{TAG_FILTER}{out_label}"


def matrix_filter(src_matrix):
    """A size-preserving filter that re-encodes YUV from `src_matrix` ("bt2020", "bt601") to the BT.709
    matrix. Accurate rounding and full chroma interpolation keep the conversion within one code value."""
    return (f"scale=in_color_matrix={src_matrix}:out_color_matrix=bt709"
            f":flags=accurate_rnd+full_chroma_int")


class RenderError(RuntimeError):
    pass


def fit_graph(src_label, out_label, w, h, fill, src_w, src_h):
    """Scale a source of src_w x src_h (as displayed) into a w x h frame."""
    if abs(src_w * h - src_h * w) <= 0.01 * src_h * w:
        return f"{src_label}scale={w}:{h}:flags=bicubic,setsar=1{out_label}"
    if fill == "cover":
        return (f"{src_label}scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic,"
                f"crop={w}:{h},setsar=1{out_label}")
    bw, bh = max(w // 8, 2) // 2 * 2, max(h // 8, 2) // 2 * 2
    return (f"{src_label}split[fg0][bg0];"
            f"[bg0]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"gblur=sigma=6,scale={w}:{h}:flags=bilinear[bg];"
            f"[fg0]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=bicubic[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1{out_label}")


def retime_filters(speed, interp, fps, fps_float, src_fps, mode):
    """Returns (filter before scaling, filter after scaling)."""
    if speed == 1.0:
        return "setpts=PTS-STARTPTS", f"fps={fps}"
    pre = f"setpts=(PTS-STARTPTS)/{speed:.6f}"
    if interp == "auto":
        starved = speed < 0.97 and src_fps * speed < fps_float * 0.97
        interp = ("blend" if mode == "draft" else "oflow") if starved else "none"
    if speed >= 1.0 or interp == "none":
        return pre, f"fps={fps}"
    if interp == "oflow":
        return pre, f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    return pre, f"minterpolate=fps={fps}:mi_mode=blend"


def segment_graph(*, w, h, fill, src_w, src_h, fps, fps_float, src_fps, speed, interp, mode, color, first,
                  len_f=None, src_matrix=""):
    """Filter graph [0:v] -> [v] for one segment. Scaling happens before interpolation because
    interpolating at the output size is far cheaper than at the source size.

    `src_matrix` ("bt2020", "bt601") converts the source's YUV matrix to BT.709 first; empty means
    the source is already BT.709 (or untagged) and the graph is unchanged."""
    pre, post = retime_filters(speed, interp, fps, fps_float, src_fps, mode)
    if src_matrix:
        pre = f"{matrix_filter(src_matrix)},{pre}"
    tail = [post]
    if color:
        tail.append(f"format=gbrp,{color},format=yuv420p")
    if first:
        fade_frames = FADE_IN_FRAMES if len_f is None else max(1, min(FADE_IN_FRAMES, len_f))
        tail.append(f"fade=t=in:st=0:d={fade_frames / fps_float:.6f}")
    tail.append("tpad=stop_mode=clone:stop_duration=2")     # never run out of frames before -frames:v
    tail.append("format=yuv420p")
    return ";".join([f"[0:v]{pre}[r]",
                     fit_graph("[r]", "[f]", w, h, fill, src_w, src_h),
                     "[f]" + ",".join(tail) + "[v]"])


def assemble_graph(segs, fps_float, fade_out_frames):
    """Filter graph joining segment inputs 0..n-1 into [vout].

    Fades are xfade filters; cuts split the timeline into runs that are joined by one concat.
    The xfade offset is placed half a frame early so float rounding can never move it by a frame."""
    parts, runs = [], []
    last, run_len = "[0:v]", segs[0].len_f
    for i in range(1, len(segs)):
        s = segs[i]
        if s.din_f == 0:
            runs.append(last)
            last, run_len = f"[{i}:v]", s.len_f
            continue
        offset = (run_len - s.din_f - 0.5) / fps_float
        label = f"[x{i}]"
        parts.append(f"{last}[{i}:v]xfade=transition={s.tin}:duration={s.din_f / fps_float:.6f}:"
                     f"offset={offset:.6f}{label}")
        last, run_len = label, run_len + s.len_f - s.din_f
    runs.append(last)
    if len(runs) > 1:
        parts.append("".join(runs) + f"concat=n={len(runs)}:v=1:a=0[cat]")
        last = "[cat]"
    tail = []
    if fade_out_frames > 0:
        total = total_frames(segs)
        tail.append(f"fade=t=out:st={(total - fade_out_frames) / fps_float:.6f}:"
                    f"d={fade_out_frames / fps_float:.6f}")
    tail.append("format=yuv420p")
    parts.append(last + ",".join(tail) + "[vout]")
    return ";".join(parts)


def _run(cmd, cwd=None):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)


def render_segment(project, seg, mode, kind, meta):
    """Render one segment with exactly seg.len_f frames. Reuses the previous file when nothing changed."""
    out = project.work_dir / f"seg_{mode}_{seg.index:03d}.mp4"
    key_path = out.with_suffix(".key")
    src = (project.clips_dir / seg.clip).resolve()
    if src.parent != project.clips_dir.resolve():
        raise RenderError(f"shot {seg.index + 1} ({seg.clip}): clip is outside the clips folder")
    w, h = project.size(mode)
    color = grade.chain(seg.prof, project.profiles)
    info = meta[seg.clip]
    src_matrix = media.source_matrix(info)
    key = json.dumps([CACHE_VERSION, seg.clip, src.stat().st_mtime_ns, round(seg.src_start, 4), seg.len_f,
                      round(seg.speed, 6), seg.interp, color, w, h, project.fill, project.fps, kind,
                      seg.index == 0, src_matrix])
    if out.exists() and key_path.exists():
        try:
            if key_path.read_text(encoding="utf-8") == key:
                return out
        except OSError:
            pass
    graph = segment_graph(w=w, h=h, fill=project.fill, src_w=info["width"], src_h=info["height"],
                          fps=project.fps, fps_float=project.fps_float, src_fps=info["fps"],
                          speed=seg.speed, interp=seg.interp, mode=mode, color=color, first=seg.index == 0,
                          len_f=seg.len_f, src_matrix=src_matrix)
    try:
        key_path.unlink()               # an interrupted render must not leave a stale key behind
    except FileNotFoundError:
        pass
    fd, tmp_name = tempfile.mkstemp(dir=project.work_dir, prefix=out.stem + "_", suffix=".mp4")
    os.close(fd)
    tmp = Path(tmp_name)
    cmd = (["ffmpeg", "-v", "error", "-y", *encoders.input_args(kind),
            "-ss", f"{seg.src_start:.4f}", "-t", f"{seg.src_len + 1.0:.4f}",
            "-i", os.path.abspath(src), "-filter_complex", tag_bt709(graph, "[v]"), "-map", "[v]", "-an",
            "-r", project.fps, "-frames:v", str(seg.len_f), *encoders.segment_args(kind, mode),
            "-pix_fmt", "yuv420p", *OUTPUT_COLOR_ARGS, str(tmp)])
    try:
        r = _run(cmd)
        if r.returncode != 0:
            raise RenderError(f"shot {seg.index + 1} ({seg.clip}): {(r.stderr or '')[-600:]}")
        fsutil.replace_with_retry(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    key_path.write_text(key, encoding="utf-8")
    return out


def assemble_command(names, segs, fps, fps_float, fade_out_frames, mode, kind="cpu", out_name=None):
    """The ffmpeg command that joins the segment files `names` (run inside the work folder)."""
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for name in names:
        cmd += ["-i", name]
    cmd += ["-filter_complex", tag_bt709(assemble_graph(segs, fps_float, fade_out_frames), "[vout]"),
            "-map", "[vout]", "-r", fps, "-frames:v", str(total_frames(segs)),
            *encoders.assemble_args(kind, mode), "-pix_fmt", "yuv420p", *OUTPUT_COLOR_ARGS, out_name or f"{mode}_video.mp4"]
    return cmd


def assemble_fits(segs, fps_float, fade_out_frames, mode, kind="cpu"):
    """True when the assemble command for `segs` fits in one command line."""
    names = [f"seg_{mode}_{i:03d}.mp4" for i in range(len(segs))]
    cmd = assemble_command(names, segs, "30000/1001", fps_float, fade_out_frames, mode, kind)
    return len(subprocess.list2cmdline(cmd)) <= MAX_COMMAND_CHARS


def assemble(project, segs, files, mode, kind, fade_out_frames):
    """Join rendered segments into work/<mode>_video.mp4 with exactly total_frames(segs) frames."""
    out = project.work_dir / f"{mode}_video.mp4"
    cmd = assemble_command([Path(f).name for f in files], segs, project.fps, project.fps_float,
                           fade_out_frames, mode, kind, out.name)
    if len(subprocess.list2cmdline(cmd)) > MAX_COMMAND_CHARS:
        raise RenderError(f"too many shots to assemble in one pass ({len(segs)} shots)")
    r = _run(cmd, cwd=project.work_dir)
    if r.returncode != 0:
        raise RenderError(f"assemble failed: {(r.stderr or '')[-600:]}")
    return out


def render(project, state, mode, log=print):
    """Render every segment, then assemble them. Returns work/<mode>_video.mp4."""
    media.require_tools()
    meta = media.clip_meta(project)
    segs = build_plan(state, project.fps_float)
    kind = encoders.detect(project.encoder)
    log(f"encoder: {kind}")
    project.work_dir.mkdir(parents=True, exist_ok=True)
    n = len(segs)
    files = [None] * n
    with ThreadPoolExecutor(max_workers=3 if kind == "nvenc" else 2) as pool:
        futures = {pool.submit(render_segment, project, s, mode, kind, meta): i for i, s in enumerate(segs)}
        for done, fut in enumerate(as_completed(futures), start=1):
            files[futures[fut]] = fut.result()
            log(f"segment {done}/{n}")
    fade_out_frames = round(state["tail"] * project.fps_float)
    return assemble(project, segs, files, mode, kind, fade_out_frames)
