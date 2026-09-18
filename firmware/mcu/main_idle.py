# main_idle.py —— micro:bit 用: 开机亮个心, 然后空闲。
# micro:bit 的 MicroPython 没有 sys.stdin, 无法常驻读命令;
# 能力通过 REPL 调用 lyco_cap.cmd("<cmd>") 使用。
import microbit as mb
import lyco_cap

mb.display.show(mb.Image.HEART)
mb.sleep(600)
mb.display.scroll(lyco_cap.VERSION[:10])
