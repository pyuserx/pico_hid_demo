"""上电后激活自定义 HID 并启动下位机主循环（不复位、不改文件）。

    python tools/activate.py                 # 完整流程：start() -> 等 HID -> main.main()
    python tools/activate.py --loop-only     # HID 已在，只启动主循环
    python tools/activate.py --port COM26    # 指定 CDC 串口

为什么需要这一步：
  实测在 boot.py / main.py 启动阶段重新配置 machine.USBDevice 会打断主机枚举，
  导致整板 USB 挂死。因此 boot.py 保持纯 CDC，HID 由本脚本在 REPL 里手动装配
  —— 这是本机上唯一稳定成功的路径。
  每次物理断电重插后，CDC 会回来但 HID 需要重新激活一次。

原理：两条语句都通过 raw REPL 下发且不等待返回（start() 会让 USB 重新枚举、
串口短暂断开），下位机自己继续执行。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import hid
import serial.tools.list_ports

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deploy  # noqa: E402


def vendor_hids():
    return [d for d in hid.enumerate()
            if d["vendor_id"] == 0x2E8A and d.get("usage_page") == 0xFF00]


def cdc_ports():
    return [p.device for p in serial.tools.list_ports.comports()
            if p.vid == 0x2E8A]


def probe_port(port):
    """独占打开一次 CDC 串口，确认没有别的程序占着。返回 None 表示可用。"""
    import serial
    try:
        s = serial.Serial(port, 115200, timeout=0.1)
        s.close()
        return None
    except Exception as e:
        return e


def send_no_wait(port, code, settle=1.5):
    """进 raw REPL，下发一条语句，不等它返回就断开串口。"""
    ser = deploy.connect(port)
    try:
        ser.write(code.encode("utf-8") + b"\x04")
        time.sleep(settle)
    finally:
        try:
            ser.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="激活 Pico 自定义 HID 并启动主循环")
    ap.add_argument("--port", default=None, help="CDC 串口，默认自动找 VID=0x2E8A")
    ap.add_argument("--loop-only", action="store_true", help="HID 已枚举，只启动主循环")
    ap.add_argument("--wait", type=int, default=30, help="等 HID 枚举的秒数")
    a = ap.parse_args()

    ports = [a.port] if a.port else cdc_ports()
    if not ports:
        print("[FAIL] 找不到 Pico CDC 串口。请物理拔插一次 USB 线（不要按 BOOTSEL）。")
        return 1
    port = ports[0]
    print("CDC 串口：", port)

    err = probe_port(port)
    if err is not None:
        print("[FAIL] 无法独占打开 %s：%s" % (port, err))
        print("       CDC 串口被另一个程序占用了。常见占用者：")
        print("         · Thonny（后端 rp2040_back.py 会一直持有 COM 口）")
        print("         · 串口监视器 / PuTTY / mpremote repl / VS Code MicroPython 插件")
        print("       二选一：")
        print("       A) 关掉占用程序（或在 Thonny 里把解释器切到「本地 Python」）后重跑")
        print("       B) 不关 Thonny：在 Thonny Shell 里依次粘贴这两行（详见 README）：")
        print("            import hid_usb; hid_usb.start()")
        print("            import main; main.main()")
        return 2

    hids = vendor_hids()
    if hids and a.loop_only:
        print("自定义 HID 已枚举：up=0xFF00 iface=%s，跳过 start()" %
              hids[0].get("interface_number"))
    elif not hids and not a.loop_only:
        print("[1/3] 下发 hid_usb.start() ...")
        try:
            send_no_wait(port, "import hid_usb; hid_usb.start()", settle=2.0)
        except Exception as e:
            print("     下发异常（串口可能已重新枚举，继续检查）：%r" % e)
        t0 = time.monotonic()
        while time.monotonic() - t0 < a.wait:
            hids = vendor_hids()
            if hids:
                break
            time.sleep(0.4)
        if not hids:
            print("[FAIL] %d 秒内 HID 没枚举出来。" % a.wait)
            print("      排查：GP14 是否悬空；或按 README 的救砖流程重刷固件。")
            return 1
        print("     已枚举：VID=2E8A up=0xFF00 use=0x01 iface=%s" %
              hids[0].get("interface_number"))
    elif hids:
        print("[1/3] 自定义 HID 已存在，跳过 start()")

    print("[2/3] 启动 main.main() 主循环 ...")
    cur = cdc_ports() or [port]
    try:
        send_no_wait(cur[0], "import main; main.main()", settle=1.2)
    except Exception as e:
        print("     下发异常：%r（若主循环已在跑可忽略）" % e)

    time.sleep(1.0)
    print("[3/3] 完成。现在运行：")
    print("      python host/check_link.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
