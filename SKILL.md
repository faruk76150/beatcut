---
name: beatcut
description: Use when the user wants to cut a short video from a folder of clips to the beat of a music track, build or edit a beat-synced edit (shots.json), pick clip in/out points and beat counts, or render a draft or final video with ffmpeg using the beatcut command line tool. Covers the whole loop - init a project, detect the beat grid, let the user choose shots in the local picker (or edit shots.json by hand), check, render a draft, inspect it with a contact sheet, and render the final only after the user approves.
---

# beatcut

beatcut turns a folder of clips and a music file into a video whose cuts land on the beats, frame-exact. State lives in two files in the project folder: `project.json` (settings) and `shots.json` (the edit). The command line tool is `beatcut.py`.

## Paths

Run commands with the Python that has the requirements installed (`pip install -r requirements.txt`; ffmpeg and ffprobe must be on `PATH`). Use the folder this skill is installed in as the repository folder:

```
python <skill folder>/beatcut.py <command> ...
```

Below, `beatcut.py` means that full path. Paths to clips and projects can be anywhere; quote them if they contain spaces. A project is always `<clips folder>/beatcut`; every command except `init` and `sheet` takes that project folder.

## Workflow

1. **Ask what is missing**: the clips folder, the music file, and the output shape (default 1920x1080 draft, 3840x2160 final; vertical sizes such as 1080x1920 are fine).
2. **Init**: `beatcut.py init <clips folder> --music <music file>`. It prints `created: <folder>` and copies the music into the project. If it says `project.json already exists`, the project exists; use it and do not re-init. Edit `project.json` if sizes, `fill`, `fps`, `encoder`, `exclude_prefix` or custom `profiles` need to change (see rules below).
3. **Beats**: `beatcut.py beats <project>`. It prints `period`, `t0` and `bpm` and writes them into `shots.json`. Detection assumes a steady tempo and can be off by a few hundredths of a second in `t0`. If it exits 1 with `fewer than 4 beats were detected`, set `period` and `t0` by hand (period = 60 / BPM; t0 = second of the first beat). Tell the user the numbers; they can correct them in the picker.
4. **Choose shots**, either way:
   - **Picker (default).** Start `beatcut.py pick <project>` in the background (it blocks until stopped; it opens the browser unless you add `--no-open`; default port 8765, change it with `--port N`). Tell the user the URL it printed, `http://127.0.0.1:<port>/`, and that the page saves automatically. Wait for the user to say they are done, then stop the server (Ctrl+C or end the background process) and read `shots.json`. Do not edit `shots.json` while the picker is open: the page would overwrite your changes.
   - **By hand**, when the user asks you to build the edit or gives exact times. Follow the rules below, then run `check`.
5. **Check**: `beatcut.py check <project>`. Exit 0 and `ok: <n> shots, <s> s` means no errors; read the warnings too (stderr). Fix every error before rendering.
6. **Draft**: `beatcut.py draft <project>`. It prints `encoder: ...`, `segment i/n` and finally the path of `out/<out_name>_draft.mp4`.
7. **Look at it**: `beatcut.py sheet <video> --out <some .jpg path>` writes a 5 by 4 contact sheet; open that image and describe the result to the user (order of shots, colour, fades, black frames, wrong crops). Choose an `--out` path outside the clips folder, for example a scratch folder.
8. **Final, only with the user's approval**: after showing the draft, ask whether to render the final. Run `beatcut.py final <project>` only after a clear yes. The final is slower and larger. Report the output path.

If the user changes the edit afterwards, go back to step 4. Renders reuse cached segments, so a second draft is faster.

## Editing shots.json by hand

Keep the file valid JSON (UTF-8). `check` reports every violation of these rules.

Top level:
- `version`: `1`. `period`: number, more than 0.05 and less than 10. `t0`: number, 0 or more. `tail`: 0 to 30 (default 1.5). `shots`: list, at most 2000.
- Optional: `bad` (clip name to list of `{"a": s, "b": s, "note": ""}` with `b > a >= 0`), `clipnotes` (clip name to text), `markers` (`{"beat": int >= 0, "title": text}`; shown in the picker only). Keep keys you do not know; the tools preserve them.

