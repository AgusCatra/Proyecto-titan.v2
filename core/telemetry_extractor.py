# core/telemetry_extractor.py
# Extracción automática de gráficos de telemetría desde reportes PDF
import os
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import pypdfium2 as pdfium
import pytesseract
import platform

# Configuración de Tesseract multiplataforma
if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
else:
    pytesseract.pytesseract.tesseract_cmd = "tesseract"

# Rangos reales por nombre oficial
Y_RANGES = {
    "Steering": (-14.0, 14.0),
    "Brake Pad": (0.0, 10.0),
    "Acceleration Pad": (0.0, 10.0),  # ajusta si es 0–2.5 en tu simulador
    "Speed In Km/h": (0.0, 2.5),
    "Fork Height In Mtrs": (0.0, 5.0),
    "Tilt Angle In Deg": (0.0, 8.0),
}

PURPLE_LOWER = np.array([110, 30, 40], dtype=np.uint8)
PURPLE_UPPER = np.array([170, 255, 255], dtype=np.uint8)

def _scale(v: float, src: Tuple[float, float], dst: Tuple[float, float]) -> float:
    (a0, a1), (b0, b1) = src, dst
    if a1 == a0:
        return b0
    return b0 + (v - a0) * (b1 - b0) / (a1 - a0)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _dedup_rects(rects: List[Tuple[int, int, int, int]]) -> List[Tuple[int, int, int, int]]:
    out: List[Tuple[int, int, int, int]] = []
    for r in sorted(rects, key=lambda t: -(t[2] * t[3])):  # grandes primero
        if all(_iou(r, o) < 0.3 for o in out):
            out.append(r)
    return out


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _find_chart_candidates(page_bgr: np.ndarray, page_idx: int, dbg_dir: str):
    h, w, _ = page_bgr.shape
    hsv = cv2.cvtColor(page_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, PURPLE_LOWER, PURPLE_UPPER)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask[:int(h * 0.06), :] = 0

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rects: List[Tuple[int, int, int, int]] = []
    for c in cnts:
        x, y, w0, h0 = cv2.boundingRect(c)
        area = w0 * h0
        if area < 20_000:
            continue
        padx = int(max(40, w0 * 0.10))
        pady = int(max(30, h0 * 0.15))
        x0 = max(0, x - padx)
        y0 = max(0, y - pady)
        x1 = min(w, x + w0 + padx)
        y1 = min(h, y + h0 + pady)
        ww, hh = x1 - x0, y1 - y0
        if ww < 500 or hh < 180 or hh > int(h * 0.7):
            continue
        rects.append((x0, y0, ww, hh))

    rects = _dedup_rects(rects)

    # guardar overlay de debug
    overlay = page_bgr.copy()
    for i, (x, y, ww, hh) in enumerate(rects):
        cv2.rectangle(overlay, (x, y), (x + ww, y + hh), (0, 0, 255), 3)
    cv2.imwrite(f"{dbg_dir}/page{page_idx + 1}_overlay.png", overlay)

    return rects


def _binarize_for_ocr(img_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY, 31, 9)
    ker = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1))
    thr = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, ker, iterations=1)
    return thr


def _ocr_title_near(page_bgr: np.ndarray, rect: Tuple[int, int, int, int]) -> str:
    x, y, w, h = rect
    H, W, _ = page_bgr.shape
    x0 = max(0, x - int(w * 0.10))
    x1 = min(W, x + int(w * 1.10))
    y0 = max(0, y - int(h * 0.32))
    y1 = max(0, y - int(h * 0.04))
    strip1 = page_bgr[y0:y1, x0:x1]
    y2 = y + int(h * 0.15)
    strip2 = page_bgr[y:y2, x:x + w]

    def ocr_strip(s: np.ndarray) -> str:
        if s.size == 0:
            return ""
        prep = _binarize_for_ocr(s)
        return pytesseract.image_to_string(prep, config="--oem 3 --psm 6").lower()

    txt = (ocr_strip(strip1) + " " + ocr_strip(strip2))
    txt = "".join(ch if ch.isalnum() or ch.isspace() or ch in "/-()" else " " for ch in txt)
    return " ".join(txt.split())


def _guess_name_from_title(txt: str) -> Optional[str]:
    if not txt:
        return None
    keys = [
        (["steering"], "Steering"),
        (["brake"], "Brake Pad"),
        (["acceleration", "accel"], "Acceleration Pad"),
        (["speed", "km/h", "kmh"], "Speed In Km/h"),
        (["fork height"], "Fork Height In Mtrs"),
        (["tilt"], "Tilt Angle In Deg"),
    ]
    for words, name in keys:
        if any(w in txt for w in words):
            return name
    return None


