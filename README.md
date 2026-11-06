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
  hidproto.py             协议帧单一来源（上下位机共用，含 SYS_INFO 载荷）
  main.py                 命令处理 + 主循环（不自动激活 HID）
  oled.py                 I2C SSD1306 / SH1106 128x64 显示驱动
  selftest.py             板端自检
host/
  pico_hid.py             hidapi 封装（自动探测 64B / 65B 写入格式）
  sysinfo.py              PC 占用采集（psutil / 标准库降级）
  sysmon.py               ★ PC 占用 -> OLED 持续推送
  check_link.py           极简指令上位机：6 步确认通讯 OK
  hid_demo.py             命令行：list/ping/echo/led/adc/temp/stream/bench/sysinfo/repl
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

**开上位机就能连，不需要先手动激活。** `host/pico_hid.py` 的 `find_device()` 在找不到自定义
HID 时，会自动通过 CDC 串口下发 `hid_usb.start()` + `main.main()`（逻辑复用
`tools/activate.py` 的 `ensure_ready()`），等 HID 枚举后再连接。`check_link.py` /
`hid_demo.py` / `web_gui.py` / `hid_gui.py` 四个入口都走这一层，所以**每次上电后直接开上位机即可**。

HID 已在线时完全不碰串口；只有连不上才触发。保护措施：进程内锁（并发只激活一次）、
失败后 15 秒冷却（网页状态栏轮询不会反复打串口）、失败时打印 `[auto]` 日志说明原因。

```powershell
cd C:\Users\Administrator\WorkBuddy\2026-11-05-14-18-45\pico_hid_demo
D:\Python313\python.exe host\check_link.py          # 需要时自动激活
D:\Python313\python.exe host\web_gui.py             # 网页版同理
```

> 直接敲 `python` 会命中 `C:\Users\...\WindowsApps\python.exe`（Store 别名，不可靠），
> 一律用绝对路径 `D:\Python313\python.exe`。

关闭自动激活（只想看设备、不想动板子）：

```powershell
$env:PICO_HID_NO_AUTO_ACTIVATE = "1"                  # 当前 PowerShell 会话有效
D:\Python313\python.exe host\hid_demo.py list        # list 本来就不触发激活
D:\Python313\python.exe host\hid_demo.py --no-auto ping
```

**自动激活失败的最常见原因**：CDC 串口被 Thonny 后端 `rp2040_back.py` / 串口监视器 /
PuTTY / `mpremote repl` 独占（`PermissionError 13, 拒绝访问`）。`[auto]` 日志会直接点名，两条路：

- **A（推荐）**：关掉 Thonny，或在右下角把解释器切到「本地 Python 3」释放串口，再重开上位机。
- **B（不想关 Thonny）**：在 Thonny Shell 里依次粘贴这两行，效果等同：
  ```python
  import hid_usb; hid_usb.start()
  import main; main.main()
  ```
  `start()` 会让 USB 重新枚举、Thonny 可能自动重连；等 HID 出来后再执行第二行启动主循环。

**手动兜底**（可选）：双击 `activate.bat`（激活 + 跑 `check_link.py`），或
`D:\Python313\python.exe tools\activate.py --port COM26`。`--port` 不写自动找
`VID=0x2E8A`；脚本只发两条 REPL 命令，不改板上文件、不复位。

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
python host/hid_demo.py sysinfo            # 单次推送 PC 占用到 OLED
python host/hid_demo.py sysmon --interval 1  # 持续推送（等价 host/sysmon.py）
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

命令表：`PING / ECHO / GET_INFO / LED_WRITE / LED_READ / ADC_READ / TEMP_READ / UPTIME / STREAM_START / STREAM_STOP / GET_STATS / SYS_INFO`，错误响应 `ERR_BAD_FRAME / ERR_BAD_CMD`，主动上报 `EVT_STATUS`。

`SYS_INFO (0x31)` 载荷固定 24 字节，编解码函数 `pack_sysinfo() / unpack_sysinfo()` 也放在
`hidproto.py` 里，两边共用：schema(1B) / cpu / mem / disk / gpu(0xFF=无) / 时分秒 / 主机名 ASCII(16B)。
响应首字节 `1` = OLED 已刷新，`0` = 屏不可用。

## PC 系统占用 → OLED12864（I2C）· 操作手册

新增 `CMD_SYS_INFO (0x31)`：上位机定时把 CPU / 内存 / 系统盘占用 + 本地时间 + 主机名
打包成 24 字节，经自定义 HID 发给 Pico，由 `firmware/oled.py` 画到 SSD1306 128x64。

### 从头跑一遍（按顺序，别跳步）

> **前提**：改完代码后板上跑的还是旧固件（没有 `oled.py`，`main.py` 不认 `SYS_INFO`），
> 直接敲 `sysmon.py` 只会收到 `ERR_BAD_CMD`。**必须先部署一次**，之后每天上电就只需第 ⑤ 步。

