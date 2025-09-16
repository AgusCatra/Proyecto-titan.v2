import fitz  # PyMuPDF
import re
import matplotlib.pyplot as plt
from collections import defaultdict

def parse_time_to_seconds(time_str: str) -> float:
    h, m, s = time_str.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)

def extraer_eventos_texto(pdf_path: str):
    eventos = defaultdict(list)
    with fitz.open(pdf_path) as doc:
        for page in doc:
            text = page.get_text("text")
            if "Student Console Events" in text:
                for line in text.splitlines():
                    if re.match(r"^\d{2}:\d{2}:\d{2}", line.strip()):
                        print("LINE DEBUG:", repr(line))
                        match = re.match(r"(\d{2}:\d{2}:\d{2})\s+([A-Za-z/ ]+?)\s+(-?\d+(?:\.\d+)?)$", line.strip())
                    if match:
                        t_str, metric, val = match.group(1), match.group(2), match.group(3)
                        t_sec = parse_time_to_seconds(t_str)
                        eventos[metric.strip()].append((t_sec, float(val)))

    return eventos

if __name__ == "__main__":
    pdf = "data/reports/report test.pdf"   # usa tu ruta real
    data = extraer_eventos_texto(pdf)

    if not data:
        print("❌ No se encontraron eventos en el PDF")
    else:
        for metric, series in data.items():
            series = sorted(series)
            t, v = zip(*series)

            # CSV
            fname = f"debug_{metric.replace(' ', '_')}.csv"
            with open(fname, "w") as f:
                f.write("t,v\n")
                for ts, val in series:
                    f.write(f"{ts},{val}\n")

            # PNG
            plt.figure(figsize=(8,3))
            plt.plot(t, v, marker="o", markersize=2, linewidth=1)
            plt.title(metric)
            plt.xlabel("Tiempo (s)")
            plt.ylabel("Valor")
            plt.grid(True, linestyle="--", alpha=0.5)
            plt.tight_layout()
            plt.savefig(f"debug_{metric.replace(' ', '_')}.png")
            plt.close()

        print("✅ Eventos extraídos. CSV y PNG guardados en el directorio actual.")
