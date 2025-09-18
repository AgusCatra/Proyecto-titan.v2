# test_debug.py
from core.telemetry_extractor import extraer_telemetria_visual
import pprint

PDF_PATH = "data/exports/report test.pdf"
DURACION = 480

print(f"Analizando {PDF_PATH} ...")
res = extraer_telemetria_visual(PDF_PATH, DURACION)

print("\n=== RESUMEN ===")
for k, v in res.items():
    if v is None:
        print(f"{k}: None")
    else:
        print(f"{k}: {len(v)} puntos")

print("\n=== PRIMEROS 5 PUNTOS DE CADA SERIE ===")
for k, v in res.items():
    if v:
        print(f"{k}: {v[:5]}")
