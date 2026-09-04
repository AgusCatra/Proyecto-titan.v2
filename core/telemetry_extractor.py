# core/telemetry_extractor.py
# Extracción automática de gráficos de telemetría desde reportes PDF
import logging
import os
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import pypdfium2 as pdfium
import pytesseract

from .graph_mapper import CANONICAL_SIGNALS, normalize_telemetry

logger = logging.getLogger(__name__)

# --- Punto 6-G: efectos secundarios en disco -------------------------------
# En False (por defecto) NO se escriben imágenes intermedias de depuración
# (overlays / máscaras / recortes) en debug_outputs/ en cada ejecución.
DEBUG_EXPORT_IMAGES = False

# Directorio de imágenes de depuración (solo se usa si DEBUG_EXPORT_IMAGES=True).
DEBUG_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug_outputs"
)

# Rangos reales por nombre oficial
Y_RANGES = {
    "Steering": (-14.0, 14.0),
    "Brake Pad": (0.0, 10.0),
    "Acceleration Pad": (0.0, 10.0),
    "Speed In Km/h": (0.0, 2.5),
    "Fork Height In Mtrs": (0.0, 5.0),
    "Tilt Angle In Deg": (0.0, 8.0),
}

PURPLE_LOWER = np.array([110, 30, 40], dtype=np.uint8)
PURPLE_UPPER = np.array([170, 255, 255], dtype=np.uint8)


# -------------------- UTILS --------------------
def _scale(v: float, src: Tuple[float, float], dst: Tuple[float, float]) -> float:
    (a0, a1), (b0, b1) = src, dst
    if a1 == a0:
        return b0
    return b0 + (v - a0) * (b1 - b0) / (a1 - a0)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


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


def _dedup_rects(rects: List[Tuple[int, int, int, int]]) -> List[Tuple[int, int, int, int]]:
    out: List[Tuple[int, int, int, int]] = []
    for r in sorted(rects, key=lambda t: -(t[2] * t[3])):  # grandes primero
        if all(_iou(r, o) < 0.3 for o in out):
            out.append(r)
    return out


# -------------------- DETECCIÓN DE CANDIDATOS --------------------
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
    # Punto 6-G: solo escribir imágenes de depuración si está explícitamente habilitado.
    if DEBUG_EXPORT_IMAGES:
        _ensure_dir(dbg_dir)
        overlay = page_bgr.copy()
        for i, (x, y, ww, hh) in enumerate(rects):
            cv2.rectangle(overlay, (x, y), (x + ww, y + hh), (0, 0, 255), 3)
            crop = page_bgr[y:y + hh, x:x + ww]
            cv2.imwrite(f"{dbg_dir}/page{page_idx+1}_cand{i+1}_crop.png", crop)
        cv2.imwrite(f"{dbg_dir}/page{page_idx+1}_overlay.png", overlay)
    return rects


# -------------------- OCR --------------------
def _binarize_for_ocr(img_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY, 31, 9)
    return thr


def _ocr_title_near(page_bgr: np.ndarray, rect: Tuple[int, int, int, int]) -> str:
    x, y, w, h = rect
    H, W, _ = page_bgr.shape
    y0 = max(0, y - int(h * 0.32))
    y1 = max(0, y - int(h * 0.04))
    strip = page_bgr[y0:y1, x:x + w]
    if strip.size == 0:
        return ""
    prep = _binarize_for_ocr(strip)
    txt = pytesseract.image_to_string(prep, config="--oem 3 --psm 6").lower()
    return " ".join(txt.split())


def _guess_name_from_title(txt: str) -> Optional[str]:
    if not txt:
        return None
    keys = [
        (["steering"], "Steering"),
        (["brake"], "Brake Pad"),
        (["acceleration", "accel"], "Acceleration Pad"),
        (["speed"], "Speed In Km/h"),
        (["fork height"], "Fork Height In Mtrs"),
        (["tilt"], "Tilt Angle In Deg"),
    ]
    for words, name in keys:
        if any(w in txt for w in words):
            return name
    return None


# -------------------- CURVAS --------------------
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


# -------------------- FALLBACK HEURÍSTICO --------------------
def _fallback_assign(name: str, series: List[Tuple[float, float]]) -> str:
    vals = [v for _, v in series]
    vmin, vmax = min(vals), max(vals)
    span = vmax - vmin
    avg = sum(vals) / len(vals)

    # Heurísticas básicas
    if span > 20 and vmax >= 14:
        return "Steering"
    if vmax <= 2.6:
        return "Speed In Km/h"
    if vmax <= 5.5:
        return "Fork Height In Mtrs"
    if vmax <= 8.5 and span > 1.0 and avg > 1.5:
        return "Tilt Angle In Deg"
    if "5_" in name or "6_" in name:
        return "Acceleration Pad"
    return "Unknown_" + name


# -------------------- FUNCIÓN PRINCIPAL --------------------
def extraer_telemetria_visual(
    ruta_pdf: str,
    duracion_total_segundos: int,
    only_canonical: bool = True,
) -> Dict[str, List[Tuple[float, float]]]:
    """Extrae las curvas de telemetría de un PDF mediante visión artificial.

    Devuelve ``{nombre_señal: [(t, v), ...]}``. Con ``only_canonical=True``
    (por defecto) la salida se normaliza a las 6 señales canónicas
    (``CANONICAL_SIGNALS``), descartando claves sin identificar como
    ``"Unknown_<página>_<cand>"``. Esto garantiza el contrato de telemetría que
    consumen ``core.pipeline`` y la tabla ``Telemetria`` de la base de datos.
    """
    dbg_dir = DEBUG_OUTPUT_DIR
    if DEBUG_EXPORT_IMAGES:
        _ensure_dir(dbg_dir)
    results: Dict[str, Optional[List[Tuple[float, float]]]] = {}

    pdf = pdfium.PdfDocument(ruta_pdf)
    for i, page in enumerate(pdf):
        bitmap = page.render(scale=2.0).to_numpy()
        page_bgr = cv2.cvtColor(bitmap, cv2.COLOR_RGB2BGR)
        rects = _find_chart_candidates(page_bgr, i, dbg_dir)
        for j, rect in enumerate(rects):
            title_txt = _ocr_title_near(page_bgr, rect)
            name_by_title = _guess_name_from_title(title_txt)
            crop = page_bgr[rect[1]:rect[1] + rect[3], rect[0]:rect[0] + rect[2]]
            pts = _extract_curve(crop)
            if pts is None:
                continue

            # Si OCR falla, usamos heurística
            name = name_by_title
            if not name:
                series = _calibrate_series(pts, crop.shape, duracion_total_segundos, "tmp")
                name = _fallback_assign(f"{i+1}_{j+1}", series)

            series = _calibrate_series(pts, crop.shape, duracion_total_segundos, name)

            if name in results:
                prev = results[name]
                if prev is None or len(series) > len(prev):
                    results[name] = series
            else:
                results[name] = series

            logger.debug("[Página %s | Cand %s] -> %s (%s puntos)", i + 1, j + 1, name, len(series))

    if only_canonical:
        # Contrato de telemetría (§6-A): emitir directamente las señales canónicas.
        results = normalize_telemetry(results)

    return results
