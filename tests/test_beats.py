import numpy as np
import pytest

from pipeline.beats import BeatError, fit_grid


def test_fit_grid_recovers_period_and_first_beat():
    rng = np.random.default_rng(7)
    k = np.array([i for i in range(120) if i not in (17, 18, 60)])      # three missed beats
    times = 0.337 + 0.5108 * k + rng.uniform(-0.012, 0.012, k.size)
    period, t0 = fit_grid(times)
    assert period == pytest.approx(0.5108, abs=0.0004)
    assert t0 == pytest.approx(0.337, abs=0.012)


def test_fit_grid_moves_t0_to_the_first_grid_point_after_zero():
    times = 10.2 + 0.5 * np.arange(40)          # ten seconds of intro before the first detected beat
    period, t0 = fit_grid(times)
    assert period == pytest.approx(0.5, abs=1e-6)
    assert t0 == pytest.approx(0.2, abs=1e-6)


def test_fit_grid_is_not_bent_by_hop_quantisation():
    hop = 512 / 22050                       # librosa reports beats on this grid
    times = np.round((0.25 + 0.5 * np.arange(120)) / hop) * hop
    period, t0 = fit_grid(times)
    assert period == pytest.approx(0.5, abs=0.0002)
    assert t0 == pytest.approx(0.25, abs=0.015)


def test_fit_grid_needs_enough_beats():
    with pytest.raises(BeatError):
        fit_grid([0.5, 1.0, 1.5])


@pytest.mark.slow
@pytest.mark.parametrize("bpm, first", [(120, 0.25), (97, 0.4), (174, 0.3)])
def test_analyze_click_track(tmp_path, bpm, first):
    import soundfile as sf
    from pipeline.beats import analyze

    sr, seconds = 22050, 30
    rng = np.random.default_rng(3)
    y = np.zeros(sr * seconds, dtype=np.float32)
    # a broadband burst: librosa ignores onsets that only touch a few frequency bands
    click = (rng.standard_normal(400) * np.exp(-np.arange(400) / 60)).astype(np.float32) * 0.5
    for n in range(int((seconds - 1) * bpm / 60)):
        i = int((first + n * 60 / bpm) * sr)
        y[i:i + 400] += click
    path = tmp_path / "click.wav"
    sf.write(path, y, sr)
    result = analyze(path)
    assert result["period"] == pytest.approx(60 / bpm, abs=0.0005)
    assert result["bpm"] == pytest.approx(bpm, abs=0.2)
    assert result["t0"] == pytest.approx(first, abs=0.03)
