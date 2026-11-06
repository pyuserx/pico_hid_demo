"""上电后激活自定义 HID 并启动下位机主循环（不复位、不改文件）。

CLI 用法：
    python tools/activate.py                 # 完整流程：start() -> 等 HID -> main.main()
    python tools/activate.py --loop-only     # HID 已在，只启动主循环
    python tools/activate.py --port COM26    # 指定 CDC 串口

库用法（上位机连接流程复用，见 host/auto_activate.py）：
    from activate import ensure_ready
    ok, msg = ensure_ready(wait=15)          # -> (bool, str)

为什么需要这一步：
  实测在 boot.py / main.py 启动阶段重新配置 machine.USBDevice 会打断主机枚举，
  导致整板 USB 挂死。因此 boot.py 保持纯 CDC，HID 由主机在 REPL 里手动装配
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


def ensure_ready(port=None, loop_only=False, wait=30, log=print):
    """确保自定义 HID 已枚举且主循环已启动。返回 (ok, msg)。

    这是 CLI 与各上位机共用的唯一激活实现。不复位、不改板上文件。
    """
    ports = [port] if port else cdc_ports()
    if not ports:
        return False, ("找不到 Pico CDC 串口（VID=2E8A）。"
                       "请物理拔插一次 USB 线（不要按 BOOTSEL）。")
    p = ports[0]

    err = probe_port(p)
    if err is not None:
        return False, (
            "无法独占打开 %s：%s\n"
            "        CDC 串口被另一个程序占用（常见：Thonny 后端 rp2040_back.py / "
            "串口监视器 / PuTTY / mpremote repl）。\n"
            "        关掉它，或在 Thonny 里把解释器切到「本地 Python 3」；"
            "也可在 Thonny Shell 依次粘贴 `import hid_usb; hid_usb.start()` "
            "和 `import main; main.main()`。" % (p, err))

    hids = vendor_hids()
    if hids:
        log("自定义 HID 已枚举：up=0xFF00 iface=%s，跳过 start()" %
            hids[0].get("interface_number"))
    elif loop_only:
        return False, "HID 未枚举，但指定了 --loop-only（只启动主循环）"
    else:
        log("[1/3] 下发 hid_usb.start() ...")
        try:
            send_no_wait(p, "import hid_usb; hid_usb.start()", settle=2.0)
        except Exception as e:
            log("     下发异常（串口可能已重新枚举，继续检查）：%r" % e)
        t0 = time.monotonic()
        while time.monotonic() - t0 < wait:
            hids = vendor_hids()
            if hids:
                break
            time.sleep(0.4)
        if not hids:
            return False, ("%d 秒内 HID 没枚举出来（排查：GP14 是否悬空；"
                           "或按 README 的救砖流程重刷固件）" % wait)
        log("     已枚举：VID=2E8A up=0xFF00 use=0x01 iface=%s" %
            hids[0].get("interface_number"))

    log("[2/3] 启动 main.main() 主循环 ...")
    cur = cdc_ports() or [p]
    try:
        send_no_wait(cur[0], "import main; main.main()", settle=1.2)
    except Exception as e:
        log("     下发异常：%r（若主循环已在跑可忽略）" % e)

    time.sleep(1.0)
    return True, "HID 已就绪（iface=%s），主循环已启动" % (
        hids[0].get("interface_number") if hids else "?")


def main():
    ap = argparse.ArgumentParser(description="激活 Pico 自定义 HID 并启动主循环")
    ap.add_argument("--port", default=None, help="CDC 串口，默认自动找 VID=0x2E8A")
    ap.add_argument("--loop-only", action="store_true", help="HID 已枚举，只启动主循环")
    ap.add_argument("--wait", type=int, default=30, help="等 HID 枚举的秒数")
    a = ap.parse_args()

    print("CDC 串口：", a.port or (cdc_ports() or ["<未找到>"])[0])
    ok, msg = ensure_ready(port=a.port, loop_only=a.loop_only, wait=a.wait)
    if not ok:
        print("[FAIL]", msg)
        return 2 if "无法独占打开" in msg else 1
    print("[3/3] " + msg)
    print("      现在运行：python host/check_link.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
