"""Regression tests for the picker's attack surface (cross-site, path tricks, hostile bodies, UI sinks).

Each case was first tried by hand against a running server; these keep the observed refusals in place."""
import re
import socket
from pathlib import Path

import pytest

from test_server import TOKEN, call, good_state, js, srv  # noqa: F401  (srv is a fixture)

HTML = (Path(__file__).resolve().parent.parent / "picker" / "index.html").read_text(encoding="utf-8")


def raw(srv, data: bytes) -> bytes:
    s = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
    try:
        s.sendall(data)
        out = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                return out
            out += chunk
    finally:
        s.close()


@pytest.mark.parametrize("site", ["cross-site", "same-site", "CROSS-SITE"])
@pytest.mark.parametrize("path", ["/", "/api/state", "/music", "/proxy/a.mp4"])
def test_cross_site_fetches_are_refused(srv, site, path):
    status, _, body = call(srv, "GET", path, **{"Sec-Fetch-Site": site})
    assert status == 403 and js(body) == {"error": "bad fetch site"}


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_own_fetches_pass(srv, site):
    assert call(srv, "GET", "/proxy/a.mp4", **{"Sec-Fetch-Site": site})[0] == 200


@pytest.mark.parametrize("host", ["127.0.0.1.evil.example:{p}", "localhost.:{p}", "127.0.0.1:{p}.evil.example",
                                  "[::1]:{p}", "0.0.0.0:{p}", "127.1:{p}"])
def test_rebinding_hosts_are_refused(srv, host):
    assert call(srv, "GET", "/api/state", Host=host.format(p=srv.port))[0] == 403


def test_the_first_of_two_host_headers_decides(srv):
    resp = raw(srv, (f"GET /api/state HTTP/1.1\r\nHost: evil.example:{srv.port}\r\n"
                     f"Host: 127.0.0.1:{srv.port}\r\nConnection: close\r\n\r\n").encode())
    assert resp.startswith(b"HTTP/1.1 403")


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "PUT", "DELETE", "PATCH", "TRACE"])
def test_other_methods_are_refused_even_as_preflight(srv, method):
    status, headers, _ = call(srv, method, "/api/job", Origin=f"http://127.0.0.1:{srv.port}",
                              **{"Access-Control-Request-Method": "POST",
                                 "Access-Control-Request-Headers": "x-beatcut-token,content-type"})
    assert status == 403 and "Access-Control-Allow-Origin" not in headers


@pytest.mark.parametrize("path", [
    "/proxy/%252e%252e%252fsecret.txt",
    "/proxy/..%5c..%5cbeatcut%5csecret.txt",
    "/proxy/C:%5cWindows%5cwin.ini",
    "/proxy/C:/Windows/win.ini",
    "/proxy/%5c%5c127.0.0.1%5cc$%5cWindows%5cwin.ini",
    "/proxy//etc/passwd",
    "/proxy/a.mp4%00.txt",
    "/proxy/CON", "/proxy/NUL", "/proxy/aux.mp4",
    "/proxy/a.mp4::$DATA", "/proxy/a.mp4.", "/proxy/a.mp4%20", "/proxy/A.MP4", "/proxy/a.mp4/",
    "/music/../secret.txt", "/music%2f..%2fsecret.txt", "/./music",
])
def test_windows_path_tricks_are_not_served(srv, path):
    status, _, body = call(srv, "GET", path)
    assert status == 404 and b"secret" not in body


