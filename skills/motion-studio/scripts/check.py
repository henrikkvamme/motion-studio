#!/usr/bin/env python3
"""The mechanical checks of the motion-studio skill. Each exits non-zero when it fails.

Usage (from the project root):
  check.py format FILE --seconds S [--fps 30] [--min-height 1080]
      H.264 + AAC, constant frame rate, height, and duration within 5% of S.
  check.py loudness FILE
      Integrated loudness -14 LUFS (+-1), true peak at or below -1 dBTP.
  check.py mix MIX.wav VOICE.wav
      MIX is the film's audio, VOICE the same render with music and effects muted. The bed is
      MIX minus VOICE. While the voice speaks, the bed must sit 18 dB (+-3) under it, as a
      median. Stretches of a second or more that are over 6 dB off are named, not failed.
  check.py sheet FILE OUT.png [--guides]
      One frame per second on one sheet, for you to view. --guides draws the 5% title-safe
      box on every tile. Also reports black frames (fail) and holds of 2 s or more.
  check.py pixel FILE SECONDS X Y
      The colour of one pixel of one frame, as hex, for the contrast check.
  check.py contrast FG BG [FG BG ...] [--min 4.5]
      The contrast ratio of each pair of hex colours.
  check.py sync
      Every cue in video/src/timing.ts against the measured speech (check-sync.mts).

Needs ffmpeg and ffprobe on PATH. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOUDNESS, LOUDNESS_TOL, TRUE_PEAK = -14.0, 1.0, -1.0
UNDER, UNDER_TOL, STRETCH_OFF, STRETCH_MIN = 18.0, 3.0, 6.0, 1.0
SILENCE = -120.7  # LUFS, the floor of ffmpeg's ebur128 meter


def run(args: list[str]) -> str:
    """Run ffmpeg or ffprobe and return stderr plus stdout (ffmpeg logs its filters on stderr)."""
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"{args[0]} failed:\n{p.stderr[-1500:]}")
    return p.stderr + p.stdout


def probe(path: str) -> dict:
    return json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )


def report(ok: bool, name: str, note: str) -> bool:
    print(f"{'pass' if ok else 'FAIL'}  {name}: {note}")
    return ok


# format


def check_format(a: argparse.Namespace) -> bool:
    info = probe(a.file)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    seconds = float(info["format"]["duration"])
    ok = True
    if video is None:
        return report(False, "format", "no video stream")
    rate = video["r_frame_rate"]
    num, den = (int(x) for x in rate.split("/"))
    ok &= report(video["codec_name"] == "h264", "codec", video["codec_name"])
    ok &= report(audio is not None and audio["codec_name"] == "aac", "audio", audio["codec_name"] if audio else "none")
    ok &= report(video["height"] >= a.min_height, "size", f"{video['width']}x{video['height']}")
    ok &= report(
        rate == video["avg_frame_rate"] and abs(num / den - a.fps) < 0.01,
        "frame rate",
        f"r {rate}, avg {video['avg_frame_rate']}, want constant {a.fps}",
    )
    ok &= report(abs(seconds - a.seconds) <= 0.05 * a.seconds, "duration", f"{seconds:.2f} s, want {a.seconds} s +-5%")
    return ok


# loudness


def summary(path: str) -> tuple[float, float]:
    """Integrated loudness (LUFS) and true peak (dBTP) of a file's audio."""
    log = run(["ffmpeg", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"])
    s = log[log.rindex("Summary:") :]
    return (float(re.search(r"I:\s+(-?[\d.]+)", s).group(1)), float(re.search(r"Peak:\s+(-?[\d.]+|-inf)", s).group(1)))


def check_loudness(a: argparse.Namespace) -> bool:
    i, tp = summary(a.file)
    ok = report(abs(i - LOUDNESS) <= LOUDNESS_TOL, "loudness", f"{i:.1f} LUFS, want {LOUDNESS:.0f} +-{LOUDNESS_TOL:.0f}")
    return report(tp <= TRUE_PEAK, "true peak", f"{tp:.1f} dBTP, want <= {TRUE_PEAK:.0f}") and ok


# mix


def momentary(path: Path) -> dict[float, float]:
    """Momentary loudness (400 ms window) by time, every 100 ms."""
    out = path.with_suffix(".m.txt")
    run(
        [
            "ffmpeg",
            "-nostats",
            "-i",
            str(path),
            "-af",
            f"ebur128=metadata=1,ametadata=print:key=lavfi.r128.M:file={out}",
            "-f",
            "null",
            "-",
        ]
    )
    text = out.read_text()
    times = [round(float(t), 1) for t in re.findall(r"pts_time:([\d.]+)", text)]
    # ffmpeg writes nan or -inf over digital silence: count it as the meter's floor.
    values = [float(v) for v in re.findall(r"lavfi\.r128\.M=(\S+)", text)]
    return dict(zip(times, (v if math.isfinite(v) else SILENCE for v in values), strict=True))


def stretches(points: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """Runs of at least STRETCH_MIN seconds where the voice is more than STRETCH_OFF dB off target."""
    runs: list[tuple[float, float, float]] = []
    cur: list[tuple[float, float]] = []

    def close() -> None:
        if cur and cur[-1][0] - cur[0][0] >= STRETCH_MIN:
            runs.append((cur[0][0], cur[-1][0], statistics.median(d for _, d in cur)))

    for t, d in points:
        off = abs(d - UNDER) > STRETCH_OFF
        if off and cur and t - cur[-1][0] <= 0.15:
            cur.append((t, d))
            continue
        close()
        cur = [(t, d)] if off else []
    close()
    return runs


def check_mix(a: argparse.Namespace) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        voice, bed = Path(tmp) / "voice.wav", Path(tmp) / "bed.wav"
        run(["ffmpeg", "-v", "error", "-y", "-i", a.voice, "-ac", "1", "-ar", "48000", str(voice)])
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                a.mix,
                "-i",
                a.voice,
                "-filter_complex",
                # Subtract first, then fold to mono the same way as the voice above.
                "[1:a]volume=-1[v];[0:a][v]amix=inputs=2:normalize=0",
                "-ac",
                "1",
                "-ar",
                "48000",
                str(bed),
            ]
        )
        voice_i, _ = summary(str(voice))
        v, b = momentary(voice), momentary(bed)
    gate = voice_i - 10
    points = sorted((t, m - b[t]) for t, m in v.items() if m >= gate and t in b)
    if not points:
        return report(False, "mix-balance", "no speech found in VOICE")
    median = statistics.median(d for _, d in points)
    note = f"music {median:.1f} dB under the voice while it speaks (median of {len(points)})"
    ok = report(abs(median - UNDER) <= UNDER_TOL, "mix-balance", f"{note}, want {UNDER:.0f} +-{UNDER_TOL:.0f}")
    for start, end, d in stretches(points):
        print(f"      {start:5.1f}-{end:5.1f} s: {d:.1f} dB under")
    return ok


# sheet


def check_sheet(a: argparse.Namespace) -> bool:
    info = probe(a.file)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    seconds = float(info["format"]["duration"])
    n = max(1, round(seconds))
    aspect = video["width"] / video["height"]
    cols = max(1, math.ceil(math.sqrt(n / aspect)))
    rows = math.ceil(n / cols)
    chain = "fps=1,scale=320:-2"
    if a.guides:
        chain += ",drawbox=x=iw*0.05:y=ih*0.05:w=iw*0.9:h=ih*0.9:color=red@0.7:t=1"
    chain += f",tile={cols}x{rows}:color=0x808080"
    run(["ffmpeg", "-v", "error", "-y", "-i", a.file, "-vf", chain, "-frames:v", "1", a.out])
    print(f"sheet {a.out}: {n} frames, {cols}x{rows}. View it.")
    log = run(
        [
            "ffmpeg",
            "-nostats",
            "-i",
            a.file,
            "-an",
            "-vf",
            "blackdetect=d=0.05:pix_th=0.10,freezedetect=n=0.001:d=2",
            "-f",
            "null",
            "-",
        ]
    )
    black = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)
    holds = re.findall(r"freeze_start: ([\d.]+)", log)
    lengths = re.findall(r"freeze_duration: ([\d.]+)", log)
    ok = report(not black, "black frames", ", ".join(f"{float(s):.2f}-{float(e):.2f} s" for s, e in black) or "none")
    for s, d in zip(holds, lengths, strict=False):  # a hold at the very end has no length
        print(f"      hold at {float(s):.1f} s for {float(d):.1f} s: is it on purpose?")
    return ok


