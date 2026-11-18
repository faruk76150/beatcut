import pytest


@pytest.mark.slow
def test_sheet_writes_a_jpeg(tmp_path):
    from pipeline.sheet import make_sheet
    from util import make_test_project

    root = make_test_project(tmp_path)
    out = make_sheet(root.parent / "wide30.mp4", tmp_path / "sheet.jpg")
    assert out.exists() and out.read_bytes()[:2] == b"\xff\xd8"
