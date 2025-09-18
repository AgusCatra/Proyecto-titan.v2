import sys, os
from core.telemetry_extractor import extraer_telemetria_visual
from core.graph_mapper import map_graphs

if __name__ == "__main__":
    ruta = "data/exports/report test.pdf"  # cambia si querés otro
    duracion = 480  # segundos aprox

    print(f"Analizando {ruta} ...")
    raw = extraer_telemetria_visual(ruta, duracion)
    print("Claves crudas detectadas:", list(raw.keys()))

    mapped = map_graphs(raw)
    print("Mapeadas a:", list(mapped.keys()))

    for nombre, datos in mapped.items():
        if datos:
            print(f" - {nombre}: {len(datos)} puntos. Ejemplo: {datos[:5]}")
        else:
            print(f" - {nombre}: sin datos")
