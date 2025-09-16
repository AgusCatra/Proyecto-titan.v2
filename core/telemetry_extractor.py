# core/telemetry_extractor.py
# Versión 5.0 – Detección automática sin depender de títulos

from typing import Dict, Optional, List, Tuple
import cv2
import numpy as np
import pypdfium2 as pdfium
import pytesseract
import re

GraphSeries = Dict[str, Optional[List[Tuple[float, float]]]]

# Rangos esperados de cada gráfico
EXPECTED_RANGES = {
    "Steering": (-14.0, 14.0),
    "Brake Pad": (0.0, 10.0),
    "Acceleration Pad": (0.0, 10.0),  # algunos reportes 0–2.5
    "Speed In Km/h": (0.0, 2.5),
    "Fork Height In Mtrs": (0.0, 5.0),
    "Tilt Angle In Deg": (0.0, 8.0),
}

# HSV púrpura
PURPLE_RANGES = [
    ((120, 40, 40), (175, 255, 255)),
]

def _render_page_bgr(page, dpi=200):
    bitmap = page.render(scale=dpi/72, rotation=0)
    pil_img = bitmap.to_pil()
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

def _find_grid_regions(img_bgr) -> List[Tuple[int,int,int,int]]:
    """Detecta áreas rectangulares tipo plot."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    kr_h = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1))
    kr_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 25))
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kr_h, iterations=1)
    vert  = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kr_v, iterations=1)
    grid = cv2.bitwise_or(horiz, vert)

    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w > 200 and h > 100:  # filtro básico
            ratio = w / max(1, h)
            if 1.0 < ratio < 10.0:
                regions.append((x, y, w, h))
    return regions

def _extract_line(plot_bgr) -> Optional[List[Tuple[int,int]]]:
    hsv = cv2.cvtColor(plot_bgr, cv2.COLOR_BGR2HSV)
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lo, hi in PURPLE_RANGES:
        mask |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    mask = cv2.medianBlur(mask, 3)

    H, W = mask.shape
    ys = []
    for x in range(W):
        idx = np.flatnonzero(mask[:, x])
        if idx.size == 0:
            ys.append(None)
        else:
            ys.append(int(np.median(idx)))
    if all(v is None for v in ys):
        return None
    xs = np.arange(W)
    y_arr = np.array([np.nan if v is None else v for v in ys], float)
    valid_x = xs[~np.isnan(y_arr)]
    valid_y = y_arr[~np.isnan(y_arr)]
    interp = np.interp(xs, valid_x, valid_y)
    return [(int(x), int(round(interp[x]))) for x in xs]

def _map_series(poly, rect, duration_sec, real_min, real_max):
    x, y, w, h = rect
    series = []
    for px, py in poly:
        t = (px / (w - 1)) * float(duration_sec)
        # invertir eje Y (arriba = max, abajo = min)
        val = real_max - ( (py - y) / (h - 1) ) * (real_max - real_min)
        series.append((round(t, 3), round(val, 3)))
    return series

def _classify_by_range(vals: List[float]) -> str:
    if not vals:
        return "Unknown"
    vmin, vmax = min(vals), max(vals)
    span = vmax - vmin
    best = None
    best_score = 1e9
    for name, (lo, hi) in EXPECTED_RANGES.items():
        score = abs(span - (hi - lo))
        if score < best_score:
            best_score = score
            best = name
    return best or "Unknown"

def extraer_telemetria_visual(ruta_pdf: str, duracion_total_segundos: int) -> GraphSeries:
    results: GraphSeries = {k: None for k in EXPECTED_RANGES.keys()}
    try:
        pdf = pdfium.PdfDocument(ruta_pdf)
    except Exception:
        return results

    found = {}
    for i in range(len(pdf)):
        page = pdf.get_page(i)
        img = _render_page_bgr(page)
        regions = _find_grid_regions(img)
        for rect in regions:
            x, y, w, h = rect
            plot = img[y:y+h, x:x+w]
            poly = _extract_line(plot)
            if not poly:
                continue
            # usar rango por defecto de 0–10 primero
            series = _map_series(poly, rect, duracion_total_segundos, 0.0, 10.0)
            vals = [v for _, v in series]
            gname = _classify_by_range(vals)
            if gname not in found:  # si ya lo extrajimos, no duplicar
                real_lo, real_hi = EXPECTED_RANGES[gname]
                series = _map_series(poly, rect, duracion_total_segundos, real_lo, real_hi)
                found[gname] = series

    for k, v in found.items():
        results[k] = v
    return results
