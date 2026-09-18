#!/usr/bin/env node
/**
 * gen_video.mjs — 把文案变成多帧视频
 *
 * 用法:
 *   node gen_video.mjs --script script.txt --out /path/to/out.mp4 [--name proj-name]
 *
 * 原理:
 *   读文案 → 按句切帧 → 每帧生成 HTML（内嵌内容，不依赖 __HV_VARS__）
 *   → writeContentGraph + writeFrameHtml（每帧独立 durationSec）
 *   → exportMp4（逐帧渲染后 concat）
 */
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

const HV_ROOT = process.env.HV_ROOT || '/home/radxa/html-video';

// ── 参数 ──────────────────────────────────────────────
const argv = process.argv.slice(2);
function arg(name, def) {
  const i = argv.indexOf('--' + name);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : def;
}
const scriptPath = arg('script');
const outPath = arg('out');
const projName = arg('name', 'auto-' + Date.now());
const kicker = arg('kicker', 'AI · 科技');
const cps = parseFloat(arg('cps', '5.2'));   // 每秒朗读字数（中文约 4-6）

if (!scriptPath || !outPath) {
  console.error('用法: node gen_video.mjs --script script.txt --out out.mp4 [--name X] [--kicker "AI · 科技"]');
  process.exit(1);
}

// ── 1. 读文案并切帧 ────────────────────────────────────
const raw = await readFile(scriptPath, 'utf8');
// 按中英文句末标点切句
const sentences = raw
  .replace(/\s+/g, ' ')
  .split(/(?<=[。！？!?；;])/)
  .map((s) => s.trim())
  .filter(Boolean);

if (sentences.length === 0) {
  console.error('文案为空');
  process.exit(1);
}

// 每 1-2 句合成一帧，避免帧太碎
const frames = [];
for (let i = 0; i < sentences.length; i += 2) {
  const chunk = sentences.slice(i, i + 2).join('');
  frames.push(chunk);
}

// 第一帧是标题帧
const title = (arg('title') || projName).replace(/[-_]/g, ' ');

// ── 2. 每帧 HTML（内嵌内容）────────────────────────────
function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function frameHtml({ kickerText, titleText, bodyText, accent }) {
  return `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden}
body{background:#0e1116;color:#e8eaed;font-family:'Noto Sans SC','PingFang SC','Microsoft YaHei',system-ui,sans-serif;
display:flex;align-items:center;justify-content:center}
.card{width:86%;max-width:1500px;padding:76px 92px;border-radius:28px;
background:linear-gradient(145deg,#171c24 0%,#10141a 100%);
border:1px solid rgba(255,255,255,.07);box-shadow:0 40px 120px rgba(0,0,0,.55);
opacity:0;animation:cardIn .55s cubic-bezier(.22,1,.36,1) .05s forwards}
@keyframes cardIn{from{opacity:0;transform:translateY(26px) scale(.985)}to{opacity:1;transform:none}}
.kicker{font-size:22px;letter-spacing:.22em;color:#5b8cff;font-weight:600;margin-bottom:26px;
opacity:0;animation:fu .45s ease-out .25s forwards}
h1{font-size:78px;line-height:1.16;font-weight:700;margin-bottom:38px;opacity:0;animation:fu .5s ease-out .38s forwards}
h1 .accent{color:#5b8cff}
.body-text{font-size:42px;line-height:1.6;color:#b8bfcc;opacity:0;animation:fu .5s ease-out .52s forwards}
@keyframes fu{from{opacity:0;transform:translateY(18px)}to{opacity:1;transform:none}}
.bar{position:fixed;left:0;bottom:0;height:6px;width:0;background:linear-gradient(90deg,#5b8cff,#8b5bff)}
</style></head><body>
<div class="card">
${kickerText ? `<div class="kicker">${esc(kickerText)}</div>` : ''}
${titleText ? `<h1>${titleText}</h1>` : ''}
${bodyText ? `<div class="body-text">${esc(bodyText)}</div>` : ''}
</div>
<div class="bar" id="bar"></div>
<script>
var D=__DURATION__;
var b=document.getElementById('bar');
b.style.transition='width '+D+'s linear';
requestAnimationFrame(function(){requestAnimationFrame(function(){b.style.width='100%'})});
</script>
</body></html>`;
}

// ── 3. 组装 graph ─────────────────────────────────────
const nodes = [];
const htmls = {};

frames.forEach((text, i) => {
  const id = `f${String(i + 1).padStart(2, '0')}`;
  const dur = Math.max(2.5, Math.round((text.length / cps) * 10) / 10);
  nodes.push({ id, kind: 'text', text: text.slice(0, 60), durationSec: dur });

  let titleHtml = '';
  let bodyText = '';
  if (i === 0) {
    // 首帧：标题 + 正文
    const parts = title.split('|');
    titleHtml = parts
      .map((p, k) => (k === 1 ? `<span class="accent">${esc(p)}</span>` : esc(p)))
      .join('');
    bodyText = text;
  } else {
    bodyText = text;
  }
  htmls[id] = frameHtml({
    kickerText: i === 0 ? kicker : '',
    titleText: titleHtml,
    bodyText,
  }).replace('__DURATION__', String(dur));
});

const graph = {
  schemaVersion: 1,
  intent: 'explainer',
  synopsis: `${projName} — ${nodes.length} 帧`,
  nodes,
  edges: nodes.slice(1).map((n, i) => ({ from: nodes[i].id, to: n.id, kind: 'sequence' })),
};

// ── 4. 调用 html-video core ───────────────────────────
const ctxMod = await import(join(HV_ROOT, 'packages/cli/dist/context.js'));
const ctx = await ctxMod.bootstrap({ cwd: HV_ROOT });

console.log(`[1/4] 创建项目 ${projName}`);
const proj = await ctx.orchestrator.create({
  name: projName,
  intent: `自动生成：${projName}`,
  preferences: { aspect: '16:9', commercial: true },
});

console.log(`[2/4] 写入内容图（${nodes.length} 帧）`);
await ctx.orchestrator.writeContentGraph(proj.id, graph);

console.log('[3/4] 写入每帧 HTML');
for (const n of nodes) {
  await ctx.orchestrator.writeFrameHtml(proj.id, n.id, htmls[n.id]);
  console.log(`      ${n.id}  ${n.durationSec}s  ${n.text.slice(0, 24)}…`);
}

console.log('[4/4] 渲染 MP4');
await mkdir(dirname(resolve(outPath)), { recursive: true });
const res = await ctx.orchestrator.exportMp4({
  projectId: proj.id,
  outputPath: resolve(outPath),
  onProgress: (pct, stage) => {
    if (pct % 25 === 0) console.log(`      ${stage} ${pct}%`);
  },
});

console.log(`\n✓ 完成: ${res.outputPath}`);
console.log(`  项目: ${proj.id}`);
console.log(`  帧数: ${nodes.length}  总时长: ${nodes.reduce((s, n) => s + n.durationSec, 0).toFixed(1)}s`);
