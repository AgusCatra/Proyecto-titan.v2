# test_final.py
import matplotlib.pyplot as plt
from core.telemetry_extractor import extraer_telemetria_visual

PDF_PATH = "data/exports/report test.pdf"
DURACION = 480

print(f"Analizando {PDF_PATH} ...")
res = extraer_telemetria_visual(PDF_PATH, DURACION)

# Mostrar resumen
print("\n=== RESUMEN FINAL ===")
for k, v in res.items():
    if v:
        print(f"{k}: {len(v)} puntos. Ejemplo: {v[:5]}")
    else:
        print(f"{k}: None")

# Graficar solo señales válidas
for name, serie in res.items():
    if not serie:
        continue
    xs, ys = zip(*serie)
    plt.figure(figsize=(10, 4))
    plt.plot(xs, ys, color="purple")
    plt.title(name)
    plt.xlabel("Tiempo (s)")
    plt.ylabel("Valor")
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(f"debug_outputs/{name.replace(' ', '_')}.png")
    plt.close()

print("\nGráficos guardados en debug_outputs/.")
