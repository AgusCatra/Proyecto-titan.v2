# test_debug.py
from core import telemetry_extractor as te

PDF_PATH = "data/exports/report test.pdf"
DURACION = 480

print(f"Analizando {PDF_PATH} ...")
res = te.extraer_telemetria_visual(PDF_PATH, DURACION)

print("\n=== RESUMEN ===")
for k, v in res.items():
    if v is None:
        print(f"{k}: None")
    else:
        print(f"{k}: {len(v)} puntos")
        print(f"  Ejemplo: {v[:5]}")
