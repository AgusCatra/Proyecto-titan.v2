import json
import os
from typing import Dict, List, Tuple, Optional

MAP_PATH = os.path.join(os.path.dirname(__file__), "..", "mapeo.json")

def load_mapping() -> List[Dict]:
    if not os.path.exists(MAP_PATH):
        return []
    with open(MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def map_graphs(raw_graphs: Dict[str, Optional[List[Tuple[float, float]]]]) -> Dict[str, Optional[List[Tuple[float, float]]]]:
    mapping = load_mapping()
    result = {}
    print("=== DEBUG map_graphs ===")
    print("Raw keys:", list(raw_graphs.keys()))
    for g in mapping:
        gkey = g["graph"]
        print(f"Buscando '{gkey}' en raw_graphs...")
        if gkey in raw_graphs:
            datos = raw_graphs[gkey]
            if datos:
                print(f" -> ENCONTRADO: {gkey} → {g['suggested_name']} ({len(datos)} puntos)")
                result[g["suggested_name"]] = datos
            else:
                print(f" -> {gkey} existe pero está vacío")
        else:
            print(f" -> NO encontrado: {gkey}")
    print("========================")
    return result
