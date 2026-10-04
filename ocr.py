# -*- coding: utf-8 -*-
"""OCR 引擎封装。

策略
----
* 首选 **PaddleOCR**（需求指定）；未安装时自动降级到 **RapidOCR**（ONNX，pip 即可装，
  不需要 paddlepaddle，Windows 上更省事）。
* 两个引擎都做了 2.x / 3.x API 差异兼容，装哪个版本都能跑。
* OpenCV 只用于可选的图像预处理（灰度 + 放大），没装也不影响主流程。
"""

from __future__ import annotations

import importlib.util
import logging
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import config

LOG = logging.getLogger("lead_scanner")


# ================================================================ 数据结构
@dataclass
class OcrLine:
    """一行识别结果（同一水平行的若干文本块已合并）。"""
    text: str
    score: float = 0.0
    box: list = field(default_factory=list)     # [[x,y] * 4]

    @property
    def x0(self) -> float:
        return min((p[0] for p in self.box), default=0.0)

    @property
    def cy(self) -> float:
        ys = [p[1] for p in self.box]
        return (min(ys) + max(ys)) / 2 if ys else 0.0


@dataclass
class OcrResult:
    lines: list[OcrLine] = field(default_factory=list)
    engine: str = ""
    image: str = ""
    elapsed: float = 0.0
    error: str = ""

    @property
    def texts(self) -> list[str]:
        return [ln.text for ln in self.lines]

    @property
    def full_text(self) -> str:
        """按阅读顺序拼成多行文本，直接喂给 phone_extract。"""
        return "\n".join(self.texts)

    @property
    def ok(self) -> bool:
        return not self.error


# ================================================================ 图像预处理
def preprocess_image(src: Path) -> tuple[Path, bool]:
    """灰度 + 放大（小截图更友好）。返回 (用于识别的路径, 是否是临时文件)。"""
    src = Path(src)
    if not config.OCR_PREPROCESS:
        return src, False
    try:
        import cv2
        import numpy as np
    except Exception:
        return src, False

    try:
        data = np.fromfile(str(src), dtype=np.uint8)      # 兼容中文路径
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            return src, False
        h, w = img.shape[:2]
        if max(h, w) < 1400 and config.OCR_UPSCALE > 1:   # 小图才放大，避免大图爆内存
            img = cv2.resize(img, None, fx=config.OCR_UPSCALE, fy=config.OCR_UPSCALE,
                             interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)     # 统一回 3 通道，兼容各引擎
        fd, tmp = tempfile.mkstemp(suffix=".png", prefix="ls_prep_")
        import os
        os.close(fd)
        cv2.imencode(".png", gray)[1].tofile(tmp)
        return Path(tmp), True
    except Exception:
        LOG.warning("图像预处理失败，使用原图", exc_info=True)
        return src, False


# ================================================================ 引擎基类
class OcrEngineBase:
    name = "base"
    display = "未命名引擎"

    def __init__(self) -> None:
        self._impl = None
        self._api = ""

    # ---------- 生命周期 ----------
    @classmethod
    def available(cls) -> bool:
        return False

    @classmethod
    def hint(cls) -> str:
        """不可用时给用户的安装提示。"""
        return ""

    def _build(self) -> None:
        raise NotImplementedError

    def ensure_ready(self) -> None:
        if self._impl is None:
            self._build()

    # ---------- 识别 ----------
    def _run(self, image_path: str) -> list[tuple[list, str, float]]:
        """子类实现：返回 [(box, text, score), ...]"""
        raise NotImplementedError

    def recognize(self, image_path: Path | str, preprocess: bool = True) -> OcrResult:
        image_path = Path(image_path)
        res = OcrResult(engine=self.name, image=str(image_path))
        t0 = time.time()
        tmp_path: Path | None = None
        try:
            self.ensure_ready()
            target = image_path
            if preprocess:
                target, is_tmp = preprocess_image(image_path)
                tmp_path = target if is_tmp else None
            items = self._run(str(target))
            res.lines = _order_lines(items)
        except Exception as exc:                     # 单张图失败不影响整批
            res.error = f"{type(exc).__name__}: {exc}"
            LOG.error("OCR 识别失败 %s: %s", image_path.name, res.error, exc_info=True)
        finally:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            res.elapsed = round(time.time() - t0, 2)
        return res


