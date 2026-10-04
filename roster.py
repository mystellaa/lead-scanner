# -*- coding: utf-8 -*-
"""公司名单字典：运营 / 账号 / 业务。

三份名单各自的用途
------------------
* **账号** -> 界面「来源视频号」下拉选项；识别出的来源平台是否属于已知账号
* **运营** -> 由账号反查运营人；校验截图里的「线索人」是不是自己人
* **业务** -> 校验「任务负责人」，并做 OCR 错字纠正

名单有两种维护方式（后者优先）：
  1. 直接改本文件的 DEFAULT_* 常量
  2. 写 ``data/roster.json``，格式见 :func:`save_template`

另外提供 :func:`export_operators_csv` / :func:`export_sales_csv`，
**一行一条**输出，直接导入飞书多维表格就是分开的记录。
"""

from __future__ import annotations

import csv
import json
import logging
from difflib import SequenceMatcher
from pathlib import Path

import config

LOG = logging.getLogger("lead_scanner")

# ---------------------------------------------------------------- 默认名单
# 运营 -> 负责的账号（账号名前缀就是平台：视频号 / 抖音 / 小红书 / 其他）
DEFAULT_OPERATORS: list[tuple[str, list[str]]] = [
    ("彭曦", ["视频号-彭曦", "本地推"]),
    ("刘一手", ["视频号-刘一手", "1688"]),
    ("梁毅", ["抖音-梁毅", "视频号-梁毅"]),
    ("王可", ["小红书-王可"]),
    ("叶虹池", ["小红书-叶虹池"]),
    ("王烔彤", ["视频号-北海服务商", "视频号-来宾服务商", "视频号-海南服务商",
                "视频号-truelike", "视频号-云南服务商"]),
]

# 业务名单（分组, 成员）
#   老业务 / 新人 / 带教 —— 带教不单独接线索，而是**跟着新人一起出现**
DEFAULT_SALES: list[tuple[str, list[str]]] = [
    ("老业务", ["张悦", "区玉清", "孔秋梅", "陈秀珍", "吴超男", "刘雅婷", "韦东雅"]),
    ("新人", ["朱倩琳", "李童瑶", "蒙丽萍", "吴嘉贤", "梁素妍", "宋玉婷", "罗淑冰"]),
    ("带教", ["胡丹"]),
]

NEWCOMER_GROUP = "新人"
MENTOR_GROUP = "带教"

# Full contact labels used when pasting people into the company table.
DEFAULT_ACCOUNT_IDS: dict[str, str] = {
    "彭曦": "彭曦(AAa琉璃瓦老彭)",
    "刘一手": "刘一手(A刘一手)",
    "梁毅": "梁毅(A真赞瓦业-梁毅)",
    "王可": "王可(Aa王可)",
    "叶虹池": "叶虹池(运营-叶虹池)",
    "王烔彤": "王烔彤运营",
    "张悦": "张悦(A真赞瓦业-张悦-18029395558)",
    "区玉清": "区玉清(A真赞瓦业-小欧18029396635)",
    "孔秋梅": "孔秋梅(真赞瓦业-孔秋梅-18029397762)",
    "陈秀珍": "陈秀珍(A真赞瓦业-陈秀珍-13360301578)",
    "吴超男": "吴超男(A真赞瓦业-吴超男-18666373836)",
    "刘雅婷": "刘雅婷(A真赞瓦业-刘雅婷-18666515957)",
    "韦东雅": "韦东雅(A真赞瓦业-韦东雅-13392262816)",
    "朱倩琳": "A真赞别墅瓦-朱倩琳18688241985",
    "李童瑶": "A-真赞别墅瓦-李童瑶18689313508(真赞瓦业-小李)",
    "蒙丽萍": "A真赞瓦业-蒙丽萍-18666367072(蒙蒙)",
    "吴嘉贤": "吴嘉贤(A真赞瓦业吴嘉贤18578366032)",
    "梁素妍": "A-真赞瓦业-梁素妍-18688245519(真赞瓦业_梁素妍)",
    "宋玉婷": "A真赞别墅瓦-宋玉婷-13392262817(A真赞别墅瓦-宋玉婷-13392262817)",
    "罗淑冰": "真赞瓦业-罗淑冰18689239098",
    "胡丹": "A佛山真赞瓦业-胡丹(A真赞瓦业-胡丹18665418517)",
}

# 名字相似度兜底阈值（同长度差一字的情况会先被精确规则捞走）
SIMILARITY_THRESHOLD = 0.75

