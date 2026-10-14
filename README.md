# motion-studio

An agent skill that makes a finished motion design video with Remotion. The agent writes, designs, animates and renders the video. You approve each gate: brief, script, style frames, transitions, animatic and final.

## Install

```sh
npx skills add henrikkvamme/motion-studio
```

## Requirements

- Node.js, for Remotion
- ffmpeg, for the loudness and format checks
- An ElevenLabs API key in `ELEVENLABS_API_KEY`, for a voiced video. Each voiced line uses credits on your account. Without a key, ask for a text and music version.

## Use

In Claude Code, run `/motion-studio <topic>`. The agent starts with the brief and stops at each gate for your answer.
