# motion-studio

An agent skill that makes a finished motion design video with Remotion. The agent writes, designs, animates and renders the video. You approve each gate: brief, script, style frames, transitions, animatic and final.

## Install

```sh
npx skills add henrikkvamme/motion-studio
```

## Requirements

- Node.js 22.18 or later, for Remotion and the sync check
- Python 3.10 or later, for the scripts. They use only the standard library.
- ffmpeg and ffprobe, for the sound, mastering and checks
- An ElevenLabs API key, for a voiced video. The scripts read `ELEVENLABS_API_KEY`, else the `ELEVENLABS_API_KEY=` line of `~/.config/elevenlabs/secrets.env`. Each voiced line, music bed and sound uses credits on your account. Without a key, ask for a text and music version.

## Use

In Claude Code, run `/motion-studio <topic>`. The agent starts with the brief and stops at each gate for your answer.

## Scripts

The skill's `scripts/` folder holds the tools the agent runs from the video project's root:

| Script | What it does |
|---|---|
| `narrate.py VOICE_ID` | Voices each beat of `script.md` with Eleven v4 at 48 kHz, cached by text |
| `sync.py` | Measures each phrase start from the audio and writes `video/src/sync.json` |
| `sound.py` | Makes the music bed and UI sounds from `sound.json`, cached by request |
| `master.py PREMASTER OUT` | Brings the mix to -14 LUFS with true peak at or below -1 dBTP |
| `check.py CHECK` | The mechanical gate checks: format, loudness, mix, sync, sheet, pixel and contrast |

## Development

```sh
uvx ruff check . && uvx ruff format --check .
python3 -m unittest discover -s tests -v
```

The tests make their own media with ffmpeg and use no network. CI runs both on every pull request.
