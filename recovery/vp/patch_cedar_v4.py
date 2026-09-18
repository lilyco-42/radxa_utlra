#!/usr/bin/env python3
"""Patch html-video render.js v4:
  - Fix pgrep guard: use spawnSync instead of execSync (shell syntax issue)
  - Cedar VE2 with Level 40 for 1080p, 31 for 720p
  - 15s timeout (reduced from 30s — D-state hang needs fast fallback)
  - Fallback: libx264 veryfast (was medium)
"""
import sys
from pathlib import Path

RENDER_JS = Path("/home/radxa/html-video/packages/adapter-hyperframes/dist/render.js")
BACKUP = RENDER_JS.with_suffix(".js.bak-cedar-20260917")

OLD_BLOCK = """    await runFfmpeg([
        '-y',
        // -ss before -i = fast input seek, drops the frozen lead-in entirely.
        ...(seekSec > 0 ? ['-ss', seekSec.toFixed(3)] : []),
        '-i', webmPath,
        // Pad-then-trim so an explicit per-frame length lands exactly (e.g. user
        // asked 4s, animation ran 2.8s \u2192 hold the final frame to fill 4s).
        ...(explicit ? ['-vf', `tpad=stop_mode=clone:stop_duration=${totalDuration}`] : []),
        // Force exact duration: playwright's recordVideo sometimes overshoots
        // by the time it takes to close the context. -t trims to the requested
        // length (seconds, accepts fractions).
        '-t', String(totalDuration),
        '-r', String(fps),
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-preset', 'medium',
        '-crf', '20',
        '-movflags', '+faststart',
        input.config.outputPath,
    ]);"""

NEW_BLOCK = """    // ── Hardware encoding: Cedar VE2 \u2192 fallback libx264 veryfast ──
    const _w = input.config.resolution.width;
    const _h = input.config.resolution.height;
    const _cedarBin = '/home/radxa/a733-cedarc/aw-h264-to-mp4';
    const _cedarDev = '/dev/cedar_dev_ve2';
    const _level = (_w >= 1920 || _h >= 1080) ? 40 : 31;
    let _usedCedar = false;
    // Guard: skip Cedar if another aw-h264-encoder is running (hardware may be stuck in D-state)
    const { spawnSync } = await import('node:child_process');
    const _pgrep = spawnSync('pgrep', ['-f', 'aw-h264-encoder'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
    const _cedarBusy = _pgrep.status === 0 && _pgrep.stdout.trim().length > 0;
    if (!_cedarBusy && existsSync(_cedarDev) && existsSync(_cedarBin) && _w % 16 === 0 && _h % 2 === 0) {
        try {
            const _ffArgs = [
                '-y',
                ...(seekSec > 0 ? ['-ss', seekSec.toFixed(3)] : []),
                '-i', webmPath,
                ...(explicit ? ['-vf', `tpad=stop_mode=clone:stop_duration=${totalDuration}`] : []),
                '-t', String(totalDuration),
                '-r', String(fps),
                '-pix_fmt', 'nv12',
                '-f', 'rawvideo',
                'pipe:1',
            ];
            const _cedarArgs = [
                '--width', String(_w),
                '--height', String(_h),
                '--fps', String(fps),
                '--level', String(_level),
                '--output', input.config.outputPath,
                '--force',
            ];
            await new Promise((resolve, reject) => {
                const ff = spawn('ffmpeg', _ffArgs, { stdio: ['ignore', 'pipe', 'pipe'] });
                const cedar = spawn(_cedarBin, _cedarArgs, { stdio: ['pipe', 'pipe', 'pipe'] });
                let cedarErr = '', ffErr = '';
                ff.stderr.on('data', (c) => { ffErr += c.toString('utf8'); });
                cedar.stderr.on('data', (c) => { cedarErr += c.toString('utf8'); });
                ff.stdout.pipe(cedar.stdin);
                let ffDone = false, cedarDone = false, ffCode = null, cedarCode = null;
                const _timer = setTimeout(() => {
                    try { ff.kill('SIGKILL'); } catch {}
                    try { cedar.kill('SIGKILL'); } catch {}
                    reject(new Error('Cedar encode timeout (15s)'));
                }, 15000);
                const finish = () => {
                    if (ffDone && cedarDone) {
                        clearTimeout(_timer);
                        if (ffCode === 0 && cedarCode === 0) resolve();
                        else reject(new Error(`Cedar: ff=${ffCode} cedar=${cedarCode} | ${cedarErr.slice(-500)}`));
                    }
                };
                ff.on('exit', (c) => { ffDone = true; ffCode = c; if (c !== 0) cedar.stdin.end(); finish(); });
                cedar.on('exit', (c) => { cedarDone = true; cedarCode = c; finish(); });
                ff.on('error', () => { ffDone = true; ffCode = -1; finish(); });
                cedar.on('error', () => { cedarDone = true; cedarCode = -1; finish(); });
            });
            _usedCedar = true;
        } catch (e) {
            // Cedar failed \u2014 fall through to libx264
        }
    }
    if (!_usedCedar) {
        await runFfmpeg([
            '-y',
            ...(seekSec > 0 ? ['-ss', seekSec.toFixed(3)] : []),
            '-i', webmPath,
            ...(explicit ? ['-vf', `tpad=stop_mode=clone:stop_duration=${totalDuration}`] : []),
            '-t', String(totalDuration),
            '-r', String(fps),
            '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p',
            '-preset', 'veryfast',
            '-crf', '23',
            '-movflags', '+faststart',
            input.config.outputPath,
        ]);
    }"""

def main():
    content = BACKUP.read_text(encoding="utf-8")
    if OLD_BLOCK not in content:
        print("ERROR: old block not found in backup", file=sys.stderr)
        sys.exit(1)
    content = content.replace(OLD_BLOCK, NEW_BLOCK, 1)
    RENDER_JS.write_text(content, encoding="utf-8")
    print(f"Patched {RENDER_JS} (v4)")
    print("  - Fixed pgrep guard: spawnSync instead of execSync")
    print("  - 15s timeout (reduced from 30s)")
    print("  - Level 40 for 1080p, 31 for 720p")
    print("  - Fallback: libx264 veryfast")

if __name__ == "__main__":
    main()
