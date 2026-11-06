"""sysinfo.py — 采集 PC 的 CPU / 内存 / 系统盘占用，无硬性第三方依赖。

优先级：psutil（如果装了） -> 标准库降级
  Windows : ctypes 调 GetSystemTimes / GlobalMemoryStatusEx / GetDiskFreeSpaceExW
  Linux   : /proc/stat、/proc/meminfo、os.statvfs

CPU 占用需要两次采样算差值，所以首次调用只建立基线返回 0；
sysmon.py 启动时会先 warmup()，实际显示的第一帧就是有效值。
"""
from __future__ import annotations

import os
import socket
import sys

try:
    import psutil
except ImportError:
    psutil = None

IS_WINDOWS = sys.platform == "win32"
SOURCE = "psutil" if psutil is not None else ("win32-ctypes" if IS_WINDOWS else "procfs")
_prev_cpu = None


def _clamp(v):
    return max(0, min(100, int(round(v))))


def _root():
    if IS_WINDOWS:
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


def _hostname():
    try:
        return socket.gethostname()
    except Exception:
        return "PC"


# ---------------------------------------------------------------- Windows
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD)]

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    _k32 = ctypes.windll.kernel32

    def _ft(v):
        return (v.dwHighDateTime << 32) | v.dwLowDateTime

    def _win_cpu_times():
        idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
        if not _k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel),
                                   ctypes.byref(user)):
            return None
        # GetSystemTimes 的 kernel 时间已包含 idle，故 total = kernel + user
        return _ft(idle), _ft(kernel) + _ft(user)

    def _win_mem():
        m = _MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(m)
        if not _k32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return 0
        return _clamp(m.dwMemoryLoad)

    def _win_disk():
        total = ctypes.c_ulonglong(0)
        free = ctypes.c_ulonglong(0)
        if not _k32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(_root()), None,
                                        ctypes.byref(total), ctypes.byref(free)):
            return 0
        if total.value <= 0:
            return 0
        return _clamp(100.0 * (total.value - free.value) / total.value)
else:
    def _win_cpu_times():
        return None

    def _win_mem():
        return 0

    def _win_disk():
        return 0


# ------------------------------------------------------------------ Linux
def _linux_cpu_times():
    try:
        with open("/proc/stat", "r") as f:
            vals = [int(x) for x in f.readline().split()[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
        return idle, sum(vals)
    except Exception:
        return None


def _linux_mem():
    try:
        total = avail = 0
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1])
        return _clamp(100.0 * (total - avail) / total) if total > 0 else 0
    except Exception:
        return 0


def _linux_disk():
    try:
        st = os.statvfs("/")
        return _clamp(100.0 * (st.f_blocks - st.f_bfree) / st.f_blocks) if st.f_blocks > 0 else 0
    except Exception:
        return 0


# ------------------------------------------------------------- 统一入口
def _native_cpu():
    global _prev_cpu
    cur = _win_cpu_times() if IS_WINDOWS else _linux_cpu_times()
    if cur is None:
        return 0
    if _prev_cpu is None:
        _prev_cpu = cur
        return 0
    d_idle = cur[0] - _prev_cpu[0]
    d_total = cur[1] - _prev_cpu[1]
    _prev_cpu = cur
    if d_total <= 0:
        return 0
    return _clamp(100.0 * (1.0 - d_idle / d_total))


def warmup():
    """建立 CPU 采样基线，避免第一帧显示 0%。"""
    if psutil is not None:
        psutil.cpu_percent(None)
    else:
        _native_cpu()


def sample():
    """返回 dict: cpu / mem / disk / gpu / host。gpu 暂时固定为 None。"""
    if psutil is not None:
        cpu = _clamp(psutil.cpu_percent(None))
        mem = _clamp(psutil.virtual_memory().percent)
        disk = _clamp(psutil.disk_usage(_root()).percent)
    else:
        cpu = _native_cpu()
        mem = _win_mem() if IS_WINDOWS else _linux_mem()
        disk = _win_disk() if IS_WINDOWS else _linux_disk()
    return {"cpu": cpu, "mem": mem, "disk": disk, "gpu": None, "host": _hostname()}


if __name__ == "__main__":
    warmup()
    import time
    time.sleep(0.2)
    print("source =", SOURCE)
    print(sample())
