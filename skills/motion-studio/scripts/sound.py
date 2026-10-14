#!/usr/bin/env python3
"""Make the music bed and the UI sounds with ElevenLabs, from the project's sound.json.

Usage (from the project root): python3 -I SKILL_DIR/scripts/sound.py

sound.json:
  {"music":   {"prompt": "Calm, minimal instrumental ... no drums, no vocals", "seconds": 78},
   "effects": {"tick": {"text": "A single soft user interface tick, short, quiet", "seconds": 0.5}}}

The music is Eleven Music (music_v1), instrumental, a little longer than the film so it can
fade under the end card. Write its prompt so it leaves room for a voice: no melody that
competes with speech. Each effect is a short Sound Effects clip, peak-normalised to -12 dBFS,
so the levels in src/timing.ts set the mix.

Writes video/public/sound/music.mp3 and video/public/sound/<name>.wav (48 kHz). Each answer is
cached in assets/sound-cache by its request, so an unchanged prompt costs no credits.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

# The skill folder may be a tracked, hashed tree: keep __pycache__ out of it.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from elevenlabs import post  # noqa: E402

ROOT = Path.cwd()
CACHE = ROOT / "assets" / "sound-cache"
OUT = ROOT / "video" / "public" / "sound"
EFFECT_PEAK = -12.0


def fetch(path: str, body: dict) -> Path:
    digest = hashlib.sha256(json.dumps([path, body], sort_keys=True).encode()).hexdigest()[:16]
    cached = CACHE / f"{digest}.mp3"
    if not cached.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(post(path, body, "output_format=mp3_44100_192"))
    return cached


def peak(path: Path) -> float:
    log = subprocess.run(
        ["ffmpeg", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    return float(re.search(r"max_volume: (-?[\d.]+) dB", log).group(1))


def main() -> None:
    spec = json.loads((ROOT / "sound.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    music = spec.get("music")
    if music:
        body = {
            "prompt": music["prompt"],
            "music_length_ms": round(music["seconds"] * 1000),
            "model_id": "music_v1",
            "force_instrumental": True,
        }
        (OUT / "music.mp3").write_bytes(fetch("/music", body).read_bytes())
        print("music.mp3")
    for name, effect in spec.get("effects", {}).items():
        body = {"text": effect["text"], "duration_seconds": effect["seconds"], "prompt_influence": 0.6}
        raw = fetch("/sound-generation", body)
        p = peak(raw)
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(raw),
                "-af",
                f"volume={EFFECT_PEAK - p}dB",
                "-ar",
                "48000",
                str(OUT / f"{name}.wav"),
            ],
            check=True,
        )
        print(f"{name}.wav  peak {p:.1f} dB, set to {EFFECT_PEAK:.0f}")


if __name__ == "__main__":
    main()