**① 断电接线** —— 先拔 USB 再接线，I2C 不要热插拔。

| OLED 引脚 | Pico 引脚 | 备注 |
|---|---|---|
| VCC | 3V3(OUT) | 物理脚 36；**不要接 VSYS/5V**（部分模块不耐 5V 逻辑） |
| GND | GND | 任一 GND |
| SDA | GP4（物理脚 6） | 若接在别处，改 `firmware/oled.py` 的 `SDA_PIN` |
| SCL | GP5（物理脚 7） | 若接在别处，改 `SCL_PIN` |

`oled.py` 顶部常量 `I2C_BUS / SDA_PIN / SCL_PIN / OLED_ADDR / SH1106` 按实物改，其余代码不用动。
地址默认 `0x3C`，`init()` 会自动再试 `0x3D`。驱动只用内置 `machine.I2C` + `framebuf`，无需字库文件。

> **1.3 寸屏注意**：这类模块多是 SH1106 控制器（132x64 RAM，可见区从列 2 开始），
> 把 `SH1106 = True` 即可，`COL_OFFSET` 自动变 2。帧缓冲**必须用 `MONO_VLSB`**
> —— SSD1306 显存里一个字节 = 一列 8 个垂直像素（bit0 在上）；用 `MONO_HLSB`
> 会把每字节当成横排 8 像素，送进屏后文字变成竖条纹乱码。初版就踩过这个坑，已修正。
> 若长线 / 弱上拉导致零星花屏，把 `I2C_FREQ` 从 400000 降到 100000。

**② 腾出串口** —— 关掉 Thonny、PuTTY、串口监视器、`mpremote repl`。COM26 是**独占**的，
有一个占着，`deploy.py` 和上位机的自动激活都会报 `PermissionError 13`。

**③ 部署固件**

```powershell
cd C:\Users\Administrator\WorkBuddy\2026-11-05-14-18-45\pico_hid_demo
D:\Python313\python.exe tools\deploy.py --port COM26
```

`--port` 可省略（自动找 `VID=0x2E8A`）。预期看到：先备份板上文件到 `backup/`，然后逐行
`写入 /oled.py  xxxx B` 等 6 个文件，最后 `校验： ['boot.py', 'hid_usb.py', 'hidproto.py',
'main.py', 'oled.py', 'selftest.py']` 里有 **oled.py**，并提示「已跳过复位」。
`deploy.py` 会先 Ctrl-C 打断正在跑的 `main.py` 再进 raw REPL，所以部署完主循环是停的，属正常。

**④ 物理拔插一次 USB 线** —— 必须。两个原因：`deploy.py` 默认不复位，`boot.py` 要重跑；
且这块板复位后 USB 不会自动重枚举。**不要**用 `machine.reset()` / `soft_reset()` 代替。
（如果屏是这时才接上的，也靠这次拔插让 `oled.init()` 重新探测。）

**⑤ 启动推送**

```powershell
D:\Python313\python.exe host\sysmon.py --interval 1
```

第一次连接时 `find_device()` 会自动经 CDC 串口下发 `hid_usb.start()` + `main.main()`，
等 HID 枚举完成后再连。**不用**先跑 `activate.py`，**不用**按 BOOTSEL，也不用手动敲 REPL。

终端预期输出：

```text
采集方式 = ctypes          （或 psutil）
已连接： b'Pico RP2040 custom HID v1.0'
开始推送，Ctrl-C 退出；周期 1.00 s
CPU  42%  MEM  67%  DISK  31%  OLED=OK  RTT=3.2 ms
CPU  39%  MEM  67%  DISK  31%  OLED=OK  RTT=2.8 ms
```

`main()` 启动瞬间屏先显示 `PICO USB HID / OLED READY`，CDC 串口打印
`[oled] SSD1306 128x64 @0x3C ready` 就表示屏认到了。随后屏幕每帧刷新为：

```text
PC SYS 12:34:56
CPU  42%  ▓▓▓▓▓░░░     （右侧 56px 像素条）
MEM  67%  ▓▓▓▓▓▓▓░
DSK  31%  ▓▓▓░░░░░
H:DESKTOP-ABC            （主机名前 12 字符，ASCII）
```

**⑥ 停止**：Ctrl-C。OLED 保留最后一帧，**5 秒后**自动变成 `NO PC SIGNAL / WAITING USB...`。

### 常用变体

| 想做什么 | 命令 |
|---|---|
| 只推一帧就退出 | `host\sysmon.py --once` 或 `host\hid_demo.py sysinfo` |
| 改推送周期 | `host\sysmon.py --interval 0.5` |
| 覆盖屏上主机名 | `host\sysmon.py --host MYPC`（最多 16 个 ASCII） |
| 不接屏、只验证 HID 链路 | 直接跑，看 `OLED=无屏/失败` 但 CPU 数字照常滚动 |
| 不想让上位机碰串口 | 加 `--no-auto`，然后自己在 Thonny 里敲 `import hid_usb; hid_usb.start()` / `import main; main.main()` |
| 看板端日志 | 115200 打开 COM26 —— **但要在自动激活完成之后**，否则会抢占串口 |

