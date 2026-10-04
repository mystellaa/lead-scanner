# -*- coding: utf-8 -*-
"""GUI 冒烟测试：不开真实交互，把「加图 -> 点识别 -> 入库 -> 出表」跑一遍。

用途：改动 ui.py / database.py / phone_extract.py 之后快速回归，
      30 秒内知道界面整条链路有没有被改坏。
数据写入临时目录，**不会污染真实的 data/leads.csv**。

用法：
    .venv\\Scripts\\python.exe samples\\gui_smoketest.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

SAMPLES = [
    ROOT / "samples" / "sample_comment.png",      # 评论区截图 -> 4 条
    ROOT / "samples" / "sample_group_chat.png",   # 群聊线索截图 -> 1 条（含线索人/业务）
]


def _make_probe_image(path: Path) -> Path:
    """现画一张带全新号码的小截图，用来测「本批」而不撞历史数据的号码。

    内容刻意做成「发布人(备注): + 正文含号码」—— 顺便验证
    「发布人 = 运营」这条规矩和按行绑定是否生效。
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (560, 170), "white")
    draw = ImageDraw.Draw(img)
    font = None
    for cand in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                 "/System/Library/Fonts/PingFang.ttc"):
        try:
            font = ImageFont.truetype(cand, 24)
            break
        except Exception:
            continue
    y = 26
    for line in ("彭曦(AAa琉璃瓦老彭):", "需要屋面瓦 13500001234"):
        draw.text((22, y), line, fill=(24, 24, 24), font=font)
        y += 48
    img.save(path)
    return path


