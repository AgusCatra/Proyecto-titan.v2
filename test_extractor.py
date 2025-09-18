from core.telemetry_extractor import extraer_telemetria_visual

# Ruta relativa dentro del repo
PDF_PATH = "data/exports/report test.pdf"
DURACION = 480

print(f"Analizando PDF: {PDF_PATH}")
data = extraer_telemetria_visual(PDF_PATH, DURACION)

print("\nResultados de extracción:")
for k, v in data.items():
    if v is None:
        print(f" - {k}: no se pudo extraer datos")
    else:
        print(f" - {k}: {len(v)} puntos extraídos")
        print(f"   Ejemplo: {v[:5]}")