# ================================================================ PaddleOCR
class PaddleEngine(OcrEngineBase):
    name = "paddleocr"
    display = "PaddleOCR"

    @classmethod
    def available(cls) -> bool:
        return importlib.util.find_spec("paddleocr") is not None

    @classmethod
    def hint(cls) -> str:
        return "pip install paddlepaddle paddleocr"

    def _build(self) -> None:
        from paddleocr import PaddleOCR

        major = _pkg_major("paddleocr")
        if major >= 3:                    # 3.x：predict 接口
            kw_list = [
                dict(lang=config.OCR_LANG, use_doc_orientation_classify=False,
                     use_doc_unwarping=False, use_textline_orientation=False,
                     device="gpu" if config.OCR_USE_GPU else "cpu"),
                dict(lang=config.OCR_LANG),
                dict(),
            ]
            self._api = "predict"
        else:                             # 2.x：ocr 接口
            kw_list = [
                dict(lang=config.OCR_LANG, use_angle_cls=True, show_log=False,
                     use_gpu=config.OCR_USE_GPU),
                dict(lang=config.OCR_LANG, use_angle_cls=True),
                dict(lang=config.OCR_LANG),
            ]
            self._api = "ocr"

        last_err: Exception | None = None
        for kw in kw_list:
            try:
                self._impl = PaddleOCR(**kw)
                return
            except Exception as exc:      # 参数在新旧版本间有差异，逐个降级尝试
                last_err = exc
        raise RuntimeError(f"PaddleOCR 初始化失败：{last_err}")

    def _run(self, image_path: str) -> list[tuple[list, str, float]]:
        if self._api == "predict":
            raw = self._impl.predict(image_path)
        else:
            raw = self._impl.ocr(image_path, cls=True)
        return _parse_paddle(raw, self._api)


def _parse_paddle(raw, api: str) -> list[tuple[list, str, float]]:
    """兼容解析 PaddleOCR 2.x / 3.x 的返回值。"""
    items: list[tuple[list, str, float]] = []
    if raw is None:
        return items

    if api == "predict":
        pages = raw if isinstance(raw, list) else [raw]
        for page in pages:
            d = None
            if isinstance(page, dict):
                d = page
            elif hasattr(page, "get") and hasattr(page, "keys"):
                d = page
            elif hasattr(page, "__getitem__"):
                try:
                    d = page
                    _ = d["rec_texts"]
                except Exception:
                    d = None
            if not d:
                continue
            try:
                texts = list(d["rec_texts"])
            except Exception:
                continue
            scores = list(d.get("rec_scores") or [0.0] * len(texts))
            polys = list(d.get("rec_polys") or d.get("dt_polys") or [])
            for i, txt in enumerate(texts):
                box = _as_box(polys[i]) if i < len(polys) else []
                sc = float(scores[i]) if i < len(scores) else 0.0
                items.append((box, str(txt), sc))
        return items

    # 2.x
    lines = raw
    if isinstance(lines, list) and lines and isinstance(lines[0], list) \
            and lines[0] and isinstance(lines[0][0], list) \
            and len(lines[0][0]) == 2 and isinstance(lines[0][0][1], tuple):
        lines = lines[0]
    for entry in lines or []:
        try:
            box, payload = entry[0], entry[1]
            text, score = (payload[0], float(payload[1]))
            items.append((_as_box(box), str(text), score))
        except Exception:
            continue
    return items


