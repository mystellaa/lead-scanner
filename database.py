# -*- coding: utf-8 -*-
"""线索存储层：CSV 读写 + 去重 + 导出。

V0.1 用 CSV 作为唯一数据源（需求指定输出 leads.csv），内存里维护索引做去重。
数据量上千条以内完全够用；将来要上 SQLite，只需替换本文件。
"""

from __future__ import annotations

import csv
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

import config

LOG = logging.getLogger("lead_scanner")


# ---------------------------------------------------------------- 记录模型
@dataclass
class LeadRecord:
    customer_id: str = ""        # 客户ID
    phone: str = ""              # 手机号（纯数字）
    content: str = ""            # 内容（消息正文）
    source_person: str = ""      # 线索人（发消息的人）
    business: str = ""           # 业务（被 @ 的人）
    note: str = ""               # 备注（系统提示）
    source_platform: str = ""    # 来源平台（视频号-XXX）
    discovered_at: str = ""      # 发现时间
    image_file: str = ""         # 图片来源（文件名）

    def to_row(self, fields: Iterable[str] | None = None) -> list[str]:
        return [str(getattr(self, f, "") or "") for f in (fields or config.CSV_FIELDS)]

    @classmethod
    def from_row(cls, row: dict) -> "LeadRecord":
        """容忍中英文两种表头，方便手工编辑过的 CSV 也能读回来。"""
        def pick(name: str) -> str:
            en, cn = name, config.CSV_HEADERS[name]
            return str(row.get(en) or row.get(cn) or "").strip()

        return cls(**{f: pick(f) for f in config.CSV_FIELDS})


@dataclass
class AppendResult:
    added: list[LeadRecord] = field(default_factory=list)
    duplicates: list[LeadRecord] = field(default_factory=list)
    invalid: list[LeadRecord] = field(default_factory=list)   # 既无ID也无号码

    @property
    def added_count(self) -> int:
        return len(self.added)

    @property
    def dup_count(self) -> int:
        return len(self.duplicates)

    def summary(self) -> str:
        parts = [f"新增 {self.added_count} 条"]
        if self.dup_count:
            parts.append(f"重复跳过 {self.dup_count} 条")
        if self.invalid:
            parts.append(f"无效 {len(self.invalid)} 条")
        return "，".join(parts)


# ---------------------------------------------------------------- CSV 帮助
def _ensure_header(path: Path) -> None:
    """文件不存在或为空时写入中文表头。"""
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding=config.CSV_ENCODING) as fh:
        csv.writer(fh).writerow([config.CSV_HEADERS[f] for f in config.CSV_FIELDS])


def is_header_row(row: list[str]) -> bool:
    """这一行像不像表头：只要有一个单元格命中已知的表头词就算。"""
    if not row:
        return False
    known = set(config.CSV_HEADERS.values()) | set(config.CSV_FIELDS)
    return any(str(c).strip() in known for c in row)


def read_csv(path: Path | str) -> list[LeadRecord]:
    """读取线索记录。两种格式都认：

    * **带表头** —— 程序自己导出的文件，按表头名对应（中英文表头都行）
    * **不带表头** —— 手工粘进去的裸数据，按位置对到 ``config.CSV_FIELDS`` 的前几列

    为什么非要判一下：``csv.DictReader`` 会**无条件**把第一行当字段名。
    如果用户直接把几行数据粘进 leads.csv（没写表头），第一条就会被无声吞掉 ——
    而且每次启动「表头迁移」都会再吞一条。所以这里先探测首行。
    """
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding=config.CSV_ENCODING) as fh:
        rows = [r for r in csv.reader(fh) if r and any(str(c).strip() for c in r)]
    if not rows:
        return []

    if is_header_row(rows[0]):
        names = [str(c).strip() for c in rows[0]]
        records = [LeadRecord.from_row(dict(zip(names, r))) for r in rows[1:]]
    else:
        # 没表头：按位置映射。粘进来的通常是「手机号,来源平台,线索人,任务负责人」
        # 这种前几列，所以前缀对应最符合预期，多出来的列忽略。
        fields = config.CSV_FIELDS
        records = [
            LeadRecord(**{f: (str(r[i]).strip() if i < len(r) else "")
                          for i, f in enumerate(fields)})
            for r in rows
        ]
    return [rec for rec in records if rec.phone or rec.customer_id]


