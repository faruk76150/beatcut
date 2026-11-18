import pytest

from picker.server import MAX_BODY, reject_reason

TOKEN = "t" * 43
PORT = 8765


def get(**over):
    h = {"Host": f"127.0.0.1:{PORT}"}
    h.update(over)
    return {k: v for k, v in h.items() if v is not None}


def post(**over):
    h = {"Host": f"127.0.0.1:{PORT}", "X-Beatcut-Token": TOKEN, "Content-Type": "application/json",
         "Content-Length": "2"}
    h.update(over)
    return {k: v for k, v in h.items() if v is not None}


def test_local_requests_pass():
    assert reject_reason("GET", get(), PORT, TOKEN) is None
    assert reject_reason("GET", get(Host=f"localhost:{PORT}"), PORT, TOKEN) is None
    assert reject_reason("GET", get(Host=f"LOCALHOST:{PORT}"), PORT, TOKEN) is None
    assert reject_reason("POST", post(), PORT, TOKEN) is None
    assert reject_reason("POST", post(**{"Content-Type": "application/json; charset=utf-8"}), PORT, TOKEN) is None


@pytest.mark.parametrize("host", ["evil.example:8765", "127.0.0.1:9999", "127.0.0.1", "evil.127.0.0.1:8765", "", None])
def test_foreign_host_is_rejected(host):
    assert reject_reason("GET", get(Host=host), PORT, TOKEN) == "bad host"
    assert reject_reason("POST", post(Host=host), PORT, TOKEN) == "bad host"


@pytest.mark.parametrize("origin", ["http://evil.example", "https://127.0.0.1:8765", "http://127.0.0.1:9999", "null"])
def test_foreign_origin_is_rejected(origin):
    assert reject_reason("GET", get(Origin=origin), PORT, TOKEN) == "bad origin"
    assert reject_reason("POST", post(Origin=origin), PORT, TOKEN) == "bad origin"


def test_own_origin_passes():
    assert reject_reason("POST", post(Origin=f"http://127.0.0.1:{PORT}"), PORT, TOKEN) is None
    assert reject_reason("POST", post(Origin=f"http://localhost:{PORT}"), PORT, TOKEN) is None


@pytest.mark.parametrize("token", [None, "", "wrong", TOKEN + "x", TOKEN[:-1]])
def test_post_needs_the_session_token(token):
    assert reject_reason("POST", post(**{"X-Beatcut-Token": token}), PORT, TOKEN) == "bad token"


def test_get_does_not_need_the_token():
    assert reject_reason("GET", get(), PORT, TOKEN) is None


@pytest.mark.parametrize("ctype", [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"])
def test_post_needs_json(ctype):
    assert reject_reason("POST", post(**{"Content-Type": ctype}), PORT, TOKEN) == "bad content type"


@pytest.mark.parametrize("length", [None, "abc", "-1", str(MAX_BODY + 1)])
def test_post_length_is_checked(length):
    assert reject_reason("POST", post(**{"Content-Length": length}), PORT, TOKEN) == "bad length"


def test_other_methods_are_rejected():
    assert reject_reason("PUT", post(), PORT, TOKEN) == "bad method"
    assert reject_reason("DELETE", get(), PORT, TOKEN) == "bad method"
