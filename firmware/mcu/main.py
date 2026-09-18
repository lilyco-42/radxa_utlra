# main.py —— lyco 能力服务固件 (MicroPython / micro:bit V2 + RP2040 通用)
#
# 设计对齐 lyco_agent 的架构: **能力 = CLI**。
# 设备通过 USB CDC 串口暴露一组命令, 主机 (Radxa A7A) 无需知道硬件细节,
# 只需发命令读结果 —— 与 A7A 上 `hw` CLI 完全同构, 路由器可把意图直接映射到这些命令。
#
# 协议: 每行一条命令, 回复一行, 以 OK / ERR 开头 (便于主机解析)
#   help | info | id | led <文本|off> | blink <n> | temp | accel | mag | btn
#   pin <n> <0|1> | pwm <n> <0-1023> | adc <n> | i2c <sda> <scl> | radio on|send <m>|recv
#
import sys
import time

try:
    import machine
    import microbit as mb
except ImportError:                      # 非 micro:bit 的 MicroPython (如 RP2040)
    mb = None
    machine = __import__("machine")

VERSION = "lyco-cap 1.0"


def uid():
    try:
        import binascii
        return binascii.hexlify(machine.unique_id()).decode()
    except Exception:
        return "unknown"


def p(*a):
    print(*a)


def do_info():
    p("OK model=%s uid=%s ver=%s" % (sys.platform, uid()[:16], VERSION))


def readline():
    return sys.stdin.readline()


def main():
    buf = ""
    p("")                                # 让 REPL 换行干净
    p("=== lyco capability server %s ===" % VERSION)
    p("model=%s uid=%s" % (sys.platform, uid()[:16]))
    p("type 'help' for commands")
    while True:
        try:
            line = readline()
            if not line:
                time.sleep(0.02)
                continue
            cmd = line.strip()
            if not cmd:
                continue
            parts = cmd.split(None, 1)
            c = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if c == "help":
                p("OK help info id led blink temp accel mag btn pin pwm adc i2c radio")
            elif c == "info":
                do_info()
            elif c == "id":
                p("OK uid=%s" % uid())
            elif c == "led":
                if mb:
                    if arg in ("off", ""):
                        mb.display.clear()
                    else:
                        mb.display.scroll(arg[:20])
                    p("OK led=%s" % (arg or "off"))
                else:
                    p("ERR no display")
            elif c == "blink":
                n = int(arg or 3)
                for _ in range(n):
                    if mb:
                        mb.display.show(mb.Image.HEART)
                        time.sleep(0.25)
                        mb.display.clear()
                        time.sleep(0.25)
                p("OK blinked=%d" % n)
            elif c == "temp":
                if mb:
                    p("OK temp_c=%d" % mb.temperature())
                else:
                    p("OK temp_c=%d" % machine.temperature())
            elif c == "accel":
                if mb:
                    p("OK accel=%s" % (mb.accelerometer.get_values(),))
                else:
                    p("ERR no accel")
            elif c == "mag":
                if mb:
                    p("OK mag=%d,%d,%d" % (mb.compass.get_x(), mb.compass.get_y(), mb.compass.get_z()))
                else:
                    p("ERR no compass")
            elif c == "btn":
                if mb:
                    p("OK a=%d b=%d" % (mb.button_a.is_pressed(), mb.button_b.is_pressed()))
                else:
                    p("ERR no buttons")
            elif c == "pin":
                n, v = arg.split()
                machine.Pin(int(n), machine.Pin.OUT).value(int(v))
                p("OK pin%s=%s" % (n, v))
            elif c == "pwm":
                n, duty = arg.split()
                pw = machine.PWM(machine.Pin(int(n)))
                pw.freq(1000)
                pw.duty_u16(int(duty) * 64)
                p("OK pwm%s=%s" % (n, duty))
            elif c == "adc":
                p("OK adc%s=%d" % (arg, machine.ADC(machine.Pin(int(arg))).read_u16()))
            elif c == "i2c":
                sda, scl = (arg.split() + ["20", "19"])[:2]
                i2c = machine.I2C(0, sda=machine.Pin(int(sda)), scl=machine.Pin(int(scl)), freq=100000)
                devs = [hex(x) for x in i2c.scan()]
                p("OK i2c=%s" % ",".join(devs))
            elif c == "radio":
                if not mb:
                    p("ERR no radio")
                else:
                    import radio
                    if arg.startswith("on"):
                        radio.on()
                        p("OK radio=on")
                    elif arg.startswith("send"):
                        radio.send(arg[5:])
                        p("OK sent")
                    elif arg.startswith("recv"):
                        p("OK recv=%s" % (radio.receive() or ""))
                    else:
                        p("OK radio")
            elif c == "reset":
                p("OK resetting")
                time.sleep(0.3)
                machine.reset()
            else:
                p("ERR unknown '%s'" % c)
        except Exception as e:
            p("ERR %s: %s" % (type(e).__name__, e))


main()
