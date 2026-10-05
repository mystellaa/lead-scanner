# -*- coding: utf-8 -*-
"""从 OCR 文本中提取客户线索：手机号 + 客户ID。

设计原则
--------
1. 手机号归一化：``138 1234 5678`` / ``138-1234-5678`` / 全角数字 统一存成 ``13812345678``
2. 不足 11 位也保留（业务需求），备注写「号码可能不完整」
3. **客户ID 按行就近绑定**：谁的行旁边有号，ID 就归谁——一张图里有多个客户也能分对
4. 纯函数 + 无副作用，方便单元测试（见 selftest.py）

重要约定
--------
* 分隔符**不含换行**。否则 "…13812345678\\n3小时前" 会被连成 138123456783。
* `1[3-9]` 已占 2 位，量词 `{5,9}` 保证号码数字总长落在 7~11 位之间。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import config

# ---------------------------------------------------------------- 正则
# 号码中间允许出现的分隔符（注意：必须排除 \n \r，否则会跨行粘连）
_SEP_CHARS = r" \t\-—–－_·.．,，"
_SEP = r"[" + _SEP_CHARS + r"]"
# 1[3-9] + 5~9 组「分隔符*数字」 => 号码数字总长 7 ~ 11 位
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9](?:" + _SEP + r"*\d){5,9})(?!\d)")
# 兜底：整串 11 位连续数字
_PHONE_STRICT_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_DIGIT_OR_SEP = re.compile(r"[" + _SEP_CHARS + r"]")

# 「用户：赵明刚」「昵称:阿May」「微信号：abc_123」
_LABEL_ALT = "|".join(re.escape(x) for x in config.ID_LABELS)
_LABEL_PAIR_RE = re.compile(r"^\s*(?:" + _LABEL_ALT + r")\s*[:：]\s*(?P<val>.+?)\s*$")
# 只有标签、没值（值在下一行）
_LABEL_ONLY_RE = re.compile(r"^\s*(?:" + _LABEL_ALT + r")\s*[:：]?\s*$")
# @昵称
_AT_NAME_RE = re.compile(r"@([A-Za-z0-9_\u4e00-\u9fa5\-]{1,24})")

# ---- 群聊场景：线索人 / 业务 ----
# 消息发送者行：「彭曦（AAa琉璃瓦老彭）：」或「彭曦（AAa琉璃瓦老彭）」
#   括号里是备注名，括号前才是人名；开头的 @ 行是「提及别人」，不算发送者
_SOURCE_WITH_NOTE_RE = re.compile(
    r"^(?!@)\s*([^（(\n:：]{1,24}?)\s*[（(][^）)]{0,60}[）)]\s*[:：]?\s*$"
)
# 只有昵称 + 冒号
_SOURCE_BARE_RE = re.compile(r"^(?!@)\s*([^（(\n:：]{1,24})\s*[:：]\s*$")
# @某人（备注）里的「某人」
_AT_PERSON_RE = re.compile(r"@\s*([^（(\s@,，。:：]{1,24})")
# 整段「@某人（备注）」—— 括号里通常是对方自己的联系方式，不能当成客户线索
_AT_BLOCK_RE = re.compile(r"@[^（(\n]{0,30}[（(][^）)]{0,80}[）)]")
# 「名字」后面紧跟一串号码 —— 微信里常见于签名档（罗淑冰18689239098），
# 那是联系人自己的电话，不是客户线索
_NAME_PHONE_TAIL_RE = re.compile(
    r"[\s\-–—_·.．,，:：]*((?:1[3-9]\d(?:" + _SEP + r"*\d){5,9}))")
# 内容尾巴上要清掉的客套词
_TRAILING_WORD_RE = re.compile(r"(联系|联系电话|电话|手机号|手机|号码|微信|加我|详聊|咨询)$")
# 被主动排除、但还留在原文里的号码（签名档那种），从「内容」里也要抹掉
_LEFTOVER_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

_CJK = r"\u4e00-\u9fa5"
# 昵称形态：2~12 字，中文/字母/数字/下划线/连字符/间隔号
_NAME_SHAPE_RE = re.compile(r"^[A-Za-z0-9_" + _CJK + r"\-·.]{2,12}$")
# 时间戳行：「3小时前」「刚刚」「昨天 18:20」「10-04」
_TIME_RE = re.compile(
    r"^(刚刚|刚才|昨天.*|今天.*|前天.*|\d+\s*(秒|分钟|分|小时|天|周|星期|个月|月|年)前"
    r"|\d{1,2}[-/月]\d{1,2}日?.*|\d{1,2}:\d{2})$"
)
# 明显是界面文案/互动数据的行
_NOISE_WORDS = ("评论", "回复", "点赞", "转发", "收藏", "分享", "关注", "展开",
                "收起", "更多", "视频号", "简介", "作者", "置顶", "搜索", "小时前",
                "分钟前", "刚刚", "阅读", "推荐", "广告")

# 头像旁的楼层序号：OCR 常把它和昵称连成一行，如「2 阿May」「3 陈老板」
_LEADING_INDEX_RE = re.compile(r"^\s*\d{1,3}\s+")


# ---------------------------------------------------------------- 数据结构
@dataclass
class PhoneHit:
    """一个手机号命中结果。"""
    phone: str                 # 归一化后的号码（只含数字）
    raw: str                   # 原文片段（保留分隔符，便于人工核对）
    complete: bool             # 是否满 11 位
    note: str = ""             # 「号码可能不完整」等提示


@dataclass
class Lead:
    """一条待入库的线索。"""
    customer_id: str = ""
    phone: str = ""
    content: str = ""          # 内容：消息正文（去掉号码后的文字）
    source_person: str = ""    # 线索人：发消息的人
    business: str = ""         # 业务：被 @ 的人
    note: str = ""
    raw_text: str = ""
    phones: list[PhoneHit] = field(default_factory=list)
    id_source: str = ""        # 客户ID 的判定依据，调试用
    person_source: str = ""    # 线索人 的判定依据，调试用


# ---------------------------------------------------------------- 文本归一化
def normalize(text: str) -> str:
    """全角转半角、统一各类连字符/冒号，保留换行结构。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)         # 全角数字/字母/标点 -> 半角
    for ch in "—–－":
        text = text.replace(ch, "-")
    text = text.replace("：", ":").replace("，", ",").replace("（", "(").replace("）", ")")
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    return text


