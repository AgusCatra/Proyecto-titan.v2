# test_extractor.py
from core.telemetry_extractor import extraer_telemetria_visual

# Ruta fija al PDF de prueba
ruta = "/home/agustin/Documentos/backup proyecto/backupo GIT?/Proyecto-titan/data/exports/report test.pdf"

# Duración total en segundos (cambia el número si conocés el valor real del reporte)
duracion = 300  

print(f"Analizando PDF: {ruta}")
data = extraer_telemetria_visual(ruta, duracion)

print("\nResultados de extracción:")
for grafico, serie in data.items():
    if serie is None:
        print(f" - {grafico}: no se pudo extraer datos")
    else:
        print(f" - {grafico}: {len(serie)} puntos extraídos")
        print(f"   Ejemplo: {serie[:5]}")
