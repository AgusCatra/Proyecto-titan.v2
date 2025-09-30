# test_debug2.py
import os
import matplotlib.pyplot as plt
from core.telemetry_extractor import extraer_telemetria_visual

PDF_PATH = "data/exports/report test.pdf"
DURACION = 480
OUT_DIR = "debug_outputs/series"
os.makedirs(OUT_DIR, exist_ok=True)

print(f"Analizando {PDF_PATH} ...")
res = extraer_telemetria_visual(PDF_PATH, DURACION)

print("\n=== RESUMEN ===")
for k, v in res.items():
    if not v:
        print(f"{k}: None")
        continue
    print(f"{k}: {len(v)} puntos | Ejemplo: {v[:5]}")

    # Graficar cada serie
    t, vals = zip(*v)
    plt.figure(figsize=(10, 4))
    plt.plot(t, vals, color="purple", linewidth=1)
    plt.title(k)
    plt.xlabel("Tiempo (s)")
    plt.ylabel("Valor")
    plt.grid(True, linestyle="--", alpha=0.6)

    out_file = os.path.join(OUT_DIR, f"{k}.png")
    plt.savefig(out_file)
    plt.close()
    print(f"  → Gráfico guardado en {out_file}")