def header_names(fields: Iterable[str] | None = None) -> list[str]:
    """把内部字段名翻成中文表头。"""
    return [config.CSV_HEADERS[f] for f in (fields or config.CSV_FIELDS)]


def read_header(path: Path | str) -> list[str]:
    """读出 CSV 第一行（用于检测表头是否需要迁移）。"""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding=config.CSV_ENCODING) as fh:
        try:
            return next(csv.reader(fh))
        except StopIteration:
            return []


def write_csv(path: Path | str, records: Iterable[LeadRecord],
              fields: Iterable[str] | None = None,
              with_header: bool = True) -> None:
    """写 CSV。

    fields      —— 要写哪些列（导出时按用户勾选传入），默认全部
    with_header —— 是否写表头行
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(fields or config.CSV_FIELDS)
    with path.open("w", newline="", encoding=config.CSV_ENCODING) as fh:
        w = csv.writer(fh)
        if with_header:
            w.writerow(header_names(cols))
        for rec in records:
            w.writerow(rec.to_row(cols))


# ---------------------------------------------------------------- 数据库
class LeadDatabase:
    """基于 leads.csv 的轻量存储 + 去重。

    ``readonly=True`` 时退化成**纯内存库**：接口完全一样（增删改查、去重都照跑），
    但不读也不写任何文件 —— 界面上的「临时模式」就是拿它当本批线索的暂存区，
    用完即弃，绝不污染总库。
    """

    def __init__(self, csv_path: Path | str | None = None, readonly: bool = False):
        self.readonly = bool(readonly)
        self.csv_path = Path(csv_path or config.LEADS_CSV)
        self._records: list[LeadRecord] = []
        self._phone_index: set[str] = set()
        self._id_index: set[str] = set()
        self._id_source_index: set[tuple[str, str]] = set()
        self._id_phones: dict[str, set[str]] = {}
        self.migration_failed = False     # 表头没整理成功（多半是文件被 Excel 占着）
        self.load()

    # -------------------------------------------------- 读取 / 索引
    def load(self) -> None:
        if self.readonly:                    # 内存库：起点永远是空的
            self._records = []
            self._reindex()
            return
        self._migrate_header_if_needed()
        self._records = read_csv(self.csv_path)
        self._reindex()

    def _migrate_header_if_needed(self) -> bool:
        """列结构变化后，旧文件直接追加会错位 —— 检测到就备份后按新表头重写。"""
        if self.readonly:
            return False
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            return False
        old = read_header(self.csv_path)
        want = header_names()
        if old == want:
            return False
        try:
            records = read_csv(self.csv_path)      # 按老表头解析（中英文都认）
            bak = self.backup()
            write_csv(self.csv_path, records)      # 按新表头重写
            LOG.info("检测到旧版表头，已迁移：%s -> %s 列（备份：%s）",
                     len(old), len(want), bak)
            return True
        except PermissionError:
            # 文件被 Excel 之类占着。数据照读不误，只是列名维持原样，下次启动再试。
            self.migration_failed = True
            LOG.warning("表头迁移失败：leads.csv 被其它程序占用（可能正被 Excel 打开）")
            return False
        except Exception:
            LOG.warning("表头迁移失败，保留原文件不动", exc_info=True)
            return False

    def _reindex(self) -> None:
        self._phone_index = {r.phone for r in self._records if r.phone}
        self._id_index = {r.customer_id for r in self._records if r.customer_id}
        self._id_source_index = {
            (r.customer_id, r.source_platform) for r in self._records if r.customer_id
        }
        self._id_phones = {}
        for r in self._records:
            if r.customer_id and r.phone:
                self._id_phones.setdefault(r.customer_id, set()).add(r.phone)

    @property
    def records(self) -> list[LeadRecord]:
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)

    @property
    def stats(self) -> dict:
        phones = {r.phone for r in self._records if r.phone}
        ids = {r.customer_id for r in self._records if r.customer_id}
        sources = {r.source_platform for r in self._records if r.source_platform}
        return {
            "total": len(self._records),
            "phones": len(phones),
            "customers": len(ids) or len(phones),
            "sources": len(sources),
        }

    # -------------------------------------------------- 去重判定
    def check_duplicate(self, rec: LeadRecord) -> str:
        """返回重复原因；不是重复则返回空串。"""
        phone = (rec.phone or "").strip()
        cid = (rec.customer_id or "").strip()

        if phone:
            if phone in self._phone_index:
                return f"手机号重复({phone})"
            if config.DEDUP_STRICT_BY_ID and cid and cid in self._id_index:
                return f"客户ID重复({cid})"
            return ""
        # 无手机号：用 客户ID+来源 判重
        if cid:
            if (cid, rec.source_platform) in self._id_source_index:
                return f"客户ID重复({cid})"
            if cid in self._id_index:
                return f"客户ID重复({cid})"
        return ""

    # -------------------------------------------------- 写入
    def add(self, records: Iterable[LeadRecord], auto_time: bool = True) -> AppendResult:
        """批量入库；批内也会互相去重。返回本次结果明细。"""
        result = AppendResult()
        to_write: list[LeadRecord] = []

        for rec in records:
            if isinstance(rec, dict):
                rec = LeadRecord(**{k: v for k, v in rec.items() if k in config.CSV_FIELDS})
            rec.phone = (rec.phone or "").strip()
            rec.customer_id = (rec.customer_id or "").strip()
            rec.note = (rec.note or "").strip()

            if not rec.phone and not rec.customer_id:
                result.invalid.append(rec)
                continue

            reason = self.check_duplicate(rec)
            if reason:
                rec.note = _append_note(rec.note, reason)
                result.duplicates.append(rec)
                continue

            # 非严格模式：同一客户ID已有号码，本次是新号码 -> 保留并标注
            if (not config.DEDUP_STRICT_BY_ID) and rec.phone and rec.customer_id:
                prev = self._id_phones.get(rec.customer_id)
                if prev and rec.phone not in prev:
                    rec.note = _append_note(rec.note, "同一客户ID已有其它号码")

            if auto_time and not rec.discovered_at:
                rec.discovered_at = datetime.now().strftime(config.TIME_FORMAT)

            to_write.append(rec)
            result.added.append(rec)
            self._register(rec)          # 批内即时生效

        if to_write:
            self._append_rows(to_write)
        return result

    def _register(self, rec: LeadRecord) -> None:
        self._records.append(rec)
        if rec.phone:
            self._phone_index.add(rec.phone)
        if rec.customer_id:
            self._id_index.add(rec.customer_id)
            self._id_source_index.add((rec.customer_id, rec.source_platform))
            if rec.phone:
                self._id_phones.setdefault(rec.customer_id, set()).add(rec.phone)

    def _append_rows(self, records: list[LeadRecord]) -> None:
        if self.readonly:                    # 内存库不落盘
            return
        _ensure_header(self.csv_path)
        with self.csv_path.open("a", newline="", encoding=config.CSV_ENCODING) as fh:
            w = csv.writer(fh)
            for rec in records:
                w.writerow(rec.to_row())

    # -------------------------------------------------- 修改 / 删除
    def _save_all(self) -> None:
        """全量重写 CSV 并重建索引。

        任何改动（改字段 / 删记录 / 改来源）都走这里，保证文件和内存永远一致。
        数据量小时重写整份文件最省心，也杜绝了增量改写出错的可能。
        内存库只重建索引。
        """
        if not self.readonly:
            write_csv(self.csv_path, self._records)
        self._reindex()

    def update_field(self, index: int, field: str, value: str) -> bool:
        """改一条记录的某个字段（界面双击编辑用）。"""
        if not (0 <= index < len(self._records)):
            return False
        if field not in config.CSV_FIELDS:
            return False
        value = str(value or "").strip()
        if str(getattr(self._records[index], field, "") or "") == value:
            return True
        setattr(self._records[index], field, value)
        self._save_all()
        return True

    def update_many(self, changes: Iterable[tuple[int, str, str]]) -> int:
        """批量改字段：``(索引, 字段名, 新值)``，只重写一次文件。返回改动条数。

        「按来源平台补全线索人」这类成批操作走这里 —— 逐条调 update_field
        会把整份 CSV 重写 N 遍，没必要。
        """
        n = 0
        for index, fname, value in changes:
            try:
                index = int(index)
            except (TypeError, ValueError):
                continue
            if not (0 <= index < len(self._records)) or fname not in config.CSV_FIELDS:
                continue
            value = str(value or "").strip()
            if str(getattr(self._records[index], fname, "") or "") == value:
                continue
            setattr(self._records[index], fname, value)
            n += 1
        if n:
            self._save_all()
        return n

    def delete_at(self, indexes: Iterable[int]) -> int:
        """按索引删除记录，返回实际删掉的条数。"""
        valid = sorted({int(i) for i in indexes if 0 <= int(i) < len(self._records)},
                       reverse=True)
        if not valid:
            return 0
        for i in valid:
            del self._records[i]
        self._save_all()
        return len(valid)

    def rebind_source(self, indexes: Iterable[int], source: str) -> int:
        """把若干条记录的「来源平台」改掉（导入时选错来源就用它）。返回改动条数。"""
        source = str(source or "").strip()
        if not source:
            return 0
        n = 0
        for i in indexes:
            i = int(i)
            if 0 <= i < len(self._records) and self._records[i].source_platform != source:
                self._records[i].source_platform = source
                n += 1
        if n:
            self._save_all()
        return n

    def get(self, index: int) -> LeadRecord | None:
        """按索引取记录。"""
        if 0 <= index < len(self._records):
            return self._records[index]
        return None

    # -------------------------------------------------- 导出 / 维护
    def export(self, dest: Path | str,
               fields: Iterable[str] | None = None,
               with_header: bool = True,
               records: Iterable[LeadRecord] | None = None) -> Path:
        """另存一份 CSV（不破坏主库）。

        fields      —— 要导出的列（按用户勾选传入），None = 全部
        with_header —— 是否写表头
        records     —— 要导出的记录，None = 整库（临时模式下界面会传「本批」）
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_csv(dest, self._records if records is None else records,
                  fields=fields, with_header=with_header)
        return dest

    def backup(self, folder: Path | str | None = None) -> Path | None:
        """把当前 CSV 备份成 leads_YYYYmmdd_HHMMSS.csv.bak。内存库无文件可备。"""
        if self.readonly or not self.csv_path.exists():
            return None
        folder = Path(folder or self.csv_path.parent / "backup")
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = folder / f"{self.csv_path.stem}_{stamp}.csv.bak"
        shutil.copy2(self.csv_path, target)
        return target

    def clear(self, keep_backup: bool = True) -> Path | None:
        """清空主库（默认先备份）。内存库直接丢掉记录，不碰文件。"""
        if self.readonly:
            self._records = []
            self._reindex()
            return None
        bak = self.backup() if keep_backup else None
        self._records = []
        self._reindex()
        write_csv(self.csv_path, [])
        return bak

    def rows_for_ui(self) -> list[tuple[int, list[str]]]:
        """给 Treeview 用：``(记录索引, 行数据)``，按时间倒序。

        带上索引是为了让界面能精确定位到某条记录 —— 双击改值、删行、改来源都靠它。
        """
        pairs = sorted(enumerate(self._records),
                       key=lambda t: t[1].discovered_at, reverse=True)
        return [(i, rec.to_row()) for i, rec in pairs]


def _append_note(note: str, extra: str) -> str:
    note = (note or "").strip()
    extra = (extra or "").strip()
    if not extra:
        return note
    if not note:
        return extra
    if extra in note:
        return note
    return f"{note}；{extra}"


def make_record(customer_id: str = "", phone: str = "", note: str = "",
                source: str = "", image: str = "",
                content: str = "", source_person: str = "", business: str = "") -> LeadRecord:
    """便捷构造（自动补时间）。"""
    return LeadRecord(
        customer_id=customer_id,
        phone=phone,
        content=content,
        source_person=source_person,
        business=business,
        note=note,
        source_platform=source,
        discovered_at=datetime.now().strftime(config.TIME_FORMAT),
        image_file=image,
    )
