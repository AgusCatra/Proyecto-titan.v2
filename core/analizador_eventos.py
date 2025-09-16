import numpy as np
from typing import Dict, List, Tuple

def calcular_metricas(series: List[Tuple[float, float]]) -> Dict[str, float]:
    """Calcula métricas simples de una serie temporal (tiempo, valor)."""
    if not series:
        return {}

    valores = np.array([v for _, v in series])
    if len(valores) < 2:
        return {}

    difs = np.diff(valores)

    metricas = {
        "n_puntos": int(len(valores)),
        "min": float(np.min(valores)),
        "max": float(np.max(valores)),
        "media": float(np.mean(valores)),
        "std": float(np.std(valores)),
        # cantidad de cambios de dirección (signo)
        "n_correcciones": int(np.sum(np.sign(difs[1:]) != np.sign(difs[:-1])))
    }
    return metricas

def generar_feedback(eventos: Dict[str, List[Tuple[float, float]]]) -> str:
    """Genera texto de devolución a partir de métricas básicas."""
    texto = "📋 **Devolución preliminar basada en eventos crudos**\n\n"

    for metrica, series in eventos.items():
        metricas = calcular_metricas(series)
        if not metricas:
            continue

        texto += f"- **{metrica}**: "
        texto += f"máx={metricas['max']}, mín={metricas['min']}, media={metricas['media']:.2f}, variabilidad={metricas['std']:.2f}. "

        if metricas["std"] > 5:
            texto += "→ Alta variabilidad, indica falta de control.\n"
        elif metricas["std"] < 1:
            texto += "→ Movimiento estable, buen control.\n"
        else:
            texto += "→ Variabilidad moderada.\n"

    return texto
import pdfplumber
import re

def extraer_eventos_crudos(pdf_path: str) -> Dict[str, List[Tuple[float, float]]]:
    """Extrae eventos crudos desde el bloque 'Student Console Events' en el PDF."""
    eventos: Dict[str, List[Tuple[float, float]]] = {}

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text and "Student Console Events" in text:
                for line in text.splitlines():
                    match = re.match(r"(\d{2}:\d{2}:\d{2})\s+(.+?)\s+(-?\d+(?:\.\d+)?)$", line.strip())
                    if match:
                        t_str, metric, val = match.groups()
                        h, m, s = map(int, t_str.split(":"))
                        t_sec = h*3600 + m*60 + s
                        eventos.setdefault(metric.strip(), []).append((t_sec, float(val)))
    return eventos
