import pdfplumber
import re
import matplotlib.pyplot as plt
from collections import defaultdict

def parse_time_to_seconds(time_str: str) -> float:
    """Convierte 00:MM:SS a segundos."""
    h, m, s = time_str.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)

def safe_filename(name: str) -> str:
    """Convierte nombre de métrica en un nombre seguro de archivo."""
    return re.sub(r'[^A-Za-z0-9_]+', '_', name.strip())

def extraer_eventos_texto_plumber(pdf_path: str):
    eventos = defaultdict(list)

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text and "Student Console Events" in text:
                for line in text.splitlines():
                    # ejemplo esperado: "00:07:03 Fork Tilt 10"
                    match = re.match(r"(\d{2}:\d{2}:\d{2})\s+(.+?)\s+(-?\d+(?:\.\d+)?)$", line.strip())
                    if match:
                        t_str, metric, val = match.groups()
                        t_sec = parse_time_to_seconds(t_str)
                        eventos[metric.strip()].append((t_sec, float(val)))
    return eventos

if __name__ == "__main__":
    pdf = "data/reports/report test.pdf"  # ajusta si tu PDF está en otro lugar
    data = extraer_eventos_texto_plumber(pdf)

    if not data:
        print("❌ No se encontraron eventos en el PDF.")
    else:
        for metric, series in data.items():
            series = sorted(series)
            t, v = zip(*series)

            safe_name = safe_filename(metric)

            # Guardar CSV
            csv_file = f"debug_{safe_name}.csv"
            with open(csv_file, "w") as f:
                f.write("t,v\n")
                for ts, val in series:
                    f.write(f"{ts},{val}\n")

            # Guardar PNG
            plt.figure(figsize=(8, 3))
            plt.plot(t, v, marker="o", markersize=2, linewidth=1)
            plt.title(metric)
            plt.xlabel("Tiempo (s)")
            plt.ylabel("Valor")
            plt.grid(True, linestyle="--", alpha=0.5)
            plt.tight_layout()
            png_file = f"debug_{safe_name}.png"
            plt.savefig(png_file)
            plt.close()

            print(f"✅ {metric} → {csv_file}, {png_file}")

        print("\n🏁 Extracción completada con pdfplumber.")
