"""Take the teacher out of Isaac's mic track after an online class, before Whisper reads it.

On speakers (no headphones) the mic hears the class coming out of the Mac about a tenth of a second after the
system-audio tap recorded it. We have both tracks, so the class track is a clean copy of the echo: find the
delay, learn how loud the echo is at each pitch, and keep the mic only where the echo can't explain what it heard.
What is left is Isaac. Plain signal maths (no model, no network), run once per class, a few seconds.

Dropping whole lines by matching words could not do this: Whisper's lines mix a few of Isaac's words with
seconds of echo, so they either all go or all stay.
"""
from __future__ import annotations

import numpy as np

SR = 16000
N = 512                      # 32 ms frames
HOP = N // 2
ENV_HOP = 160                # 10 ms steps for finding the delay
MAX_DELAY_S = 0.5
OVER = 2.0                   # take away a bit more than the echo estimate, so echo leaves ~nothing
LEFT = 0.25                  # a step is Isaac when over a quarter of the mic is left once the echo is taken away
NOISE_X = 4.0                # ...and what is left is louder than 4x the room's quiet
MARGIN = 15                  # 240 ms kept either side of Isaac
RAMP = 160
FLOOR = 0.1                  # inside Isaac's stretches the echo drops to a tenth, never to a hole
CHUNK = 4096                 # frames per pass (~65 s), keeps memory flat on a 2 hour class
_WIN = np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N) / N)).astype(np.float32)   # sqrt periodic Hann


def _align(ref: np.ndarray, n: int, offset: int) -> np.ndarray:
    """ref shifted so out[i] == ref[i + offset], zero where ref has nothing, length n."""
    out = np.zeros(n, dtype=np.float32)
    lo, hi = max(0, -offset), min(n, len(ref) - offset)
    if hi > lo:
        out[lo:hi] = ref[lo + offset:hi + offset]
    return out


def _env(x: np.ndarray) -> np.ndarray:
    k = len(x) // ENV_HOP
    f = x[:k * ENV_HOP].reshape(k, ENV_HOP)
    return np.log(np.sqrt((f ** 2).mean(1)) + 1e-4)


