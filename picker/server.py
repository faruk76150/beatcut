"""Local picker server. Standard library only.

It binds to 127.0.0.1 and treats every request as hostile until reject_reason() clears it:
a page on another site must not be able to read the project or start a render, neither
through a cross-site request nor through DNS rebinding.
"""
from __future__ import annotations

import codecs
import hmac
import json
import os
import queue
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from pipeline import grade, media, schema

MAX_BODY = 2 * 1024 * 1024
JOBS = ("check", "draft", "final")


def reject_reason(method, headers, port, token):
    """Why a request must be refused, or None when it may proceed.

    `headers` is any mapping with .get(); http.server passes a case-insensitive one."""
    if method not in ("GET", "POST"):
        return "bad method"
    host = (headers.get("Host") or "").lower()
    if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
        return "bad host"
    origin = headers.get("Origin")
    if origin is not None and origin.lower() not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
        return "bad origin"
    if method == "POST":
        sent = headers.get("X-Beatcut-Token") or ""
        if not hmac.compare_digest(sent.encode("utf-8"), token.encode("utf-8")):
            return "bad token"
        if not (headers.get("Content-Type") or "").lower().startswith("application/json"):
            return "bad content type"
        try:
            length = int(headers.get("Content-Length"))
        except (TypeError, ValueError):
            return "bad length"
        if length < 0 or length > MAX_BODY:
            return "bad length"
    return None


REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = Path(__file__).resolve().parent / "index.html"
TOKEN_PLACEHOLDER = "__BEATCUT_TOKEN__"
MAX_LOG = 200_000
MAX_KEPT_JOBS = 50
PROXY_WORKERS = 3
CHUNK = 64 * 1024

_SCRIPT_OPEN_RE = re.compile(r"<script(?=[\s>])", re.IGNORECASE)


def csp_for(nonce):
    """The Content-Security-Policy for one page response; only scripts carrying `nonce` may run."""
    return (f"default-src 'self'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self'; connect-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'")

MUSIC_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/mp4",
               ".flac": "audio/flac", ".ogg": "audio/ogg"}

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
_RANGE_RE = re.compile(r"^bytes=([0-9]{0,18})-([0-9]{0,18})$")
# Fetch metadata values a page of our own (or the user typing the URL) produces.
_OK_FETCH_SITES = ("same-origin", "none")


