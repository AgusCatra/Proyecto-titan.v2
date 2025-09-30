# test_fallback.py
from core.telemetry_extractor import extraer_telemetria_visual

PDF_PATH = "data/exports/report test.pdf"
DURACION = 480

print(f"Analizando {PDF_PATH} ...")
res = extraer_telemetria_visual(PDF_PATH, DURACION)

print("\n=== DEBUG FALLBACK PARA UNKNOWNs ===")
for k, v in res.items():
    if k.startswith("Unknown") and v:
        valores = [p[1] for p in v]
        vmin, vmax = min(valores), max(valores)
        prom = sum(valores) / len(valores)
        es_plano = (abs(vmax - vmin) < 0.05)
        print(f"{k}: {len(v)} puntos | rango=({vmin:.2f}, {vmax:.2f}) | "
              f"promedio={prom:.2f} | plano={es_plano}")
