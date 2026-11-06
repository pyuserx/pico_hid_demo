# oled.py — I2C OLED12864 (SSD1306 128x64) 显示驱动
#
# 接线：按实际模块改下面 4 个常量即可，其余代码不用动。
#   VCC -> 3V3(OUT)   GND -> GND
#   SDA -> GP4 (Pico 物理脚 6)    SCL -> GP5 (Pico 物理脚 7)
#   I2C 地址常见 0x3C，少数模块是 0x3D（init() 会自动都试一遍）
#
# 只依赖 MicroPython 内置 machine.I2C 与 framebuf（官方 v1.28.0 固件自带
# 8x8 ASCII 字体，fb.text() 可直接用）。不做 busy-wait，show() 约 20~30 ms，
# 只在收到主机 SYS_INFO 帧时调用，不进 USB 回调。

import machine
import framebuf

I2C_BUS = 0
SDA_PIN = 4
SCL_PIN = 5
I2C_FREQ = 400000
OLED_ADDR = 0x3C
WIDTH = 128
HEIGHT = 64

# SSD1306 128x64 初始化序列（控制字节 0x00 由 _cmd 添加）
_INIT = (
    0xAE,             # display off
    0xD5, 0x80,       # clock divide / oscillator
    0xA8, 0x3F,       # multiplex ratio = 64
    0xD3, 0x00,       # display offset = 0
    0x40,             # start line = 0
    0x8D, 0x14,       # charge pump on
    0x20, 0x00,       # horizontal addressing mode
    0xA1,             # segment remap
    0xC8,             # COM scan direction
    0xDA, 0x12,       # COM pins config for 128x64
    0x81, 0xCF,       # contrast
    0xD9, 0xF1,       # pre-charge period
    0xDB, 0x40,       # VCOMH deselect level
    0xA4,             # output follows RAM content
    0xA6,             # normal (non-inverted) display
    0xAF,             # display on
)


class SSD1306:
    def __init__(self, i2c, addr):
        self.i2c = i2c
        self.addr = addr
        self.buf = bytearray(WIDTH * HEIGHT // 8)
        self.fb = framebuf.FrameBuffer(self.buf, WIDTH, HEIGHT, framebuf.MONO_HLSB)
        for c in _INIT:
            self._cmd(c)
        self.fb.fill(0)
        self.show()

    def _cmd(self, c):
        self.i2c.writeto(self.addr, bytes((0x00, c)))

    def show(self):
        # 分页写，每块 32 字节，兼容各种 I2C 缓冲长度限制
        for page in range(HEIGHT // 8):
            self._cmd(0xB0 + page)
            self._cmd(0x00)            # column low nibble
            self._cmd(0x10)            # column high nibble
            base = page * WIDTH
            for x in range(0, WIDTH, 32):
                chunk = bytes(self.buf[base + x:base + x + 32])
                self.i2c.writeto(self.addr, b"\x40" + chunk)


def _bar(fb, x, y, w, h, pct):
    fb.rect(x, y, w, h, 1)
    inner = (w - 4) * pct // 100
    if inner > 0:
        fb.fill_rect(x + 2, y + 2, inner, h - 4, 1)


def show_system(oled, info):
    """把主机送来的系统占用画到 128x64：标题 / 三条占用条 / 主机名。"""
    fb = oled.fb
    fb.fill(0)
    fb.text("PC SYS " + info["time"], 0, 0, 1)
    rows = (("CPU", info["cpu"], 14),
            ("MEM", info["mem"], 28),
            ("DSK", info["disk"], 42))
    for name, value, y in rows:
        fb.text("%-4s%3d%%" % (name, value), 0, y, 1)
        _bar(fb, 68, y, 56, 8, value)
    fb.text("H:" + info["host"][:12], 0, 56, 1)
    oled.show()
    return True


def show_banner(oled, line1, line2):
    fb = oled.fb
    fb.fill(0)
    fb.text(line1[:16], 0, 20, 1)
    fb.text(line2[:16], 0, 36, 1)
    oled.show()


def init():
    """探测并初始化 OLED；接错线/没接屏都返回 None，绝不影响 HID 主循环。"""
    try:
        i2c = machine.I2C(I2C_BUS, sda=machine.Pin(SDA_PIN),
                          scl=machine.Pin(SCL_PIN), freq=I2C_FREQ)
        found = i2c.scan()
        addr = None
        candidates = (OLED_ADDR, 0x3D) if OLED_ADDR == 0x3C else (OLED_ADDR,)
        for a in candidates:
            if a in found:
                addr = a
                break
        if addr is None:
            print("[oled] not found, i2c scan = %s (check SDA=GP%d SCL=GP%d ADDR=0x%02X)" %
                  (found, SDA_PIN, SCL_PIN, OLED_ADDR))
            return None
        d = SSD1306(i2c, addr)
        show_banner(d, "PICO USB HID", "OLED READY")
        print("[oled] SSD1306 %dx%d @0x%02X ready" % (WIDTH, HEIGHT, addr))
        return d
    except Exception as e:
        print("[oled] init failed:", e)
        return None
