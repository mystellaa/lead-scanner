# -*- coding: utf-8 -*-
"""环境自检 + 核心逻辑测试。

直接运行：  python selftest.py

它会做三件事：
  1. 检查 Python / Tkinter / Pillow / OpenCV / OCR 引擎 是否就绪
  2. 用真实数据跑一遍「文本 -> 手机号+客户ID -> 去重 -> 入库 -> 导出」
  3. 打印每条测试的通过情况

不依赖 Tkinter，也不依赖 OCR 引擎，纯逻辑验证。
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config          # noqa: E402
import database as db  # noqa: E402
import ocr             # noqa: E402
import phone_extract as pe  # noqa: E402

PASS, FAIL = 0, 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {desc}")
    else:
        FAIL += 1
        print(f"  [FAIL] {desc}" + (f"   -> {detail}" if detail else ""))


def eq(desc: str, got, want) -> None:
    check(desc, got == want, f"期望 {want!r}，实际 {got!r}")


# ================================================================ 1 环境
def test_env() -> None:
    print("\n[1] 运行环境")
    print(f"  Python {sys.version.split()[0]}  ({sys.executable})")

    try:
        import tkinter
        check(f"Tkinter 可用（图形界面必需）  Tk {tkinter.TkVersion}", True)
    except Exception as exc:
        check("Tkinter 可用（图形界面必需）", False, f"{exc} —— 界面无法启动！")

    try:
        import PIL
        check(f"Pillow 可用（图片预览）  {PIL.__version__}", True)
    except Exception:
        check("Pillow 可用（图片预览）", False, "缺少不影响识别，仅无缩略图")

    try:
        import cv2
        check(f"OpenCV 可用（图像预处理）  {cv2.__version__}", True)
    except Exception:
        check("OpenCV 可用（图像预处理）", False, "缺少不影响主流程")

    engines = ocr.available_engines()
    for name, ok, hint in engines:
        check(f"OCR 引擎 {name}", ok, "" if ok else f"未安装 -> {hint}")
    if not ocr.pick_engine_key():
        print("  !! 没有任何 OCR 引擎，识别功能不可用。任选其一：")
        print("       pip install rapidocr-onnxruntime   (轻量，推荐)")
        print("       pip install paddlepaddle paddleocr (需求指定)")


# ================================================================ 2 手机号
def test_phone() -> None:
    print("\n[2] 手机号提取")
    cases = [
        ("标准 11 位", "联系13812345678", ["13812345678"]),
        ("带空格", "手机 138 1234 5678", ["13812345678"]),
        ("带横线", "电话138-1234-5678", ["13812345678"]),
        ("带点号", "138.1234.5678", ["13812345678"]),
        ("全角数字", "电话１３８１２３４５６７８", ["13812345678"]),
        ("不完整 9 位", "138123456", ["138123456"]),
        ("号码在文字中间", "需要屋面瓦联系13812345678详聊", ["13812345678"]),
        ("一行两个号码", "13812345678 或 13900002222", ["13812345678", "13900002222"]),
        ("无效前缀不提取", "12345678901", []),
        ("超长数字串不误判", "18812345678901", []),
        ("无关数字不提取", "价格 5000 元 电话 8 位 12345678", []),
        ("不跨行粘连(时间戳干扰)", "13812345678\n3小时前", ["13812345678"]),
        ("12位数字串不误判", "13812345678 3", ["13812345678"]),
        ("13位连续数字不误判", "1381234567890", []),
    ]
    for desc, text, want in cases:
        got = [h.phone for h in pe.extract_phones(text)]
        eq(desc, got, want)

    hits = pe.extract_phones("138123456")
    check("不完整号码带备注", hits and hits[0].note == "号码可能不完整",
          f"note={hits[0].note if hits else 'None'!r}")
    hits = pe.extract_phones("13812345678")
    check("完整号码无备注", hits and hits[0].note == "", "")


# ================================================================ 3 客户ID
def test_customer_id() -> None:
    print("\n[3] 客户ID 提取")
    cases = [
        ("标签同行", "昵称: 阿May\n手机13812345678", "阿May"),
        ("标签在上一行", "用户：\n赵明刚\n评论：\n需要屋面瓦联系13812345678", "赵明刚"),
        ("客户标签", "客户：李四\n138-1234-5678", "李四"),
        ("微信号当ID", "微信号：abc_123\n13812345678", "abc_123"),
        ("@昵称", "@小张 有货联系13812345678", "小张"),
        ("中文冒号", "姓名：王小明\n电话13812345678", "王小明"),
        ("无ID时留空", "需要屋面瓦联系13812345678", ""),
        ("不把号码当ID", "用户：13812345678", ""),
    ]
    for desc, text, want in cases:
        got = pe.extract_leads(text)
        cid = got[0].customer_id if got else pe.extract_customer_id(pe.to_lines(text))[0]
        eq(desc, cid, want)


# ================================================================ 4 完整提取
def test_extract() -> None:
    print("\n[4] 端到端提取（PRD 示例）")
    text = "用户：\n赵明刚\n评论：\n需要屋面瓦联系13812345678"
    leads = pe.extract_leads(text)
    check("提取到 1 条线索", len(leads) == 1, f"实际 {len(leads)}")
    if leads:
        eq("客户ID", leads[0].customer_id, "赵明刚")
        eq("手机号", leads[0].phone, "13812345678")

    text2 = "用户：陈老板\n电话13900001111 或 13900002222"
    leads2 = pe.extract_leads(text2)
    eq("同行两个号码 -> 两条线索", len(leads2), 2)
    check("两条共享同一客户ID", all(l.customer_id == "陈老板" for l in leads2))
    check("同行多号有备注提示", any("同一行出现多个号码" in l.note for l in leads2),
          str([l.note for l in leads2]))

    check("无号码返回空", pe.extract_leads("今天天气不错") == [])

    # ---- 真实 OCR 输出回归 ----
    # 下面这段是 samples/sample_comment.png 经 RapidOCR 识别的真实结果，
    # 曾暴露出「号码跨行粘连」「客户ID全绑第一个人」两个 bug，固化为回归用例。
    real = "\n".join([
        "视频号·北海服务商", "赵明刚", "需要屋面瓦，联系13812345678", "3小时前",
        "阿May", "屋顶翻新咨询13988886666", "3小时前",
        "陈老板", "广西来宾的139-0000-2222", "3小时前",
        "罗工", "先加我微信luogong_2026", "3小时前",
        "韦师傅", "电话138123456", "3小时前",
    ])
    leads3 = pe.extract_leads(real)
    eq("真实截图提取到 4 条线索", len(leads3), 4)
    eq("客户与号码一一对应（就近绑定）",
       [(l.customer_id, l.phone) for l in leads3],
       [("赵明刚", "13812345678"), ("阿May", "13988886666"),
        ("陈老板", "13900002222"), ("韦师傅", "138123456")])
    check("不完整号码带备注", leads3 and leads3[-1].note == "号码可能不完整",
          leads3[-1].note if leads3 else "")
    check("号码长度均合法（无跨行粘连）", all(7 <= len(l.phone) <= 11 for l in leads3),
          str([l.phone for l in leads3]))
    check("时间戳未被当成客户ID", all("小时前" not in l.customer_id for l in leads3))

    # 新版 RapidOCR(1.4.x) 会把头像旁的楼层序号一起识别进昵称行（「2 阿May」），
    # 导致昵称行带空格而匹配失败。单独固化一组用例。
    real_v2 = "\n".join([
        "视频号·北海服务商", "赵明刚", "需要屋面瓦，联系13812345678", "3小时前",
        "2 阿May", "屋顶翻新咨询13988886666", "3小时前",
        "3 陈老板", "广西来宾的 139-0000-2222", "3小时前",
        "4 罗工", "先加我微信luogong_2026", "3小时前",
        "5 韦师傅", "电话138123456", "3小时前",
    ])
    leads4 = pe.extract_leads(real_v2)
    eq("带楼层序号时仍提取 4 条", len(leads4), 4)
    eq("楼层序号被正确剥离",
       [(l.customer_id, l.phone) for l in leads4],
       [("赵明刚", "13812345678"), ("阿May", "13988886666"),
        ("陈老板", "13900002222"), ("韦师傅", "138123456")])


# ================================================================ 5 群聊字段
def test_group_chat() -> None:
    """群聊截图：线索人 / 业务 / 内容（以下为真实 OCR 输出，固化为回归用例）。"""
    print("\n[5] 群聊截图：线索人 / 业务 / 内容")
    real = "\n".join([
        "壹曦（AAa琉璃瓦老彭）",
        "18697110000青海西宁别墅130方左右刘总",
        "壹曦（AAa琉璃瓦老彭）",
        "彭曦（AAa琉璃瓦老彭）：",
        "18697110000青海西宁别墅130方左右刘总",
        "@韦东雅（A真赞瓦业-韦东雅-13392262816）",
    ])
    leads = pe.extract_leads(real)
    eq("只提取 1 条线索", len(leads), 1)
    if leads:
        ld = leads[0]
        eq("手机号", ld.phone, "18697110000")
        eq("线索人", ld.source_person, "彭曦")
        eq("业务", ld.business, "韦东雅")
        eq("内容", ld.content, "青海西宁别墅130方左右刘总")
    check("业务员自己的号码未被误提取",
          all(l.phone != "13392262816" for l in leads), str([l.phone for l in leads]))

    eq("排除 @某人（…）括号内的号码",
       [h.phone for h in pe.extract_phones("客户13812345678 @小王（备用13900000000）")],
       ["13812345678"])
    eq("但 @ 行外的号码照常提取",
       [h.phone for h in pe.extract_phones("@小王（备用13900000000）13812345678")],
       ["13812345678"])
    eq("线索人优先采纳引用块发送者（带冒号）",
       pe.extract_source_person(pe.to_lines("张三（备注）\n张三（备注）："))[0], "张三")
    eq("内容清掉尾部客套词",
       pe.extract_content_from_line("需要屋面瓦，联系13812345678"), "需要屋面瓦")
    eq("纯号码消息内容为空", pe.extract_content_from_line("13812345678"), "")
    eq("无 @ 时业务为空", pe.extract_business(pe.to_lines("随便一句话"))[0], "")


# ================================================================ 6 存储去重
def test_database() -> None:
    print("\n[6] 存储与去重（临时库）")
    tmpdir = Path(tempfile.mkdtemp(prefix="ls_test_"))
    csv_path = tmpdir / "leads.csv"
    try:
        store = db.LeadDatabase(csv_path)
        eq("初始为空", len(store), 0)

        first = store.add([
            db.make_record("赵明刚", "13812345678", "", "视频号-北海服务商", "a.png"),
            db.make_record("李四", "138 1234 5679".replace(" ", ""), "", "视频号-北海服务商", "a.png"),
        ])
        eq("首次入库 2 条", first.added_count, 2)

        dup = store.add([
            db.make_record("赵明刚", "13812345678", "", "视频号-北海服务商", "b.png"),
        ])
        eq("重复手机号被拦截", dup.added_count, 0)
        eq("重复计数正确", dup.dup_count, 1)
        check("重复原因写入备注", "重复" in dup.duplicates[0].note, dup.duplicates[0].note)

        # 非严格模式：同一客户ID的新号码应当保留
        more = store.add([db.make_record("赵明刚", "13900008888", "", "视频号-北海服务商", "b.png")])
        eq("同客户ID新号码保留", more.added_count, 1)
        check("新号码有标注", "同一客户ID已有其它号码" in more.added[0].note, more.added[0].note)

        # 无手机号 -> 用 客户ID+来源 判重
        n1 = store.add([db.make_record("王五", "", "", "视频号-来宾服务商", "c.png")])
        n2 = store.add([db.make_record("王五", "", "", "视频号-来宾服务商", "d.png")])
        eq("无号码记录可入库", n1.added_count, 1)
        eq("同ID同来源无号码判重", n2.dup_count, 1)

        bad = store.add([db.make_record("", "", "", "视频号-来宾服务商", "e.png")])
        eq("空记录被过滤", bad.added_count + bad.dup_count + len(bad.invalid), 1)
        eq("空记录归入 invalid", len(bad.invalid), 1)

        eq("库内总数", len(store), 4)

        out = store.export(tmpdir / "export.csv")
        check("导出文件生成", out.exists() and out.stat().st_size > 0)
        eq("CSV 中文表头", db.read_header(out), db.header_names())

        # 导出列勾选 + 表头开关（界面导出对话框用的就是这两个参数）
        picked = store.export(tmpdir / "picked.csv",
                              fields=["phone", "source_person"], with_header=True)
        eq("自定义列的表头", db.read_header(picked), ["联系方式", "线索人"])
        body = picked.read_text(encoding=config.CSV_ENCODING).splitlines()
        check("只写勾选的列", all(len(ln.split(",")) == 2 for ln in body[1:]), str(body[:2]))

        nohead = store.export(tmpdir / "nohead.csv",
                              fields=["phone"], with_header=False)
        first = nohead.read_text(encoding=config.CSV_ENCODING).splitlines()[0]
        check("可以不带表头导出", first.isdigit(), first)

        reloaded = db.LeadDatabase(csv_path)
        eq("重新加载条数一致", len(reloaded), 4)
        eq("重新加载后去重索引有效",
           reloaded.add([db.make_record("赵明刚", "13812345678", "", "视频号-北海服务商", "x.png")]).added_count, 0)

        ui_rows = store.rows_for_ui()
        eq("界面行数据列数与配置一致",
           len(ui_rows[0][1]) if ui_rows else 0, len(config.CSV_FIELDS))
        check("界面行数据带记录索引（编辑/删除定位用）",
              bool(ui_rows) and isinstance(ui_rows[0][0], int), str(ui_rows[:1]))

        # ---- 就地修改 / 删除 / 批量改来源 ----
        before = len(store)
        check("能改字段", store.update_field(0, "business", "罗淑冰,胡丹"))
        eq("改完读得到", store.get(0).business, "罗淑冰,胡丹")
        eq("改字段不改条数", len(store), before)
        check("字段名不合法时拒绝", not store.update_field(0, "不存在的字段", "x"))
        check("索引越界时拒绝", not store.update_field(999, "business", "x"))

        eq("批量改来源的条数", store.rebind_source([0, 1], "视频号-测试来源"), 2)
        eq("来源已改", store.get(0).source_platform, "视频号-测试来源")
        eq("改成相同来源返回 0", store.rebind_source([0, 1], "视频号-测试来源"), 0)

        eq("删除 1 条", store.delete_at([len(store) - 1]), 1)
        eq("删除后条数", len(store), before - 1)

        again = db.LeadDatabase(csv_path)
        eq("重载后改动还在", again.get(0).business, "罗淑冰,胡丹")
        eq("重载后条数一致", len(again), before - 1)
        eq("重载后来源改动还在", again.get(0).source_platform, "视频号-测试来源")

        bak = store.backup()
        check("备份文件生成", bak is not None and bak.exists())

        # 旧版本 6 列 CSV -> 新表头自动迁移，数据不能丢
        legacy = tmpdir / "legacy.csv"
        legacy.write_text(
            "客户ID,手机号,备注,来源平台,发现时间,图片来源\n"
            "老张,13800001111,历史备注,视频号-佛山服务商,2026-01-01 10:00,old.png\n",
            encoding=config.CSV_ENCODING)
        st = db.LeadDatabase(legacy)
        eq("旧表头迁移：条数不变", len(st), 1)
        eq("旧表头迁移：写成新表头", db.read_header(legacy), db.header_names())
        eq("旧表头迁移：老数据保留", st.records[0].note, "历史备注")
        eq("旧表头迁移：新列留空", st.records[0].source_person, "")
        eq("旧表头迁移：新列可正常写入",
           st.add([db.make_record("新客户", "13700002222", "", "视频号-佛山服务商", "n.png")]).added_count, 1)

        # 手工粘进去的裸数据（没有表头）—— 第一行不能被当表头吞掉
        manual = tmpdir / "manual.csv"
        manual.write_text(
            "18376976323,抖音-梁毅,梁毅,韦东雅\n"
            "17503729913,视频号-彭曦,彭曦,区玉清\n",
            encoding=config.CSV_ENCODING)
        check("无表头文件：首行不被误判成表头",
              not db.is_header_row(["18376976323", "抖音-梁毅", "梁毅"]))
        mrecs = db.read_csv(manual)
        eq("无表头文件：整份都读出来（不吞第一条）", len(mrecs), 2)
        eq("无表头文件：按位置对上前几列", mrecs[0].source_person, "梁毅")
        eq("无表头文件：来源平台列对得上", mrecs[1].source_platform, "视频号-彭曦")
        mstore = db.LeadDatabase(manual)
        eq("无表头文件：迁移后条数不变", len(mstore), 2)
        eq("无表头文件：迁移后写成标准表头", db.read_header(manual), db.header_names())

        nine = tmpdir / "nine.csv"
        nine.write_text(
            "13800002222,视频号-梁毅,梁毅,罗淑冰,需要瓦,刘总,备注X,2026-10-04 16:00,x.png\n",
            encoding=config.CSV_ENCODING)
        eq("9 列无表头：靠后的列也能对位", db.read_csv(nine)[0].content, "需要瓦")

        # ---- 内存库（界面「临时模式」的底层）：接口与总库一致，但不碰任何文件 ----
        mem_path = tmpdir / "never_created.csv"
        mem = db.LeadDatabase(mem_path, readonly=True)
        eq("内存库初始为空", len(mem), 0)
        eq("内存库能写入",
           mem.add([db.make_record("临时客户", "13700003333", "", "视频号-梁毅", "t.png")]).added_count, 1)
        eq("内存库同样去重",
           mem.add([db.make_record("临时客户", "13700003333", "", "视频号-梁毅", "t.png")]).dup_count, 1)
        check("内存库能改字段", mem.update_field(0, "content", "内存内容"))
        eq("内存库改完读得到", mem.get(0).content, "内存内容")
        eq("内存库批量改字段", mem.update_many([(0, "source_person", "梁毅"), (0, "note", "n1")]), 2)
        eq("内存库批量改完读得到", mem.get(0).source_person, "梁毅")
        eq("批量改重复值不计数", mem.update_many([(0, "source_person", "梁毅")]), 0)
        eq("内存库能删", mem.delete_at([0]), 1)
        mem.clear(keep_backup=False)
        eq("内存库清空", len(mem), 0)
        check("内存库全程没有生成文件（这就是「不写总库」的保证）", not mem_path.exists())
        check("内存库没有备份可做", mem.backup() is None)
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        # 清理可能被 make_record 影响的全局状态（lead_scanner/data 未动）
    print("  （临时目录已清理）")


# ================================================================ 6 来源
def test_sources() -> None:
    print("\n[7] 来源视频号配置")
    srcs = config.load_sources()
    check("来源列表非空", len(srcs) > 0, str(srcs))
    check("默认来源含 4 个服务商", len(srcs) >= 4, str(srcs))
    saved = config.save_sources(srcs + ["视频号-测试来源", "视频号-测试来源"])
    check("来源去重保存", saved.count("视频号-测试来源") == 1, str(saved))
    config.save_sources([s for s in saved if s != "视频号-测试来源"])   # 还原
    check("还原成功", "视频号-测试来源" not in config.load_sources())

    # 默认来源读写（会改真实配置，所以先记下来最后还原）
    original_default = config.load_default_source()
    try:
        config.set_default_source("视频号-北海服务商")
        eq("设完默认能读回", config.load_default_source(), "视频号-北海服务商")
        check("默认来源一定在列表里", "视频号-北海服务商" in config.load_sources())
        config.set_default_source("")
        eq("可以清掉默认（界面就留空）", config.load_default_source(), "")
    finally:
        config.set_default_source(original_default)
    eq("默认来源已还原", config.load_default_source(), original_default)

    # 导出偏好（「保存为默认导出选项」）—— 用临时文件，别动真实配置
    import shutil
    tmp = Path(tempfile.mkdtemp(prefix="ls_pref_"))
    original_prefs = config.EXPORT_PREFS_FILE
    config.EXPORT_PREFS_FILE = tmp / "export_prefs.json"
    try:
        fields, _ = config.load_export_prefs()
        eq("没保存过时用默认列", fields, list(config.EXPORT_DEFAULT_FIELDS))

        config.save_export_prefs(["phone", "content"], False)
        eq("保存后能原样读回", config.load_export_prefs(), (["phone", "content"], False))

        config.save_export_prefs(["不存在的列", "phone"], True)
        eq("非法列会被过滤掉", config.load_export_prefs(), (["phone"], True))
    finally:
        config.EXPORT_PREFS_FILE = original_prefs
        shutil.rmtree(tmp, ignore_errors=True)

    # 复制到剪贴板的偏好（默认不带表头）
    tmp2 = Path(tempfile.mkdtemp(prefix="ls_copy_"))
    original_copy = config.COPY_PREFS_FILE
    config.COPY_PREFS_FILE = tmp2 / "copy_prefs.json"
    try:
        c_fields, c_header = config.load_copy_prefs()
        check("复制默认不带表头（粘多维表格不然会多一行）", c_header is False)
        eq("复制默认列 = 全部列", c_fields, list(config.CSV_FIELDS))

        config.save_copy_prefs(["phone", "source_person", "source_platform"], True)
        eq("复制设置能存能读",
           config.load_copy_prefs(),
           (["phone", "source_person", "source_platform"], False))

        config.COPY_PREFS_FILE.write_text(
            '{"fields": ["phone"], "header": true}', encoding="utf-8")
        eq("旧设置含表头时复制仍不带列名", config.load_copy_prefs(), (["phone"], False))

        config.save_copy_prefs(["不存在的列"], False)
        eq("非法列被过滤掉后退回默认",
           config.load_copy_prefs()[0], list(config.CSV_FIELDS))
    finally:
        config.COPY_PREFS_FILE = original_copy
        shutil.rmtree(tmp2, ignore_errors=True)


# ================================================================ 7 OCR 行排序
def test_line_order() -> None:
    print("\n[8] OCR 文本行排序（不依赖引擎）")
    items = [
        ([[10, 60], [200, 60], [200, 80], [10, 80]], "第二行", 0.9),
        ([[10, 10], [100, 10], [100, 30], [10, 30]], "第一行左", 0.9),
        ([[120, 12], [220, 12], [220, 32], [120, 32]], "第一行右", 0.9),
    ]
    lines = ocr._order_lines(items)
    eq("合并为两行", len(lines), 2)
    if len(lines) == 2:
        eq("第一行内容", lines[0].text, "第一行左 第一行右")
        eq("第二行内容", lines[1].text, "第二行")
    check("低置信度被过滤", len(ocr._order_lines([([[0, 0], [9, 0], [9, 9], [0, 9]], "噪音", 0.1)])) == 0)


# ================================================================ 9 名单
def test_roster() -> None:
    """运营 / 账号 / 业务名单，以及按名单做名字纠错。"""
    print("\n[9] 名单与名字纠错")
    import shutil

    import roster

    eq("运营人数", len(roster.operator_names()), 6)
    check("王烔彤在运营名单里", roster.is_operator("王烔彤"))
    eq("账号反查运营：北海服务商", roster.operator_of("视频号-北海服务商"), "王烔彤")
    eq("账号反查运营：本地推", roster.operator_of("本地推"), "彭曦")
    eq("账号反查运营：抖音-梁毅", roster.operator_of("抖音-梁毅"), "梁毅")

    eq("业务总人数", len(roster.sales_names()), 15)
    eq("胡丹的分组（带教）", roster.sales_group("胡丹"), "带教")
    eq("新人人数", len(roster.newcomers()), 7)
    eq("带教名单", roster.mentors(), ["胡丹"])
    eq("新人会自动带上带教", roster.with_mentor(["罗淑冰"]), ["罗淑冰", "胡丹"])
    eq("老业务不带带教", roster.with_mentor(["韦东雅"]), ["韦东雅"])
    eq("排序：新人在前、带教垫后",
       roster.order_responsibles(["胡丹", "罗淑冰"]), ["罗淑冰", "胡丹"])
    eq("韦东雅的分组", roster.sales_group("韦东雅"), "老业务")
    eq("老业务人数", sum(1 for n in roster.sales_names()
                          if roster.sales_group(n) == "老业务"), 7)

    # 来源平台 -> 运营（界面上选完平台就能顺推线索归谁）
    eq("平台反查：账号全名", roster.operator_for_source("视频号-北海服务商"), ("王烔彤", "账号登记"))
    eq("平台反查：小红书账号", roster.operator_for_source("小红书-王可")[0], "王可")
    eq("平台反查：本地推", roster.operator_for_source("本地推")[0], "彭曦")
    eq("平台反查：没登记过的写法按名字推断",
       roster.operator_for_source("抖音-见真-梁毅")[0], "梁毅")
    eq("平台反查：认不出来就留空", roster.operator_for_source("视频号-未知服务商"), ("", ""))
    eq("平台反查：空串返回空", roster.operator_for_source(""), ("", ""))

    # 运营 -> 名下账号（界面「双击来源平台」时用这个收窄候选）
    eq("彭曦名下的平台", roster.accounts_of("彭曦"), ["视频号-彭曦", "本地推"])
    eq("OCR 错字也能查到平台", roster.accounts_of("壹曦"), ["视频号-彭曦", "本地推"])
    eq("非运营查不到平台", roster.accounts_of("韦东雅"), [])
    eq("平台与发布人一致时不提醒", roster.consistency_note("彭曦", "本地推"), "")
    check("平台与发布人不一致时提醒",
          "不一致" in roster.consistency_note("彭曦", "视频号-北海服务商"),
          roster.consistency_note("彭曦", "视频号-北海服务商"))

    # 发布人 = 运营，且**按行绑定**：一图多人各归各的，号码不会串
    two = pe.extract_leads(
        "彭曦(AAa琉璃瓦老彭):\n青海西宁别墅 18697110000\n"
        "刘一手(A真赞瓦业-刘一手):\n佛山需要瓦 13999998888")
    eq("一图两位运营各绑各的",
       [(l.source_person, l.phone) for l in two],
       [("彭曦", "18697110000"), ("刘一手", "13999998888")])

    # 光秃秃的昵称（无括号无冒号）—— 群里最常见、也最容易漏的写法
    bare = pe.extract_leads(
        "彭曦\n青海西宁别墅 18600001111\n"
        "刘一手\n佛山需要瓦 13900002222")
    eq("光秃秃昵称也能各绑各的",
       [(l.source_person, l.phone) for l in bare],
       [("彭曦", "18600001111"), ("刘一手", "13900002222")])

    # 昵称和正文被 OCR 连成一行
    merged = pe.extract_leads(
        "彭曦（AAa琉璃瓦老彭）青海西宁别墅 18600001111\n"
        "刘一手（A真赞瓦业-刘一手）佛山需要瓦 13900002222")
    eq("昵称和正文连成一行也能分对",
       [(l.source_person, l.phone) for l in merged],
       [("彭曦", "18600001111"), ("刘一手", "13900002222")])

    # 多运营、各 @ 不同的人：联系方式 / 线索人 / 业务三者一起对得上
    mixed = pe.extract_leads(
        "彭曦\n客户A 18600001111\n@韦东雅（A真赞瓦业-韦东雅）\n"
        "梁毅\n客户B 13900002222\n@吴超男（A真赞瓦业-吴超男）\n"
        "刘一手\n客户C 13700003333\n@陈秀珍（A真赞瓦业-陈秀珍）")
    eq("多运营各 @ 不同业务：三者一一对应",
       [(l.phone, l.source_person, l.business) for l in mixed],
       [("18600001111", "彭曦", "韦东雅"),
        ("13900002222", "梁毅", "吴超男"),
        ("13700003333", "刘一手", "陈秀珍")])

    # @ 写在正文上方（引用式），业务也不能越界抓到下一条消息的 @
    above = pe.extract_leads(
        "彭曦\n@韦东雅（A真赞瓦业-韦东雅）\n客户A 18600001111\n"
        "刘一手\n@罗淑冰（A真赞瓦业-罗淑冰）\n客户B 13900002222")
    eq("@ 在上方也各归各的",
       [(l.phone, l.source_person, l.business) for l in above],
       [("18600001111", "彭曦", "韦东雅"),
        ("13900002222", "刘一手", "罗淑冰,胡丹")])

    # 业务（非运营）发消息 —— 不能当发布人，退回平台兜底
    sales_post = pe.extract_leads("韦东雅(A真赞瓦业-韦东雅):\n需要瓦 13777776666",
                                  source_platform="视频号-彭曦")
    eq("业务不算发布人（改按平台兜底）",
       sales_post[0].source_person if sales_post else "", "彭曦")

    # 发布人和所选平台对不上 -> 备注里提醒
    mism = pe.extract_leads("刘一手(A真赞瓦业-刘一手):\n需要瓦 13999998888",
                            source_platform="视频号-北海服务商")
    check("发布人与平台不一致会写进备注",
          bool(mism) and "不一致" in mism[0].note,
          mism[0].note if mism else "(没有提取到线索)")

    # 选了来源平台后，截图里认不出线索人时自动兜底
    fallback = pe.extract_leads("需要屋面瓦 13812345678", source_platform="视频号-北海服务商")
    eq("认不出线索人时按平台兜底",
       fallback[0].source_person if fallback else "", "王烔彤")
    # 发布人必须是运营：截图里的「阿May」不在运营名单 -> 不认作线索人，改走平台兜底
    named = pe.extract_leads("阿May（屋顶翻新）\n需要瓦 13899998888",
                             source_platform="视频号-北海服务商")
    eq("非运营的名字不会被当成线索人",
       named[0].source_person if named else "", "王烔彤")
    check("并备注说明这个名字不在名单",
          bool(named) and "不在运营名单" in named[0].note,
          named[0].note if named else "(没提取到线索)")
    plain = pe.extract_leads("需要屋面瓦 13812345678")
    eq("不传平台则留空（默认行为不变）", plain[0].source_person if plain else "x", "")

    eq("名单里的人原样返回", roster.check_source_person("彭曦"), ("彭曦", ""))
    eq("3 字名字 OCR 错字自动纠正",
       roster.check_business("韦冬雅"), ("韦东雅", "已按名单校正：韦冬雅 → 韦东雅"))
    eq("2 字名字也能纠正", roster.check_source_person("壹曦")[0], "彭曦")
    check("不在名单的人会在备注里提示",
          "不在" in roster.check_source_person("赵六")[1],
          str(roster.check_source_person("赵六")))

    # 真实元数据串（用户给的格式）：一行 4 个字段，末段是「新人,带教」的微信提及串
    meta = ("13985305396\t抖音-见真-梁毅\t梁毅(A真赞瓦业-梁毅)\t"
            "真赞瓦业-罗淑冰18689239098,A佛山真赞瓦业-胡丹(A真赞瓦业-胡丹18665418517)")
    eq("元数据串只提取客户号码",
       [h.phone for h in pe.extract_phones(meta)], ["13985305396"])
    meta_leads = pe.extract_leads(meta)
    eq("元数据串提取 1 条线索", len(meta_leads), 1)
    if meta_leads:
        eq("任务负责人是两人且带教在后", meta_leads[0].business, "罗淑冰,胡丹")

    tmp = Path(tempfile.mkdtemp(prefix="ls_roster_"))
    try:
        op_csv = roster.export_operators_csv(tmp / "op.csv")
        lines = op_csv.read_text(encoding=config.CSV_ENCODING).strip().splitlines()
        eq("运营表表头", lines[0], "运营,账号")
        eq("运营表行数 = 账号数 + 表头", len(lines), len(roster.accounts()) + 1)
        eq("运营表首行", lines[1], "彭曦,视频号-彭曦")

        sales_csv = roster.export_sales_csv(tmp / "sales.csv")
        slines = sales_csv.read_text(encoding=config.CSV_ENCODING).strip().splitlines()
        eq("业务表表头", slines[0], "分组,业务")
        eq("业务表行数 = 人数 + 表头", len(slines), len(roster.sales_names()) + 1)
        check("胡丹已写进业务表", any("胡丹" in ln for ln in slines))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ================================================================ main
def main() -> int:
    print("=" * 62)
    print(f" 视频号线索采集助手 V{config.APP_VERSION}  ——  环境自检 & 逻辑测试")
    print("=" * 62)
    for fn in (test_env, test_phone, test_customer_id, test_extract,
               test_group_chat, test_database, test_sources, test_line_order,
               test_roster):
        try:
            fn()
        except Exception:
            global FAIL
            FAIL += 1
            print(f"  [ERROR] {fn.__name__} 抛出异常：")
            traceback.print_exc()

    print("\n" + "=" * 62)
    print(f" 结果： {PASS} 项通过，{FAIL} 项失败")
    print("=" * 62)
    if FAIL == 0:
        print(" 核心逻辑全部正常。若 Tkinter 与 OCR 引擎都已就绪，直接运行：python main.py")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