def to_lines(text: str) -> list[str]:
    """拆行 + 去空行 + 去首尾空白。"""
    return [ln.strip() for ln in normalize(text).splitlines() if ln.strip()]


# ---------------------------------------------------------------- 手机号
def _digits_only(s: str) -> str:
    return _DIGIT_OR_SEP.sub("", s)


def excluded_spans(text: str) -> list[tuple[int, int]]:
    """要跳过的号码区间：

    ① 「@某人（备注）」整段 —— 括号里是被 @ 的人自己的联系方式
    ② 公司名单里的人名后面紧跟的号码 —— 微信签名档那种（罗淑冰18689239098）
    """
    text = text or ""
    spans = [(m.start(), m.end()) for m in _AT_BLOCK_RE.finditer(text)]

    try:
        import roster
        names = roster.operator_names() + roster.sales_names()
    except Exception:
        names = []

    for name in names:
        for m in re.finditer(re.escape(name), text):
            tail = text[m.end(): m.end() + 24]
            mm = _NAME_PHONE_TAIL_RE.match(tail)
            if mm:
                spans.append((m.end(), m.end() + mm.end()))
    return spans


def _in_excluded(pos: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= pos and end <= e for s, e in spans)


def extract_phones(text: str) -> list[PhoneHit]:
    """提取手机号（已归一化、已去重、按出现顺序）。

    规则：
      * 11 位且 1[3-9] 开头   -> 完整号码
      * 7~10 位且 1[3-9] 开头 -> 保留，备注「号码可能不完整」
      * 少于 7 位             -> 丢弃（截断噪声太多，误报严重）
      * 落在「@某人（…）」括号里的号码 -> 跳过（那是被 @ 的人自己的电话）
    """
    text = normalize(text or "")
    spans = excluded_spans(text)
    hits: list[PhoneHit] = []
    seen: set[str] = set()

    for m in _PHONE_RE.finditer(text):
        if spans and _in_excluded(m.start(), m.end(), spans):
            continue
        raw = m.group(1)
        digits = _digits_only(raw)
        if not digits.startswith(("13", "14", "15", "16", "17", "18", "19")):
            continue
        if len(digits) < 7 or digits in seen:
            continue
        seen.add(digits)
        if len(digits) == 11:
            hits.append(PhoneHit(digits, raw, True, ""))
        else:
            hits.append(PhoneHit(digits, raw, False, "号码可能不完整"))

    # 兜底：某些情况下候选正则被邻接字符干扰，用严格 11 位再扫一遍
    for m in _PHONE_STRICT_RE.finditer(text):
        if spans and _in_excluded(m.start(), m.end(), spans):
            continue
        digits = m.group(1)
        if digits not in seen:
            seen.add(digits)
            hits.append(PhoneHit(digits, digits, True, ""))
    return hits


