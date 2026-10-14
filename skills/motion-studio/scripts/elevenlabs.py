"""ElevenLabs access for the motion-studio scripts: the API key and a POST with retries.

The key comes from ELEVENLABS_API_KEY, else from the ELEVENLABS_API_KEY= line of
~/.config/elevenlabs/secrets.env. It is sent only in the xi-api-key header and never printed.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.elevenlabs.io/v1"
KEY_FILE = Path.home() / ".config" / "elevenlabs" / "secrets.env"


def key() -> str:
    if os.environ.get("ELEVENLABS_API_KEY"):
        return os.environ["ELEVENLABS_API_KEY"]
    if KEY_FILE.exists():
        for line in KEY_FILE.read_text().splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit("elevenlabs: no ELEVENLABS_API_KEY. Set it, or make a text and music version.")


def post(path: str, body: dict, query: str = "", timeout: int = 300) -> bytes:
    """POST JSON to the API and return the raw answer. Retries on 429, 5xx and network errors."""
    req = urllib.request.Request(
        f"{API}{path}{'?' + query if query else ''}",
        data=json.dumps(body).encode(),
        headers={"xi-api-key": key(), "Content-Type": "application/json"},
    )
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if (e.code != 429 and e.code < 500) or attempt == 4:
                sys.exit(f"elevenlabs: {e.code} on {path}: {e.read().decode(errors='replace')[:300]}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 4:
                sys.exit(f"elevenlabs: request to {path} failed: {e}")
        time.sleep(2**attempt)
    raise AssertionError("unreachable")
