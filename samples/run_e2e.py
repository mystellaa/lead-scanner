# -*- coding: utf-8 -*-
"""OCR 端到端冒烟测试（不开界面，直接看识别效果）。

用法：
    python samples/run_e2e.py                       # 用自带示例图
    python samples/run_e2e.py 图片1.png 图片2.png    # 指定图片
    python samples/run_e2e.py --dir 某个文件夹       # 整个文件夹

它会打印每张图的 OCR 原文和提取到的线索，方便调参 / 排查识别不准的问题。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config          # noqa: E402
import ocr             # noqa: E402
import phone_extract as pe  # noqa: E402


def collect(argv: list[str]) -> list[Path]:
    if "--dir" in argv:
        folder = Path(argv[argv.index("--dir") + 1])
        return sorted(p for p in folder.iterdir()
                      if p.is_file() and p.suffix.lower() in config.SUPPORTED_EXTS)
    files = [Path(a) for a in argv if not a.startswith("-")]
    if not files:
        default = Path(__file__).resolve().parent / "sample_comment.png"
        if not default.exists():
            print("没有指定图片，且示例图不存在。请先运行： python samples/make_sample.py")
            raise SystemExit(2)
        files = [default]
    return files


def main() -> int:
    files = collect(sys.argv[1:])
    if not files:
        print("没有找到符合条件的图片。")
        return 2

    key = ocr.pick_engine_key()
    if not key:
        print("未检测到 OCR 引擎。请先执行： pip install rapidocr-onnxruntime")
        return 2

    print(f"OCR 引擎：{ocr.engine_status()[0]}")
    print(f"待处理：{len(files)} 张\n")

    total_leads = 0
    for path in files:
        print("=" * 70)
        print(f"图片：{path.name}")
        print("=" * 70)
        t0 = time.time()
        res = ocr.recognize(path)
        if not res.ok:
            print(f"  识别失败：{res.error}")
            continue
        print(f"  耗时 {res.elapsed}s，识别到 {len(res.lines)} 行文字：")
        for i, ln in enumerate(res.lines, 1):
            print(f"    {i:>2}. {ln.text}")

        leads = pe.extract_leads(res.full_text)
        if leads:
            print(f"\n  提取到 {len(leads)} 条线索：")
            for i, ld in enumerate(leads, 1):
                print(f"    ── 第 {i} 条 ──────────────────────────")
                print(f"       客户ID : {ld.customer_id or '(空)'}")
                print(f"       手机号 : {ld.phone}")
                print(f"       内容   : {ld.content or '(空)'}")
                print(f"       线索人 : {ld.source_person or '(空)'}")
                print(f"       业务   : {ld.business or '(空)'}")
                if ld.note:
                    print(f"       备注   : {ld.note}")
            total_leads += len(leads)
        else:
            cid, src = pe.extract_customer_id(pe.to_lines(res.full_text))
            print(f"\n  未提取到手机号。客户ID猜测：{cid or '(空)'}  [依据：{src or '无'}]")
        print(f"  （单张耗时 {round(time.time() - t0, 2)}s）\n")

    print("=" * 70)
    print(f"合计提取线索：{total_leads} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
