English | [Türkçe](README.tr.md)

# beatcut

beatcut cuts a short video to the beat of a music track. You browse a folder of clips in a local picker page in your browser, choose an in point, an out point and a beat count for each shot, and beatcut writes the choices to `shots.json` and renders the video with ffmpeg. Every shot starts and ends on a whole output frame, so the picture stays in step with the music.

It is a Claude Code skill (see `SKILL.md`) and also a plain command line tool: everything below works without Claude.

![beatcut demo](docs/demo.gif)

## Requirements

- Python 3.10 or newer
- `ffmpeg` and `ffprobe` on your `PATH` (not included in this repository)
- Optional: an NVIDIA graphics card. With `encoder` set to `auto` (the default), beatcut uses NVENC if ffmpeg lists it and a one-frame trial encode succeeds; otherwise it uses the CPU.

## Install

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` lists `librosa`, `numpy` and `soundfile`; they are needed for beat detection (`beats`).

### Install as a Claude Code skill

Clone the repository into your personal skills folder, `~/.claude/skills/beatcut` (on Windows `%USERPROFILE%\.claude\skills\beatcut`), or into a project's `.claude/skills/beatcut` folder:

```
git clone https://github.com/faruk76150/beatcut.git ~/.claude/skills/beatcut
```

Then create the virtual environment and install the requirements inside that folder, as shown above. Claude Code finds the skill through `SKILL.md`, which tells it to run `beatcut.py` from the folder the skill is installed in.

## Try the demo

```
python beatcut.py check examples/demo/beatcut
python beatcut.py draft examples/demo/beatcut
```

The draft is written to `examples/demo/beatcut/out/demo_draft.mp4`.

## Start with your own clips

Run the commands from the repository folder. Every command takes a project folder (the `beatcut` folder that `init` creates), except `init` and `sheet`.

| Command | What it does | Exit codes |
|---|---|---|
| `python beatcut.py init <clips folder> --music <file>` | Creates `<clips folder>/beatcut/`, copies the music file into it, and writes `project.json` (every default spelled out) and an empty `shots.json`. Prints `created: <folder>`. Refuses, changing nothing, if `project.json` already exists. | 0 done; 1 clips folder or music file missing, or project exists |
| `python beatcut.py beats <project>` | Analyses the music and writes `period` and `t0` to `shots.json` (other fields are kept; the old file is saved as `shots.json.bak`). Prints `period`, `t0` and `bpm`. Needs `librosa`. | 0 done; 1 music file missing or fewer than 4 beats found; 2 beat detection unavailable |
| `python beatcut.py pick <project> [--port N] [--no-open]` | Starts the picker (default port 8765), prints `beatcut picker: http://127.0.0.1:<port>/  (Ctrl+C to stop)` and opens your browser unless `--no-open` is given. Stop it with Ctrl+C. | 0 after Ctrl+C |
| `python beatcut.py check <project>` | Validates `project.json`, `shots.json`, the clips and the plan. Warnings and errors go to stderr, one per line. Prints `ok: <n> shots, <s> s` when there are no errors. | 0 ok (warnings allowed); 1 problems in your input; 2 ffmpeg missing or other run-time failure |
| `python beatcut.py draft <project>` | Runs the same check, then renders `out/<out_name>_draft.mp4` at the `draft` size. Prints `encoder: <cpu or nvenc>`, `segment i/n` lines and the output path. | as `check`; 2 also when another render holds the project |
| `python beatcut.py final <project>` | Same, at the `final` size, into `out/<out_name>_final.mp4`. | as `draft` |
| `python beatcut.py sheet <video> [--out file.jpg]` | Writes a 5 by 4 contact sheet (20 evenly spaced frames) as a JPEG. Default: `<video name>_sheet.jpg` in the current folder. Prints the path. | 0 done; 1 file not found; 2 ffmpeg problem |

A usual session: `init`, `beats`, `pick`, `check`, `draft`, look at the result (`sheet`), then `final`. A project is always `<clips folder>/beatcut`, so pass that folder, not the clips folder.

`draft` and `final` refuse to start when `check` would report an error. Draft output is H.264; final output is H.265 (`hvc1`) on the CPU or NVENC, with AAC audio. Both are tagged BT.709. The video fades in from black over the first 30 frames and fades out over the last `tail` seconds.

