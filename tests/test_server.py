import http.client
import json
import threading
import time
from types import SimpleNamespace

import pytest

from pipeline.config import parse_project
from pipeline.schema import empty_state
from picker.server import MAX_BODY, make_server

TOKEN = "t" * 43
PROXY_BYTES = bytes(range(256)) * 8


@pytest.fixture
def srv(tmp_path):
    clips = tmp_path / "clips"
    root = clips / "beatcut"
    (root / "proxy").mkdir(parents=True)
    (clips / "a.mp4").write_bytes(b"x")
    (clips / "b.MOV").write_bytes(b"x")
    (root / "music.mp3").write_bytes(b"ID3" + b"0" * 2000)
    (root / "proxy" / "a.mp4.mp4").write_bytes(PROXY_BYTES)
    (root / "secret.txt").write_text("secret", encoding="utf-8")
    project = parse_project({"music": "music.mp3"}, root)
    gate = threading.Event()
    gate.set()
    ran = []

    def fake_probe(path):
        return {"duration": 10.0, "width": 1920, "height": 1080, "fps": 30.0, "rotation": 0}

    def fake_job(app, job_id, name):
        gate.wait(5)
        ran.append(name)
        app.jobs[job_id].update(log="finished\n", done=True, rc=0)

    server, app = make_server(project, port=0, token=TOKEN, probe_fn=fake_probe,
                              music_duration_fn=lambda p: 123.0, job_fn=fake_job, start_proxies=False)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield SimpleNamespace(port=app.port, project=project, app=app, ran=ran, gate=gate)
    gate.set()
    server.shutdown()
    server.server_close()


def call(srv, method, path, body=None, **headers):
    h = {"Host": f"127.0.0.1:{srv.port}"}
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        h.update({"X-Beatcut-Token": TOKEN, "Content-Type": "application/json"})
    h.update(headers)
    h = {k: v for k, v in h.items() if v is not None}
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    try:
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        conn.close()


def js(raw):
    return json.loads(raw.decode("utf-8"))


def good_state():
    st = empty_state()
    st["shots"] = [{"clip": "a.mp4", "in": 1.0, "out": 3.0, "beats": 4}]
    return st


def test_index_carries_the_token_and_security_headers(srv):
    status, headers, body = call(srv, "GET", "/")
    text = body.decode("utf-8")
    assert status == 200 and TOKEN in text and "__BEATCUT_TOKEN__" not in text
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in headers


def test_state_lists_clips_and_project(srv):
    status, _, body = call(srv, "GET", "/api/state")
    data = js(body)
    assert status == 200
    assert [c["name"] for c in data["clips"]] == ["a.mp4", "b.MOV"]
    assert data["clips"][0]["duration"] == 10.0 and data["clips"][0]["w"] == 1920
    assert data["project"]["jobs"] == ["check", "draft", "final"]
    assert "neutral" in data["project"]["profiles"]
    assert data["state"] == empty_state() and data["state_errors"] == []
    assert data["music_duration"] == 123.0


def test_foreign_host_and_origin_are_refused(srv):
    assert call(srv, "GET", "/api/state", Host="evil.example")[0] == 403
    assert call(srv, "GET", "/", Host=f"evil.example:{srv.port}")[0] == 403
    assert call(srv, "GET", "/api/state", Origin="http://evil.example")[0] == 403
    assert call(srv, "GET", "/music", Origin="http://evil.example")[0] == 403


def test_post_without_token_is_refused_and_writes_nothing(srv):
    body = {"state": good_state()}
    assert call(srv, "POST", "/api/state", body, **{"X-Beatcut-Token": None})[0] == 403
    assert call(srv, "POST", "/api/state", body, **{"X-Beatcut-Token": "wrong"})[0] == 403
    assert call(srv, "POST", "/api/state", body, Origin="http://evil.example")[0] == 403
    assert call(srv, "POST", "/api/state", body, **{"Content-Type": "text/plain"})[0] == 403
    assert call(srv, "POST", "/api/job", {"job": "draft"}, **{"X-Beatcut-Token": None})[0] == 403
    assert not srv.project.shots_path.exists() and srv.ran == []


def test_state_is_saved_with_a_backup(srv):
    assert call(srv, "POST", "/api/state", {"state": good_state()})[0] == 200
    saved = json.loads(srv.project.shots_path.read_text(encoding="utf-8"))
    assert saved["shots"][0]["clip"] == "a.mp4"
    second = good_state()
    second["period"] = 0.6
    assert call(srv, "POST", "/api/state", {"state": second})[0] == 200
    assert json.loads(srv.project.shots_path.read_text(encoding="utf-8"))["period"] == 0.6
    assert json.loads((srv.project.root / "shots.json.bak").read_text(encoding="utf-8"))["period"] == 0.5
    assert js(call(srv, "GET", "/api/state")[2])["state"]["period"] == 0.6