# ---------------------------------------------------------------- 运行期状态
OPERATORS: list[tuple[str, list[str]]] = []
SALES: list[tuple[str, list[str]]] = []
ACCOUNT_IDS: dict[str, str] = {}
_ACCOUNT_TO_OPERATOR: dict[str, str] = {}
_ACCOUNTS: list[str] = []
_OPERATOR_NAMES: list[str] = []
_SALES_GROUP: dict[str, str] = {}
_SALES_NAMES: list[str] = []
_MENTOR_NAMES: list[str] = []
_loaded = False


def roster_file() -> Path:
    return config.DATA_DIR / "roster.json"


# ---------------------------------------------------------------- 加载 / 保存
def _rebuild_index() -> None:
    global _ACCOUNT_TO_OPERATOR, _ACCOUNTS, _OPERATOR_NAMES
    global _SALES_GROUP, _SALES_NAMES, _MENTOR_NAMES
    _ACCOUNT_TO_OPERATOR = {}
    _ACCOUNTS = []
    _OPERATOR_NAMES = []
    for name, accounts in OPERATORS:
        if not name:
            continue
        _OPERATOR_NAMES.append(name)
        for acc in accounts:
            acc = str(acc).strip()
            if acc and acc not in _ACCOUNT_TO_OPERATOR:
                _ACCOUNT_TO_OPERATOR[acc] = name
                _ACCOUNTS.append(acc)
    _SALES_GROUP = {}
    _SALES_NAMES = []
    for group, members in SALES:
        for m in members:
            m = str(m).strip()
            if m and m not in _SALES_GROUP:
                _SALES_GROUP[m] = group
                _SALES_NAMES.append(m)
    _MENTOR_NAMES = [n for n in _SALES_NAMES if _SALES_GROUP.get(n) == MENTOR_GROUP]


def load(force: bool = False) -> None:
    """载入名单：内置默认 -> 若存在 data/roster.json 则覆盖。"""
    global OPERATORS, SALES, ACCOUNT_IDS, _loaded
    if _loaded and not force:
        return

    OPERATORS = [(n, list(a)) for n, a in DEFAULT_OPERATORS]
    SALES = [(g, list(m)) for g, m in DEFAULT_SALES]
    ACCOUNT_IDS = dict(DEFAULT_ACCOUNT_IDS)

    f = roster_file()
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            ops = [(str(o.get("name", "")).strip(), [str(x).strip() for x in o.get("accounts", [])])
                   for o in data.get("operators", []) if o.get("name")]
            sales = [(str(g.get("group", "")).strip(), [str(x).strip() for x in g.get("members", [])])
                     for g in data.get("sales", []) if g.get("members")]
            if ops:
                OPERATORS = ops
            if sales:
                SALES = sales
            overrides = data.get("account_ids", {})
            if isinstance(overrides, dict):
                ACCOUNT_IDS.update({str(n).strip(): str(v).strip()
                                    for n, v in overrides.items() if isinstance(v, str) and v.strip()})
            LOG.info("已从 %s 载入名单", f)
        except Exception:
            LOG.warning("roster.json 解析失败，回退内置名单", exc_info=True)

    _rebuild_index()
    _loaded = True


