"""用 USB 串口 REPL(COM21) 部署/备份 Pico 固件，无需 mpremote、无需 Pico 联网。

    python tools/deploy.py --port COM26                    # 备份 + 部署（默认不复位）
    python tools/deploy.py --port COM26 --backup           # 只备份板上文件到 backup/
    python tools/deploy.py --port COM26 --restore          # 卸载 HID 固件（回到纯 CDC）
    python tools/deploy.py --port COM26 --reset            # 只软复位
    python tools/deploy.py --port COM26 --reset-after      # 部署后软复位

重要：实测这块板在 soft_reset()/machine.reset() 之后 USB 不会自动重新枚举，
必须物理拔插 USB 线才回来。因此部署默认【不复位】，改完文件请手动拔插一次。
（--no-reset 保留为兼容参数，现已是默认行为。）
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import time

import serial
import serial.tools.list_ports

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRMWARE = os.path.join(ROOT, "firmware")
BACKUP = os.path.join(ROOT, "backup")

FILES = ["boot.py", "hid_usb.py", "hidproto.py", "main.py", "selftest.py"]
REMOVE_ON_RESTORE = FILES + ["hid_boot_error.txt"]


# --------------------------------------------------------------- raw REPL
def connect(port, baud=115200):
    ser = serial.Serial(port, baud, timeout=0.2, write_timeout=30)
    ser.write(b"\r\x03\x03")            # 中断正在运行的 main.py
    time.sleep(0.15)
    ser.reset_input_buffer()
    ser.write(b"\r\x01")                # Ctrl-A 进入 raw REPL
    buf = bytearray()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        c = ser.read(1)
        if c:
            buf += c
            if b"raw REPL; CTRL-B to exit" in buf and buf.endswith(b">"):
                return ser
    ser.close()
    raise RuntimeError("无法进入 MicroPython raw REPL：%r" % bytes(buf[:200]))


def raw_exec(ser, code, timeout=30):
    ser.reset_input_buffer()
    ser.write(code.encode("utf-8") + b"\x04")
    buf = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            n = ser.in_waiting
            chunk = ser.read(n) if n else ser.read(1)
        except Exception:
            raise RuntimeError("串口在命令执行期间断开（USB 重新枚举）")
        if chunk:
            buf += chunk
        else:
            time.sleep(0.004)
        if buf.count(b"\x04") >= 2:
            break
    text = bytes(buf).decode("utf-8", "replace")
    if not text.startswith("OK"):
        raise RuntimeError("raw REPL 无 OK 响应：%r" % text[:300])
    parts = text.split("\x04")
    out = parts[0][2:] if parts else ""
    err = parts[1] if len(parts) > 1 else ""
    if err.strip():
        raise RuntimeError("板端异常：%s" % err.strip()[:500])
    return out.strip()


def read_remote(ser, path):
    code = ("import ubinascii\n"
            "try:\n"
            " f=open(%r,'rb'); d=f.read(); f.close()\n"
            " print(ubinascii.b2a_base64(d).decode(), end='')\n"
            "except OSError:\n"
            " print('NOFILE')" % path)
    out = raw_exec(ser, code, timeout=20)
    if out == "NOFILE" or not out:
        return None
    return base64.b64decode(out)


def put_file(ser, local_path, remote_path, chunk_size=4096):
    with open(local_path, "rb") as f:
        data = f.read()
    raw_exec(ser, "f=open(%r,'wb'); f.close()" % remote_path)
    for off in range(0, len(data), chunk_size):
        chunk = data[off:off + chunk_size]
        raw_exec(ser, "f=open(%r,'ab'); f.write(%r); f.close()" % (remote_path, chunk))
    print("  写入 /%-18s %6d B" % (os.path.basename(remote_path), len(data)))


def remove_file(ser, path):
    raw_exec(ser, "import os\ntry: os.remove(%r)\nexcept OSError: pass" % path)


def soft_reset(ser):
    try:
        raw_exec(ser, "import machine\nmachine.soft_reset()", timeout=2)
    except Exception:
        pass        # 复位后一定收不到响应


def auto_port():
    for p in serial.tools.list_ports.comports():
        if p.vid == 0x2E8A:
            return p.device
    return None


def main():
    ap = argparse.ArgumentParser(description="Pico HID Demo 固件部署工具（串口 raw REPL）")
    ap.add_argument("--port", default=None, help="串口号，默认自动找 VID=0x2E8A")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--backup", action="store_true", help="只备份板上文件")
    ap.add_argument("--restore", action="store_true", help="卸载 HID 固件，恢复纯 CDC")
    ap.add_argument("--reset", action="store_true", help="只软复位")
    ap.add_argument("--reset-after", action="store_true",
                    help="部署后软复位（本板复位后 USB 通常需物理拔插才重新枚举）")
    ap.add_argument("--no-reset", action="store_true", help="兼容旧参数；现已是默认行为")
    a = ap.parse_args()

    port = a.port or auto_port()
    if not port:
        print("找不到 Pico 串口。请用 --port COM21 指定，或上电时把 GP14 接 GND 进安全模式。")
        return 1
    print("连接 %s ..." % port)
    try:
        ser = connect(port, a.baud)
    except Exception as e:
        print("连接失败：", e)
        return 1

    try:
        print("固件：", raw_exec(ser, "import sys; print(sys.implementation.version, sys.platform)"))
        os.makedirs(BACKUP, exist_ok=True)

        if a.reset:
            soft_reset(ser)
            print("已软复位")
            return 0

        print("备份板上现有文件 ->", BACKUP)
        for name in FILES:
            data = read_remote(ser, "/" + name)
            if data is not None:
                with open(os.path.join(BACKUP, name), "wb") as f:
                    f.write(data)
                print("  备份 /%-18s %6d B" % (name, len(data)))
        if a.backup:
            return 0

        if a.restore:
            for name in REMOVE_ON_RESTORE:
                remove_file(ser, "/" + name)
            print("已移除 HID 固件文件，重新上电后为纯 CDC")
            if not a.no_reset:
                soft_reset(ser)
            return 0

        print("部署固件 ->")
        for name in FILES:
            put_file(ser, os.path.join(FIRMWARE, name), "/" + name)

        print("校验：", raw_exec(ser, "import os; print(sorted(os.listdir('/')))"))
        if a.reset_after:
            print("软复位中（USB 会重新枚举；本板通常需要再拔插一次 USB）...")
            soft_reset(ser)
        else:
            print("已跳过复位（默认）。要让 boot.py 生效，请物理拔插一次 USB 线。")
        print("\n完成。设备重新枚举后运行：")
        print("  python host/hid_demo.py list")
        print("  python host/hid_demo.py ping")
        print("\n若 COM 口变化或 REPL 丢失：上电时把 GP14 短接 GND 进入安全模式，")
        print("再执行 python tools/deploy.py --port <COM号> --restore")
        return 0
    finally:
        try:
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