# ================================================================ RapidOCR
class RapidEngine(OcrEngineBase):
    name = "rapidocr"
    display = "RapidOCR (ONNX)"

    @classmethod
    def available(cls) -> bool:
        return (importlib.util.find_spec("rapidocr_onnxruntime") is not None
                or importlib.util.find_spec("rapidocr") is not None)

    @classmethod
    def hint(cls) -> str:
        return "pip install rapidocr-onnxruntime"

    def _build(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR      # 经典版
            self._api = "legacy"
        except ImportError:
            from rapidocr import RapidOCR                  # v2+
            self._api = "v2"
        self._impl = RapidOCR()

    def _run(self, image_path: str) -> list[tuple[list, str, float]]:
        out = self._impl(image_path)
        items: list[tuple[list, str, float]] = []

        if isinstance(out, tuple) and len(out) == 2:        # (result, elapse)
            result = out[0]
            for row in result or []:
                try:
                    box, text, score = row[0], row[1], row[2]
                    items.append((_as_box(box), str(text), float(score)))
                except Exception:
                    continue
            return items

        # v2 返回对象：.boxes / .txts / .scores
        boxes = getattr(out, "boxes", None)
        txts = getattr(out, "txts", None)
        scores = getattr(out, "scores", None)
        if boxes is not None and txts is not None:
            scores = list(scores) if scores is not None else [0.0] * len(txts)
            for i, txt in enumerate(txts):
                box = _as_box(boxes[i]) if i < len(boxes) else []
                sc = float(scores[i]) if i < len(scores) else 0.0
                items.append((box, str(txt), sc))
        return items


# ================================================================ 工具
def _as_box(box) -> list:
    try:
        return [[float(p[0]), float(p[1])] for p in box]
    except Exception:
        return []


def _pkg_major(name: str) -> int:
    import importlib.metadata as md
    try:
        return int(md.version(name).split(".")[0])
    except Exception:
        return 2


def _order_lines(items: list[tuple[list, str, float]]) -> list[OcrLine]:
    """把文本块按「自上而下、自左而右」排序并合并成行。"""
    entries = []
    for box, text, score in items:
        text = str(text).strip()
        if not text or score < config.OCR_MIN_SCORE:
            continue
        entries.append({"text": text, "score": score, "box": box,
                        "x0": min((p[0] for p in box), default=0.0),
                        "cy": (min((p[1] for p in box), default=0.0)
                               + max((p[1] for p in box), default=0.0)) / 2,
                        "h": max((p[1] for p in box), default=0.0)
                             - min((p[1] for p in box), default=0.0)})
    if not entries:
        return []

    entries.sort(key=lambda e: (e["cy"], e["x0"]))
    rows: list[dict] = []
    for e in entries:
        for row in rows:
            tol = max(e["h"], row["h"], 8.0) * 0.6
            if abs(e["cy"] - row["cy"]) <= tol:
                row["items"].append(e)
                row["cy"] = sum(i["cy"] for i in row["items"]) / len(row["items"])
                row["h"] = max(row["h"], e["h"])
                break
        else:
            rows.append({"cy": e["cy"], "h": e["h"], "items": [e]})

    rows.sort(key=lambda r: r["cy"])
    lines: list[OcrLine] = []
    for row in rows:
        row["items"].sort(key=lambda i: i["x0"])
        text = " ".join(i["text"] for i in row["items"])
        score = sum(i["score"] for i in row["items"]) / len(row["items"])
        box = [p for i in row["items"] for p in (i["box"] or [])]
        lines.append(OcrLine(text=text, score=round(score, 3), box=box))
    return lines


# ================================================================ 引擎管理
_ENGINE: OcrEngineBase | None = None
_LOCK = threading.Lock()

_ENGINE_CLASSES = {
    "paddleocr": PaddleEngine,
    "rapidocr": RapidEngine,
}


def available_engines() -> list[tuple[str, bool, str]]:
    """返回 [(引擎名, 是否可用, 安装提示)]，按偏好顺序。"""
    out = []
    for key in config.OCR_ENGINE_PREFERENCE:
        cls = _ENGINE_CLASSES.get(key)
        if cls:
            out.append((cls.display, cls.available(), cls.hint()))
    return out


def pick_engine_key() -> str:
    for key in config.OCR_ENGINE_PREFERENCE:
        cls = _ENGINE_CLASSES.get(key)
        if cls and cls.available():
            return key
    return ""


def get_engine(force_new: bool = False) -> OcrEngineBase:
    """获取（并缓存）OCR 引擎实例；首次调用会加载模型，比较慢。"""
    global _ENGINE
    with _LOCK:
        if _ENGINE is not None and not force_new:
            return _ENGINE
        key = pick_engine_key()
        if not key:
            raise RuntimeError(
                "未检测到可用的 OCR 引擎。请任选其一安装：\n"
                "  ① pip install paddlepaddle paddleocr   （需求指定，模型较大）\n"
                "  ② pip install rapidocr-onnxruntime     （轻量替代，推荐先试）"
            )
        engine = _ENGINE_CLASSES[key]()
        engine.ensure_ready()          # 提前加载，避免第一张图超时
        _ENGINE = engine
        LOG.info("OCR 引擎已就绪: %s", engine.display)
        return _ENGINE


def engine_status() -> tuple[str, str]:
    """返回 (引擎显示名, 说明文本)，供界面显示。"""
    key = pick_engine_key()
    if not key:
        return "未检测到 OCR 引擎", "需要安装 paddleocr 或 rapidocr-onnxruntime"
    cls = _ENGINE_CLASSES[key]
    loaded = "（已加载）" if _ENGINE is not None and _ENGINE.name == key else ""
    return f"{cls.display}{loaded}", "OCR 就绪"


def recognize(image_path: Path | str) -> OcrResult:
    """模块级快捷方法：单张图片识别。"""
    return get_engine().recognize(image_path)