def find_delay(mic: np.ndarray, ref: np.ndarray) -> tuple[int, float]:
    """(samples the echo trails ref by, how well they match 0..1). ref is already on mic's clock. Each minute
    votes; the median vote is the delay, so a minute of Isaac talking over silence can't move it."""
    em, er = _env(mic), _env(ref)
    n = min(len(em), len(er))
    maxlag, block = int(MAX_DELAY_S * SR / ENV_HOP), 6000
    votes, scores = [], []
    for b0 in range(maxlag, n - block // 4, block):
        a = em[b0:b0 + block]
        if len(a) < block // 4 or a.std() < 1e-3:
            continue
        a = (a - a.mean()) / a.std()
        best, lag = -1.0, 0
        for d in range(0, maxlag + 1):
            r = er[b0 - d:b0 - d + len(a)]
            if r.std() < 1e-3:
                continue
            c = float((a * (r - r.mean())).mean() / r.std())
            if c > best:
                best, lag = c, d
        if best > 0.4:
            votes.append(lag)
            scores.append(best)
    if not votes:
        return 0, 0.0
    return int(np.median(votes)) * ENV_HOP, float(np.median(scores))


def _frames(x: np.ndarray, f0: int, f1: int) -> np.ndarray:
    """STFT of frames f0..f1-1 (frame f starts at sample f*HOP); zero past the ends."""
    need = (f1 - 1) * HOP + N
    seg = np.zeros(need - f0 * HOP, dtype=np.float32)
    lo, hi = f0 * HOP, min(len(x), need)
    if hi > lo:
        seg[:hi - lo] = x[lo:hi]
    idx = np.arange(f1 - f0)[:, None] * HOP + np.arange(N)[None, :]
    return np.fft.rfft(seg[idx] * _WIN, axis=1)


def _smear(mag: np.ndarray) -> np.ndarray:
    """Each frame takes the loudest of itself, the 3 before (the room's tail) and 1 after (delay slack)."""
    out = mag.copy()
    for k in (1, 2, 3):
        out[k:] = np.maximum(out[k:], mag[:-k])
    out[:-1] = np.maximum(out[:-1], mag[1:])
    return out


def suppress(mic: np.ndarray, ref: np.ndarray, ref_offset: int) -> tuple[np.ndarray, dict]:
    """Isaac's mic with the class taken out. ref_offset: mic sample i was recorded at ref sample i + ref_offset
    (from the tracks' start times). Returns (cleaned, info); cleaned is mic unchanged when there is no echo.

    Each 16 ms step asks: once the echo the class track predicts is taken away, how much of the mic is left?
    Isaac talking leaves nearly all of it, pure echo leaves nearly none (measured on a real class: 0.99 vs 0).
    Steps that are Isaac (plus a short margin) keep the mic with the echo turned down; the rest go silent."""
    mic = np.asarray(mic, dtype=np.float32)
    ref = _align(np.asarray(ref, dtype=np.float32), len(mic), ref_offset)
    delay, match = find_delay(mic, ref)
    info = {"delay_ms": round(delay / SR * 1000), "match": round(match, 2), "applied": False}
    if match < 0.5 or len(mic) < N * 4:
        return mic, info                   # headphones, or nothing to go on: leave the mic alone
    ref = _align(ref, len(mic), -delay)    # now the echo in mic[i] lines up with ref[i]
    frames = (len(mic) - N) // HOP + 1

    # how loud the echo is at each pitch: mic over class, on frames where the class is loud
    ratios, energy = [], []
    for f0 in range(0, frames, CHUNK):
        f1 = min(frames, f0 + CHUNK)
        r, m = np.abs(_frames(ref, f0, f1)), np.abs(_frames(mic, f0, f1))
        energy.append((m ** 2).sum(1))
        e = r.sum(1)
        loud = e > np.quantile(e, 0.7) if e.max() > 0 else np.zeros(len(e), bool)
        if loud.any():
            ratios.append(m[loud] / (r[loud] + 1e-6))
    if not ratios:
        return mic, info
    h = np.median(np.concatenate(ratios), axis=0).astype(np.float32)
    energy = np.concatenate(energy)
    noise = float(np.quantile(energy, 0.3)) * NOISE_X     # a step quieter than this is room hiss, not a voice

    mine = np.zeros(frames, dtype=bool)
    soft = np.zeros(len(mic) + N, dtype=np.float32)       # the mic with the echo turned down (not off)
    for f0 in range(0, frames, CHUNK):
        f1 = min(frames, f0 + CHUNK)
        pad = 3 if f0 else 0
        r = _smear(np.abs(_frames(ref, f0 - pad, f1 + 1)))[pad:pad + f1 - f0]
        spec = _frames(mic, f0, f1)
        m = np.abs(spec)
        g = np.clip(1.0 - OVER * (h * r) / (m + 1e-6), 0.0, 1.0)
        left = ((g * m) ** 2).sum(1)
        mine[f0:f1] = (left > LEFT * (m ** 2).sum(1)) & (left > noise)
        y = np.fft.irfft(spec * np.maximum(g, FLOOR), n=N, axis=1).astype(np.float32) * _WIN
        for i in range(f1 - f0):                           # overlap-add: sqrt-Hann in and out sums to 1
            soft[(f0 + i) * HOP:(f0 + i) * HOP + N] += y[i]
    mine = _widen(mine, MARGIN)
    mask = np.zeros(len(mic), dtype=np.float32)
    for a, b in _runs(mine):
        mask[a * HOP:min(len(mic), b * HOP + N)] = 1.0
    ramp = np.ones(RAMP, dtype=np.float32) / RAMP              # 10 ms fades, no clicks for Whisper to read
    mask = np.convolve(mask, ramp, mode="same")
    info.update(applied=True, kept=round(float(mine.mean()), 3))
    # Isaac's stretches keep the turned-down mic (the teacher talking over him gets quieter, he doesn't);
    # everything else is silent, since Whisper reads even a quiet leftover teacher as words
    return soft[:len(mic)] * mask, info


def _widen(x: np.ndarray, k: int) -> np.ndarray:
    """True k steps either side of every True: keeps the soft start and end of each word."""
    c = np.convolve(x.astype(np.int32), np.ones(2 * k + 1, dtype=np.int32), mode="same")
    return c > 0


def _runs(x: np.ndarray) -> list[tuple[int, int]]:
    d = np.diff(np.concatenate([[0], x.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))