def normalize_phone(raw: str) -> str:
    """把任意写法的号码转成纯数字（对外工具函数）。"""
    return re.sub(r"\D", "", normalize(raw or ""))


# ---------------------------------------------------------------- 客户ID
def _clean_id(val: str) -> str:
    val = re.sub(r"\s+", " ", str(val or "")).strip()
    # 剥掉头像楼层序号（OCR 会把「2」和昵称连成一行：2 阿May）
    val = _LEADING_INDEX_RE.sub("", val, count=1)
    return val.strip(":：,，。.、|").strip()


def _looks_like_value(val: str) -> bool:
    """判断标签后的内容是否是可用的客户ID。"""
    val = _clean_id(val)
    if not val or len(val) > 24:
        return False
    if extract_phones(val):                  # 值是手机号，不是ID
        return False
    digits = re.sub(r"\D", "", val)          # 几乎全是数字的长串（QQ号等）不算ID
    if digits and len(digits) >= 6 and len(digits) >= len(val) - 1:
        return False
    if val in config.ID_BLOCKLIST:
        return False
    if re.fullmatch(r"[\d\s\-_:：.]+", val):  # 纯数字/符号
        return False
    return True


def is_name_candidate(line: str) -> bool:
    """这一行看起来像不像「用户昵称」行。"""
    s = _clean_id(line)
    if not s or len(s) > 24:
        return False
    if extract_phones(s):                    # 带号码的是评论正文
        return False
    if s in config.ID_BLOCKLIST:
        return False
    if _TIME_RE.match(s):                    # 「3小时前」之类
        return False
    if any(w in s for w in _NOISE_WORDS):
        return False
    if re.fullmatch(r"[\d\s\-_:：.。、|]+", s):   # 纯数字/符号
        return False
    if re.search(r"[:：]", s):               # 「xx: yy」结构多半是文案
        return False
    return bool(_NAME_SHAPE_RE.match(s))


def _heuristic_name(lines: list[str]) -> str:
    """兜底：全文找第一个像昵称的行。"""
    for ln in lines:
        if is_name_candidate(ln):
            return _clean_id(ln)
    return ""


def _id_for_line(lines: list[str], idx: int) -> tuple[str, str]:
    """给第 idx 行的号码找它的主人（客户ID）。

    顺序：
      ① 本行自带标签 / @昵称
      ② 向上回溯最多 4 行，取最近的一个「标签行」或「昵称行」
         —— 中途撞见别的号码行就停（说明跨到另一个客户了）
    """
    line = lines[idx]

    m = _LABEL_PAIR_RE.match(line)
    if m and _looks_like_value(m.group("val")):
        return _clean_id(m.group("val")), "标签同行"
    m = _AT_NAME_RE.search(line)
    if m and _looks_like_value(m.group(1)):
        return _clean_id(m.group(1)), "@昵称"

    for j in range(idx - 1, max(-1, idx - 5), -1):
        cand = lines[j]
        if extract_phones(cand):            # 撞见上一个客户的号码行，停止回溯
            break
        m2 = _LABEL_PAIR_RE.match(cand)
        if m2 and _looks_like_value(m2.group("val")):
            return _clean_id(m2.group("val")), "上一行标签"
        if is_name_candidate(cand):
            return _clean_id(cand), "就近昵称行"
    return "", ""


