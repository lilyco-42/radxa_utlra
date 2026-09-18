#!/usr/bin/env node
/**
 * gen_video_srt.mjs — 用 SRT 时间轴驱动多帧视频，并合成 TTS 音轨
 *
 * 用法:
 *   node gen_video_srt.mjs --srt tts.srt --audio tts.mp3 --out final.mp4 \
 *        [--name X] [--title 'A|B|C'] [--kicker 'AI · 科技'] [--bgm bgm.mp3] [--bgm-vol 0.12]
 *
 * 流程:
 *   SRT cue → 每帧 HTML（时长 = cue 时长）→ writeContentGraph + writeFrameHtml
 *   → exportMp4（无声）→ ffmpeg 混入 TTS（+可选 BGM）→ final.mp4
 */
import { readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { spawn } from 'node:child_process';

const HV_ROOT = process.env.HV_ROOT || '/home/radxa/html-video';

const argv = process.argv.slice(2);
const arg = (n, d) => {
  const i = argv.indexOf('--' + n);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};
const srtPath = arg('srt');
const audioPath = arg('audio');
const outPath = arg('out');
const projName = arg('name', 'auto-' + Date.now());
const kicker = arg('kicker', 'AI · 科技');
const titleRaw = arg('title', projName.replace(/[-_]/g, ' '));
const bgmPath = arg('bgm');
const bgmVol = arg('bgm-vol', '0.10');

if (!srtPath || !outPath) {
  console.error('用法: node gen_video_srt.mjs --srt tts.srt --audio tts.mp3 --out final.mp4 [--name X] [--title "A|B|C"]');
  process.exit(1);
}

// ── 1. 解析 SRT ───────────────────────────────────────
function parseSrt(text) {
  const cues = [];
  const blocks = text.replace(/\r/g, '').split(/\n\n+/);
  for (const b of blocks) {
    const lines = b.split('\n').filter((l) => l.trim());
    if (lines.length < 2) continue;
    const tm = lines.find((l) => l.includes('-->'));
    if (!tm) continue;
    const m = tm.match(/(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)/);
    if (!m) continue;
    const toSec = (h, mi, s, ms) => (+h) * 3600 + (+mi) * 60 + (+s) + (+ms) / 1000;
    const start = toSec(m[1], m[2], m[3], m[4]);
    const end = toSec(m[5], m[6], m[7], m[8]);
    const textIdx = lines.indexOf(tm) + 1;
    const txt = lines.slice(textIdx).join(' ').trim();
    if (!txt) continue;
    cues.push({ start, end, text: txt });
  }
  return cues;
}

const cues = parseSrt(await readFile(srtPath, 'utf8'));
if (cues.length === 0) {
  console.error('SRT 里没解析出任何字幕');
  process.exit(1);
}
console.log(`解析到 ${cues.length} 条字幕`);

// ── 2. 每帧 HTML ──────────────────────────────────────
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function frameHtml({ kickerText, titleHtml, bodyText, dur }) {
  return `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden}
body{background:#0e1116;color:#e8eaed;font-family:'Noto Sans SC','PingFang SC','Microsoft YaHei',system-ui,sans-serif;
display:flex;align-items:center;justify-content:center}
.card{width:88%;max-width:1560px;padding:76px 92px;border-radius:28px;
background:linear-gradient(145deg,#171c24 0%,#10141a 100%);
border:1px solid rgba(255,255,255,.07);box-shadow:0 40px 120px rgba(0,0,0,.55);
opacity:0;animation:cardIn .45s cubic-bezier(.22,1,.36,1) .03s forwards}
@keyframes cardIn{from{opacity:0;transform:translateY(22px) scale(.988)}to{opacity:1;transform:none}}
.kicker{font-size:22px;letter-spacing:.22em;color:#5b8cff;font-weight:600;margin-bottom:26px;
opacity:0;animation:fu .38s ease-out .18s forwards}
h1{font-size:78px;line-height:1.16;font-weight:700;margin-bottom:36px;opacity:0;animation:fu .42s ease-out .28s forwards}
h1 .accent{color:#5b8cff}
.body-text{font-size:46px;line-height:1.58;color:#c3cad6;opacity:0;animation:fu .42s ease-out .38s forwards}
@keyframes fu{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
.bar{position:fixed;left:0;bottom:0;height:6px;width:0;background:linear-gradient(90deg,#5b8cff,#8b5bff)}
</style></head><body>
<div class="card">
${kickerText ? `<div class="kicker">${esc(kickerText)}</div>` : ''}
${titleHtml ? `<h1>${titleHtml}</h1>` : ''}
${bodyText ? `<div class="body-text">${esc(bodyText)}</div>` : ''}
</div>
<div class="bar" id="bar"></div>
<script>
var D=${dur};
var b=document.getElementById('bar');
b.style.transition='width '+D+'s linear';
requestAnimationFrame(function(){requestAnimationFrame(function(){b.style.width='100%'})});
</script>
</body></html>`;
}

const titleParts = titleRaw.split('|');
const titleHtml = titleParts
  .map((p, i) => (i === 1 ? `<span class="accent">${esc(p)}</span>` : esc(p)))
  .join('');

const nodes = [];
const htmls = {};
cues.forEach((c, i) => {
  const id = `f${String(i + 1).padStart(2, '0')}`;
  // 帧时长 = 该 cue 时长；末帧补一点收尾
  const dur = Math.max(1.0, Math.round((c.end - c.start) * 100) / 100);
  nodes.push({ id, kind: 'text', text: c.text.slice(0, 60), durationSec: dur });
  htmls[id] = frameHtml({
    kickerText: i === 0 ? kicker : '',
    titleHtml: i === 0 ? titleHtml : '',
    bodyText: c.text,
    dur,
  });
});

const graph = {
  schemaVersion: 1,
  intent: 'explainer',
  synopsis: `${projName} — ${nodes.length} 帧（SRT 驱动）`,
  nodes,
  edges: nodes.slice(1).map((n, i) => ({ from: nodes[i].id, to: n.id, kind: 'sequence' })),
};

// ── 3. 渲染无声视频 ────────────────────────────────────
const ctxMod = await import(join(HV_ROOT, 'packages/cli/dist/context.js'));
const ctx = await ctxMod.bootstrap({ cwd: HV_ROOT });

console.log(`[1/5] 创建项目 ${projName}`);
const proj = await ctx.orchestrator.create({
  name: projName,
  intent: `SRT 驱动：${projName}`,
  preferences: { aspect: '16:9', commercial: true },
});

console.log(`[2/5] 写入内容图（${nodes.length} 帧）`);
await ctx.orchestrator.writeContentGraph(proj.id, graph);

console.log('[3/5] 写入每帧 HTML');
for (const n of nodes) {
  await ctx.orchestrator.writeFrameHtml(proj.id, n.id, htmls[n.id]);
}

console.log('[4/5] 渲染无声 MP4');
await mkdir(dirname(resolve(outPath)), { recursive: true });
const silent = resolve(outPath).replace(/\.mp4$/, '.silent.mp4');
const res = await ctx.orchestrator.exportMp4({
  projectId: proj.id,
  outputPath: silent,
  onProgress: (pct, stage) => { if (pct % 50 === 0) console.log(`      ${stage} ${pct}%`); },
});
const totalDur = nodes.reduce((s, n) => s + n.durationSec, 0);
console.log(`      无声视频: ${res.outputPath}  总时长 ${totalDur.toFixed(2)}s`);

// ── 4. ffmpeg 混音 ─────────────────────────────────────
function run(bin, args) {
  return new Promise((ok, bad) => {
    const p = spawn(bin, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let err = '';
    p.stderr.on('data', (d) => (err += d));
    p.on('close', (c) => (c === 0 ? ok() : bad(new Error(`${bin} exit ${c}: ${err.slice(-600)}`))));
  });
}

console.log('[5/5] 合成音轨');
const final = resolve(outPath);
if (audioPath && existsSync(audioPath)) {
  const args = ['-y', '-v', 'error', '-i', silent, '-i', audioPath];
  if (bgmPath && existsSync(bgmPath)) {
    args.push('-i', bgmPath);
    args.push(
      '-filter_complex',
      `[1:a]volume=1.0[voice];[2:a]volume=${bgmVol},aloop=loop=-1:size=2e9[bg];[voice][bg]amix=inputs=2:duration=first:dropout_transition=0[aout]`,
      '-map', '0:v', '-map', '[aout]',
    );
  } else {
    args.push('-map', '0:v', '-map', '1:a');
  }
  args.push('-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-shortest', final);
  await run('ffmpeg', args);
  await rm(silent, { force: true });
  console.log(`✓ 完成: ${final}`);
} else {
  console.log(`⚠️ 未提供音频，保留无声视频: ${silent}`);
  console.log(`   （用 --audio <mp3> 可合成音轨）`);
}

console.log(`  项目: ${proj.id}`);
console.log(`  帧数: ${nodes.length}  视频总时长: ${totalDur.toFixed(2)}s`);
