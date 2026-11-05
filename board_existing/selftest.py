# selftest.py — 不依赖 boot.py 的在线自测：手动激活 HID，跑 10 秒命令循环
# 用于安全模式（GP14 接地、只有 CDC）下验证 HID 链路是否真的通。

import time
import hid_usb
import main as M

ok = hid_usb.start()
print("[selftest] start:", ok)

t0 = time.ticks_ms()
frames = 0
while time.ticks_diff(time.ticks_ms(), t0) < 10000:
    hid_usb.poll()
    f = hid_usb.recv()
    if f:
        frames += 1
        M.handle(f)
    time.sleep_ms(1)

print("[selftest] frames:", frames, hid_usb.stats())
