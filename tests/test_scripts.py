"""Tests for the motion-studio scripts. They make their own media with ffmpeg and use no network.

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "motion-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check  # noqa: E402
import narrate  # noqa: E402
import sync  # noqa: E402


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def script(name: str, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "ELEVENLABS_API_KEY"} | {"HOME": str(cwd)}
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / name), *args], cwd=cwd, env=env, capture_output=True, text=True)


# Speech stand-in: a 1 kHz tone on for 0.0-1.5 s and 2.5-4.0 s, at about -20 LUFS.
SPEECH = "aevalsrc='0.14*sin(2*PI*1000*t)*(lt(t,1.5)+gte(t,2.5))':s=48000:d=4"
# Music stand-in: a 500 Hz tone at the same level, so a gain of -18 dB puts it 18 dB under.
BED = "aevalsrc='0.14*sin(2*PI*500*t)':s=48000:d=4"


class Pure(unittest.TestCase):
    def test_contrast(self) -> None:
        self.assertAlmostEqual(check.ratio("#000000", "#FFFFFF"), 21.0, places=2)
        self.assertAlmostEqual(check.ratio("#6E6E73", "#F5F5F7"), 4.66, places=2)
        self.assertAlmostEqual(check.ratio("#FFFFFF", "#000000"), 21.0, places=2)

    def test_stretches(self) -> None:
        on = [(round(i * 0.1, 1), 18.0) for i in range(20)]
        off = [(round(2 + i * 0.1, 1), 30.0) for i in range(15)]
        short = [(round(4 + i * 0.1, 1), 5.0) for i in range(5)]
        runs = check.stretches(on + off + short)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][:2], (2.0, 3.4))

    def test_words(self) -> None:
        meta = {"chars": "A bid. A", "starts": [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], "ends": [0.1] * 8}
        self.assertEqual([w["w"] for w in narrate.words(meta)], ["A", "bid.", "A"])

    def test_phrases(self) -> None:
        words = [{"w": w} for w in "Local, region, enterprise. Nobody has to".split()]
        self.assertEqual(sync.phrases(words), [[0], [1], [2], [3, 4, 5]])

    def test_beats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "script.md"
            p.write_text("| # | Narration | Visual |\n|---|---|---|\n| 1 | A bid. | cards |\n| 2 | A bond. | more |\n")
            self.assertEqual(narrate.beats(p), ["A bid.", "A bond."])


class Media(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def film(self, name: str, video: str = "testsrc2=s=1920x1080:r=30:d=4") -> Path:
        out = self.dir / name
        ffmpeg("-f", "lavfi", "-i", video, "-f", "lavfi", "-i", SPEECH, "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-shortest", str(out))  # fmt: skip
        return out

    def test_format(self) -> None:
        f = self.film("film.mp4")
        self.assertEqual(script("check.py", "format", str(f), "--seconds", "4", cwd=self.dir).returncode, 0)
        bad = script("check.py", "format", str(f), "--seconds", "10", cwd=self.dir)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("FAIL  duration", bad.stdout)

    def test_master_then_loudness(self) -> None:
        pre, out = self.film("pre.mp4"), self.dir / "final.mp4"
        self.assertEqual(script("master.py", str(pre), str(out), cwd=self.dir).returncode, 0)
        r = script("check.py", "loudness", str(out), cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(script("check.py", "loudness", str(pre), cwd=self.dir).returncode, 1)

    def test_mix(self) -> None:
        voice, mix, loud = self.dir / "voice.wav", self.dir / "mix.wav", self.dir / "loud.wav"
        ffmpeg("-f", "lavfi", "-i", SPEECH, str(voice))
        for out, gain in ((mix, "-18dB"), (loud, "-8dB")):
            ffmpeg("-i", str(voice), "-f", "lavfi", "-i", BED, "-filter_complex",
                   f"[1:a]volume={gain}[b];[0:a][b]amix=inputs=2:normalize=0", str(out))  # fmt: skip
        r = script("check.py", "mix", str(mix), str(voice), cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(script("check.py", "mix", str(loud), str(voice), cwd=self.dir).returncode, 1)

    def test_sheet_finds_black(self) -> None:
        f = self.film("black.mp4", "color=c=black:s=1920x1080:r=30:d=4")
        sheet = self.dir / "sheet.png"
        r = script("check.py", "sheet", str(f), str(sheet), "--guides", cwd=self.dir)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(sheet.exists())

    def test_pixel(self) -> None:
        f = self.film("red.mp4", "color=c=0xFF0000:s=1920x1080:r=30:d=4")
        r = script("check.py", "pixel", str(f), "1", "10", "10", cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout.strip(), r"^#F[CDEF]0[0-3]0[0-3]$")

    def test_sync(self) -> None:
        voice = self.dir / "video" / "public" / "voice"
        src = self.dir / "video" / "src"
        voice.mkdir(parents=True)
        src.mkdir(parents=True)
        ffmpeg("-f", "lavfi", "-i", SPEECH, "-ac", "1", str(voice / "beat-01.wav"))
        with wave.open(str(voice / "beat-01.wav")) as w:
            frames = round(w.getnframes() / w.getframerate() * 30)
        (src / "beats.json").write_text(json.dumps([{"n": 1, "voice": 30, "speech": frames}]))

        def timing(cues: str) -> None:
            (src / "timing.ts").write_text(
                'import beats from "./beats.json";\nexport const FPS = 30;\n'
                f"export const BEATS: {{ n: number; voice: number; speech: number }}[] = beats;\n"
                f"export const CUES = {{ {cues} }};\n"
            )

        timing("first: 30, second: 105, inside: 50")
        r = script("check.py", "sync", cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2 verified, 1 estimated, 0 off", r.stdout)
        timing("first: 30, gap: 90")
        self.assertEqual(script("check.py", "sync", cwd=self.dir).returncode, 1)


if __name__ == "__main__":
    unittest.main()
