#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mb.py —— micro:bit / RP2040 MicroPython REPL 客户端 (板上运行)

用法:
  python3 mb.py scan                      # 列出所有 MicroPython 串口
  python3 mb.py info                      # 读设备信息/传感器
  python3 mb.py raw "print(1+1)"          # 执行一条 MicroPython 语句
  python3 mb.py put <本地文件> <远程名>    # 写入文件到设备 (经 REPL)
  python3 mb.py run <远程文件>             # 执行设备上的文件
  python3 mb.py deploy <firmware.py>      # 部署能力服务并重启

设计: 走 USB CDC 的 MicroPython REPL (raw-paste 模式), 不依赖 ampy/mpremote。
"""
import glob
import os
import sys
import time

import serial


def find_ports():
    ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return ports


def open_port(dev=None, baud=115200, timeout=2):
    dev = dev or (find_ports() or [None])[0]
    if not dev:
        print("找不到串口设备")
        sys.exit(1)
    s = serial.Serial(dev, baud, timeout=timeout)
    s.dtr = True
    s.rts = True
    time.sleep(0.3)
    s.reset_input_buffer()
    return s


def enter_raw(s):
    """进入 raw-paste 模式 (Ctrl-A), 失败则退回普通 REPL"""
    s.write(b"\x03\x03")          # Ctrl-C 打断当前程序
    time.sleep(0.3)
    s.reset_input_buffer()
    s.write(b"\x01")              # Ctrl-A raw-paste
    time.sleep(0.3)
    resp = s.read(64)
    return b"raw REPL" in resp or resp == b""


def exit_raw(s):
    s.write(b"\x02")              # Ctrl-D 执行并退出 raw
    time.sleep(0.2)


def exec_raw(s, code):
    """在 raw-paste 模式下执行代码, 返回输出文本"""
    if not enter_raw(s):
        # 退化: 普通 REPL, 用 print 包裹
        s.reset_input_buffer()
        s.write(("\r\x03" + code.replace("\n", "\r\n") + "\r").encode())
        time.sleep(1.0)
        return s.read(4096).decode("utf-8", "replace")
    s.write(code.replace("\n", "\r\n").encode() + b"\x04")
    time.sleep(0.2)
    out = b""
    t0 = time.time()
    while time.time() - t0 < 8:
        chunk = s.read(256)
        if chunk:
            out += chunk
            if b"\x04>" in out or out.endswith(b"\x04"):
                break
        elif out:
            break
    exit_raw(s)
    txt = out.decode("utf-8", "replace")
    return txt.replace("\x04", "").replace(">", "").strip()


def cmd_scan():
    for p in find_ports():
        try:
            s = serial.Serial(p, 115200, timeout=1)
            s.write(b"\x03\x03")
            time.sleep(0.5)
            banner = s.read(256).decode("utf-8", "replace").strip()
            s.close()
            # 尝试读 MicroPython 唯一 ID
            s = serial.Serial(p, 115200, timeout=2)
            uid = exec_raw(s, "import machine;print(machine.unique_id().hex())")
            s.close()
            print(f"{p}  uid={uid or '?'}")
        except Exception as e:
            print(f"{p}  打不开: {e}")


def cmd_info(dev=None):
    s = open_port(dev)
    code = (
        "import os,microbit\n"
        "print('UNAME:',os.uname())\n"
        "print('UID:',__import__('machine').unique_id().hex())\n"
        "print('TEMP:',microbit.temperature())\n"
        "print('ACCEL:',microbit.accelerometer.get_values())\n"
        "print('MAG:',microbit.compass.get_x(),microbit.compass.get_y(),microbit.compass.get_z())\n"
        "print('BTN:',microbit.button_a.is_pressed(),microbit.button_b.is_pressed())\n"
        "print('FILES:',os.listdir())\n"
    )
    print(exec_raw(s, code))
    s.close()


def cmd_raw(code):
    s = open_port()
    print(exec_raw(s, code))
    s.close()


def cmd_put(src, dst):
    data = open(src, "r", encoding="utf-8").read()
    s = open_port()
    # 分块写文件 (避免单次 raw 执行过长)
    lines = data.split("\n")
    exec_raw(s, f"f=open('{dst}','w')")
    for i in range(0, len(lines), 20):
        chunk = "\n".join(lines[i:i + 20])
        exec_raw(s, "f.write(%r)" % (chunk + "\n"))
    exec_raw(s, "f.close()")
    out = exec_raw(s, f"import os;print('WROTE',os.stat('{dst}')[6])")
    print(out)
    s.close()


def cmd_cap(args):
    """经 REPL 调用设备上的能力层, 例如: python3 mb.py cap temp"""
    s = open_port()
    code = "import lyco_cap;print(lyco_cap.cmd(%r))" % args
    print(exec_raw(s, code))
    s.close()


def cmd_run(remote):
    s = open_port()
    print(exec_raw(s, f"exec(open('{remote}').read())"))
    s.close()


def cmd_deploy(fw):
    """部署 main.py 并软重启"""
    print(f"== 部署 {fw} -> main.py ==")
    cmd_put(fw, "main.py")
    s = open_port()
    print("重启设备...")
    s.write(b"\x04")   # Ctrl-D 软重启
    time.sleep(3)
    print(s.read(2048).decode("utf-8", "replace"))
    s.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    a = sys.argv[1]
    if a == "scan":
        cmd_scan()
    elif a == "info":
        cmd_info(sys.argv[2] if len(sys.argv) > 2 else None)
    elif a == "raw":
        cmd_raw(sys.argv[2])
    elif a == "put":
        cmd_put(sys.argv[2], sys.argv[3])
    elif a == "cap":
        cmd_cap(sys.argv[2])
    elif a == "run":
        cmd_run(sys.argv[2])
    elif a == "deploy":
        cmd_deploy(sys.argv[2])
    else:
        print(__doc__)