Each shot:
- `clip`: file name with extension, as it appears in the clips folder. No `/` or `\`, no `:`. Must be a file directly in `clips_dir` and match `extensions`.
- `in` (0 or more) and `out` (greater than `in`): seconds in the source clip.
- `beats`: integer from 1 to 512.
- Optional, with defaults: `fit` (`fit` or `cut`; default `fit`), `interp` (`oflow`, `blend`, `none`; leave it out for automatic), `prof` (a built-in name `neutral`, `warm`, `cool`, `contrast`, `golden`, or a name defined in `project.json` `profiles`; default `neutral`), `tin` (`cut`, `fade`, `fadewhite`; default `cut`), `tdur` (0 to 5 seconds, default 0.5), `note` (text, at most 2000 characters).

How the numbers are used:
- Shots play back to back; shot N occupies `beats` beats on the grid starting at `t0 + period * (sum of earlier beats)`. The first shot starts at video frame 0 on its `in` point, so it also covers the time before the first beat and reads past its `out`.
- `fit`: speed = `(out - in) / (beats * period)`. Speed within 0.015 of 1 counts as 1. `check` rejects speeds outside 0.1x to 8x. To keep a shot at normal speed with `fit`, set `out = in + beats * period`.
- `cut`: plays at 1x; `out` is not used for speed (the picker writes `out = in + beats * period`). Do the same.
- The last shot is extended by `tail` seconds and the picture fades out over `tail`. The whole edit must not be longer than the music.
- Every shot needs source material up to its end (including the `tail` and first-shot lead-in) with 0.1 s to spare before the end of the clip. A `fade` or `fadewhite` transition borrows half its length from each neighbour, so the earlier shot needs source past its `out` and the later one needs source before its `in`.
- A transition must be shorter than the shots around it. `tdur` is rounded to an even number of frames; under 2 frames means a plain cut.
- Slow motion: automatic interpolation happens only when the shot runs slower than 0.97x and the source frame rate times the speed is below the output frame rate (`blend` in drafts, `oflow` in finals). Do not set `interp` unless the user asks.
- Choose `in`/`out` so the range does not overlap a `bad` range; `check` warns if it does.

`project.json` rules (when you change it):
- Unknown keys are an error. `music` is relative to the project folder, no `..`, not absolute. `draft` and `final` are `{"width": n, "height": n}` with positive even integers. `fps` is a string such as `"25"` or `"30000/1001"`. `fill`: `cover` or `blur`. `encoder`: `auto`, `cpu` or `nvenc`. `loudness`: -40 to -5.
- `profiles`: name (`[a-z0-9_-]`, 1 to 32 characters) to one ffmpeg filter chain, with no `;`, `[` or `]`. Shots refer to profiles by name only.

## Messages and what to do

Warnings begin with `warning:` and do not stop a render. Errors begin with `error:` and go to stderr.

| Message | Meaning and action |
|---|---|
| `ffmpeg was not found on PATH; install FFmpeg and try again` (exit 2) | ffmpeg or ffprobe is not on `PATH`. Ask the user to install FFmpeg; do not install it yourself unless asked. |
| `no project.json in <folder> (did you mean <folder>\beatcut?)` (exit 1) | You passed the clips folder. Use the `beatcut` folder inside it. |
| `unknown key(s): ...`, `<field>: must be ...` | A `project.json` field is invalid. Fix that field. |
| `<path>: invalid JSON (...)` (`<path>` is the full path of `shots.json`) | Syntax error in the file. Fix it; the previous version may be in `shots.json.bak`. |
| `shots[i].<field>: must be ...` | A shot breaks the rules above (`i` counts from 0). Fix it. |
| `no shots` | `shots` is empty. Add shots (picker or by hand). |
| `shot N (clip): clip not found in the clips folder` | The file name is wrong or the file is missing or excluded by `extensions` or `exclude_prefix`. Check spelling; never create or copy files into the clips folder to fix it. |
| `shot N (clip): needs source up to X s but the clip is Y s long` | The range plus lead-in, tail or transition runs past the clip. Move `in` earlier, use fewer beats, use `fit` with a shorter `out`, or shorten the transition. |
| ``shot N (clip): needs X s before the start of the clip; move `in` later`` | A first-shot lead-in or a transition reaches before 0. Raise `in` or shorten the transition. |
| `shot N (clip): shorter than its transitions; ...` | Transition longer than the shot. Lower `tdur` or add beats. |
| `shot N (clip): speed X is outside 0.1x-8.0x` | Range and beat count do not fit. Change `beats` or the range. |
| `the edit is X s long but the music is only Y s` | Remove beats or shots, lower `tail`, or use a longer music file. |
| `warning: shot N (clip): uses a range marked bad (...)` | The shot overlaps a `bad` range. Move it, or tell the user. |
| `warning: <clip>: HDR source (...); colours will look washed out ...` | HDR clip; beatcut does not tone-map. Tell the user; suggest SDR clips. |
| `warning: <clip>: cannot be read (...)` | ffprobe cannot read the clip. Tell the user. |
| `music file not found: <name>` | The `music` file is missing from the project folder. |
| `profile <name>: ffmpeg rejects the filter chain` | A custom profile is wrong. Fix its chain in `project.json`. |
| `too many shots to assemble in one pass (N); split the edit` | The edit is too long for one render. Tell the user; split it into several projects or fewer shots. |
| `another render is already running for this project` (exit 2) | A render (maybe started from the picker) is running. Wait for it; do not delete `work/`. |
| `cannot write <file>; close it in any player and try again` | The output is open elsewhere. Ask the user to close it. |
| `fewer than 4 beats were detected; set period and t0 by hand` (exit 1) | See step 3. |

Exit codes: 0 success, 1 a problem with the user's input (fix and re-run), 2 environment or run-time failure.

## Do not

- Do not modify, move, rename or delete the source clips, and do not write into the clips folder other than the `beatcut` project folder `init` creates. Everything the tool writes is inside the project folder.
- Do not delete `project.json`, `shots.json` or the music file. Do not run `init` again over an existing project.
- Do not run `final` without the user's approval after they have seen the draft.
- Do not expose the picker to a network: no port forwarding, tunnels, reverse proxies, and do not try to bind it to another address. It listens on `127.0.0.1` only and is not protected against other programs on the same computer.
- Do not put filter chains in `shots.json`, and do not edit `shots.json` while the picker runs.
- Do not claim the result is good without looking at the draft (contact sheet or the video itself).
- Do not promise features the tool does not have: no HDR tone mapping, no face detection, no CapCut export, no sound effects. Linux and macOS are untested; Windows is the tested platform.