def _follow_list_id(lines: list[str], idx: int) -> tuple[str, str]:
    """Bind a phone in a contacts/following list to the compact ID row.

    List rows often contain a display nickname followed by the account ID. The
    ID is usually the short 2-4 Chinese-character candidate immediately above
    the phone, so it must be preferred over a longer display nickname.
    """
    candidates = []
    for j in range(max(0, idx - 4), idx + 1):
        line = re.sub(r"\s+", "", lines[j])
        line = _LEADING_INDEX_RE.sub("", line)
        line = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9_-]", "", line)
        if not line or extract_phones(line) or line in config.ID_BLOCKLIST:
            continue
        if 2 <= len(line) <= 4 and re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", line):
            candidates.append((j, line))
    if candidates:
        return candidates[-1][1], "关注列表短ID"
    return _id_for_line(lines, idx)


def extract_customer_id(lines) -> tuple[str, str]:
    """全局提取一个客户ID（整段文本只找最显眼的那一个）。

    返回 (客户ID, 判定依据)。仅在单条线索场景作为兜底使用。
    """
    if isinstance(lines, str):
        lines = to_lines(lines)

    # ① 标签同行有值：「用户：赵明刚」
    for ln in lines:
        m = _LABEL_PAIR_RE.match(ln)
        if m and _looks_like_value(m.group("val")):
            return _clean_id(m.group("val")), "标签同行"
    # ② 标签独占一行，值在下一行
    for i, ln in enumerate(lines):
        if _LABEL_ONLY_RE.match(ln) and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if _looks_like_value(nxt) and not _LABEL_ONLY_RE.match(nxt):
                return _clean_id(nxt), "标签下一行"
    # ③ @昵称
    for ln in lines:
        m = _AT_NAME_RE.search(ln)
        if m and _looks_like_value(m.group(1)):
            return _clean_id(m.group(1)), "@昵称"
    # ④ 启发式兜底
    guess = _heuristic_name(lines)
    if guess:
        return _clean_id(guess), "启发式"
    return "", ""


# ---------------------------------------------------------------- 线索人 / 业务 / 内容
def _is_person_name(name: str) -> bool:
    """像不像一个人名（用于线索人 / 业务字段）。"""
    if not name or len(name) > 20:
        return False
    if extract_phones(name):
        return False
    if name in config.ID_BLOCKLIST:
        return False
    if re.fullmatch(r"[\d\s\-_:：.]+", name):
        return False
    if any(w in name for w in _NOISE_WORDS):
        return False
    return True


def extract_source_person(lines) -> tuple[str, str]:
    """整篇文本里最靠前的那个**发布人**（一对一的兜底）。

    发布人一定是运营，所以先认「行首就是运营名」的行 —— 这一条能覆盖
    「彭曦」这种光秃秃的昵称（旧规则只认「昵称(备注)」和「昵称:」，
    会把它们整批漏掉，最后只能退回「按平台推」，多运营的图就全挂一个人头上）。

    认不到再去认「昵称(备注)」这类形态 —— 哪怕名字不在名单里也返回，
    这样调用方能给用户一句「XX 不在运营名单」的提示。
    """
    if isinstance(lines, str):
        lines = to_lines(lines)

    for ln in lines:                      # ① 行首就是运营名（最可靠）
        name = _operator_at(ln)
        if name:
            return name, "发布人行"

    noted: list[tuple[bool, str]] = []    # ② 旧规则兜底（为了那条「不在名单」提示）
    for ln in lines:
        m = _SOURCE_WITH_NOTE_RE.match(ln)
        if not m:
            continue
        name = _clean_id(m.group(1))
        if _is_person_name(name):
            noted.append((ln.rstrip().endswith(":"), name))
    if noted:
        for has_colon, name in noted:
            if has_colon:
                return name, "引用块发送者"
        return noted[0][1], "昵称(备注)"

    for ln in lines:
        m = _SOURCE_BARE_RE.match(ln)
        if m:
            name = _clean_id(m.group(1))
            if _is_person_name(name):
                return name, "昵称行"
    return "", ""


