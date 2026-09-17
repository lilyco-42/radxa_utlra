# `hw` —— A7A 硬件统一 CLI

一个脚本把板子的硬件能力收敛成**一条命令**，给人和给模型用同一套接口
（"能力 = CLI"，模型只做意图路由，不直接摸硬件）。

```bash
sudo hw led blue on|off|status|blink [n]    # 蓝灯（状态灯）
sudo hw led green on|off|status             # 绿灯（电源灯）
sudo hw temp                                # 8 个温度区（cpub/cpul/ddr/npu/gpu/skin…）
sudo hw cpu                                 # 频率 / governor / 负载
sudo hw mem | hw disk | hw info             # 内存 / 磁盘 / 板子信息
sudo hw fan [0-255]                         # 风扇 PWM（hwmon0 pwmfan）
sudo hw gpio get|set <chip> <line> [0|1]    # GPIO（gpiod v2，gpio 组免 root）
```

## 安装

```bash
sudo install -m 755 scripts/hw /usr/local/bin/hw
echo "radxa ALL=(root) NOPASSWD: /usr/local/bin/hw" | sudo tee /etc/sudoers.d/radxa-hw
sudo chmod 440 /etc/sudoers.d/radxa-hw
```

## 权限模型

- LED / 风扇的 sysfs 写入需要 root → sudoers **只对本脚本免密**（最小权限）
- GPIO 走 gpiod，`radxa` 在 `gpio` 组，本就免 root

## 三个写 shell 脚本必踩的坑（都在本项目里踩过）

1. **脚本头必须 `export PATH`** —— 回放/受限环境的 PATH 可能是空的，
   内部 `awk`/`cat` 找不到就报 127
   ```sh
   PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; export PATH
   ```
2. **`for` 循环末尾 `[ -n "$v" ] && echo` 为假时整脚本 exit 1** —— 只读分支要显式 `exit 0`
3. **sudoers 命令规格不能含带冒号的参数路径**（`radxa:blue:user` 的 `:` 撞 sudoers 语法）
   → 所以必须包一层脚本，**只对脚本免密**

## 实测（A7A，Debian 13 trixie / 6.6.98-4-aw2511）

```
$ sudo hw led blue on      → blue led: on（回读 brightness=1）
$ sudo hw fan 100          → fan pwm=100（读回一致）
$ sudo hw temp             → cpub 35.5°C / ddr 36.2°C / npu 34.7°C / gpu 34.7°C / skin 31.0°C
```
