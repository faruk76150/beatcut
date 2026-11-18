import json

import pytest

from pipeline.schema import (SHOT_DEFAULTS, SchemaError, empty_state, load_shots, save_shots,
                             validate_shots, with_defaults)
from util import patched

PROFILES = {"neutral", "warm"}
GOOD = {"version": 1, "period": 0.5, "t0": 0.2, "tail": 1.5,
        "shots": [{"clip": "a.mp4", "in": 1.0, "out": 3.0, "beats": 4}],
        "bad": {"a.mp4": [{"a": 0.0, "b": 1.0, "note": "shaky"}]},
        "clipnotes": {"a.mp4": "sunrise"},
        "markers": [{"beat": 16, "title": "chorus"}]}


def shot(d):
    return d["shots"][0]


def test_good_state_is_valid():
    assert validate_shots(GOOD, PROFILES) == []
    assert validate_shots(empty_state(), PROFILES) == []


def test_unknown_fields_are_allowed():
    d = patched(GOOD, lambda d: (d.update(custom=1), shot(d).update(extra="x")))
    assert validate_shots(d, PROFILES) == []


@pytest.mark.parametrize("fn, needle", [
    (lambda d: d.update(version=2), "version"),
    (lambda d: d.update(period=0), "period"),
    (lambda d: d.update(period=float("nan")), "period"),
    (lambda d: d.update(period=True), "period"),
    (lambda d: d.update(t0=-1), "t0"),
    (lambda d: d.update(tail=31), "tail"),
    (lambda d: d.update(shots={}), "shots"),
    (lambda d: d.update(shots=["x"]), "shots[0]"),
    (lambda d: shot(d).update(clip="../x.mp4"), "shots[0].clip"),
    (lambda d: shot(d).update(clip="a\\b.mp4"), "shots[0].clip"),
    (lambda d: shot(d).update(clip=""), "shots[0].clip"),
    (lambda d: shot(d).update({"in": -1}), "shots[0].in"),
    (lambda d: shot(d).update(out=1.0), "shots[0].out"),
    (lambda d: shot(d).pop("out"), "shots[0].out"),
    (lambda d: shot(d).update(beats=0), "shots[0].beats"),
    (lambda d: shot(d).update(beats=1.5), "shots[0].beats"),
    (lambda d: shot(d).update(beats=True), "shots[0].beats"),
    (lambda d: shot(d).update(fit="stretch"), "shots[0].fit"),
    (lambda d: shot(d).update(interp="magic"), "shots[0].interp"),
    (lambda d: shot(d).update(prof="nope"), "shots[0].prof"),
    (lambda d: shot(d).update(tin="wipe"), "shots[0].tin"),
    (lambda d: shot(d).update(tdur=6), "shots[0].tdur"),
    (lambda d: shot(d).update(note=5), "shots[0].note"),
    (lambda d: d.update(bad={"a.mp4": [{"a": 2, "b": 1}]}), "bad"),
    (lambda d: d.update(clipnotes={"a.mp4": 3}), "clipnotes"),
    (lambda d: d.update(markers=[{"beat": -1, "title": "x"}]), "markers[0].beat"),
])
def test_invalid_states(fn, needle):
    errors = validate_shots(patched(GOOD, fn), PROFILES)
    assert errors and any(e.startswith(needle) for e in errors), errors


def test_root_must_be_an_object():
    assert validate_shots([], PROFILES)


def test_all_errors_are_reported():
    d = patched(GOOD, lambda d: (d.update(period=0), shot(d).update(beats=0, fit="x")))
    assert len(validate_shots(d, PROFILES)) == 3


def test_too_many_shots():
    d = patched(GOOD, lambda d: d.update(shots=[dict(shot(d)) for _ in range(2001)]))
    assert any(e.startswith("shots") for e in validate_shots(d, PROFILES))


def test_with_defaults_fills_without_mutating():
    minimal = {"version": 1, "period": 0.5, "t0": 0.0,
               "shots": [{"clip": "a.mp4", "in": 0, "out": 1, "beats": 2}]}
    full = with_defaults(minimal)
    assert full["tail"] == 1.5 and full["bad"] == {} and full["clipnotes"] == {} and full["markers"] == []
    for k, v in SHOT_DEFAULTS.items():
        assert full["shots"][0][k] == v
    assert "interp" not in full["shots"][0]
    assert "tail" not in minimal and "fit" not in minimal["shots"][0]


def test_load_shots(tmp_path):
    p = tmp_path / "shots.json"
    with pytest.raises(SchemaError, match="shots.json"):
        load_shots(p, PROFILES)
    p.write_text("{oops", encoding="utf-8")
    with pytest.raises(SchemaError):
        load_shots(p, PROFILES)
    p.write_text(json.dumps(patched(GOOD, lambda d: d.update(period=0))), encoding="utf-8")
    with pytest.raises(SchemaError) as e:
        load_shots(p, PROFILES)
    assert e.value.errors and e.value.errors[0].startswith("period")
    p.write_text(json.dumps(GOOD), encoding="utf-8")
    assert load_shots(p, PROFILES)["shots"][0]["tin"] == "cut"


def test_save_shots_keeps_backup_and_unknown_keys(tmp_path):
    p = tmp_path / "shots.json"
    save_shots(p, {**empty_state(), "custom": 1, "clipnotes": {"a.mp4": "çiğ"}})
    newer = empty_state()
    newer["period"] = 0.6
    save_shots(p, newer)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["period"] == 0.6 and data["custom"] == 1
    assert json.loads((tmp_path / "shots.json.bak").read_text(encoding="utf-8"))["period"] == 0.5
    assert "çiğ" in (tmp_path / "shots.json.bak").read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))