def save_template(path: Path | str | None = None) -> Path:
    """导出一份 roster.json 模板，改完放到 data/ 下即可覆盖内置名单。"""
    load()
    path = Path(path or roster_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "operators": [{"name": n, "accounts": a} for n, a in OPERATORS],
        "sales": [{"group": g, "members": m} for g, m in SALES],
        "account_ids": {n: ACCOUNT_IDS.get(n, n) for n in operator_names() + sales_names()},
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 查询
def export_contact_labels(value: str, *, business: bool = False) -> str:
    """Expand canonical names for export; keep every assignee in one cell."""
    load()
    allowed = set(sales_names() if business else operator_names())
    labels = {name: ACCOUNT_IDS.get(name, name) for name in allowed}
    reverse = {label: name for name, label in labels.items()}
    names = []
    for part in str(value or "").replace("，", ",").split(","):
        part = part.strip()
        name = reverse.get(part, part)
        if name and name not in names:
            names.append(name)
    if business:
        names = order_responsibles(with_mentor(names))
    return ",".join(labels.get(name, name) for name in names)


def accounts() -> list[str]:
    load()
    return list(_ACCOUNTS)


def operator_names() -> list[str]:
    load()
    return list(_OPERATOR_NAMES)


def operator_of(account: str) -> str:
    """账号 -> 运营人；查不到返回空串。"""
    load()
    return _ACCOUNT_TO_OPERATOR.get(str(account or "").strip(), "")


def operator_for_source(source: str) -> tuple[str, str]:
    """来源平台 -> 负责的运营人，返回 ``(运营名, 判定依据)``。

    界面上选了来源平台，就能顺推出这条线索该归谁 —— 依次尝试：

      1. **账号全名精确命中** —— 「视频号-北海服务商」-> 王烔彤
      2. **包含匹配** —— 「视频号-北海服务商(备用号)」这类写法也能认出来
      3. **扫运营名单** —— 「抖音-见真-梁毅」这种没登记过的自由写法，
         靠名字兜底捞出「梁毅」

    三招都不中就返回 ``("", "")``，让调用方决定留空还是提示，
    绝不硬猜一个运营挂上去。
    """
    load()
    text = str(source or "").strip()
    if not text:
        return "", ""

    hit = _ACCOUNT_TO_OPERATOR.get(text)
    if hit:
        return hit, "账号登记"

    for acc, op in _ACCOUNT_TO_OPERATOR.items():
        if acc and (acc in text or text in acc):
            return op, f"账号登记（{acc}）"

    names = _scan(text, _OPERATOR_NAMES)
    if len(names) == 1:
        return names[0], "按名称推断"
    if names:
        return names[0], f"按名称推断（另有候选：{'、'.join(names[1:])}，请核对）"
    return "", ""


def accounts_of(operator: str) -> list[str]:
    """某位运营名下负责的账号 —— 「按运营挑平台」用。

    传入的名字会先按名单纠错（OCR 常把「彭曦」认成「壹曦」），
    所以界面上拿到一个线索人，就能直接问出他该选哪些平台。
    """
    load()
    name = str(operator or "").strip()
    if not name:
        return []
    fixed, _ = match_name(name, _OPERATOR_NAMES)
    if fixed:
        name = fixed
    for n, accs in OPERATORS:
        if n == name:
            return [str(a).strip() for a in accs if str(a).strip()]
    return []


def consistency_note(person: str, platform: str) -> str:
    """线索人（发布人）和来源平台对不对得上；对得上就返回空串。

    规矩是「发布人一定是他自己账号的运营」，所以「彭曦发布的线索 + 来源平台
    写的是视频号-梁毅」是矛盾的 —— 提醒一句，别把线索挂错账号。
    查不出平台归属（不在名单）时不提醒，因为那只是没登记，不一定是错。
    """
    load()
    person = str(person or "").strip()
    platform = str(platform or "").strip()
    if not person or not platform or not is_operator(person):
        return ""
    op = operator_of(platform)
    if not op:
        inferred, why = operator_for_source(platform)
        op = inferred if why == "账号登记" or why.startswith("账号登记") else ""
    if not op or op == person:
        return ""
    return f"来源平台「{platform}」属于 {op}，与线索人 {person} 不一致，请核对"


def sales_names() -> list[str]:
    load()
    return list(_SALES_NAMES)


def sales_group(name: str) -> str:
    """业务姓名 -> 分组（老业务 / 新人）；查不到返回空串。"""
    load()
    return _SALES_GROUP.get(str(name or "").strip(), "")


def is_operator(name: str) -> bool:
    load()
    return str(name or "").strip() in _OPERATOR_NAMES


def is_sales(name: str) -> bool:
    load()
    return str(name or "").strip() in _SALES_NAMES


# ---------------------------------------------------------------- 任务负责人（可多人）
def newcomers() -> list[str]:
    """新人列表。"""
    load()
    return [n for n in _SALES_NAMES if _SALES_GROUP.get(n) == NEWCOMER_GROUP]


def mentors() -> list[str]:
    """带教列表（胡丹）。"""
    load()
    return list(_MENTOR_NAMES)


def is_newcomer(name: str) -> bool:
    return sales_group(name) == NEWCOMER_GROUP


def order_responsibles(names: list[str]) -> list[str]:
    """任务负责人排序：新人 → 其它 → 带教（带教永远垫在最后）。"""
    load()
    new_ = [n for n in names if _SALES_GROUP.get(n) == NEWCOMER_GROUP]
    men_ = [n for n in names if _SALES_GROUP.get(n) == MENTOR_GROUP]
    others = [n for n in names if n not in new_ and n not in men_]
    return new_ + others + men_


def with_mentor(names: list[str]) -> list[str]:
    """分给新人的线索，自动把带教一起带上（排在最后）。"""
    load()
    if not names or not any(_SALES_GROUP.get(n) == NEWCOMER_GROUP for n in names):
        return list(names)
    out = list(names)
    for m in _MENTOR_NAMES:
        if m and m not in out:
            out.append(m)
    return out


def _scan(text: str, candidates: list[str]) -> list[str]:
    """在文本里扫出所有出现过的名单成员，按首次出现位置排序并去重。"""
    hits: list[tuple[int, str]] = []
    for name in candidates:
        pos = text.find(name)
        if pos >= 0:
            hits.append((pos, name))
    hits.sort(key=lambda t: t[0])
    out: list[str] = []
    for _, n in hits:
        if n not in out:
            out.append(n)
    return out


def scan_sales(text: str) -> list[str]:
    """在整段文本里扫业务名单。

    能直接从「真赞瓦业-罗淑冰18689239098,A佛山真赞瓦业-胡丹(…)」这种
    微信提及串里把名字捞出来，手机号、微信备注这类噪音会被自动过滤掉。
    """
    load()
    return order_responsibles(_scan(text, _SALES_NAMES))


def scan_operators(text: str) -> list[str]:
    """在整段文本里扫运营名单。"""
    load()
    return _scan(text, _OPERATOR_NAMES)


# ---------------------------------------------------------------- 名字纠错
def _one_char_diff(a: str, b: str) -> bool:
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


def match_name(name: str, candidates: list[str]) -> tuple[str, str]:
    """在名单里找 ``name`` 对应的规范写法。

    返回 ``(规范名字, 说明)``：
      * 精确命中 -> ``(name, "")``
      * 同长度、只差一个字（典型的 OCR 误识）-> ``(正确名字, "已按名单校正…")``
      * 相似度够高 -> 同上
      * 都不是 -> ``("", "")`` 表示名单里没有这个人
    """
    name = str(name or "").strip()
    if not name or not candidates:
        return "", ""
    if name in candidates:
        return name, ""

    near = [c for c in candidates if _one_char_diff(name, c)]
    if len(near) == 1:                       # 只差一个字，且只匹配到一个人
        if config.ROSTER_AUTO_CORRECT:
            return near[0], f"已按名单校正：{name} → {near[0]}"
        return "", f"疑似「{near[0]}」，请核对"

    scored = sorted(((SequenceMatcher(None, name, c).ratio(), c) for c in candidates),
                    key=lambda t: t[0], reverse=True)
    if scored and scored[0][0] >= SIMILARITY_THRESHOLD:
        if config.ROSTER_AUTO_CORRECT:
            return scored[0][1], f"已按名单校正：{name} → {scored[0][1]}"
        return "", f"疑似「{scored[0][1]}」，请核对"
    return "", ""


def check_source_person(name: str) -> tuple[str, str]:
    """校验「线索人」：应为运营名单里的人。返回 (最终名字, 备注)。"""
    load()
    if not name:
        return "", ""
    matched, note = match_name(name, _OPERATOR_NAMES)
    if matched:
        return matched, note
    if not config.CHECK_NAMES_AGAINST_ROSTER:
        return name, ""
    return name, f"线索人「{name}」不在运营名单"


def check_business(name: str) -> tuple[str, str]:
    """校验「任务负责人」：应为业务名单里的人。返回 (最终名字, 备注)。"""
    load()
    if not name:
        return "", ""
    matched, note = match_name(name, _SALES_NAMES)
    if matched:
        return matched, note
    if not config.CHECK_NAMES_AGAINST_ROSTER:
        return name, ""
    return name, f"任务负责人「{name}」不在业务名单"


# ---------------------------------------------------------------- 导出
def export_operators_csv(path: Path | str) -> Path:
    """运营-账号对照表，**一行一个账号**，可直接导入多维表格。"""
    load()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding=config.CSV_ENCODING) as fh:
        w = csv.writer(fh)
        w.writerow(["运营", "账号"])
        for name, accounts_ in OPERATORS:
            for acc in accounts_:
                w.writerow([name, acc])
    return path


def export_sales_csv(path: Path | str) -> Path:
    """业务名单，**一行一个人**，带分组列。"""
    load()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding=config.CSV_ENCODING) as fh:
        w = csv.writer(fh)
        w.writerow(["分组", "业务"])
        for group, members in SALES:
            for m in members:
                w.writerow([group, m])
    return path