# pixel and contrast


def check_pixel(a: argparse.Namespace) -> bool:
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(a.seconds),
            "-i",
            a.file,
            "-frames:v",
            "1",
            "-vf",
            f"format=rgb24,crop=1:1:{a.x}:{a.y}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    print("#" + raw[:3].hex().upper())
    return True


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        sys.exit(f"contrast: {hex_colour} is not a #RRGGBB colour")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(fg: str, bg: str) -> float:
    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def check_contrast(a: argparse.Namespace) -> bool:
    if len(a.colours) % 2:
        sys.exit("contrast: give the colours in FG BG pairs")
    ok = True
    for fg, bg in zip(a.colours[::2], a.colours[1::2], strict=True):
        r = ratio(fg, bg)
        ok &= report(r >= a.min, "text-contrast", f"{fg} on {bg}: {r:.2f}:1, want {a.min}:1")
    return ok


# sync


def check_sync(_: argparse.Namespace) -> bool:
    return subprocess.run(["node", str(HERE / "check-sync.mts")]).returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="check", required=True)
    p = sub.add_parser("format")
    p.add_argument("file")
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--fps", type=float, default=30)
    p.add_argument("--min-height", type=int, default=1080)
    p.set_defaults(fn=check_format)
    p = sub.add_parser("loudness")
    p.add_argument("file")
    p.set_defaults(fn=check_loudness)
    p = sub.add_parser("mix")
    p.add_argument("mix")
    p.add_argument("voice")
    p.set_defaults(fn=check_mix)
    p = sub.add_parser("sheet")
    p.add_argument("file")
    p.add_argument("out")
    p.add_argument("--guides", action="store_true")
    p.set_defaults(fn=check_sheet)
    p = sub.add_parser("pixel")
    p.add_argument("file")
    p.add_argument("seconds", type=float)
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.set_defaults(fn=check_pixel)
    p = sub.add_parser("contrast")
    p.add_argument("colours", nargs="+")
    p.add_argument("--min", type=float, default=4.5)
    p.set_defaults(fn=check_contrast)
    p = sub.add_parser("sync")
    p.set_defaults(fn=check_sync)
    a = parser.parse_args()
    sys.exit(0 if a.fn(a) else 1)


if __name__ == "__main__":
    main()
