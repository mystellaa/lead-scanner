# -*- coding: utf-8 -*-
"""生成一张模拟「视频号评论区」截图，用于验证 OCR 链路是否通畅。

用法：  python samples/make_sample.py
产物：  samples/sample_comment.png
"""

from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("需要 Pillow： pip install pillow")
    raise SystemExit(1)

HERE = Path(__file__).resolve().parent
OUT = HERE / "sample_comment.png"

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]

COMMENTS = [
    ("赵明刚", "需要屋面瓦，联系13812345678"),
    ("阿May", "屋顶翻新咨询 139 8888 6666"),
    ("陈老板", "广西来宾的 139-0000-2222"),
    ("罗工", "先加我微信 luogong_2026"),
    ("韦师傅", "电话138123456"),          # 故意留一个不完整号码
]


def load_font(size: int):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def main() -> int:
    W, H = 760, 760
    img = Image.new("RGB", (W, H), "#ffffff")
    d = ImageDraw.Draw(img)
    f_title = load_font(26)
    f_name = load_font(24)
    f_text = load_font(23)
    f_small = load_font(19)

    d.rectangle([0, 0, W, 62], fill="#f7f8fa")
    d.text((24, 18), "视频号 · 北海服务商", font=f_title, fill="#1f2329")
    d.line([0, 62, W, 62], fill="#e5e6eb", width=1)

    y = 88
    for i, (name, text) in enumerate(COMMENTS, 1):
        # 头像占位
        d.ellipse([24, y, 68, y + 44], fill="#e8eaed")
        d.text((38, y + 9), str(i), font=f_small, fill="#8a9099")

        d.text((82, y + 2), name, font=f_name, fill="#1f2329")
        d.text((82, y + 34), text, font=f_text, fill="#333333")
        d.text((82, y + 68), "3小时前", font=f_small, fill="#a8adb5")

        y += 108
        d.line([24, y - 12, W - 24, y - 12], fill="#f0f1f3", width=1)

    img.save(OUT)
    print(f"已生成测试截图：{OUT}  ({W}x{H})")
    print("预期识别结果：")
    print("  赵明刚   13812345678")
    print("  阿May    13988886666   (空格写法)")
    print("  陈老板   13900002222   (横线写法)")
    print("  罗工     —             (无手机号，不产生线索)")
    print("  韦师傅   138123456     (号码可能不完整)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
