#!/usr/bin/env python3
"""Align the narration's phrases to the speech in the beat WAVs.

Usage (from the project root, after narrate.py): python3 -I SKILL_DIR/scripts/sync.py

Eleven v4's character timestamps drift by up to a whole phrase on short lines (one test put
"joint" where the audio says "A bond."). The sync check needs times within 2 frames, so this
script takes each phrase's start from the audio: it splits the beat's text at punctuation,
finds the pauses of 0.2 s or more with ffmpeg silencedetect, and pairs phrases with speech
segments in order. A comma is not always a pause: when the counts differ, each segment
anchors the nearest phrase. Inside each anchored span, v4's word times are stretched
linearly onto the span's speech. Only phrase starts after a pause are measured from the audio.

Writes video/src/sync.json: per beat, each phrase's start (pause: it starts after a pause), and
every word's start. src/timing.ts takes its cues from this file, not from voice.json.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
VOICE = ROOT / "video" / "src" / "voice.json"
WAVS = ROOT / "video" / "public" / "voice"
OUT = ROOT / "video" / "src" / "sync.json"

NOISE_DB = -40
MIN_PAUSE = 0.2


def speech_segments(wav: Path, seconds: float) -> list[tuple[float, float]]:
    log = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(wav),
            "-af",
            f"silencedetect=noise={NOISE_DB}dB:d={MIN_PAUSE}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", log)]
    silences = list(zip(starts, ends + [seconds] * (len(starts) - len(ends)), strict=True))
    segments, t = [], 0.0
    for s, e in silences:
        if s > t:
            segments.append((t, s))
        t = e
    if t < seconds:
        segments.append((t, seconds))
    return segments


def phrases(words: list[dict]) -> list[list[int]]:
    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        if re.search(r"[.,?!;:]$", w["w"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def main() -> int:
    beats = json.loads(VOICE.read_text())
    out, failed = [], False
    for b in beats:
        segs = speech_segments(WAVS / f"beat-{b['beat']:02d}.wav", b["seconds"])
        groups = phrases(b["words"])
        p0 = [b["words"][g[0]]["s"] for g in groups]
        if len(segs) == len(groups):
            anchor = dict(enumerate(segs))
        else:
            # A comma is not always a pause. Anchor phrase 0 to the first segment, then each later
            # segment to the next phrase whose v4 start is nearest; other phrases follow their anchor.
            anchor, last = {0: segs[0]}, 0
            for seg in segs[1:]:
                if last + 1 >= len(groups):
                    print(f"beat {b['beat']}: more speech segments than phrases", file=sys.stderr)
                    failed = True
                    break
                j = min(range(last + 1, len(groups)), key=lambda j: abs(p0[j] - seg[0]))
                if abs(p0[j] - seg[0]) > 1.2:
                    print(f"beat {b['beat']}: no phrase near the speech at {seg[0]:.2f} s", file=sys.stderr)
                    failed = True
                anchor[j], last = seg, j
        # Inside each anchored span, v4's times are stretched linearly onto the audio segment.
        starts = sorted(anchor)
        span_end = {a: (starts[i + 1] if i + 1 < len(starts) else len(groups)) for i, a in enumerate(starts)}
        words, ph = [], []
        for k, g in enumerate(groups):
            a = max(j for j in anchor if j <= k)
            s, e = anchor[a]
            v0 = p0[a]
            v1 = max(b["words"][i]["e"] for i in groups[span_end[a] - 1])
            scale = (e - s) / (v1 - v0) if v1 > v0 else 0.0
            if 0.6 <= scale <= 1.6:
                times = {i: round(min(e, s + max(0.0, b["words"][i]["s"] - v0) * scale), 3) for i in g}
            else:
                # v4 is far off here: spread the span's words by their share of its characters.
                span = [i for gg in groups[a : span_end[a]] for i in gg]
                chars = [len(b["words"][i]["w"]) + 1 for i in span]
                before = {i: sum(chars[:n]) for n, i in enumerate(span)}
                times = {i: round(s + (e - s) * before[i] / sum(chars), 3) for i in g}
                print(f"beat {b['beat']}: v4 times off by {scale:.1f}x, words spread by characters", file=sys.stderr)
            for i in g:
                words.append({"w": b["words"][i]["w"], "t": times[i]})
            ph.append({"text": " ".join(b["words"][i]["w"] for i in g), "start": times[g[0]], "pause": k in anchor})
        out.append({"beat": b["beat"], "seconds": b["seconds"], "phrases": ph, "words": words})
        print(f"beat {b['beat']:2d}: " + " | ".join(f"{p['start']:.2f} {p['text']}" for p in ph))
    if failed:
        return 1
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