def test_post_without_content_length_is_refused(srv):
    resp = raw(srv, (f"POST /api/state HTTP/1.1\r\nHost: 127.0.0.1:{srv.port}\r\nX-Beatcut-Token: {TOKEN}\r\n"
                     "Content-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                     "2\r\n{}\r\n0\r\n\r\n").encode())
    assert resp.startswith(b"HTTP/1.1 403") and b"bad length" in resp


@pytest.mark.parametrize("body", [
    b'{"state": {"version": 1, "period": NaN, "t0": 0, "shots": []}}',
    b'{"state": {"version": 1, "period": 0.5, "t0": Infinity, "shots": []}}',
    b'{"state": {"version": 1, "period": 0.5, "t0": 1e999, "shots": []}}',
    b'{"state": {"version": 1, "period": 0.5, "t0": ' + b"9" * 5000 + b', "shots": []}}',
    b"[" * 100000 + b"]" * 100000,
    b'{"state": "\xff"}',
    b'{"state": {"version": 1, "period": 0.5, "t0": 0, "shots": []}, "state": 5}',
])
def test_hostile_bodies_are_refused_and_write_nothing(srv, body):
    assert call(srv, "POST", "/api/state", body)[0] == 400
    assert not srv.project.shots_path.exists()


@pytest.mark.parametrize("clip", ["../a.mp4", "..\\a.mp4", "sub/a.mp4", "\\\\host\\share\\a.mp4", "..", "."])
def test_clip_names_with_separators_are_refused(srv, clip):
    st = good_state()
    st["shots"][0]["clip"] = clip
    assert call(srv, "POST", "/api/state", {"state": st})[0] == 400
    assert not srv.project.shots_path.exists()


def test_duplicate_job_key_cannot_smuggle_a_name(srv):
    assert call(srv, "POST", "/api/job", b'{"job": "check", "job": "x"}')[0] == 400
    assert call(srv, "POST", "/api/job", b'{"job": "check\\u0000"}')[0] == 400
    assert srv.ran == []


def test_errors_do_not_echo_the_request(srv):
    status, _, body = call(srv, "GET", "/%3Cscript%3Ealert(1)%3C/script%3E")
    assert status == 404 and b"script" not in body.lower()
    resp = raw(srv, b"GET /<script>alert(1)</script> HTTP/1.1\r\nHost: \xff\r\n\r\n")
    assert b"<script>" not in resp


def test_every_response_carries_the_hardening_headers(srv):
    for path in ["/", "/api/state", "/proxy/a.mp4", "/music", "/nope"]:
        _, headers, _ = call(srv, "GET", path)
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Cache-Control"] == "no-store"
        assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert headers["X-Frame-Options"] == "DENY"
        assert "Access-Control-Allow-Origin" not in headers


# -- the page -----------------------------------------------------------------------------------

SCRIPT = re.search(r"<script>\s*'use strict';(.*?)</script>", HTML, re.S).group(1)


def test_page_has_no_attribute_or_url_sinks():
    for sink in ["setAttribute", ".href", "location.href =", "location =", "javascript:", ".srcdoc",
                 "cssText", "insertRule", "createContextualFragment", "DOMParser", "setTimeout('",
                 "setInterval('", "postMessage", "addEventListener('message'", "window.name"]:
        assert sink not in SCRIPT, sink


def test_the_only_src_assignment_is_the_encoded_proxy_url():
    assert re.findall(r"\.src\s*=\s*([^;]+);", SCRIPT) == ["want"]
    assert "'/proxy/' + encodeURIComponent(cur)" in SCRIPT


def test_language_comes_from_a_fixed_list():
    assert "if (!LANGS.includes(l)) l = storeGet('beatcut.lang');" in SCRIPT
    assert "return LANGS.includes(l) ? l : 'en';" in SCRIPT


def test_per_clip_maps_are_read_through_own():
    # S.bad and S.clipnotes come from a shared shots.json; reading them with a plain index would
    # follow __proto__ or constructor keys.
    assert re.findall(r"S\.clipnotes\[[^\]]+\]", SCRIPT) == ["S.clipnotes[cur]", "S.clipnotes[cur]"]
    assert "own(S.bad, cur, [])" in SCRIPT and "own(S.clipnotes, c.name, '')" in SCRIPT


# ---- security review follow-ups: H4 (request target form), H5 (script nonce) ----------------------

@pytest.mark.parametrize("target", ["http://evil.example/api/status", "http://127.0.0.1/api/status",
                                    "*", "api/status", "evil.example:80"])
def test_request_targets_not_starting_with_a_slash_are_refused(srv, target):
    resp = raw(srv, (f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1:{srv.port}\r\n"
                     f"Connection: close\r\n\r\n").encode())
    assert resp.startswith(b"HTTP/1.1 400"), resp[:80]


def test_origin_form_targets_still_work(srv):
    assert call(srv, "GET", "/api/status")[0] == 200


def test_index_uses_a_fresh_script_nonce_per_response(srv):
    seen = []
    for _ in range(2):
        status, headers, body = call(srv, "GET", "/")
        csp = headers["Content-Security-Policy"]
        m = re.search(r"script-src 'nonce-([A-Za-z0-9_-]{16,})'(;| |$)", csp)
        assert status == 200 and m, csp
        nonce = m.group(1)
        text = body.decode("utf-8")
        assert "unsafe-inline" not in csp.split("style-src")[0]
        assert "'self'" not in csp.split("script-src")[1].split(";")[0]
        tags = re.findall(r"<script\b[^>]*>", text)
        assert tags and all(f'nonce="{nonce}"' in t for t in tags), tags
        seen.append(nonce)
    assert seen[0] != seen[1]


def test_a_second_server_cannot_bind_the_same_port(srv):
    from picker.server import make_server

    with pytest.raises(OSError):
        make_server(srv.project, port=srv.port, start_proxies=False)
