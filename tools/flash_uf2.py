"""等待 RP2040 BOOTSEL 盘出现并刷入 MicroPython UF2（稳健版）。

用法：
    python tools/flash_uf2.py                # 等待盘出现(默认 120s) -> 逐块刷入 -> 等 CDC
    python tools/flash_uf2.py --wait 30      # 自定义等待秒数
    python tools/flash_uf2.py --uf2 <path>  # 指定镜像

物理操作：按住 Pico 上的 BOOTSEL 按钮，插入 USB 线，保持 2 秒后松开。
ROM bootloader 不依赖 Flash 内容，出现 RPI-RP2 盘即说明硬件完好。

与 shutil.copy 的区别：逐 512B 无缓冲写入并 flush，保证块顺序与完整性；
UF2 bootloader 收到最后一块时会立即重启，close() 抛异常属正常现象。
"""
from __future__ import annotations

import argparse
import os
import string
import struct
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_UF2 = os.path.join(ROOT, "uf2", "RPI_PICO-20260406-v1.28.0.uf2")


def find_bootsel():
    """RPI-RP2 盘根目录一定有 INFO_UF2.TXT，比卷标判断更可靠。"""
    for letter in string.ascii_uppercase:
        drive = letter + ":\\"
        try:
            if os.path.isfile(os.path.join(drive, "INFO_UF2.TXT")):
                return drive
        except OSError:
            pass
    return None


def verify_uf2(data):
    if len(data) % 512:
        raise ValueError("UF2 长度不是 512 的整数倍")
    n = len(data) // 512
    for i in range(n):
        b = data[i * 512:(i + 1) * 512]
        m1, m2, flags, addr, size, blkno, nblk, fam = struct.unpack_from("<8I", b, 0)
        if m1 != 0x0A324655 or m2 != 0x9E5D5157:
            raise ValueError("第 %d 块 magic 错误" % i)
        if size != 256 or blkno != i or nblk != n:
            raise ValueError("第 %d 块序号/长度错误" % i)
        if fam != 0xE48BFF56:
            raise ValueError("第 %d 块 family ID 非 RP2040: 0x%08X" % (i, fam))
    return n


def wait_cdc(timeout=35):
    import serial.tools.list_ports
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        ports = [p.device for p in serial.tools.list_ports.comports() if p.vid == 0x2E8A]
        if ports:
            return ports
        time.sleep(0.5)
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uf2", default=DEFAULT_UF2)
    ap.add_argument("--wait", type=int, default=120, help="等待 BOOTSEL 盘的秒数")
    a = ap.parse_args()

    data = open(a.uf2, "rb").read()
    n = verify_uf2(data)
    print("镜像校验通过：%d B / %d 块 / RP2040" % (len(data), n), flush=True)
    print("等待 BOOTSEL 盘（最多 %d 秒）... 请按住 BOOTSEL 插入 USB 线" % a.wait, flush=True)

    drive = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < a.wait:
        drive = find_bootsel()
        if drive:
            break
        time.sleep(0.4)
    if not drive:
        print("\n[TIMEOUT] 没等到 RPI-RP2 盘。", flush=True)
        print("排查：① 按住 BOOTSEL 再插线（保持 2 秒）；② 换一根确认能传数据的 USB 线；"
              "③ 换一个 USB 口；④ 若有 RESET 脚，按住 BOOTSEL 点一下 RESET。", flush=True)
        return 1

    info = ""
    try:
        info = open(os.path.join(drive, "INFO_UF2.TXT"), "r", errors="replace").read().strip()
    except OSError:
        pass
    print("发现 BOOTSEL 盘：%s  %s" % (drive, info.replace("\n", " | ")), flush=True)

    dst = os.path.join(drive, "RPI_PICO-20260406-v1.28.0.uf2")
    print("逐块写入 %d 块 ..." % n, flush=True)
    written = 0
    try:
        with open(dst, "wb", buffering=0) as f:
            for i in range(0, len(data), 512):
                f.write(data[i:i + 512])
                written += 1
                if written % 200 == 0:
                    print("  %d/%d" % (written, n), flush=True)
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    except OSError as e:
        # 最后一块写完 bootloader 立即重启，设备消失属正常
        print("  写入中断（通常=已收完最后一块并重启）：%s" % e, flush=True)
    print("已写出 %d/%d 块" % (written, n), flush=True)
    if written != n:
        print("[WARN] 块数不足，可能未完整写入", flush=True)

    print("等待盘消失（bootloader 重启）...", flush=True)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 20:
        if find_bootsel() is None:
            print("  盘已消失，固件已接收并重启", flush=True)
            break
        time.sleep(0.4)
    else:
        print("  盘仍在，可能写入未完成", flush=True)

    print("等待 MicroPython CDC 串口 ...", flush=True)
    ports = wait_cdc(35)
    if ports:
        print("[OK] CDC 串口回来了：%s" % ", ".join(ports), flush=True)
        return 0
    print("[WARN] 35 秒内没等到 CDC。请再拔插一次 USB（不要按 BOOTSEL）后检查。", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
