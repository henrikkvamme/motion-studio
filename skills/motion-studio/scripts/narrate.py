#!/usr/bin/env python3
"""Voice every beat of script.md with ElevenLabs Eleven v4, one request per beat.

Usage (from the project root): python3 -I SKILL_DIR/scripts/narrate.py VOICE_ID

Reads the beat table in script.md: each row starts "| N | narration |", and the narration is
used word for word. Each beat is sent with the beat before it as previous_text, so the
delivery runs on. The audio is 48 kHz PCM: lower rates sound dull.

Writes:
  video/public/voice/beat-NN.wav   48 kHz mono, one per beat
  video/src/voice.json             per beat: text, seconds, and v4's start and end of each word

Each answer is cached in assets/voice-cache by model, voice, rate, text and previous text, so
an unchanged line costs no credits. v4's word times drift by up to a phrase: run sync.py next.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import wave
from pathlib import Path

# The skill folder may be a tracked, hashed tree: keep __pycache__ out of it.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from elevenlabs import post  # noqa: E402

MODEL, RATE = "eleven_v4", 48000
ROOT = Path.cwd()
CACHE = ROOT / "assets" / "voice-cache"


def beats(script: Path) -> list[str]:
    rows = [line for line in script.read_text().splitlines() if re.match(r"^\| *\d+ *\|", line)]
    if not rows:
        sys.exit(f"narrate: no beat table in {script} (rows start with '| 1 | narration |')")
    return [row.split("|")[2].strip() for row in rows]


def synth(voice: str, text: str, previous: str | None) -> tuple[bytes, dict]:
    digest = hashlib.sha256(json.dumps([MODEL, voice, RATE, text, previous]).encode()).hexdigest()[:16]
    pcm, meta = CACHE / f"{digest}.pcm", CACHE / f"{digest}.json"
    if not meta.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        body = {"inputs": [{"text": text, "voice_id": voice}], "model_id": MODEL}
        if previous:
            body["previous_text"] = previous[-100:]
        result = json.loads(post("/text-to-dialogue/with-timestamps", body, f"output_format=pcm_{RATE}", 120))
        pcm.write_bytes(base64.b64decode(result["audio_base64"]))
        a = result["alignment"]
        meta.write_text(
            json.dumps(
                {
                    "text": text,
                    "chars": "".join(a["characters"]),
                    "starts": a["character_start_times_seconds"],
                    "ends": a["character_end_times_seconds"],
                }
            )
        )
    return pcm.read_bytes(), json.loads(meta.read_text())


def words(meta: dict) -> list[dict]:
    out, cur, start, end = [], "", None, 0.0
    for ch, s, e in zip(meta["chars"], meta["starts"], meta["ends"], strict=True):
        if ch.isspace():
            if cur:
                out.append({"w": cur, "s": round(start, 3), "e": round(end, 3)})
            cur, start = "", None
            continue
        if start is None:
            start = s
        cur += ch
        end = e
    if cur:
        out.append({"w": cur, "s": round(start, 3), "e": round(end, 3)})
    return out


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    voice = sys.argv[1]
    out_dir = ROOT / "video" / "public" / "voice"
    out_dir.mkdir(parents=True, exist_ok=True)
    result, previous = [], None
    for i, text in enumerate(beats(ROOT / "script.md"), start=1):
        pcm, meta = synth(voice, text, previous)
        write_wav(out_dir / f"beat-{i:02d}.wav", pcm)
        result.append({"beat": i, "text": text, "seconds": round(len(pcm) / 2 / RATE, 3), "words": words(meta)})
        print(f"beat {i:2d}: {result[-1]['seconds']:.2f}s  {text}")
        previous = text
    (ROOT / "video" / "src" / "voice.json").write_text(json.dumps(result, indent=1) + "\n")
    print(f"total speech {sum(b['seconds'] for b in result):.2f}s")


if __name__ == "__main__":
    main()