def _infer_axis_range(crop_bgr: np.ndarray) -> Optional[str]:
    h, w, _ = crop_bgr.shape
    left = crop_bgr[:, :max(40, int(0.14 * w))].copy()
    gray = cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                cv2.THRESH_BINARY_INV, 31, 7)
    ker = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3))
    thr = cv2.morphologyEx(thr, cv2.MORPH_OPEN, ker, iterations=1)

    cfg = "--oem 3 --psm 6 -c tessedit_char_whitelist=-0123456789."
    txt = pytesseract.image_to_string(thr, config=cfg)
    nums = []
    for tok in txt.replace("\n", " ").split():
        try:
            if tok.count('.') <= 1 and any(ch.isdigit() for ch in tok):
                nums.append(float(tok))
        except:
            pass
    if not nums:
        return None

    vmin, vmax = min(nums), max(nums)
    absmax = max(abs(vmin), abs(vmax))
    if abs(absmax - 14.0) <= 1.2 and any(n <= -12 for n in nums):
        return "Steering"
    if abs(absmax - 10.0) <= 1.2:
        return "0_10"
    if abs(absmax - 2.5) <= 0.4:
        return "Speed In Km/h"
    if abs(absmax - 5.0) <= 0.7:
        return "Fork Height In Mtrs"
    if abs(absmax - 8.0) <= 1.0:
        return "Tilt Angle In Deg"
    return None


def _clean_crop(crop: np.ndarray) -> np.ndarray:
    h, w, _ = crop.shape
    cv2.rectangle(crop, (0, 0), (w, int(h * 0.12)), (0, 0, 0), -1)
    cv2.rectangle(crop, (0, int(h * 0.94)), (w, h), (0, 0, 0), -1)
    return crop


def _extract_curve(crop_bgr: np.ndarray) -> Optional[List[Tuple[int, int]]]:
    crop_bgr = _clean_crop(crop_bgr.copy())
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, PURPLE_LOWER, PURPLE_UPPER)
    if cv2.countNonZero(mask) < 500:
        return None
    h, w = mask.shape
    pts: List[Tuple[int, int]] = []
    white = np.where(mask > 0)
    if white[0].size == 0:
        return None
    col_min = np.full(w, -1, dtype=np.int32)
    ys, xs = white
    for x, y in zip(xs, ys):
        if col_min[x] == -1 or y < col_min[x]:
            col_min[x] = y
    for x in range(w):
        y = col_min[x]
        if y >= 0:
            pts.append((x, int(y)))
    if len(pts) < 200:
        return None
    return pts


def _calibrate_series(px_pts, crop_shape, total_duration, name):
    h, w, _ = crop_shape
    x_range = (0, w)
    y_range_px = (h, 0)
    y_real = Y_RANGES.get(name, (0.0, 10.0))
    out = []
    for x, y in px_pts:
        t = _scale(x, x_range, (0.0, float(total_duration)))
        v = _scale(y, y_range_px, y_real)
        out.append((round(t, 2), round(v, 3)))
    return out


def extraer_telemetria_visual(ruta_pdf: str, duracion_total_segundos: int):
    dbg_dir = "debug_outputs"
    _ensure_dir(dbg_dir)
    results: Dict[str, Optional[List[Tuple[float, float]]]] = {
        "Steering": None,
        "Brake Pad": None,
        "Acceleration Pad": None,
        "Speed In Km/h": None,
        "Fork Height In Mtrs": None,
        "Tilt Angle In Deg": None,
    }
    pdf = pdfium.PdfDocument(ruta_pdf)
    for i, page in enumerate(pdf):
        bitmap = page.render(scale=2.0).to_numpy()
        page_bgr = cv2.cvtColor(bitmap, cv2.COLOR_RGB2BGR)
        rects = _find_chart_candidates(page_bgr, i, dbg_dir)
        for j, rect in enumerate(rects):
            title_txt = _ocr_title_near(page_bgr, rect)
            name_by_title = _guess_name_from_title(title_txt)
            crop = page_bgr[rect[1]:rect[1] + rect[3], rect[0]:rect[0] + rect[2]]

            # guardar crop y mask para debug
            crop_path = f"{dbg_dir}/page{i+1}_cand{j+1}.png"
            cv2.imwrite(crop_path, crop)
            mask = cv2.inRange(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV), PURPLE_LOWER, PURPLE_UPPER)
            cv2.imwrite(crop_path.replace(".png", "_mask.png"), mask)

            pts = _extract_curve(crop)
            if pts is None:
                continue
            name_by_ticks = _infer_axis_range(crop)
            if name_by_ticks == "0_10":
                if name_by_title == "Brake Pad":
                    name = "Brake Pad"
                elif name_by_title == "Acceleration Pad":
                    name = "Acceleration Pad"
                else:
                    name = "Acceleration Pad"
            else:
                name = name_by_title or name_by_ticks or f"Unknown_{i + 1}_{j + 1}"
            series = _calibrate_series(pts, crop.shape, duracion_total_segundos, name)
            if name in results:
                prev = results[name]
                if prev is None or len(series) > len(prev):
                    results[name] = series
            else:
                results[name] = series
    return results