⚠️ 同一时间只开**一个**上位机，多个进程抢同一个 HID 句柄会互相收错包。

### 排错对照

| 现象 | 多半是 | 处理 |
|---|---|---|
| `ERR_BAD_CMD` / 一直 `发送失败` | 板上还是旧 `main.py` | 回到 ③ 重新部署 + ④ 拔插 |
| `连接失败` + `[auto] ... 拒绝访问` | Thonny 等占着 COM26 | 关掉，或把 Thonny 解释器切到「本地 Python 3」 |
| `OLED=无屏/失败`，但 CPU 数字在滚 | HID 完全正常，只是屏没认到 | 看 CDC 日志 `[oled] not found, i2c scan = ...` |
| `i2c scan = []` | 没供电 / SDA-SCL 接反 / 接错 GPIO | 断电重接，核对 `SDA_PIN` `SCL_PIN` |
| 有显示但字是竖条纹 / 看不清 | 帧缓冲像素格式错（`MONO_HLSB`） | 已修正为 `MONO_VLSB`，重新部署 + 拔插 |
| 字能看清但整体左移 2 列 / 右边缘缺 | 屏是 SH1106（1.3 寸常见） | `oled.py` 设 `SH1106 = True` |
| 随机雪花、偶尔错位 | I2C 400 kHz 对长线 / 弱上拉不稳 | `I2C_FREQ` 降到 100000 |
| 只显示上半屏、下半空白 | 屏实际是 128x32，不是 128x64 | 改 `HEIGHT` / `0xA8` / `0xDA`，或换屏 |
| 屏接上后仍 `OLED=无屏/失败` | `oled.init()` 只在 `main()` 启动时跑一次 | 重新拔插 USB，让主循环重跑 |
| COM 号不是 26 | 刷机后变号 | 省略 `--port`，`deploy.py` / 激活脚本会自动找 VID |
| `python` 不是想要的解释器 | 命中 Store 别名 | 一律用绝对路径 `D:\Python313\python.exe` |

> 采集优先用 `psutil`；没装则自动降级到 Windows `ctypes` / Linux `procfs`，零额外依赖。
> OLED 没接或接错线**不影响 HID 链路**：固件仍回 ACK，只是首字节为 0，
> 上位机会打印 `OLED=无屏/失败` 并提示检查接线；`init()` 失败不会中断主循环。
> 一次 `show()` 约 20~30 ms，期间主循环暂停，与 `stream` 同时跑会丢少量事件帧。

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
4. 盘消失、CDC 回来后：`deploy.py --no-reset` → 直接跑 `check_link.py`（上位机会自动激活 HID）

## 已知限制与实测踩坑

1. **下位机冷启动阶段不能自动激活 HID**。在 `boot.py` / `main.py` 启动阶段重新配置 `machine.USBDevice` 会打断主机枚举，整板 USB 挂死（Windows 报「未知 USB 设备(设备描述符请求失败)」）。尝试版留在 `firmware_auto_attempt/`。因此激活必须由主机侧通过 CDC 串口下发 —— 现已内嵌到上位机连接流程（`host/auto_activate.py` → `tools/activate.py::ensure_ready()`），开上位机即自动完成。
2. **刷 UF2 不清 MicroPython 的 LFS 文件系统**，必须用 `flash_nuke.uf2` 擦全片才能真正清掉旧 `boot.py`。
3. **复位后 USB 不自动重新枚举**，所有复位操作后都要物理拔插一次。
4. 写 RP2 伪 FAT 卷的 UF2 **不能用 `open(..., buffering=0)`**（`PermissionError 13`）；用普通缓冲写或 `Copy-Item`，必要时换文件名。
5. `host/pico_hid.py` 里设备信息字典属性名是 `device_info`，不要与 `info()` 方法重名（早期版本因此报 `'dict' object is not callable`）。
6. **隔离 Python 没有 tkinter**，所以 `hid_gui.py` 要用 `D:\Python313\python.exe` 跑；该环境原来装的是 `hid 1.0.9`（ctypes 版，要外部 `hidapi.dll`），已换成 `hidapi 0.15.0` 编译扩展。若不想装图形环境，`host/web_gui.py` 是等价的零依赖替代。
7. 本机 **8765 端口被其它程序占用**，网页版上位机默认用 8766（并会自动往后找空闲端口）。

## 后续可迭代

- 找到启动阶段安全配置 USBDevice 的时序（例如等主机 `SET_CONFIGURATION` 回调后再追加接口），实现真正的下位机冷启动自激活 —— 目前由上位机连接流程代偿，使用上已无感。
- 加入 Report ID 以兼容更多平台；增加分片传输支持超过 59B 的 payload。
