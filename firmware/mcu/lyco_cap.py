# lyco_cap.py —— lyco 能力层固件 (MicroPython, 全平台通用)
#
# 为什么不是一个常驻 stdin 服务?
#   micro:bit 的 MicroPython **没有 sys.stdin** (USB CDC 不可当文件读),
#   RP2040/Pico 的 MicroPython 有。所以设计成**能力库 + 可选常驻壳**:
#     · 能力一律通过本模块的 cmd() 暴露 —— 与 A7A 上 `hw` CLI 同构 (能力=CLI)
#     · micro:bit : 主机经 REPL 调用 lyco_cap.cmd("temp")
#     · RP2040    : main.py 里循环读 sys.stdin 调用同一个 cmd()  (见 main_server.py)
#
# 命令协议 (单行文本 -> "OK ..." 或 "ERR ..."):
#   help info id led blink temp accel mag btn pin pwm adc i2c radio reset
import sys
import time

try:
    import machine
except ImportError:
    machine = None

try:
    import microbit as mb
except ImportError:
    mb = None

VERSION = "lyco-cap 1.1"


def _uid():
    try:
        raw = machine.unique_id()
        if isinstance(raw, bytes):
            # micro:bit 的 MicroPython 没有 binascii, 手工转 hex
            return "".join("%02x" % b for b in raw)[:16]
        return str(raw)[:16]
    except Exception:
        pass
    try:
        # 兜底: micro:bit V2 的硬件 ID
        return str(mb.microbit_id)[:16]
    except Exception:
        return "unknown"


def cmd(line):
    """执行一条能力命令, 返回一行结果文本"""
    parts = (line or "").strip().split(None, 1)
    if not parts:
        return "ERR empty"
    c = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    try:
        if c == "help":
            return "OK commands: info id led blink temp accel mag btn pin pwm adc i2c radio reset"
        if c == "info":
            return "OK platform=%s uid=%s cap_version=%s" % (sys.platform, _uid()[:16], VERSION)
        if c == "id":
            return "OK uid=%s" % _uid()
        if c == "led":
            if mb:
                if arg in ("", "off"):
                    mb.display.clear()
                    return "OK led=off"
                mb.display.scroll(arg[:20])
                return "OK led=%s" % arg
            return "ERR no display"
        if c == "blink":
            n = int(arg or 3)
            for _ in range(n):
                if mb:
                    mb.display.show(mb.Image.HEART)
                    time.sleep(0.25)
                    mb.display.clear()
                    time.sleep(0.25)
            return "OK blinked=%d" % n
        if c == "temp":
            if mb:
                return "OK temp_c=%d" % mb.temperature()
            return "OK temp_c=%d" % machine.temperature()
        if c == "accel":
            if mb:
                x, y, z = mb.accelerometer.get_values()
                return "OK accel=%d,%d,%d" % (x, y, z)
            return "ERR no accel"
        if c == "mag":
            if mb:
                return "OK mag=%d,%d,%d" % (mb.compass.get_x(), mb.compass.get_y(), mb.compass.get_z())
            return "ERR no compass"
        if c == "btn":
            if mb:
                return "OK a=%d b=%d" % (mb.button_a.is_pressed(), mb.button_b.is_pressed())
            return "ERR no buttons"
        if c == "pin":
            n, v = arg.split()
            machine.Pin(int(n), machine.Pin.OUT).value(int(v))
            return "OK pin%s=%s" % (n, v)
        if c == "pwm":
            n, duty = arg.split()
            pw = machine.PWM(machine.Pin(int(n)))
            pw.freq(1000)
            pw.duty_u16(int(duty) * 64)
            return "OK pwm%s=%s" % (n, duty)
        if c == "adc":
            return "OK adc%s=%d" % (arg, machine.ADC(machine.Pin(int(arg))).read_u16())
        if c == "i2c":
            a = (arg.split() + ["20", "19"])[:2]
            i2c = machine.I2C(0, sda=machine.Pin(int(a[0])), scl=machine.Pin(int(a[1])), freq=100000)
            return "OK i2c=%s" % ",".join(hex(x) for x in i2c.scan())
        if c == "radio":
            if not mb:
                return "ERR no radio"
            import radio
            if arg.startswith("on"):
                radio.on()
                return "OK radio=on"
            if arg.startswith("send"):
                radio.send(arg[5:])
                return "OK sent"
            if arg.startswith("recv"):
                return "OK recv=%s" % (radio.receive() or "")
            return "OK radio"
        if c == "reset":
            machine.reset()
            return "OK resetting"
        return "ERR unknown '%s'" % c
    except Exception as e:
        return "ERR %s: %s" % (type(e).__name__, e)