def extract_business(lines) -> tuple[str, str]:
    """任务负责人：可能是**多个人**（新人 + 带教胡丹）。

    策略：
      ① 用业务名单在全文里扫描 —— 能过滤掉手机号、微信备注等噪音，
         还能一次捞出多个人（微信 @提及 串里经常挤着两个人）
      ② 扫不到再退回 @ 语法，只取一个名字并做名单纠错

    返回 ``(逗号分隔的名字, 备注)``，例如 ``("罗淑冰,胡丹", "")``。
    """
    if isinstance(lines, str):
        lines = to_lines(lines)
    text = "\n".join(lines)

    try:
        import roster
        names = roster.scan_sales(text)
        if names:
            names = roster.order_responsibles(roster.with_mentor(names))
            return ",".join(names), ""
    except Exception:
        pass

    for ln in lines:
        m = _AT_PERSON_RE.search(ln)
        if m:
            name = _clean_id(m.group(1))
            if _is_person_name(name):
                try:
                    import roster
                    fixed, note = roster.check_business(name)
                    return fixed or name, note
                except Exception:
                    return name, ""
    return "", ""


def _trusted_operator(name: str) -> tuple[str, str]:
    """只有运营名单里的人才有资格当「发布人」。

    这条规矩是关键：截图里能认出来的名字其实有两类 —— **发布人**和**评论者**。
    发布人必定是公司运营（彭曦 / 刘一手 / …），评论者才是客户。
    拿运营名单当闸门，就能把「赵明刚」这种客户昵称挡在「线索人」字段之外。

    返回 ``(规范名, 提示)``，不在名单时规范名为空串。
    """
    name = str(name or "").strip()
    if not name:
        return "", ""
    try:
        import roster
    except Exception:
        return name, ""                   # 名单不可用时不做拦截，保证主流程能跑
    fixed, note = roster.check_source_person(name)
    # 注意 check_source_person 是「校验 + 提示」语义：认不出来时它会把原值还回来，
    # 所以这里必须再用 is_operator 确认一次，不能只看返回值非空。
    if fixed and roster.is_operator(fixed):
        return fixed, note                # note 可能是「已按名单校正：壹曦 → 彭曦」
    return "", f"「{name}」不在运营名单"


def _operator_at(line: str) -> str:
    """这一行**行首**的运营名 —— 判断「发布人行」的核心依据。

    为什么以行首为准：发布人一定是那 6 位运营之一，所以「行首就是某个运营名」
    是最可靠的信号。它能一次覆盖 OCR 的多种输出形态：

        彭曦                    ← 群里光秃秃的昵称（**最容易被漏掉的一种**）
        2 彭曦                  ← OCR 把头像旁的楼层序号混进来了
        彭曦（AAa琉璃瓦老彭）      ← 昵称(备注)
        彭曦：                   ← 引用块发送者
        彭曦（AAa琉璃瓦老彭）正文… ← 昵称和正文被 OCR 连成了一行

    以 ``@`` 开头的行是「提到别人」，不算发布人，直接排除。
    """
    text = (line or "").strip()
    if not text or text.startswith("@"):
        return ""
    try:
        import roster
        names = roster.operator_names()
    except Exception:
        return ""

    lead = _LEADING_INDEX_RE.sub("", text).lstrip("@ 　")   # 先剥掉头像旁的楼层序号
    for name in names:
        if lead.startswith(name):
            return name
    # OCR 错字兜底：行首取同样长度，差一个字也能纠正（壹曦 -> 彭曦）
    for name in names:
        seg = lead[:len(name)]
        if len(seg) != len(name):
            continue
        fixed, _note = roster.check_source_person(seg)
        if fixed and roster.is_operator(fixed):
            return fixed
    return ""


