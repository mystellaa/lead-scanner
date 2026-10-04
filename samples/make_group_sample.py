# -*- coding: utf-8 -*-
"""生成一张「群聊线索」模拟截图，用于验证 线索人 / 业务 / 内容 三个字段。

用法：  python samples/make_group_sample.py

画面模仿视频号线索群：群友转发客户信息，并 @业务 去跟进。
用的是虚构姓名和号码，可以放心提交到代码库。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "sample_group_chat.png"
W, H = 720, 520
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]

BG = (237, 237, 237)
CARD = (255, 255, 255)
TEXT = (25, 25, 25)
MUTED = (140, 140, 140)
NAME = (87, 107, 149)
LINK = (57, 96, 168)
QUOTE_BG = (240, 240, 240)

# (发送者昵称, 备注名, 消息正文, 是否引用块)
MESSAGES = [
    ("彭曦", "AAa琉璃瓦老彭", "18697110000青海西宁别墅130方左右刘总", False),
    ("彭曦", "AAa琉璃瓦老彭", "18697110000青海西宁别墅130方左右刘总", True),
]
MENTION = ("韦东雅", "A真赞瓦业-韦东雅-13392262816")


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def main() -> int:
    f_title = load_font(22)
    f_name = load_font(19)
    f_body = load_font(21)
    f_small = load_font(16)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 62], fill=CARD)
    d.text((24, 20), "真赞瓦业 · 线索群（48）", font=f_title, fill=TEXT)
    d.line([0, 62, W, 62], fill=(220, 220, 220))

    y = 92
    for name, note, body, quoted in MESSAGES:
        # 头像占位（真实头像是图片，这里画纯色圆避免文字被 OCR 混进昵称行）
        d.ellipse([24, y, 66, y + 42], fill=(210, 216, 226))

        d.text((80, y + 10), f"{name}（{note}）", font=f_name, fill=MUTED)
        y += 40

        if quoted:
            # 引用块：白底 + 左侧竖线，宽度自适应不折行（折行会把内容截断）
            qw = int(d.textlength(body, font=f_body)) + 62
            d.rectangle([80, y, 80 + qw, y + 74], fill=CARD)
            d.rectangle([80 + 14, y + 14, 80 + 17, y + 60], fill=(200, 200, 200))
            d.text((80 + 28, y + 12), f"{name}（{note}）：", font=f_small, fill=MUTED)
            d.text((80 + 28, y + 38), body, font=f_body, fill=LINK)
            y += 82
        else:
            tw = d.textlength(body, font=f_body)
            d.rounded_rectangle([80, y, 80 + tw + 34, y + 46], radius=8, fill=CARD)
            d.text((97, y + 11), body, font=f_body, fill=TEXT)
            y += 76

        # @业务
        mention = f"@{MENTION[0]}（{MENTION[1]}）"
        d.rounded_rectangle([80, y, 80 + d.textlength(mention, font=f_small) + 30, y + 40],
                            radius=8, fill=QUOTE_BG)
        d.text((95, y + 12), mention, font=f_small, fill=(70, 70, 70))
        y += 54

    img.save(OUT)
    print(f"已生成群聊测试截图：{OUT}  ({W}x{H})")
    print("预期识别结果：")
    print("  手机号 : 18697110000")
    print("  内容   : 青海西宁别墅130方左右刘总")
    print("  线索人 : 彭曦")
    print("  业务   : 韦东雅")
    print("  注意   : @ 括号里的 13392262816 是业务员的号，不应被当成客户线索")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
