# main_server.py —— RP2040 / Pico 专用: 常驻能力服务 (需要 sys.stdin)
# micro:bit 不要用这个 (它没有 sys.stdin, 用 REPL 调 lyco_cap.cmd()).
# 部署: 把 lyco_cap.py 与本文件一起拷到设备, 并把本文件改名为 main.py
import sys
import time

import lyco_cap

print("")                      # 干净换行
print("=== lyco capability server %s ===" % lyco_cap.VERSION)
print(lyco_cap.cmd("info"))
print("type 'help' for commands")

while True:
    try:
        line = sys.stdin.readline()
        if not line:
            time.sleep(0.02)
            continue
        line = line.strip()
        if not line:
            continue
        print(lyco_cap.cmd(line))
    except Exception as e:
        print("ERR %s: %s" % (type(e).__name__, e))
        time.sleep(0.1)
