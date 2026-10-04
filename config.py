# -*- coding: utf-8 -*-
"""Lead Scanner V0.1 - 全局配置 / 路径管理 / 来源视频号持久化。

所有可调参数集中在这里，改本文件即可，不需要动业务代码。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 应用信息
APP_NAME = "视频号线索采集助手"
APP_VERSION = "0.4.1"
APP_TITLE = f"{APP_NAME}  V{APP_VERSION}"

# 默认窗口尺寸：按「两个程序并排双开」设计，半屏就能看全，不用最大化
WINDOW_SIZE = "980x660"
WINDOW_MIN_WIDTH = 850
WINDOW_MIN_HEIGHT = 560

# 更新日志（程序里「更新日志」按钮直接读这里，新增版本往最前面加一条）
CHANGELOG: list[dict] = [
    {
        "version": "V0.4.1",
        "date": "2026-10-04",
        "items": [
            "修复：一张图里多位运营各自发线索时，联系方式 / 线索人 / 业务对不上的问题",
            "—— 发布人识别不再只认「昵称(备注)」和「昵称:」，光秃秃的昵称（如「彭曦」）也能认出来",
            "—— 被 @ 的人只在**同一条消息**里找，不会再越界抓到别人的 @",
            "—— 昵称和正文被 OCR 连成一行、@ 写在正文上方，也都能正确对应",
            "新增快捷键：**Enter = 开始识别**、**T = 清空面板**（焦点在输入框/列表里时不触发，不会误吃按键）",
            "「清空本批」改为「清空面板」：同时清掉待识别图片和列表显示，且始终可用（不删总库数据）",
        ],
    },
    {
        "version": "V0.4.0",
        "date": "2026-10-04",
        "items": [
            "识别逻辑按业务规矩重做：截图里的「发布人」一定是运营，发布人才是「线索人」",
            "手机号与发布人**按行绑定**：一张图里好几位运营各自发言时，号码和人也不会串",
            "不在运营名单的名字不再被当成线索人（客户名 / 业务名会被挡住），并在备注里说明",
            "选了来源平台后，双击「来源平台」列只列出**该线索人负责的账号**"
            "（彭曦 → 视频号-彭曦 / 本地推）",
            "一批识别完，若发布人都是同一位运营，来源下拉自动收窄到他负责的平台",
            "发布人和所选来源平台对不上时，备注会提醒，避免线索挂错账号",
            "临时模式改成「分批视图」：线索**照常写入总库**，列表只显示本批，方便复制",
            "「清空本批」只清列表显示，不会删数据",
            "复制到剪贴板默认**不带表头**，列可自选（和导出同一套交互），设置会记住",
        ],
    },
    {
        "version": "V0.3.1",
        "date": "2026-10-04",
        "items": [
            "修复：手工粘进 leads.csv 的裸数据（没写表头）不再被吃掉第一行 —— 以前每次启动都会少一条",
            "修复：导出 CSV 的默认文件名不再叫 leads.csv，并且不允许保存到主库路径（主库是原始数据，覆盖后找不回来）",
        ],
    },
    {
        "version": "V0.3.0",
        "date": "2026-10-04",
        "items": [
            "默认仍是「总库模式」：识别结果直接写入 leads.csv",
            "新增「临时模式」开关：识别结果只放在界面里，不写总库；适合先看一眼再决定要不要入库",
            "临时模式下可「清空本批」重来，或「复制本批」把本次线索直接复制到剪贴板（制表符分隔，粘到多维表格自动分列）",
            "从临时模式切回总库时，会问一句「这批要不要导入总库」，选否就丢弃",
            "选了来源平台后自动推出对应的运营：新线索的「线索人」为空时用它兜底（如视频号-北海服务商 → 王烔彤）",
            "新增「补全线索人」按钮：对已有数据按来源平台批量补上线索人",
        ],
    },
    {
        "version": "V0.2.3",
        "date": "2026-10-04",
        "items": [
            "窗口默认尺寸改成 980×660 —— 两个程序并排双开时半屏就能看全，不用最大化",
            "界面重新排版：卡片式分区、间距更紧凑、表头更清爽",
            "导出选项可以点「保存为默认」，下次打开对话框自动带出上次的勾选",
            "双击编辑更省事：来源平台 / 线索人 变成下拉选择，任务负责人变成勾选名单",
        ],
    },
    {
        "version": "V0.2.2",
        "date": "2026-10-04",
        "items": [
            "来源视频号下拉框支持直接打字筛选，不用再翻长列表",
            "来源初始不再默认选中第一个 —— 避免没注意就绑错来源；想固定就点「设为默认」",
            "识别结果可以双击单元格直接修改，改完立即写入 leads.csv",
            "新增「改选中行为当前来源」：导入时选错来源，可以事后批量纠正",
            "新增「删除选中行」：识别错了可以直接删掉",
            "表格右键菜单：修改此单元格 / 改为当前来源 / 打开原图 / 删除选中行",
        ],
    },
    {
        "version": "V0.2.1",
        "date": "2026-10-04",
        "items": [
            "「任务负责人」支持多个人：分给新人的线索会自动带上带教，输出「罗淑冰,胡丹」",
            "胡丹的角色由「新人」改为「带教」，并且永远排在任务负责人最后一位",
            "名单里的人名后面紧跟的号码会被排除 —— 业务员签名档里的电话不再被当成客户线索",
            "名单管理里的业务名单改用「老业务 / 新人 / 带教」三个分组，导出 CSV 同步更新",
        ],
    },    {
        "version": "V0.2.0",
        "date": "2026-10-04",
        "items": [
            "新增「名单管理」：运营 / 账号 / 业务三份名单，可在程序内查看并一键导出",
            "线索人、任务负责人会自动按名单校正 OCR 错字（例：壹曦 → 彭曦）",
            "不在名单里的名字会在备注里提示，方便发现漏登和识别错误",
            "导出列顺序对齐多维表格：联系方式 → 来源平台 → 线索人 → 任务负责人",
            "表头调整：「手机号」改为「联系方式」，「业务」改为「任务负责人」",
            "「来源视频号」下拉框自动补齐名单里登记的全部账号",
            "新增本更新日志窗口",
        ],
    },
    {
        "version": "V0.1.1",
        "date": "2026-10-04",
        "items": [
            "支持 Ctrl+V 直接粘贴截图（不用先存文件再挑图）",
            "新增「内容 / 线索人 / 业务」三个字段",
            "导出 CSV 可勾选要哪些列，并支持不带表头",
            "排除 @某某（…）括号里的号码，避免把业务员自己的电话当成客户线索",
            "客户ID 改为按行就近绑定，一张图里多个客户不会串行",
        ],
    },
    {
        "version": "V0.1.0",
        "date": "2026-10-04",
        "items": [
            "首个版本：选择来源视频号 → 上传截图 → OCR 识别 → 提取手机号 → 存 CSV",
            "支持 jpg / png / jpeg，支持多选与整个文件夹批量",
            "手机号归一化（空格、横线、全角数字），不完整号码也保留",
            "自动去重，CSV 带中文表头（Excel 双击不乱码）",
        ],
    },
]

# ---------------------------------------------------------------- 工作模式
# 启动时是否直接进「临时模式」
#   False（默认）—— 总库模式：识别结果直接写入 leads.csv
#   True         —— 临时模式：结果只放在界面里，不写总库，可复制/清空
TEMP_MODE_DEFAULT = False

# 线索人识别不出来时，是否用「来源平台 -> 运营」的对应关系自动补上
# 例：选了「视频号-北海服务商」，截图里没有昵称 -> 线索人自动填「王烔彤」
# 注意：只有在截图里**认不出**线索人时才兜底，不覆盖已识别出的结果
AUTO_FILL_PERSON_BY_PLATFORM = True


# 名字是否按名单校验（发现不在名单里的人就在备注里提示）
CHECK_NAMES_AGAINST_ROSTER = True
# 识别出的名字不在名单、但和某个人只差一个字时，是否自动改正
# True  = 直接改成名单里的写法，备注记录「已按名单校正：原值 → 新值」
# False = 不改数据，只在备注里提示「疑似 xxx，请核对」
ROSTER_AUTO_CORRECT = True


# ---------------------------------------------------------------- 路径
def _base_dir() -> Path:
    """打包成 exe 后，数据目录跟随 exe；源码运行时跟随本文件。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = _base_dir()
