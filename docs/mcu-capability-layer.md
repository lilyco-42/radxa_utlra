# MCU 能力层 (micro:bit / RP2040)

把 MCU 当成 A7A 的**能力节点**：主机不需要知道硬件细节，只发命令读结果 —— 与 `hw` CLI 同构，
所以**路由器可以把自然语言意图直接映射到这些命令**（"现在多少度" → `hw mcu temp`）。

## 架构

```
A7A (Linux)                         MCU (micro:bit / RP2040)
  hw mcu <cmd>  ──USB CDC──►  lyco_cap.cmd("<cmd>")  ──►  硬件
                                (能力库, 返回 "OK ..."/"ERR ...")
```

- **`lyco_cap.py`** —— 能力库（全平台通用）：`cmd(line) -> "OK ..."`，命令集：
  `help info id led blink temp accel mag btn pin pwm adc i2c radio reset`
- **`main_server.py`**（RP2040/Pico 专用）—— 常驻服务：循环读 `sys.stdin`，把每行交给 `lyco_cap.cmd()`
- **`main_idle.py`**（micro:bit 用）—— 开机亮心后空闲（**micro:bit 的 MicroPython 没有 `sys.stdin`**，
  无法常驻读命令，能力改由主机经 REPL 调用）
- 主机侧：`scripts/mb.py`（REPL 客户端：scan/info/cap/put/run/deploy）

## 用法

```bash
hw mcu list                 # 列出 MCU 串口
hw mcu info                 # 平台/UID/固件版本
hw mcu temp                 # 温度
hw mcu accel                # 加速度
hw mcu led HI               # 点阵滚动显示
hw mcu blink 3              # 闪灯
hw mcu pin 1 1              # GPIO 置位
hw mcu pwm 0 512            # PWM 占空比
hw mcu i2c 20 19            # I2C 扫描
hw mcu radio on             # 无线开
```

## 实测 (micro:bit V2, nRF52833, MicroPython v1.18)

```
hw mcu info  -> OK platform=microbit uid=7cd8aaf9aed22be7 cap_version=lyco-cap 1.1
hw mcu temp  -> OK temp_c=26
hw mcu accel -> OK accel=-92,260,996
hw mcu btn   -> OK a=0 b=0
```

## 部署

```bash
# micro:bit
python3 mb.py put firmware/mcu/lyco_cap.py lyco_cap.py
python3 mb.py put firmware/mcu/main_idle.py main.py
python3 mb.py raw "import machine;machine.reset()"

# RP2040 / Pico
python3 mb.py put firmware/mcu/lyco_cap.py lyco_cap.py
python3 mb.py put firmware/mcu/main_server.py main.py
python3 mb.py raw "import machine;machine.reset()"
```

## 坑

1. **micro:bit MicroPython 无 `sys.stdin`** → 常驻服务跑不了，只能 REPL 驱动
2. **多字符丢失**: 直接 printf 拼命令经 shell 多层转义会丢字符（`import` 变 `impot`）→ 用 pyserial + raw-paste
3. **`mpremote`/`ampy` 在 micro:bit v2 + MicroPython 1.18 上不可用**（无文件系统抽象差异）→ 自己写 REPL 客户端更稳
4. 串口要 `dialout` 组权限: `sudo usermod -aG dialout radxa`