### Project folder

```
<clips folder>/
  clip1.mp4 ...        your source clips (never modified)
  beatcut/
    project.json       settings
    shots.json         the edit
    <music file>       copied by init
    shots.json.bak     previous version, made on every save
    proxy/             small preview copies, made by the picker
    work/              intermediate segments and caches
    out/               finished videos
```

Clips are read only from `clips_dir` itself (no subfolders).

### project.json

| Field | Meaning | Default |
|---|---|---|
| `clips_dir` | Folder with the clips, relative to the project folder | `..` |
| `extensions` | File extensions to scan, case-insensitive | `[".mp4", ".mov", ".mxf"]` |
| `exclude_prefix` | Files whose names start with any of these are skipped | `[]` |
| `music` | Music file, relative to the project folder (no absolute path, no `..`) | required |
| `fps` | Output frame rate, as `"25"` or `"30000/1001"` | `"30000/1001"` |
| `draft` | Draft size; width and height, both positive even integers | `{"width": 1920, "height": 1080}` |
| `final` | Final size, same rules | `{"width": 3840, "height": 2160}` |
| `fill` | When the source shape differs from the output: `cover` or `blur` | `cover` |
| `encoder` | `auto`, `cpu` or `nvenc` (`nvenc` fails if unavailable) | `auto` |
| `loudness` | Target loudness in LUFS, from -40 to -5 | `-15` |
| `profiles` | Extra colour profiles: name to ffmpeg filter chain | `{}` |
| `out_name` | Start of the output file name | the clips folder's name (for a `<clips>/beatcut` project), otherwise the project folder's name |

Unknown keys are an error. `init` writes every field except `out_name`.

### shots.json

| Field | Meaning | Default |
|---|---|---|
| `version` | Must be `1` | required |
| `period` | Seconds per beat, more than 0.05 and less than 10 | required |
| `t0` | Second of the first beat in the music, 0 or more | required |
| `tail` | Seconds after the last beat, 0 to 30. The last shot is extended by it and the picture fades out over it | `1.5` |
| `shots` | Ordered list, at most 2000 | required |
| `shots[].clip` | File name in `clips_dir`, with extension; no path separators, no `:` | required |
| `shots[].in`, `shots[].out` | Source seconds; `in` 0 or more, `out` greater than `in` | required |
| `shots[].beats` | Whole number from 1 to 512 | required |
| `shots[].fit` | `fit` (change speed to fill the beats) or `cut` (play at 1x, cut at the beat count) | `fit` |
| `shots[].interp` | Slow-motion frames: `oflow`, `blend`, `none`; leave out for automatic | automatic |
| `shots[].prof` | Colour profile name | `neutral` |
| `shots[].tin` | Transition into the shot: `cut`, `fade`, `fadewhite` | `cut` |
| `shots[].tdur` | Transition length in seconds, 0 to 5 | `0.5` |
| `shots[].note` | Free note, up to 2000 characters; no effect on the render | `""` |
| `bad` | Clip name to a list of `{"a", "b", "note"}` ranges (seconds) you do not want to use; `check` warns if a shot overlaps one | `{}` |
| `clipnotes` | Clip name to a note | `{}` |
| `markers` | `{"beat", "title"}` labels shown on the picker timeline; no effect on the render | `[]` |

Keys the program does not know are kept when the picker or `beats` saves the file.

### Speed and frame accuracy

In `fit` mode the speed is `(out - in) / (beats * period)`; a speed within 0.015 of 1 is treated as exactly 1, and `check` rejects speeds outside 0.1x to 8x. Each beat boundary is rounded to a whole output frame and each segment is rendered with an exact frame count, so the picture cannot drift from the music over a long edit.

The first shot starts at the first frame of the video, so it also covers the time before the first beat; it reads on past its `out` point to fill that, and `check` tells you if the clip is too short for it.

### Transitions

`cut` is the default. `fade` is a dissolve and `fadewhite` a flash through white; both are centred on the beat boundary, and `tdur` is rounded to an even number of frames (a length that rounds to under 2 frames becomes a cut). A transition longer than a shot is an error.

### Slow motion

When a `fit` shot runs slower than 0.97x and the source frame rate times the speed falls below the output frame rate, beatcut makes in-between frames: `blend` in a draft, `oflow` (optical flow) in a final. Otherwise it makes none. Set `interp` on a shot to override this. `cut` shots and shots that speed up never use it.