def _person_for_line(lines: list[str], idx: int) -> tuple[str, str]:
    """给第 idx 行的号码找它的**发布人**（线索人）。

    这就是「联系方式必须和发布人对应得上」的落地方式：每个号码各自往上找
    自己的发布人行，而不是整张图共用一个。

    微信里「一条消息 = 发布人行 + 正文（+ 被 @ 的人）」，所以直接找
    **上面最近的那个发布人行**就够了 —— 比按行回溯更贴合实际结构，
    也不会因为中间隔了几行就把人认错。
    """
    for j in range(idx, -1, -1):
        name = _operator_at(lines[j])
        if name:
            return name, ("本行发布人" if j == idx else f"上方第 {idx - j} 行的发布人")
    return "", ""


def _business_for_line(lines: list[str], idx: int) -> str:
    """给第 idx 行找它对应的业务（被 @ 的人）。

    @ 常写在正文**下面**一行，也可能同行、或是引用式写在上面。
    关键是**只在同一条消息（同一个发布人块）里找** —— 一旦越过下一条消息，
    就会把别人的 @ 认成自己的，这正是「多运营 + 各 @ 不同人」最容易错的地方。

    顺序：先看本行及下方，再退回上方。找不到就返回空串，
    由调用方回退到整图扫描的结果。
    """
    try:
        import roster
    except Exception:
        return ""

    start, end = _block_range(lines, idx)
    for rng in (range(idx, min(end, idx + 5)),
                range(idx - 1, start - 1, -1)):
        for j in rng:
            ln = lines[j]
            if not _AT_PERSON_RE.search(ln):
                continue
            names = roster.scan_sales(ln)
            if names:
                return ",".join(roster.order_responsibles(roster.with_mentor(names)))
    return ""


def _block_range(lines: list[str], idx: int) -> tuple[int, int]:
    """第 idx 行所在「消息块」的范围 ``[start, end)``。

    一条微信消息 = 发布人行 + 正文（+ 被 @ 的人），到下一条消息的发布人行为止。
    同一个块里的东西才是互相归属的 —— 这是「联系方式 / 线索人 / 业务」
    三者不出错的结构保证。
    """
    start = 0
    for j in range(idx, -1, -1):
        if _operator_at(lines[j]):
            start = j
            break
    end = len(lines)
    for j in range(idx + 1, len(lines)):
        if _operator_at(lines[j]):
            end = j
            break
    return start, end


def _platform_warn(person: str, source_platform: str) -> str:
    """线索人和所选来源平台对不上时给一句提醒。"""
    if not person or not source_platform:
        return ""
    try:
        import roster
        return roster.consistency_note(person, source_platform)
    except Exception:
        return ""


def _person_from_platform(source_platform: str) -> str:
    """线索人认不出来时，用来源平台对应的运营兜底。

    场景：评论截图里只有一句「需要屋面瓦 138xxxx」—— 没有昵称、没有 @，
    但你在界面上明确选了「视频号-北海服务商」，那就该知道这条归王烔彤。
    """
    if not source_platform or not config.AUTO_FILL_PERSON_BY_PLATFORM:
        return ""
    try:
        import roster
        name, _why = roster.operator_for_source(source_platform)
        return name
    except Exception:
        return ""


def extract_content_from_line(line: str) -> str:
    """从「带号码的消息行」里抠出正文。

        18697110000青海西宁别墅130方左右刘总  ->  青海西宁别墅130方左右刘总
        需要屋面瓦，联系13812345678           ->  需要屋面瓦
    """
    hits = extract_phones(line)
    if not hits:
        return ""
    text = line
    for h in hits:
        text = text.replace(h.raw, " ").replace(h.phone, " ")
    # 名单里的人名后面紧跟的号码会被 extract_phones 主动排除（那是签名档），
    # 但它们仍留在原文里 —— 不能让它们混进「内容」字段。
    text = _LEFTOVER_PHONE_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[\s，,。.、:：\-|]+$", "", text)
    text = _TRAILING_WORD_RE.sub("", text)
    return text.strip(" ，,。.、:：-|")


