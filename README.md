# Raspberry Pi Pico · 自定义 HID 通讯 Demo（全 Python）

下位机 MicroPython，上位机 Python + hidapi，双向 64B 中断报告通讯。

**当前状态：链路已实测打通** —— `PING / ECHO / LED / ADC / TEMP / INFO / STREAM / BENCH` 全部通过。

```
[3/6] PING  → Pico RP2040 custom HID v1.0
[4/6] ECHO  → b'hello pico'  OK
[5/6] LED   → 0 -> 1 -> 0  OK
[6/6] TEMP  → 24.70 °C    INFO → Pico HID | uptime=354431 ms | fw=1.0
 结论：自定义 HID 双向通讯 OK
```

## 环境

| 项 | 值 |
|---|---|
| 下位机 | Raspberry Pi Pico / RP2040，MicroPython **v1.28.0** (2026-04-06) |
| USB | 复合设备 `VID=2E8A / PID=0005`；CDC(REPL) + 自定义 HID(iface 2) |
| HID | `Usage Page 0xFF00 / Usage 0x01`，64B 中断 IN(EP0x83) + OUT(EP0x03)，无 Report ID |
| CDC 串口 | 当前为 **COM26**（刷机后可能变号，用 `--port` 或自动探测） |
| 隔离 Python | `C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（hidapi 0.15 / pyserial / mpremote，**无 tkinter**） |
| 系统 Python | `D:\Python313\python.exe`（Python 3.13.5，**带 tkinter 8.6**；原装的 `hid 1.0.9` 缺 `hidapi.dll`，已换成 `hidapi 0.15.0` 编译扩展，旧包备份在 `_backup_dpy_hid/`） |

## 目录

```
firmware/                 板上实际运行、已验证的组合
  boot.py                 纯 CDC，不碰 USB（关键）
  hid_usb.py              machine.USBDevice 追加 vendor HID 接口
  hidproto.py             协议帧单一来源（上下位机共用）
  main.py                 命令处理 + 主循环（不自动激活 HID）
  selftest.py             板端自检
host/
  pico_hid.py             hidapi 封装（自动探测 64B / 65B 写入格式）
  check_link.py           极简指令上位机：6 步确认通讯 OK
  hid_demo.py             命令行：list/ping/echo/led/adc/temp/stream/bench/repl
  hid_gui.py              tkinter 图形界面（须用 D:\Python313 运行）
  web_gui.py              ★ 网页版上位机（零依赖、不需要 tkinter）
tools/
  activate.py             ★ 每次上电后一键激活 HID + 启动主循环
  deploy.py               串口 raw REPL 部署（默认不复位）
  flash_uf2.py            等待 BOOTSEL 盘并逐块刷 UF2
board_existing/            最初从板上读回的原始 5 文件（备份）
firmware_auto_attempt/     "冷启动自动激活 HID"尝试版，实测会挂 USB，仅留档
uf2/                       官方 v1.28.0 UF2 + flash_nuke.uf2
alternative_firmware/      另一套基于 micropython-lib 的实现（备用，未上板）
_backup_dpy_hid/           D 盘 Python 原 hid 1.0.9 包备份
```

## 快速开始

设备已激活、主循环在跑时（上电后先按下一节激活一次）：

```bash
# 网页版上位机（推荐，浏览器里点按钮，不需要 tkinter）
python host/web_gui.py                     # 自动打开 http://127.0.0.1:8766

# 或：极简指令验证
python host/check_link.py
```

网页版上位机用标准库 `http.server` 起一个 127.0.0.1 本地服务，hidapi 留在后端，
浏览器只负责界面 —— 不依赖 WebHID、不联网、不需要装前端依赖。命令实现直接复用
`hid_demo.run()`，与命令行上位机行为完全一致。端口被占用时会自动往后找一个可用端口，
实际地址看启动日志。

**每次物理断电重插后**，CDC 会回来但 HID 需要重新激活一次。

**最省事：双击工程根目录的 `activate.bat`** —— 自动激活 HID、接着跑 `check_link.py` 验证，窗口会停住等你看结果。

命令行方式（PowerShell）。注意两点：**先进入工程目录**，以及 **`python` 要用绝对路径** —— 直接敲 `python` 会命中 `C:\Users\...\WindowsApps\python.exe`（Microsoft Store 别名，不可靠）。

```powershell
cd C:\Users\Administrator\WorkBuddy\2026-11-05-14-18-45\pico_hid_demo
D:\Python313\python.exe tools\activate.py
D:\Python313\python.exe host\check_link.py
```

`activate.py` 不写 `--port` 时会自动找 `VID=0x2E8A` 的 CDC 串口（当前是 COM26）；需要指定就加 `--port COM26`。脚本只发两条 REPL 命令，不改板上文件、不复位。

> **如果报 `PermissionError(13, 拒绝访问)` / 「无法独占打开 COMxx」**：CDC 串口被别的程序独占，最常见是 **Thonny**（后端 `rp2040_back.py` 一直持有 COM 口），也可能是串口监视器、PuTTY、`mpremote repl`、VS Code MicroPython 插件。两条路：
>
> - **A（推荐）**：关掉 Thonny，或在 Thonny 右下角把解释器切到「本地 Python 3」释放串口，再双击 `activate.bat`。
> - **B（不想关 Thonny）**：直接在 Thonny 的 Shell 里依次粘贴这两行，效果等同：
>   ```python
>   import hid_usb; hid_usb.start()
>   import main; main.main()
>   ```
>   `start()` 会让 USB 重新枚举、Thonny 可能自动重连；等 HID 接口出来后再执行第二行启动主循环。

更多上位机玩法：

```bash
python host/hid_demo.py list              # 枚举 HID 设备
python host/hid_demo.py ping              # 单次 PING
python host/hid_demo.py echo "hello"      # 回显
python host/hid_demo.py led on            # 板载 LED
python host/hid_demo.py adc 0             # ADC0 电压(mV)
python host/hid_demo.py temp              # 芯片温度
python host/hid_demo.py stream 100 3      # 主动上报 3 秒
python host/hid_demo.py bench 200         # 回环压测
python host/hid_demo.py repl              # 交互模式