class App:
    """Everything the request handler needs. There is no module-level state."""

    def __init__(self, project, port, token, *, probe_fn, music_duration_fn, job_fn):
        self.project = project
        self.port = port
        self.token = token
        self.status: dict = {}
        self.jobs: dict = {}
        self.probe_fn = probe_fn
        self.music_duration_fn = music_duration_fn
        self.job_fn = job_fn
        self.clips: dict = {}            # clip file name -> Path, from the last scan
        self.lock = threading.RLock()    # status, jobs, clips, running processes
        self.state_lock = threading.Lock()
        self.meta_lock = threading.Lock()
        self.closed = threading.Event()
        self._procs: set = set()
        self._proxy_queue = None
        self._music_cache = None         # ((mtime_ns, size), duration)

    # -- clips ------------------------------------------------------------------------------

    def scan(self) -> list:
        clips = media.scan_clips(self.project)
        with self.lock:
            self.clips = {c.name: c for c in clips}
        return clips

    def find_clip(self, name):
        """The scanned clip whose file name equals `name` exactly, or None."""
        with self.lock:
            hit = self.clips.get(name)
        if hit is None:
            try:
                self.scan()
            except media.MediaError:
                return None
            with self.lock:
                hit = self.clips.get(name)
        return hit

    def proxy_path(self, clip_path) -> Path:
        return Path(self.project.proxy_dir) / f"{clip_path.name}.mp4"

    def clip_status(self, clip_path) -> str:
        with self.lock:
            known = self.status.get(clip_path.name)
        if known is not None:
            return known
        return "ok" if _proxy_is_fresh(clip_path, self.proxy_path(clip_path)) else "missing"

    def queue_proxies(self, clips) -> None:
        with self.lock:
            if self.closed.is_set():
                return
            if self._proxy_queue is None:
                self._proxy_queue = queue.Queue()
                for i in range(PROXY_WORKERS):
                    threading.Thread(target=self._proxy_worker, name=f"beatcut-proxy-{i}",
                                     daemon=True).start()
            for clip in clips:
                if clip.name not in self.status:
                    self.status[clip.name] = "queued"
                    self._proxy_queue.put(clip)

    def _proxy_worker(self) -> None:
        while not self.closed.is_set():
            clip = self._proxy_queue.get()
            if clip is None or self.closed.is_set():
                return
            try:
                make_proxy(self, clip)
            except Exception:  # a worker must survive any single clip
                self.set_status(clip.name, "fail")

    def set_status(self, name, value) -> None:
        with self.lock:
            self.status[name] = value

    # -- processes --------------------------------------------------------------------------

    def run_process(self, cmd, **kwargs):
        """Start `cmd` (a fixed argument list, never a shell) and remember it so close() can stop it."""
        with self.lock:
            if self.closed.is_set():
                raise RuntimeError("server is shutting down")
            proc = subprocess.Popen(cmd, shell=False, stdin=subprocess.DEVNULL, **kwargs)
            self._procs.add(proc)
        return proc

    def forget_process(self, proc) -> None:
        with self.lock:
            self._procs.discard(proc)

    def close(self) -> None:
        """Stop proxy workers and kill any running ffmpeg or job process."""
        with self.lock:
            self.closed.set()
            procs = list(self._procs)
            if self._proxy_queue is not None:
                for _ in range(PROXY_WORKERS):
                    self._proxy_queue.put(None)
        for proc in procs:
            try:
                proc.kill()
            except OSError:
                pass

    # -- jobs -------------------------------------------------------------------------------

    def start_job(self, name):
        """New job id, or None when another job is still running."""
        with self.lock:
            if any(not j["done"] for j in self.jobs.values()):
                return None
            job_id = secrets.token_hex(8)
            self.jobs[job_id] = {"name": name, "log": "", "done": False, "rc": None}
            done = [k for k, j in self.jobs.items() if j["done"]]
            for k in done[:max(0, len(self.jobs) - MAX_KEPT_JOBS)]:
                del self.jobs[k]
        threading.Thread(target=self._run_job, args=(job_id, name), name=f"beatcut-job-{job_id}",
                         daemon=True).start()
        return job_id

    def _run_job(self, job_id, name) -> None:
        try:
            self.job_fn(self, job_id, name)
        except Exception as exc:
            self.append_log(job_id, f"\njob failed to run: {type(exc).__name__}\n")
            with self.lock:
                self.jobs[job_id].update(rc=-1)
        finally:
            with self.lock:
                self.jobs[job_id]["done"] = True

    def append_log(self, job_id, text) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job["log"] = (job["log"] + text)[-MAX_LOG:]

    def job_info(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            return {k: job[k] for k in ("name", "log", "done", "rc")}

    # -- api/state --------------------------------------------------------------------------

    def music_duration(self):
        music = Path(self.project.music)
        try:
            st = music.stat()
        except OSError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        with self.lock:
            if self._music_cache is not None and self._music_cache[0] == key:
                return self._music_cache[1]
        value = self.music_duration_fn(music)
        with self.lock:
            self._music_cache = (key, value)
        return value

    def clip_list(self):
        """(clips for the API, error message or None)."""
        try:
            clips = self.scan()
        except media.MediaError as exc:
            return [], str(exc)
        metas, errors = {}, {}
        with self.meta_lock:
            try:
                metas = media.clip_meta(self.project, clips, probe_fn=self.probe_fn, errors=errors)
            except Exception:
                # One unreadable clip must not hide the others; probe them one by one, uncached.
                for clip in clips:
                    try:
                        metas[clip.name] = dict(self.probe_fn(clip))
                    except Exception as exc:
                        errors[clip.name] = str(exc) or type(exc).__name__
        if not self.closed.is_set() and self._proxy_queue is not None:
            self.queue_proxies(clips)
        out = []
        for clip in clips:
            meta = metas.get(clip.name) or {}
            item = {"name": clip.name, "duration": meta.get("duration"), "w": meta.get("width"),
                    "h": meta.get("height"), "fps": meta.get("fps"), "status": self.clip_status(clip)}
            if clip.name in errors:
                item["error"] = errors[clip.name]
            out.append(item)
        return out, None

    def profile_names(self):
        return grade.profile_names(self.project.profiles)

    def read_state(self):
        path = Path(self.project.shots_path)
        with self.state_lock:
            if not path.exists():
                return schema.empty_state(), []
            try:
                return schema.load_shots(path, self.profile_names()), []
            except schema.SchemaError as exc:
                return schema.empty_state(), list(exc.errors)

    def write_state(self, state):
        """[] after saving, or the validation errors (nothing written)."""
        errors = schema.validate_shots(state, self.profile_names())
        if errors:
            return errors
        with self.state_lock:
            schema.save_shots(self.project.shots_path, state)
        return []

    def api_state(self):
        clips, clips_error = self.clip_list()
        state, state_errors = self.read_state()
        project = self.project
        data = {
            "project": {"name": project.out_name, "fps": project.fps_float,
                        "profiles": sorted(self.profile_names()), "jobs": list(JOBS)},
            "clips": clips,
            "state": state,
            "state_errors": state_errors,
            "music_duration": self.music_duration(),
        }
        if clips_error:
            data["clips_error"] = clips_error
        return data


def _proxy_is_fresh(src, target) -> bool:
    try:
        return Path(target).stat().st_mtime_ns >= Path(src).stat().st_mtime_ns
    except OSError:
        return False


def _replace(src, dst, attempts=10) -> None:
    """os.replace, retried briefly: on Windows it fails while a reader still has `dst` open."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.2)


def make_proxy(app, clip_path) -> None:
    """Write proxy_dir/<clip name>.mp4 unless it exists and is newer than the source."""
    clip_path = Path(clip_path)
    name = clip_path.name
    target = app.proxy_path(clip_path)
    if _proxy_is_fresh(clip_path, target):
        app.set_status(name, "ok")
        return
    app.set_status(name, "working")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".proxy-", suffix=".mp4")
    os.close(fd)
    ok = False
    try:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", os.path.abspath(clip_path),
               "-vf", "scale='if(gt(iw,ih),960,-2)':'if(gt(iw,ih),-2,960)'", "-an",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-g", "12", "-keyint_min", "12",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp]
        proc = app.run_process(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            rc = proc.wait()
        finally:
            app.forget_process(proc)
        if rc == 0 and os.path.getsize(tmp) > 0:
            _replace(tmp, target)
            ok = True
    except (OSError, RuntimeError):
        ok = False
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    app.set_status(name, "ok" if ok else "fail")


def run_job(app, job_id, name) -> None:
    """Run `beatcut.py <name> <project root>` and collect its combined output into the job log."""
    if name not in JOBS:  # the handler checks this too; never trust a single gate
        raise ValueError("unknown job")
    cmd = [sys.executable, str(REPO_ROOT / "beatcut.py"), name, str(Path(app.project.root).resolve())]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        proc = app.run_process(cmd, cwd=str(REPO_ROOT), env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    except (OSError, RuntimeError) as exc:
        app.append_log(job_id, f"could not start: {exc}\n")
        with app.lock:
            app.jobs[job_id].update(done=True, rc=-1)
        return
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while True:
            chunk = proc.stdout.read1(4096)
            if not chunk:
                break
            app.append_log(job_id, decoder.decode(chunk))
        app.append_log(job_id, decoder.decode(b"", final=True))
        rc = proc.wait()
    finally:
        proc.stdout.close()
        app.forget_process(proc)
    with app.lock:
        app.jobs[job_id].update(done=True, rc=rc)


def _reject_constant(name):
    raise ValueError(f"{name} is not allowed")


def _parse_range(header, size):
    """(start, end) inclusive, None for no usable Range header, or "bad" for a 416."""
    if header is None:
        return None
    m = _RANGE_RE.match(header.strip())
    if not m:
        return "bad"
    a, b = m.group(1), m.group(2)
    if a == "" and b == "":
        return "bad"
    if a == "":
        n = int(b)
        if n == 0 or size == 0:
            return "bad"
        return max(0, size - n), size - 1
    start = int(a)
    end = size - 1 if b == "" else min(int(b), size - 1)
    if start >= size or (b != "" and int(b) < start):
        return "bad"
    return start, end


class Handler(BaseHTTPRequestHandler):
    server_version = "beatcut"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 60
    app: App = None  # set on the per-server subclass

    def log_message(self, format, *args):  # noqa: A002 - signature from the base class
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    # -- responses --------------------------------------------------------------------------

    def _send(self, status, body: bytes, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=True).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status, message):
        self._json(status, {"error": message})

    def send_error(self, code, message=None, explain=None):
        # Used by http.server for malformed requests; answer in the same plain JSON form.
        # The request line is never echoed back; only the standard reason phrase is sent.
        self.close_connection = True
        try:
            self._error(code, self.responses.get(code, ("error",))[0].lower())
        except OSError:
            pass

    def _not_found(self):
        self._error(404, "not found")

    def _guard(self):
        """True when the request may proceed; otherwise a 400 or 403 has been sent."""
        if not self.path.startswith("/"):    # absolute URIs, "*" and authority forms are never served
            self.close_connection = True
            self._error(400, "bad request target")
            return False
        reason = reject_reason(self.command, self.headers, self.app.port, self.app.token)
        if reason is None:
            site = self.headers.get("Sec-Fetch-Site")
            if site is not None and site.lower() not in _OK_FETCH_SITES:
                reason = "bad fetch site"
        if reason is None:
            return True
        self.close_connection = True
        self._error(403, reason)
        return False

    def _send_file(self, path, ctype):
        try:
            f = open(path, "rb")
        except OSError:
            return self._not_found()
        with f:
            try:
                size = os.fstat(f.fileno()).st_size
            except OSError:
                return self._not_found()
            rng = _parse_range(self.headers.get("Range"), size)
            if rng == "bad":
                return self._send(416, b"", ctype, {"Content-Range": f"bytes */{size}",
                                                    "Accept-Ranges": "bytes"})
            if rng is None:
                start, end, status, extra = 0, size - 1, 200, {}
            else:
                start, end = rng
                status, extra = 206, {"Content-Range": f"bytes {start}-{end}/{size}"}
            length = max(0, end - start + 1)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            f.seek(start)
            remaining = length
            while remaining > 0:
                data = f.read(min(CHUNK, remaining))
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)
            if remaining:
                self.close_connection = True  # the file shrank; the client must not reuse this stream

    # -- methods ----------------------------------------------------------------------------

    def _refuse_method(self):
        self._guard()  # always refuses: reject_reason only lets GET and POST through

    do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = _refuse_method

    def do_GET(self):
        if not self._guard():
            return
        app = self.app
        url = urlsplit(self.path)
        path = url.path
        if path == "/":
            try:
                html = INDEX_PATH.read_text(encoding="utf-8")
            except OSError:
                return self._error(500, "index.html is missing")
            nonce = secrets.token_urlsafe(16)
            html = html.replace(TOKEN_PLACEHOLDER, app.token)
            # The page source has bare <script> tags; each served copy gets this response's nonce.
            body = _SCRIPT_OPEN_RE.sub(f'<script nonce="{nonce}"', html).encode("utf-8")
            return self._send(200, body, "text/html; charset=utf-8",
                              {"Content-Security-Policy": csp_for(nonce)})
        if path == "/api/state":
            return self._json(200, app.api_state())
        if path == "/api/status":
            with app.lock:
                status = dict(app.status)
            return self._json(200, status)
        if path == "/api/job":
            ids = parse_qs(url.query).get("id") or [""]
            info = app.job_info(ids[0])
            if info is None:
                return self._not_found()
            return self._json(200, info)
        if path == "/music":
            music = Path(app.project.music)
            return self._send_file(music, MUSIC_TYPES.get(music.suffix.lower(), "application/octet-stream"))
        if path.startswith("/proxy/"):
            requested = unquote(path[len("/proxy/"):])
            clip = app.find_clip(requested) if requested else None
            if clip is None:
                return self._not_found()
            # The path is built only from the scanned clip's own name, never from the request.
            return self._send_file(app.proxy_path(clip), "video/mp4")
        return self._not_found()

    def do_POST(self):
        if not self._guard():
            return
        path = urlsplit(self.path).path
        if path not in ("/api/state", "/api/job"):
            self.close_connection = True
            return self._not_found()
        length = int(self.headers.get("Content-Length"))
        raw = self.rfile.read(length) if length else b""
        if len(raw) != length:
            self.close_connection = True
            return self._error(400, "incomplete body")
        try:
            data = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return self._error(400, "body is not valid JSON")
        if not isinstance(data, dict):
            return self._error(400, "body must be a JSON object")
        if path == "/api/state":
            if "state" not in data:
                return self._json(400, {"errors": ["state: required"]})
            try:
                errors = self.app.write_state(data["state"])
            except (OSError, RecursionError, ValueError):
                return self._error(500, "could not save shots.json")
            if errors:
                return self._json(400, {"errors": errors})
            return self._json(200, {"ok": True})
        name = data.get("job")
        if not isinstance(name, str) or name not in JOBS:
            return self._error(400, "unknown job")
        job_id = self.app.start_job(name)
        if job_id is None:
            return self._error(409, "a job is already running")
        return self._json(200, {"id": job_id})


class PickerServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that refuses a port another process already holds.

    On Windows SO_REUSEADDR lets a second process bind a port that is in use, so there the
    socket is bound with SO_EXCLUSIVEADDRUSE instead. Elsewhere SO_REUSEADDR only skips
    TIME_WAIT and is kept."""

    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(project, port=8765, token=None, *, probe_fn=media.probe,
                music_duration_fn=media.probe_duration, job_fn=run_job, start_proxies=True):
    """Bind ("127.0.0.1", port) and return (server, app). Port 0 picks a free port."""
    if token is None:
        token = secrets.token_urlsafe(32)
    if not isinstance(token, str) or not _TOKEN_RE.match(token):
        raise ValueError("token must be 16 to 256 characters of [A-Za-z0-9_-]")
    app = App(project, port, token, probe_fn=probe_fn, music_duration_fn=music_duration_fn, job_fn=job_fn)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = PickerServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    app.port = server.server_address[1]
    try:
        clips = app.scan()
    except media.MediaError:
        clips = []
    if start_proxies:
        app.queue_proxies(clips)
    return server, app


def serve(project_root, port=8765, open_browser=True) -> None:
    """Run the picker for the project at `project_root` until Ctrl+C."""
    import webbrowser

    from pipeline.config import load_project

    project = load_project(project_root)
    media.require_tools()
    server, app = make_server(project, port=port)
    url = f"http://127.0.0.1:{app.port}/"
    print(f"beatcut picker: {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopping", flush=True)
    finally:
        app.close()
        server.server_close()
