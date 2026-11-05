# boot.py -- 开机阶段配置 USB（在 USB 子系统初始化之前执行）
#
# 救生开关：上电时把 GP0 短接到 GND 即跳过自定义 HID，回到纯 CDC(COM21)，
#           方便改写/删除文件。正常使用时 GP0 悬空即可。
import machine

_SAFE_PIN = machine.Pin(0, machine.Pin.IN, machine.Pin.PULL_UP)

if _SAFE_PIN.value():
    try:
        import hid_vendor       # 模块内部完成 CDC + HID 复合设备配置
    except Exception as _e:
        # 出错绝不把 REPL 弄丢：尽量把 USB 拉回可用的内置 CDC 状态
        try:
            with open("hid_boot_error.txt", "w") as _f:
                _f.write(repr(_e))
        except Exception:
            pass
        try:
            _usbd = machine.USBDevice()
            _usbd.builtin_driver = _usbd.BUILTIN_DEFAULT
            _usbd.active(True)
        except Exception:
            pass
else:
    # 安全模式：只保留内置 CDC，不改动任何 USB 配置
    pass