def test_invalid_state_is_refused_and_the_file_is_untouched(srv):
    call(srv, "POST", "/api/state", {"state": good_state()})
    before = srv.project.shots_path.read_bytes()
    bad = good_state()
    bad["shots"][0]["beats"] = 0
    bad["shots"][0]["prof"] = "nope"
    status, _, body = call(srv, "POST", "/api/state", {"state": bad})
    assert status == 400 and len(js(body)["errors"]) == 2
    assert call(srv, "POST", "/api/state", {"nope": 1})[0] == 400
    assert call(srv, "POST", "/api/state", b"{not json")[0] == 400
    assert srv.project.shots_path.read_bytes() == before


def test_invalid_file_on_disk_is_reported(srv):
    srv.project.shots_path.write_text('{"version": 9}', encoding="utf-8")
    data = js(call(srv, "GET", "/api/state")[2])
    assert data["state"] == empty_state() and data["state_errors"]


def test_oversized_body_is_refused(srv):
    status, _, _ = call(srv, "POST", "/api/state", b"{}", **{"Content-Length": str(MAX_BODY + 1)})
    assert status == 403


def test_jobs_are_a_fixed_list(srv):
    for name in ["rm -rf /", "draft; calc", "../beatcut", "", None, ["draft"]]:
        assert call(srv, "POST", "/api/job", {"job": name})[0] == 400
    assert srv.ran == []


def test_job_runs_and_reports(srv):
    status, _, body = call(srv, "POST", "/api/job", {"job": "check"})
    assert status == 200
    job_id = js(body)["id"]
    for _ in range(40):
        info = js(call(srv, "GET", f"/api/job?id={job_id}")[2])
        if info["done"]:
            break
        time.sleep(0.05)
    assert info == {"name": "check", "log": "finished\n", "done": True, "rc": 0}
    assert srv.ran == ["check"]
    assert call(srv, "GET", "/api/job?id=nope")[0] == 404


def test_only_one_job_at_a_time(srv):
    srv.gate.clear()
    assert call(srv, "POST", "/api/job", {"job": "draft"})[0] == 200
    assert call(srv, "POST", "/api/job", {"job": "check"})[0] == 409
    srv.gate.set()


def test_proxy_is_served_with_ranges(srv):
    status, headers, body = call(srv, "GET", "/proxy/a.mp4")
    assert status == 200 and body == PROXY_BYTES and headers["Content-Type"] == "video/mp4"
    status, headers, body = call(srv, "GET", "/proxy/a.mp4", Range="bytes=10-19")
    assert status == 206 and body == PROXY_BYTES[10:20]
    assert headers["Content-Range"] == f"bytes 10-19/{len(PROXY_BYTES)}"


@pytest.mark.parametrize("path", [
    "/proxy/b.MOV",                      # known clip, proxy not made yet
    "/proxy/unknown.mp4",
    "/proxy/../secret.txt",
    "/proxy/..%2Fsecret.txt",
    "/proxy/..%5Csecret.txt",
    "/proxy/%2e%2e/%2e%2e/a.mp4",
    "/secret.txt",
    "/project.json",
    "/shots.json",
    "/api/nope",
])
def test_nothing_else_is_served(srv, path):
    status, _, body = call(srv, "GET", path)
    assert status == 404 and b"secret" not in body


def test_music_is_served(srv):
    status, headers, body = call(srv, "GET", "/music")
    assert status == 200 and body.startswith(b"ID3") and headers["Content-Type"] == "audio/mpeg"
    status, _, body = call(srv, "GET", "/music", Range="bytes=0-2")
    assert status == 206 and body == b"ID3"


@pytest.mark.slow
def test_real_proxies_are_built(tmp_path):
    from pipeline.config import load_project
    from util import make_test_project

    root = make_test_project(tmp_path)
    server, app = make_server(load_project(root), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for _ in range(240):
            if app.status and all(v == "ok" for v in app.status.values()):
                break
            time.sleep(0.25)
        assert app.status == {"tall30.mp4": "ok", "wide30.mp4": "ok", "wide60.mp4": "ok"}
        assert (root / "proxy" / "tall30.mp4.mp4").stat().st_size > 1000
    finally:
        server.shutdown()
        server.server_close()
