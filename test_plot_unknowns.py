# test_plot_unknowns.py
import os
import matplotlib.pyplot as plt
from core.telemetry_extractor import extraer_telemetria_visual

PDF_PATH = "data/exports/report test.pdf"
DURACION = 480
OUT_DIR = "debug_outputs/unknowns"
os.makedirs(OUT_DIR, exist_ok=True)

print(f"Analizando {PDF_PATH} ...")
res = extraer_telemetria_visual(PDF_PATH, DURACION)

for k, v in res.items():
    if k.startswith("Unknown") and v:
        xs, ys = zip(*v)
        plt.figure(figsize=(8, 3))
        plt.plot(xs, ys, color="purple", linewidth=1)
        plt.title(k)
        plt.xlabel("Tiempo (s)")
        plt.ylabel("Valor")
        plt.grid(True, linestyle="--", alpha=0.5)
        out_path = os.path.join(OUT_DIR, f"{k}.png")
        plt.savefig(out_path, dpi=120, bbox_inches="tight")
        plt.close()
        print(f"Guardado {out_path}")
