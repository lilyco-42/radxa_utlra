#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kws_features_test.py —— 真音频特征 → NPU encoder 最小验证

encoder_float_a733.nb 的 I/O 规格(vpm_run 实测):
  input 0 : 80 x 29   ← 80 维 fbank x 29 帧 (唯一真正的音频输入)
  input 1..37: cache/状态 (首帧填 0, 流式时回灌上帧输出)
  input 38 : 标量
  output 0: 320 x 4 (encoder 输出) + 其余为下一帧的 cache

用法: python3 kws_features_test.py [wav路径]  (不给则生成一段扫频音)
"""
import struct
import subprocess
import sys
import wave

import numpy as np

D = "/home/radxa/npu-sdk/examples/vpm_run/operator/v2"
RUN = "/home/radxa/npu-bin/usr/bin/vpm_run"
SR = 16000
N_MEL = 80
FRAMES = 29          # encoder input 0 的第二维
HOP = 160            # 10ms
WIN = 400            # 25ms


def load_or_make_wav(path):
    if path:
        with wave.open(path, "rb") as w:
            assert w.getframerate() == SR, f"需要 16k, 实为 {w.getframerate()}"
            n = w.getnframes()
            x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
        return x
    # 扫频音 (200Hz→3kHz), 1 秒
    t = np.arange(SR) / SR
    freq = 200 + (3000 - 200) * t
    x = 0.3 * np.sin(2 * np.pi * np.cumsum(freq) / SR)
    return x.astype(np.float32)


def hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def mel_filterbank(n_mels=N_MEL, n_fft=512):
    lo, hi = hz_to_mel(20.0), hz_to_mel(min(8000, SR / 2))
    pts = np.linspace(lo, hi, n_mels + 2)
    hz = 700.0 * (10 ** (pts / 2595.0) - 1.0)
    bins = np.floor((n_fft + 1) * hz / SR).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for m in range(1, n_mels + 1):
        l, c, r = bins[m - 1], bins[m], bins[m + 1]
        if c == l:
            c += 1
        if r == c:
            r = c + 1
        for k in range(l, c):
            fb[m - 1, k] = (k - l) / (c - l)
        for k in range(c, r):
            fb[m - 1, k] = (r - k) / (r - c)
    return fb


def fbank(x):
    """返回 (T, 80) log-mel"""
    fb = mel_filterbank()
    win = np.hanning(WIN).astype(np.float32)
    need = (FRAMES - 1) * HOP + WIN
    if len(x) < need:
        x = np.pad(x, (0, need - len(x)))
    out = np.zeros((FRAMES, N_MEL), dtype=np.float32)
    for i in range(FRAMES):
        seg = x[i * HOP:i * HOP + WIN] * win
        sp = np.abs(np.fft.rfft(seg, n=512)) ** 2
        out[i] = np.log(np.maximum(fb @ sp, 1e-10))
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    x = load_or_make_wav(path)
    f = fbank(x)                      # (29, 80)
    print(f"音频 {len(x)} 样点 ({len(x)/SR:.2f}s) → fbank {f.shape}, "
          f"均值 {f.mean():.3f} 标准差 {f.std():.3f}")

    # input 0 = 80 x 29 → 按 vpm 的 dim 顺序展平 (80 是 dim[0], 29 是 dim[1])
    f.tofile(f"{D}/e0.dat")
    for i in range(1, 39):            # 其余 38 个: 状态/缓存, 首帧填 0
        if i == 38:
            np.zeros(1, dtype=np.float32).tofile(f"{D}/e{i}.dat")
        else:
            # 尺寸按实测 dims 填零 (这里用 4096 字节占位, vpm 按 dim 读)
            np.zeros(1024, dtype=np.float32).tofile(f"{D}/e{i}.dat")

    import os
    env = dict(os.environ, LD_LIBRARY_PATH="/home/radxa/lib")
    r = subprocess.run([RUN, "-s", "s_enc39.txt", "-l", "1"], cwd=D, env=env,
                       capture_output=True, text=True, timeout=180)
    tail = [l for l in (r.stdout + r.stderr).splitlines()
            if any(k in l for k in ("run network", "inference time", "ret=", "error", "fail"))]
    print("--- NPU encoder 结果 ---")
    print("\n".join(tail[-6:]))


if __name__ == "__main__":
    main()