# ---------------------------------------------------------------- 汇总
def merge_note(*parts: str) -> str:
    """合并备注，去重去空，用「；」连接。"""
    out: list[str] = []
    for p in parts:
        p = (p or "").strip()
        if p and p not in out:
            out.append(p)
    return "；".join(out)


def extract_lead(text: str, source_platform: str = "") -> Lead:
    """从一段 OCR 文本中抽取一条线索（取首个手机号）。"""
    leads = extract_leads(text, source_platform=source_platform)
    if leads:
        return leads[0]
    lines = to_lines(text)
    cid, src = extract_customer_id(lines)
    person, person_src = extract_source_person(lines)
    business, _ = extract_business(lines)
    person, person_note = _trusted_operator(person)     # 发布人必须是运营
    if not person:                       # 只有认不出谁发的，才用平台兜底
        filled = _person_from_platform(source_platform)
        if filled:
            # 保留 person_note：它相当于「截图里那个名字没被认下来」的说明，有追溯价值
            person, person_src = filled, "来源平台推断"
    return Lead(customer_id=cid, source_person=person, business=business,
                note=merge_note("未识别到手机号", person_note,
                                _platform_warn(person, source_platform)),
                raw_text=text, id_source=src, person_source=person_src)


def extract_leads(text: str, source_platform: str = "", layout: str = "chat") -> list[Lead]:
    """从一段 OCR 文本中抽取全部线索。

    规则：
      * 逐行找号码，每个号码一条记录
      * 客户ID 按行就近绑定（同一张图里多个客户也能各归各位）
      * 整图只有一个号码且就近找不到 ID 时，才用全局兜底
      * 同一张图里重复出现的同一个号码只保留一次
      * 线索人认不出来时，用 ``source_platform`` 对应的运营兜底

    ``source_platform`` —— 界面上选的来源平台；传空串则不做平台兜底。
    """
    lines = to_lines(text)
    if not lines:
        return []

    per_line: list[tuple[int, list[PhoneHit]]] = []
    for idx, ln in enumerate(lines):
        hits = extract_phones(ln)
        if hits:
            per_line.append((idx, hits))
    if not per_line:
        return []

    total_hits = sum(len(h) for _, h in per_line)
    global_cid, global_src = extract_customer_id(lines)
    business_global, business_note = extract_business(lines)
    person_global, person_src = extract_source_person(lines)
    person_global, person_note = _trusted_operator(person_global)   # 发布人必须是运营
    if not person_global:                    # 认不出谁发的 -> 用账号所属运营兜底
        filled = _person_from_platform(source_platform)
        if filled:
            # person_note 保留：「截图里那个名字不在名单」这条信息值得留痕
            person_global, person_src = filled, "来源平台推断"
    seen_phones: set[str] = set()
    leads: list[Lead] = []

    for idx, hits in per_line:
        fresh = [h for h in hits if h.phone not in seen_phones]
        if not fresh:
            continue
        cid, src = (_follow_list_id(lines, idx) if layout == "follow_list"
                    else _id_for_line(lines, idx))
        if not cid and total_hits == 1:
            cid, src = global_cid, global_src

        # 发布人按行绑定：手机号跟谁在同一段，线索人就归谁。
        # 这样一张图里有好几位运营发言时，号码和人都不会串。
        person, p_src = _person_for_line(lines, idx)
        own_person = bool(person)
        if not person:
            person, p_src = person_global, person_src

        business = _business_for_line(lines, idx) or business_global
        content = extract_content_from_line(lines[idx])
        multi_in_line = len(fresh) > 1

        for hit in fresh:
            seen_phones.add(hit.phone)
            note = merge_note(hit.note, business_note)
            if not own_person:
                note = merge_note(note, person_note)
            note = merge_note(note, _platform_warn(person, source_platform))
            if multi_in_line:
                note = merge_note(note, "同一行出现多个号码，请人工确认")
            leads.append(Lead(
                customer_id=cid,
                phone=hit.phone,
                content=content,
                source_person=person,
                business=business,
                note=note,
                raw_text=text,
                phones=[hit],
                id_source=src,
                person_source=p_src,
            ))
    return leads
