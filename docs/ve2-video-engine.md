# VE2 视频引擎 (A733 硬件 H.264/H.265 编解码)

A733 内置 Video Engine 2，官方镜像**自带完整用户态栈**（含开发头文件 + 演示程序），
所以硬件编解码开箱可用（不必额外装 SDK）。

## 硬件与软件现状

```
设备: /dev/cedar_dev   /dev/cedar_dev_ve2
内核: sunxi_ve 模块已加载
库:   libvencoder.so libvenc_base.so libvenc_codec.so libvdecoder.so   (/usr/lib/aarch64-linux-gnu)
头:   /usr/include/vencoder.h  memoryAdapter.h  veInterface.h  sc_interface.h
工具: /usr/bin/vencoderdemo
```

## 实测性能 (1280x720 / 1920x1080, H.264)

| 规格 | 帧数 | 编码器耗时 | 速度 |
|------|------|-----------|------|
| 1280x720 | 60 | ~1s | **60 fps** (2× 实时@30fps) |
| 1920x1080 | 120 | **2s** | **60 fps** (2× 实时@30fps) |

码流校验：`00 00 00 01 67 64 ... 00 00 00 01 68 ...` = 合法 H.264 Annex-B（SPS/PPS 齐全）。
**CPU 全程接近空闲** —— 这就是把视频负载从 CPU 挪到 VE2 的意义。

## 用法

```bash
hw ve2 info                                     # 设备/库/工具状态
hw ve2 encode in.yuv out.h264 1920x1080 120     # NV12 裸流 -> H.264
```

官方演示程序（底层）：
```bash
sudo vencoderdemo -i in.yuv -n 120 -f 0 -o out.h264 -s 1920x1080 -d 1920x1080 -r 30 -enc_num 1
# -f 0=H264  1=JPEG  3=H265
```

## ⚠️ 最大的坑：`VencBaseConfig` ABI 与头文件版本

从 GitHub 随便找个 `vencoder.h`（老 cedarx）会踩死坑：

```c
/* 老版 (错!) */                 /* 板上正确版 */
unsigned int nInputWidth;         unsigned char bEncH264Nalu;   // ← 第一个字段!
unsigned int nInputHeight;        unsigned int  nInputWidth;
...                               ...
VENC_PIXEL_FMT eInputFormat;      VENC_PIXEL_FMT eInputFormat;
                                  struct ScMemOpsS *memops;      // ← 必须由应用提供
                                  VeOpsS *veOpsS;
```

用错版本 → 结构体整体错位 → 报
`ERROR: cedarc h264CheckCapability: ve do not support the color_format <垃圾数值>`。

**正确做法**：直接用板上的 `/usr/include/vencoder.h`，并链接 `-lvencoder -lMemAdapter -lVE`，
填入 `cfg.memops = MemAdapterGetOpsS()`（`CdcMemOpen` 打开）。

## 下一步

- [ ] **硬解码**（libvdecoder / `/usr/bin/vdecoderdemo`）验证
- [ ] 完整链路：摄像头/文件 → VE2 编码 → 网络推流 / 存储
- [ ] 路由器接入：`hw ve2 encode ...` 已可被模型路由调用
