# core/pdf_parser.py
# Versión: 23.0 - Corregida la extracción de "Class Name"

import pdfplumber
import os
import re
from datetime import datetime
import locale
from typing import Dict, List, Optional

# Constantes para mejor mantenimiento
REGEX_PATTERNS = {
    'nombre_operador': re.compile(r"Student Name\s+(.+)"),
    # --- REGEX CORREGIDO Y AÑADIDO ---
    # Busca "Class name" (ignorando mayúsculas/minúsculas) y captura el resto de la línea.
    'nombre_clase': re.compile(r"Class name\s+(.+)", re.IGNORECASE),
    'nombre_ejercicio': re.compile(r"Exercise Name\s+([\w\s]+?)\n"),
    'duracion_segundos': re.compile(r"Exercise Duration\s+([\d:]+)"),
    'puntaje_final': re.compile(r"Score\s+([\d.-]+)"),
    'fecha_hora_inicio': re.compile(r"Exercise Start Time\s+([\d\w\s,:]+\s+[ap]m)"),
    'consolidated_results': re.compile(
        r"^\s*([A-Za-z].*?)\s+(\d+)\s+([\d.-]+)\s+([\d.-]+)\s*$",
        re.MULTILINE
    )
}

def parse_pdf_report(pdf_path: str) -> Optional[Dict]:
    """
    Extrae datos de un reporte PDF, incluyendo nombre de clase.
    """
    print(f"  - Ejecutando parser v23.0...")
    
    try:
        _setup_locale()
        
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return None
                
            page1 = pdf.pages[0]
            text_page1 = page1.extract_text(x_tolerance=2) or ""

            session_data = _extract_session_data(text_page1, pdf_path)
            summary_events = _extract_summary_events(text_page1)
            
            if not session_data or not session_data.get('nombre_operador'):
                print("  - ⚠️  No se pudieron extraer datos de sesión válidos.")
                return None

            return {
                "session_data": session_data, 
                "summary_events": summary_events
            }

    except Exception as e:
        print(f"  - ERROR CRÍTICO: Falla irrecuperable al procesar el archivo {pdf_path}. Error: {e}")
        return None

def _setup_locale() -> None:
    """Configura el locale para parsing de fechas."""
    try:
        locale.setlocale(locale.LC_TIME, 'en_US.UTF-8')
    except locale.Error:
        pass

def _extract_session_data(text: str, pdf_path: str) -> Optional[Dict]:
    """Extrae los datos de sesión del texto del PDF."""
    print("  - Reconstruyendo datos de sesión desde la página 1...")
    
    raw_data = {}
    for key, pattern in REGEX_PATTERNS.items():
        if key == 'consolidated_results':
            continue
        match = pattern.search(text)
        # Usamos .splitlines()[0] para evitar capturar texto de la siguiente línea
        raw_data[key] = match.group(1).strip().splitlines()[0] if match else None
    
    if not raw_data.get('nombre_operador'):
        return None
    
    # --- LÓGICA DE ASIGNACIÓN ACTUALIZADA ---
    session_data = {
        'nombre_operador': raw_data.get('nombre_operador'),
        'nombre_clase': raw_data.get('nombre_clase'), # Ahora debería encontrar el valor
        'nombre_ejercicio': raw_data.get('nombre_ejercicio'),
        'nombre_archivo_origen': os.path.basename(pdf_path),
        'puntaje_final': _parse_float(raw_data.get('puntaje_final', '0.0')),
        'fecha_hora_inicio': _parse_datetime(raw_data.get('fecha_hora_inicio')),
        'duracion_segundos': _parse_duration(raw_data.get('duracion_segundos'))
    }
    
    return session_data

def _extract_summary_events(text: str) -> List[Dict]:
    """Extrae los eventos de resumen de la tabla Consolidated Results."""
    matches = REGEX_PATTERNS['consolidated_results'].finditer(text)
    headers = ['type', 'total_events', 'rewards', 'penalties']
    
    summary_events = [dict(zip(headers, match.groups())) for match in matches]
    
    return summary_events

def _parse_float(value: str) -> float:
    try:
        return float(value) if value else 0.0
    except (ValueError, TypeError):
        return 0.0

def _parse_datetime(date_str: str) -> Optional[str]:
    if not date_str: return None
    try:
        clean_date_str = date_str.replace(" at ", " ")
        date_obj = datetime.strptime(clean_date_str, "%d %B %Y %I:%M %p")
        return date_obj.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None

def _parse_duration(duration_str: str) -> Optional[int]:
    if not duration_str: return None
    try:
        h, m, s = map(int, duration_str.split(':'))
        return h * 3600 + m * 60 + s
    except (ValueError, IndexError, AttributeError):
        return None