DATA_DIR = BASE_DIR / "data"
IMAGE_DIR = DATA_DIR / "images"          # 预留：需要归档原图时使用
PASTE_DIR = DATA_DIR / "paste"           # Ctrl+V 粘贴进来的截图存这里
LEADS_CSV = DATA_DIR / "leads.csv"
SOURCES_FILE = DATA_DIR / "sources.json"
EXPORT_PREFS_FILE = DATA_DIR / "export_prefs.json"    # 导出对话框记住的选择
COPY_PREFS_FILE = DATA_DIR / "copy_prefs.json"        # 复制到剪贴板记住的选择
LOG_FILE = DATA_DIR / "lead_scanner.log"


def ensure_dirs() -> None:
    for d in (DATA_DIR, IMAGE_DIR, PASTE_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 图片
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
IMAGE_FILETYPES = [
    ("图片文件", "*.jpg *.jpeg *.png *.bmp *.webp"),
    ("JPG", "*.jpg *.jpeg"),
    ("PNG", "*.png"),
    ("所有文件", "*.*"),
]


# ---------------------------------------------------------------- CSV 字段
# 内部字段名 -> 导出表头（中文，符合需求文档）
# 顺序 = CSV 列顺序 = 结果表格列顺序
CSV_FIELDS = [
    "phone",             # 联系方式
    "source_platform",   # 来源平台
    "source_person",     # 线索人
    "business",          # 任务负责人
    "content",           # 内容（消息正文，如「青海西宁别墅130方左右刘总」）
    "customer_id",       # 客户ID
    "note",              # 备注（系统提示，如「号码可能不完整」）
    "discovered_at",     # 发现时间
    "image_file",        # 图片来源
]
# 表头与列的先后顺序按「导入飞书多维表格」的字段顺序对齐
CSV_HEADERS = {
    "phone": "联系方式",
    "source_platform": "来源平台",
    "source_person": "线索人",
    "business": "任务负责人",
    "content": "内容",
    "customer_id": "客户ID",
    "note": "备注",
    "discovered_at": "发现时间",
    "image_file": "图片来源",
}

# 导出对话框里可勾选的列（默认全部勾选，用户自己取消不要的）
EXPORT_FIELDS = list(CSV_FIELDS)
EXPORT_DEFAULT_FIELDS = list(CSV_FIELDS)
EXPORT_HEADER_DEFAULT = True

CSV_ENCODING = "utf-8-sig"               # 带 BOM，Excel 双击打开不乱码
TIME_FORMAT = "%Y-%m-%d %H:%M"


# ---------------------------------------------------------------- 复制偏好
# 「复制本批到剪贴板」用的设置。默认**不带表头** —— 粘到多维表格时，
# 表头会变成一行数据，所以复制这种场景一般不需要它。
COPY_FIELDS = list(CSV_FIELDS)
COPY_HEADER_DEFAULT = False


def load_copy_prefs() -> tuple[list[str], bool]:
    """读取复制设置，返回 ``(列, 是否含表头)``；没保存过就用默认。"""
    fields: list[str] = []
    header = COPY_HEADER_DEFAULT
    if COPY_PREFS_FILE.exists():
        try:
            data = json.loads(COPY_PREFS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                fields = [f for f in data.get("fields", []) if f in CSV_FIELDS]
                # Clipboard rows are pasted into an existing table, including for old preferences.
                header = False
        except Exception:
            logging.getLogger("lead_scanner").warning(
                "copy_prefs.json 解析失败，用默认复制设置", exc_info=True)
    if not fields:
        fields = list(COPY_FIELDS)
    return fields, header


def save_copy_prefs(fields: list[str], with_header: bool) -> None:
    """保存复制设置。"""
    ensure_dirs()
    clean = [f for f in fields if f in CSV_FIELDS]
    try:
        COPY_PREFS_FILE.write_text(
            json.dumps({"fields": clean, "header": False},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logging.getLogger("lead_scanner").warning(
            "copy_prefs.json 写入失败", exc_info=True)


# ---------------------------------------------------------------- 去重策略
# True  : 客户ID 相同即判为重复（严格按需求文档字面）
# False : 只有「没有手机号」时才用 客户ID+来源 判重；允许同一客户ID保留多个不同号码，
#         并在备注里标注「同一客户ID已有其它号码」——避免一张截图里同一客户的
#         两个号码互相把对方挤掉（默认值）
DEDUP_STRICT_BY_ID = False


# ---------------------------------------------------------------- 来源视频号
# 这里只是兜底默认值；实际启动时会再把 roster.py 里登记的账号全部合并进来
DEFAULT_SOURCES = [
    "视频号-北海服务商",
    "视频号-来宾服务商",
    "视频号-佛山服务商",
    "视频号-广西服务商",
    "视频号-海南服务商",
    "视频号-truelike",
    "视频号-云南服务商",
    "视频号-彭曦",
    "视频号-刘一手",
    "视频号-梁毅",
    "抖音-梁毅",
    "小红书-王可",
    "小红书-叶虹池",
    "本地推",
    "1688",
]


def default_source_names() -> list[str]:
    """默认来源 = DEFAULT_SOURCES + 名单里登记的全部账号（去重保序）。"""
    out = list(DEFAULT_SOURCES)
    try:
        from roster import accounts as roster_accounts   # 延迟导入，避免循环依赖
        for acc in roster_accounts():
            if acc not in out:
                out.append(acc)
    except Exception:
        logging.getLogger("lead_scanner").warning("读取名单账号失败", exc_info=True)
    return out


def _read_sources_file() -> tuple[list[str], str]:
    """读 sources.json，返回 ``(来源列表, 默认来源)``。

    兼容两种格式：旧的纯数组 ``["a","b"]`` 和新的对象 ``{"sources":[...],"default":"a"}``。
    """
    if not SOURCES_FILE.exists():
        return [], ""
    try:
        raw = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except Exception:
        logging.getLogger("lead_scanner").warning("sources.json 解析失败", exc_info=True)
        return [], ""

    if isinstance(raw, dict):
        items = raw.get("sources") or raw.get("items") or []
        default = str(raw.get("default") or "").strip()
    else:
        items, default = raw, ""
    return [str(x).strip() for x in items if str(x).strip()], default


def load_sources() -> list[str]:
    """来源列表；名单里新增的账号会自动补齐进来（用户手动删掉的不会硬塞回去）。"""
    ensure_dirs()
    names, _ = _read_sources_file()
    merged = list(names)
    for extra in default_source_names():
        if extra not in merged:
            merged.append(extra)
    if merged != names:
        merged = save_sources(merged)
    return merged


def load_default_source() -> str:
    """默认来源（启动时自动选中它）。没设过就返回空串 —— 这时界面初始为空。"""
    _, default = _read_sources_file()
    return default


def set_default_source(name: str) -> str:
    """把某个来源设为默认。"""
    name = str(name or "").strip()
    save_sources(load_sources(), default=name)
    return name


# ---------------------------------------------------------------- 导出偏好
def load_export_prefs() -> tuple[list[str], bool]:
    """读取上次「保存为默认」的导出设置，返回 ``(列, 是否含表头)``。

    没保存过就返回 EXPORT_DEFAULT_FIELDS / EXPORT_HEADER_DEFAULT。
    """
    fields: list[str] = []
    header = EXPORT_HEADER_DEFAULT

    if EXPORT_PREFS_FILE.exists():
        try:
            data = json.loads(EXPORT_PREFS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                fields = [f for f in data.get("fields", []) if f in CSV_FIELDS]
                header = bool(data.get("header", EXPORT_HEADER_DEFAULT))
        except Exception:
            logging.getLogger("lead_scanner").warning(
                "export_prefs.json 解析失败，用默认导出设置", exc_info=True)

    if not fields:                       # 一个都没勾等于没法导出，退回默认
        fields = list(EXPORT_DEFAULT_FIELDS)
    return fields, header


def save_export_prefs(fields: list[str], with_header: bool) -> None:
    """把导出对话框当前的选择存为默认。"""
    ensure_dirs()
    clean = [f for f in fields if f in CSV_FIELDS]
    try:
        EXPORT_PREFS_FILE.write_text(
            json.dumps({"fields": clean, "header": bool(with_header)},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logging.getLogger("lead_scanner").warning(
            "export_prefs.json 写入失败", exc_info=True)


def save_sources(names: list[str], default: str | None = None) -> list[str]:
    """保存来源列表。

    ``default=None`` 表示保持已存的默认值不变（日常增删来源时用）。
    """
    ensure_dirs()
    cleaned: list[str] = []
    for n in names:
        n = str(n).strip()
        if n and n not in cleaned:
            cleaned.append(n)

    if default is None:
        _, default = _read_sources_file()
    default = str(default or "").strip()
    if default and default not in cleaned:
        cleaned.append(default)

    try:
        SOURCES_FILE.write_text(
            json.dumps({"sources": cleaned, "default": default},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logging.getLogger("lead_scanner").warning("sources.json 写入失败", exc_info=True)
    return cleaned


# ---------------------------------------------------------------- OCR
# 引擎探测顺序：先 PaddleOCR（需求指定），装不上时自动降级 RapidOCR
OCR_ENGINE_PREFERENCE = ["paddleocr", "rapidocr"]
OCR_LANG = "ch"
OCR_USE_GPU = False
OCR_PREPROCESS = True                    # OpenCV 灰度 + 放大，提升小截图识别率
OCR_UPSCALE = 2.0                        # 放大倍数
OCR_MIN_SCORE = 0.35                     # 低于该置信度的文本行丢弃


# ---------------------------------------------------------------- 客户ID 提取
# 「用户：赵明刚」「昵称: 阿May」这类标签，值可能是同行也可能是下一行
ID_LABELS = [
    "用户", "用户名", "昵称", "客户", "客户名", "客户ID", "姓名", "名字",
    "联系人", "账号", "帐号", "微信", "微信号", "备注名", "ID",
]
# 这些词一定是界面文案，不能当客户ID
ID_BLOCKLIST = {
    "评论", "回复", "点赞", "转发", "收藏", "分享", "关注", "展开", "收起",
    "更多", "视频号", "简介", "简介：", "已关注", "作者", "置顶", "搜索",
    "请输入", "确定", "取消", "发送", "全部", "最新", "最热", "好友",
}


# ---------------------------------------------------------------- 界面
UI_FONT_CANDIDATES = [
    "Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "PingFang SC",
    "Source Han Sans SC", "SimHei", "Arial",
]
UI_FONT_SIZE = 10
UI_TITLE_FONT_SIZE = 11


# ---------------------------------------------------------------- 日志
def setup_logging(level: int = logging.INFO) -> logging.Logger:
    ensure_dirs()
    logger = logging.getLogger("lead_scanner")
    if logger.handlers:
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


LOGGER = logging.getLogger("lead_scanner")   # 使用时再 setup_logging()


def now_str() -> str:
    return datetime.now().strftime(TIME_FORMAT)
