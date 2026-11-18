import json
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "picker" / "index.html").read_text(encoding="utf-8")


def test_token_placeholder_appears_exactly_once():
    assert HTML.count("__BEATCUT_TOKEN__") == 1


def test_no_html_injection_sinks():
    for sink in ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"]:
        assert sink not in HTML, sink


def test_no_external_resources():
    assert "http://" not in HTML and "https://" not in HTML
    assert not re.search(r"<(script|link|img|iframe)[^>]+\bsrc\s*=\s*[\"']//", HTML)


def test_i18n_dictionaries_match():
    m = re.search(r'<script type="application/json" id="i18n">(.*?)</script>', HTML, re.S)
    assert m, "i18n block not found"
    words = json.loads(m.group(1))
    assert set(words) == {"en", "tr"}
    assert set(words["en"]) == set(words["tr"]) and len(words["en"]) >= 30
    for lang in words.values():
        assert all(isinstance(v, str) and v.strip() for v in lang.values())


def test_posts_send_the_token():
    assert "X-Beatcut-Token" in HTML and "application/json" in HTML


def test_removed_features_are_gone():
    for word in ["capcut", "seam", "sfx", "roll"]:
        assert not re.search(rf"\b{word}\b", HTML, re.I), word
