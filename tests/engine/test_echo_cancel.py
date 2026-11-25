import numpy as np

from misty_engine import echo_cancel as ec

SR = ec.SR


def _talk(rng, seconds, on=0.5):
    """Noise in syllable-length bursts: enough like speech for the envelope maths."""
    n = int(seconds * SR)
    env = np.repeat((rng.random(int(seconds * 5)) < on).astype(np.float32), SR // 5)[:n]
    env = np.convolve(env, np.ones(160) / 160, mode="same")
    return (rng.standard_normal(n).astype(np.float32) * env * 0.3)


def _room(x, delay):
    """What the mic hears from the speakers: later, quieter, a little smeared."""
    y = np.zeros_like(x)
    y[delay:] = x[:-delay]
    return np.convolve(y, [0.25, 0.15, 0.08], mode="same").astype(np.float32)


def _level(x, a, b):
    return float(np.sqrt((x[int(a * SR):int(b * SR)] ** 2).mean()))


def test_echo_is_muted_and_isaac_is_kept():
    rng = np.random.default_rng(1)
    teacher = _talk(rng, 120)
    me_alone = np.zeros_like(teacher)
    for a in (20, 61, 97):                                 # Isaac talks while the teacher pauses
        teacher[a * SR:(a + 3) * SR] = 0
        me_alone[a * SR:(a + 3) * SR] = _talk(rng, 3, on=0.9)
    mic = _room(teacher, 1920) + me_alone + rng.standard_normal(len(teacher)).astype(np.float32) * 1e-3
    out, info = ec.suppress(mic, teacher, 0)
    assert info["applied"] and abs(info["delay_ms"] - 120) <= 10
    for a in (20, 61, 97):                                 # Isaac comes through untouched
        assert _level(out, a + 0.3, a + 2.7) > 0.9 * _level(mic, a + 0.3, a + 2.7)
    assert _level(out, 30, 50) < 0.1 * _level(mic, 30, 50)  # pure echo goes quiet


def test_tracks_started_apart_are_lined_up_first():
    rng = np.random.default_rng(2)
    teacher = _talk(rng, 90)
    lead = int(0.4 * SR)                                   # the mic started 0.4 s after the class track
    mic = _room(teacher, 1920)[lead:]
    out, info = ec.suppress(mic, teacher, lead)
    assert info["applied"] and abs(info["delay_ms"] - 120) <= 10
    assert _level(out, 10, 60) < 0.1 * _level(mic, 10, 60)


def test_headphones_leave_the_mic_alone():
    rng = np.random.default_rng(3)
    teacher, mine = _talk(rng, 90), _talk(rng, 90)          # no echo: the two tracks have nothing in common
    out, info = ec.suppress(mine, teacher, 0)
    assert not info["applied"]
    assert np.array_equal(out, mine.astype(np.float32))