D:\Python313\python.exe host\hid_gui.py   # 原生 tkinter 图形界面
```

⚠️ **同一时间只开一个上位机**：多个进程同时打开同一个 HID 句柄会互相抢收包，导致响应错乱。

## 协议

64 字节定长报告，**无 Report ID**。Windows 上 hidapi 写入时首字节要补 `0x00`（`pico_hid.py` 自动探测 `id0` / `raw` 两种模式）。

```
0      1      2      3      4 ... 4+L-1   4+L    5..63
MAGIC  CMD    SEQ    LEN    PAYLOAD(L)    CRC8   补零
0xA5                       L <= 59
```

- `CRC8`：多项式 0x07，初值 0x00，覆盖 `MAGIC..PAYLOAD`
- 响应命令 = `cmd | 0x80`
- `firmware/hidproto.py` 与 `host/pico_hid.py` 共用同一份实现，避免两边不同步

命令表：`PING / ECHO / GET_INFO / LED_WRITE / LED_READ / ADC_READ / TEMP_READ / UPTIME / STREAM_START / STREAM_STOP / GET_STATS`，错误响应 `ERR_BAD_FRAME / ERR_BAD_CMD`，主动上报 `EVT_STATUS`。

## 部署与救砖

```bash
# 部署 firmware/ 到板上（先自动备份到 backup/，默认不复位）
python tools/deploy.py --port COM26 --no-reset

# 只备份 / 卸载回纯 CDC
python tools/deploy.py --port COM26 --backup
python tools/deploy.py --port COM26 --restore
```

⚠️ **不要用 `soft_reset()` / `machine.reset()`** —— 实测这块板复位后 USB 不会自动重新枚举，必须物理拔插 USB 线才回来。

若板子彻底不被识别（CDC 也没有），标准恢复流程：

1. 按住 **BOOTSEL** 插 USB，出现 `RPI-RP2` 盘
2. 复制 `uf2/flash_nuke.uf2` 进盘 → **擦除整片 Flash**（UF2 只覆盖固件区，不清文件系统，坏 `boot.py` 会残留）
3. 等盘重新出现，复制 `uf2/RPI_PICO-20260406-v1.28.0.uf2`（建议改名为 `micropython.uf2`，可避开 Windows 卷缓存导致的 `Permission denied`）
4. 盘消失、CDC 回来后：`deploy.py --no-reset` → `activate.py` → `check_link.py`

## 已知限制与实测踩坑

1. **冷启动自动激活 HID 目前不可用**。在 `boot.py` / `main.py` 启动阶段重新配置 `machine.USBDevice` 会打断主机枚举，整板 USB 挂死（Windows 报「未知 USB 设备(设备描述符请求失败)」）。尝试版留在 `firmware_auto_attempt/`。当前采用 `tools/activate.py` 在 REPL 里手动装配，这是本机上唯一稳定成功的路径。
2. **刷 UF2 不清 MicroPython 的 LFS 文件系统**，必须用 `flash_nuke.uf2` 擦全片才能真正清掉旧 `boot.py`。
3. **复位后 USB 不自动重新枚举**，所有复位操作后都要物理拔插一次。
4. 写 RP2 伪 FAT 卷的 UF2 **不能用 `open(..., buffering=0)`**（`PermissionError 13`）；用普通缓冲写或 `Copy-Item`，必要时换文件名。
5. `host/pico_hid.py` 里设备信息字典属性名是 `device_info`，不要与 `info()` 方法重名（早期版本因此报 `'dict' object is not callable`）。
6. **隔离 Python 没有 tkinter**，所以 `hid_gui.py` 要用 `D:\Python313\python.exe` 跑；该环境原来装的是 `hid 1.0.9`（ctypes 版，要外部 `hidapi.dll`），已换成 `hidapi 0.15.0` 编译扩展。若不想装图形环境，`host/web_gui.py` 是等价的零依赖替代。
7. 本机 **8765 端口被其它程序占用**，网页版上位机默认用 8766（并会自动往后找空闲端口）。

## 后续可迭代

- 找到启动阶段安全配置 USBDevice 的时序（例如等主机 `SET_CONFIGURATION` 回调后再追加接口），实现冷启动自动激活，去掉 `activate.py` 这一步。
- 加入 Report ID 以兼容更多平台；增加分片传输支持超过 59B 的 payload。