def main() -> int:
    missing = [p.name for p in SAMPLES if not p.exists()]
    if missing:
        print(f"缺少示例图 {missing}，请先运行： python samples/make_sample.py 和 make_group_sample.py")
        return 2

    # 把数据目录指向临时位置，避免污染真实线索库
    tmp = Path(tempfile.mkdtemp(prefix="ls_gui_"))
    config.DATA_DIR = tmp
    config.PASTE_DIR = tmp / "paste"
    config.LEADS_CSV = tmp / "leads.csv"
    config.SOURCES_FILE = tmp / "sources.json"
    config.EXPORT_PREFS_FILE = tmp / "export_prefs.json"
    config.COPY_PREFS_FILE = tmp / "copy_prefs.json"

    import ui  # 必须在改写 config 之后再导入

    app = ui.LeadScannerApp()
    app.source_var.set("视频号-北海服务商")
    app._extend_files(list(SAMPLES))

    print(">>> 模拟点击「开始识别」（2 张图）", flush=True)
    app._start_scan()

    deadline = time.time() + 240
    while time.time() < deadline:
        app.update()
        time.sleep(0.05)
        if app.batch["images"] >= len(SAMPLES) and not (app.worker and app.worker.is_alive()):
            for _ in range(40):          # 把队列里剩下的消息排空
                app.update()
                time.sleep(0.02)
            break

    ok = True
    print("批次统计  :", {k: v for k, v in app.batch.items() if k != "customers"}, flush=True)
    print("统计标签  :", app.stat_label["text"], flush=True)
    print("状态栏    :", app.status["text"], flush=True)

    rows = app.tree.get_children()
    values = [tuple(str(v) for v in app.tree.item(i)["values"]) for i in rows]
    idx = {name: n for n, name in enumerate(config.CSV_FIELDS)}

    print(f"\n表格内容（{len(rows)} 行）：", flush=True)
    for v in values:
        print("   ", " | ".join(v), flush=True)

    def fail(msg: str) -> None:
        nonlocal ok
        ok = False
        print(f"  !! {msg}", flush=True)

    if len(rows) != 5:
        fail(f"期望 5 行（评论区 4 + 群聊 1），实际 {len(rows)} 行")
    if app.batch["failed"]:
        fail("有图片识别失败")
    if any(not v[idx["customer_id"]] and not v[idx["phone"]] for v in values):
        fail("存在既无客户ID又无手机号的行")

    # 群聊那张图的三要素
    chat = [v for v in values if v[idx["phone"]] == "18697110000"]
    if len(chat) != 1:
        fail(f"群聊线索应恰好 1 条，实际 {len(chat)} 条")
    else:
        row = chat[0]
        if row[idx["content"]] != "青海西宁别墅130方左右刘总":
            fail(f"内容不对：{row[idx['content']]!r}")
        if row[idx["source_person"]] != "彭曦":
            fail(f"线索人不对：{row[idx['source_person']]!r}")
        if row[idx["business"]] != "韦东雅":
            fail(f"业务不对：{row[idx['business']]!r}")
        if row[idx["source_platform"]] != "视频号-北海服务商":
            fail(f"来源平台不对：{row[idx['source_platform']]!r}")

    # 业务员自己的号码不能混进来
    if any(v[idx["phone"]] == "13392262816" for v in values):
        fail("业务员的号码被误当成客户线索")

    # 评论区那张图的客户ID
    if not any(v[idx["customer_id"]] == "赵明刚" for v in values):
        fail("评论区截图的客户ID（赵明刚）丢失")

    # ---- 单元格编辑 / 批量改来源 / 删除 ----
    print("\n>>> 测试表格编辑：改字段 → 改来源 → 删除", flush=True)
    first_iid = rows[0]

    app._commit_cell_edit(first_iid, "content", "人工修改后的内容")
    if app.tree.set(first_iid, "content") != "人工修改后的内容":
        fail("改字段没生效")

    app.source_var.set("视频号-测试来源")
    picked = list(app.tree.get_children()[:2])
    app.tree.selection_set(*picked)
    app._reassign_source()
    if any(app.tree.set(i, "source_platform") != "视频号-测试来源" for i in picked):
        fail("批量改来源没生效")

    before_del = len(app.tree.get_children())
    original_ask = ui.messagebox.askyesno
    ui.messagebox.askyesno = lambda *a, **k: True      # 自动点「是」
    try:
        app.tree.selection_set(app.tree.get_children()[0])
        app._delete_rows()
    finally:
        ui.messagebox.askyesno = original_ask
    if len(app.tree.get_children()) != before_del - 1:
        fail("删除行没生效")

    if app.tree.set(app.tree.get_children()[-1], "content") == "人工修改后的内容":
        fail("删除后剩下的行不对")
    print(f"    编辑/改来源/删除 均生效，剩 {len(app.tree.get_children())} 行", flush=True)

    print("\n>>> leads.csv 实际落盘内容：", flush=True)
    print((tmp / "leads.csv").read_text(encoding="utf-8-sig"), flush=True)

    # ---- 临时模式（分批视图）：数据照常入库，列表只显示本批 ----
    print("\n>>> 测试临时模式（分批视图）", flush=True)
    import roster  # noqa: E402

    # 现画一张带新号码的截图，顺便验证「发布人 = 线索人」
    probe = _make_probe_image(tmp / "probe_batch.png")
    csv_before_tmp = (tmp / "leads.csv").read_text(encoding="utf-8-sig")
    rows_before = len(app.db)

    app.batch_records.clear()
    app.temp_var.set(True)
    app._toggle_temp_mode()
    app.update()
    if not app.temp_mode:
        fail("没切到临时模式")
    if app.tree.get_children():
        fail(f"刚切到临时模式时列表该是空的，实际 {len(app.tree.get_children())} 行")
    if app.btn_clear_batch.instate(["disabled"]):
        fail("「清空面板」按钮应该始终可用（它不删数据）")

    app._clear_files()
    app.source_var.set("视频号-彭曦")
    app._extend_files([probe])
    app._start_scan()
    deadline = time.time() + 240
    while time.time() < deadline:
        app.update()
        time.sleep(0.05)
        if app.batch["images"] >= 1 and not (app.worker and app.worker.is_alive()):
            for _ in range(40):
                app.update()
                time.sleep(0.02)
            break

    # ① 必须照常写入总库（这是本轮修正的重点）
    if len(app.db) != rows_before + 1:
        fail(f"临时模式下线索应照常入库：{rows_before} -> {len(app.db)}（期望 +1）")
    if (tmp / "leads.csv").read_text(encoding="utf-8-sig") == csv_before_tmp:
        fail("临时模式下 leads.csv 没被更新（应该照常写）")
    # ② 列表只显示本批
    if len(app.batch_records) != 1:
        fail(f"本批应有 1 条，实际 {len(app.batch_records)}")
    if len(app.tree.get_children()) != len(app.batch_records):
        fail(f"列表应只显示本批：表 {len(app.tree.get_children())} 行 / 本批 {len(app.batch_records)} 条")
    # ③ 发布人 = 彭曦（运营）→ 线索人必须绑到他，号码也要对得上
    if app.batch_records:
        rec0 = app.batch_records[0]
        if rec0.source_person != "彭曦":
            fail(f"线索人没绑到发布人：{rec0.source_person!r}（应为 彭曦）")
        if rec0.phone != "13500001234":
            fail(f"号码不对：{rec0.phone!r}")
    print(f"    本批 {len(app.batch_records)} 条 · 总库 {rows_before} -> {len(app.db)} 条"
          f" · 发布人绑定 ✔ · 列表只显示本批 ✔", flush=True)

    # 复制：默认**不带表头**
    copy_cols, copy_header = config.load_copy_prefs()
    if copy_header:
        fail("复制默认不该带表头")
    app._copy_batch()
    app.update()
    clip_lines = app.clipboard_get().splitlines()
    if len(clip_lines) != len(app.batch_records):
        fail(f"剪贴板应有 {len(app.batch_records)} 行（无表头），实际 {len(clip_lines)}")
    if any(len(x.split("\t")) != len(copy_cols) for x in clip_lines):
        fail("剪贴板各行列数不一致")
    print(f"    剪贴板 {len(clip_lines)} 行 × {len(copy_cols)} 列（无表头）✔", flush=True)

    # 双击「来源平台」时，候选应收窄到该行线索人负责的账号
    target = app.tree.get_children()[0] if app.tree.get_children() else None
    if target is None:
        fail("表里没有行，无法验证平台联动")
    else:
        got = app._platform_choices(target)
        want = roster.accounts_of("彭曦")
        if got != want:
            fail(f"平台候选没按运营收窄：{got} != {want}")
        print(f"    双击来源平台候选收窄为 {got} ✔", flush=True)

    # 清空面板（T 键）：只清视图与图片列表，数据不动
    rows_after_scan = len(app.db)
    app._reset_panel()
    app.update()
    if app.batch_records or app.tree.get_children():
        fail("T 键清空面板没生效")
    if len(app.db) != rows_after_scan:
        fail(f"清空面板不该动数据：{rows_after_scan} -> {len(app.db)}")
    print(f"    清空面板 ✔（总库仍 {len(app.db)} 条，数据没删）", flush=True)

    # 快捷键守门：Enter 触发识别（mock 掉真正的扫描，不跑 OCR）
    fired = []
    orig_scan, orig_typing = app._start_scan, app._typing_somewhere
    app._start_scan = lambda: fired.append("scan")
    app._typing_somewhere = lambda: False
    try:
        app._hotkey_scan()
    finally:
        app._start_scan, app._typing_somewhere = orig_scan, orig_typing
    if "scan" not in fired:
        fail("Enter 键没有触发识别")
    print("    Enter 快捷键触发识别 ✔", flush=True)

    # 切回总库：显示全部
    app.temp_var.set(False)
    app._toggle_temp_mode()
    app.update()
    if app.temp_mode:
        fail("没切回总库模式")
    if len(app.tree.get_children()) != len(app.db):
        fail(f"总库模式应显示全部：表 {len(app.tree.get_children())} / 库 {len(app.db)}")
    print(f"    切回总库显示全部 {len(app.db)} 条 ✔", flush=True)

    app.destroy()
    shutil.rmtree(tmp, ignore_errors=True)

    print(f">>> 冒烟测试{'通过' if ok else '不通过'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