### Colour profiles

Built in: `neutral` (no change), `warm`, `cool`, `contrast`, `golden`. Add your own in `project.json`:

```json
"profiles": { "teal": "colorbalance=bs=.03:bm=.03,eq=saturation=1.1" }
```

A profile is one ffmpeg filter chain, applied to RGB frames. Names match `[a-z0-9_-]` (1 to 32 characters); chains must not contain `;`, `[` or `]`. A custom profile with a built-in name replaces it. `check` has ffmpeg try each profile in use. Filters live only in `project.json`; `shots.json` carries names, so a `shots.json` from someone else cannot run filters.

### Orientation and framing

Output size comes from `draft` and `final`, so vertical output (for example 1080 by 1920) works. Source rotation metadata is respected. If the source shape differs from the output, `fill` decides: `cover` crops to fill the frame, `blur` fits the whole clip and fills the empty space with a blurred copy of it. It applies to the whole project.

## The picker

`pick` serves a page at `http://127.0.0.1:<port>/`. Pick a clip on the left, set in and out on the scrub bar or with the keys, choose a beat count (the chips show the resulting speed), and add the shot. Changes save to `shots.json` automatically. The page also edits period, first beat and tail, reorders shots on the timeline by dragging (drag a block's right edge to change its beats), marks bad ranges, and runs `check`, `draft` and `final` from the header. The language follows `?lang=en` or `?lang=tr` in the address, then your last choice, then the browser language.

| Key | Action |
|---|---|
| Space | Play or pause |
| Left / Right | Back or forward 0.5 s |
| Shift + Left / Right | Back or forward 5 s |
| Alt + Left / Right, `,` `.` | Back or forward one frame |
| I | Set the in point to the playhead |
| O | Set the out point to the playhead |
| A | Add the shot (when no shot is selected) |
| X | Mark the range as bad |
| P | Play the range at the computed speed |
| `[` `]` or Up / Down | Previous or next clip |
| Delete | Delete the selected shot |
| Esc | Deselect (new shot) or close a dialog |
| Ctrl + Z | Undo (80 steps) |
| ? | Show or hide the shortcut list |

Previews are small copies made in `proxy/` (960 px, no audio); the first run converts every clip in the background.

## Security note

The picker listens only on `127.0.0.1`. It checks the `Host` and `Origin` headers, refuses requests from other sites, and requires a random session key (put in the page when it is served) on every request that changes anything. This stops a web page you have open from reading your project or starting a render. It does not protect against other programs running on the same computer, which can read the project data and fetch the page and its key. Do not expose the server to a network (no port forwarding, tunnels or reverse proxies).

## Limits

- Tested on Windows only. Linux and macOS are untested.
- HDR sources are not tone-mapped. `check` warns when a used clip is HDR; colours will look washed out. Output is always tagged BT.709; sources with a BT.2020 or BT.601 matrix are converted to BT.709.
- Very long edits cannot be joined in one pass. `check` reports `too many shots to assemble in one pass` (the limit is a command line length, so it is hundreds of shots with fades and over a thousand with only cuts) and you must split the edit.
- One render per project at a time; a second one stops with `another render is already running for this project`. The picker runs one job at a time.
- The edit must not be longer than the music.
- Beat detection assumes a steady tempo; check `period` and `t0` in the picker and correct them by hand if needed.

## Roadmap

Not in the first version:

- CapCut draft export
- Face detection and recognition
- Horizon levelling
- Finding a seam to shorten the music
- Sound effect generation and placement
- Tone mapping for HDR sources
- Converting projects from older tools
- A PyPI package and a Claude Code plugin marketplace listing

## Tests

```
pip install -r requirements-dev.txt
python -m pytest
```

This runs the fast tests. The slow tests render real video and need ffmpeg: `python -m pytest -m slow`.

## License

The code is MIT licensed (see `LICENSE`). The video clips in `examples/demo/` (`*.mp4`) and `docs/demo.gif`, which is made from them, are not covered by the MIT license; they are the repository owner's own footage, all rights reserved, and are included only so the tool can be tried. The demo music, "Our expanse" by Bobjt, is CC0 1.0 (public domain). See `examples/demo/CREDITS.md` for details.
