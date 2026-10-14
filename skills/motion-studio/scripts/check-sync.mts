// The sync check: every cue in video/src/timing.ts against the speech in the beat WAVs.
// Usage (from the project root): node SKILL_DIR/scripts/check-sync.mts
//
// timing.ts must export FPS, BEATS (each {n, voice, speech}: the first frame and the length in
// frames of beat n's voice line) and CUES (name -> frame), and may import only JSON files.
// The beat WAVs are video/public/voice/beat-NN.wav, as narrate.py writes them.
//
// It measures the audio itself (ffmpeg silencedetect, as sync.py does), so it does not trust
// Eleven v4's timestamps. A cue on the first word after a pause must be within 2 frames of
// that speech onset: "verified". A cue inside a phrase must fall inside speech: "estimated",
// because only its phrase start is measured. A cue outside every voice line fails.
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = process.cwd();
const src = join(root, "video/src");

// timing.ts imports its JSON the bundler's way; Node needs absolute paths and the import attribute.
const timing = readFileSync(join(src, "timing.ts"), "utf8");
const relative = [...timing.matchAll(/from\s+["'](\.[^"']*)["']/g)].map((m) => m[1]).filter((p) => !p.endsWith(".json"));
if (relative.length) {
  console.error(`check-sync: timing.ts may import only JSON files, not ${relative.join(", ")}`);
  process.exit(2);
}
const probe = join(mkdtempSync(join(tmpdir(), "motion-sync-")), "timing.mts");
writeFileSync(
  probe,
  timing.replace(
    /from\s+["'](\.[^"']*\.json)["'];?/g,
    (_, p: string) => `from ${JSON.stringify(resolve(src, p))} with { type: "json" };`,
  ),
);
const { BEATS, CUES, FPS } = await import(probe);
type Beat = { n: number; voice: number; speech: number };

/** Speech segments in a WAV, in seconds: the spans between pauses of 0.2 s or more. */
const speech = (wav: string, seconds: number): [number, number][] => {
  const run = spawnSync("ffmpeg", ["-hide_banner", "-nostats", "-i", wav, "-af", "silencedetect=noise=-40dB:d=0.2", "-f", "null", "-"], {
    encoding: "utf8",
  });
  if (run.status !== 0) {
    console.error(`check-sync: ffmpeg could not read ${wav}`);
    process.exit(2);
  }
  const starts = [...run.stderr.matchAll(/silence_start: ([\d.]+)/g)].map((m) => Number(m[1]));
  const ends = [...run.stderr.matchAll(/silence_end: ([\d.]+)/g)].map((m) => Number(m[1]));
  const out: [number, number][] = [];
  let t = 0;
  starts.forEach((s, i) => {
    if (s > t) out.push([t, s]);
    t = ends[i] ?? seconds;
  });
  if (t < seconds) out.push([t, seconds]);
  return out;
};

const tolerance = 2 / FPS;
const counts = { verified: 0, estimated: 0, FAIL: 0 };
for (const [name, frame] of Object.entries(CUES as Record<string, number>)) {
  const beat = (BEATS as Beat[]).find((b) => frame >= b.voice && frame <= b.voice + b.speech);
  if (!beat) {
    console.log(`FAIL      ${name.padEnd(18)} frame ${frame} is outside every voice line`);
    counts.FAIL++;
    continue;
  }
  const wav = join(root, `video/public/voice/beat-${String(beat.n).padStart(2, "0")}.wav`);
  const segs = speech(wav, beat.speech / FPS);
  const t = (frame - beat.voice) / FPS;
  const onset = segs.some(([s]) => Math.abs(s - t) <= tolerance);
  const inside = segs.some(([s, e]) => t >= s - tolerance && t <= e + tolerance);
  const state = onset ? "verified" : inside ? "estimated" : "FAIL";
  counts[state]++;
  console.log(`${state.padEnd(9)} ${name.padEnd(18)} beat ${String(beat.n).padStart(2)}  ${t.toFixed(2)} s  frame ${frame}`);
}
console.log(`\n${counts.verified} verified, ${counts.estimated} estimated, ${counts.FAIL} off their speech.`);
process.exit(counts.FAIL ? 1 : 0);
