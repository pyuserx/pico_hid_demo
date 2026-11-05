# boot.py — 上电装配 USB：内置 CDC + 自定义 HID 接口
#
# 【安全开关】GP14（物理脚 19）用杜邦线短接到 GND 后再上电，
# 则跳过 HID 配置，只保留原生 CDC 串口 REPL —— 用来改代码 / 救砖。
# 正常使用时 GP14 悬空（内部上拉），自动启用 HID。

import machine

SAFE_PIN = 14

def _safe_mode():
    try:
        p = machine.Pin(SAFE_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
        return p.value() == 0
    except Exception:
        return False

if _safe_mode():
    print("[boot] SAFE mode (GP14=GND): HID disabled, CDC only")
else:
    try:
        import hid_usb
        ok = hid_usb.start()
        print("[boot] usb hid:", "ok" if ok else "failed, fallback to CDC only")
    except Exception as e:
        print("[boot] usb hid init failed:", e)
