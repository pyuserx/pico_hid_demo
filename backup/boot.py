# boot.py — 保持原生 CDC，不在此阶段改动 USB 配置
#
# 【实测教训】在 boot.py 里调用 hid_usb.start() 重新配置 machine.USBDevice，
# 会打断主机正在进行的枚举，表现为整块板 USB 挂死（Windows 报
# "未知 USB 设备(设备描述符请求失败)"，CDC 与 HID 全部消失）。
# 空闲文件系统(只有内置 CDC)冷启动正常，写入含 start() 的 boot.py 后冷启动
# 必挂 —— 对照实验已确认。
#
# 因此自定义 HID 的装配推迟到 main.py：那时系统已就绪、主机枚举已完成。
# 在 REPL 里手动调用 hid_usb.start() 能成功，也印证了这个时序判断。
#
# 【安全开关】GP14（物理脚 19）短接 GND 上电 -> main.py 跳过 HID 激活，
# 只保留纯 CDC REPL，用于改代码 / 救砖。正常使用时 GP14 悬空（内部上拉）。

print("[boot] CDC only; custom HID will be started by main.py")
