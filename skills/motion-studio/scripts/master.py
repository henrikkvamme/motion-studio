#!/usr/bin/env python3
"""Master the build: bring the mix to -14 LUFS with true peak at or below -1 dBTP.

Usage: master.py PREMASTER.mp4 OUT.mp4
Measures the premaster's integrated loudness, applies the gain to -14 LUFS, and catches the
few voice peaks above -2 dBFS with a limiter at 4x oversampling. The video stream is copied.
It exits non-zero if the result is out of spec.

A fixed gain plus a limiter keeps the mix as it was balanced. ffmpeg's loudnorm switches to
dynamic mode when a linear gain would break its true-peak target, and then it pumps the music
bed under the voice.
"""

from __future__ import annotations
import re
import subprocess
import sys

TARGET, CEILING = -14.0, 0.7943  # -2 dBFS before the encoder, so the AAC true peak stays under -1.


def loudness(path: str) -> tuple[float, float]:
    err = subprocess.run(
        ["ffmpeg", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True
    ).stderr
    summary = err[err.rindex("Summary:") :]
    return float(re.search(r"I:\s+(-?[\d.]+)", summary).group(1)), float(re.search(r"Peak:\s+(-?[\d.]+)", summary).group(1))


def main() -> None:
    src, out = sys.argv[1], sys.argv[2]
    i, _ = loudness(src)
    # The limiter takes a little off the loudest peaks, so ask for 0.6 dB more than the plain gain.
    gain = TARGET - i + 0.6
    chain = f"volume={gain:.2f}dB,aresample=192000,alimiter=limit={CEILING}:attack=1:release=60:level=false,aresample=48000"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            src,
            "-c:v",
            "copy",
            "-af",
            chain,
            "-c:a",
            "aac",
            "-b:a",
            "320k",
            "-movflags",
            "+faststart",
            out,
        ],
        check=True,
    )
    i2, tp = loudness(out)
    print(f"premaster {i:.1f} LUFS, gain {gain:+.2f} dB -> {i2:.1f} LUFS, true peak {tp:.1f} dBTP")
    if abs(i2 - TARGET) > 1 or tp > -1:
        sys.exit("master: out of spec")


if __name__ == "__main__":
    main()
