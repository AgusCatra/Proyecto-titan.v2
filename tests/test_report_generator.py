# tests/test_report_generator.py
# Verificación de la generación de documentos (PDF) y de su saneado Latin-1.

import io

from core import report_generator as rg
from core.ai_advisor import generar_devolucion_pedagogica
from core.evaluador_diagnostico import obtener_payload_para_llm


def _devolucion_de_prueba(conn_semillada):
    payload = obtener_payload_para_llm(1, conn_semillada, 2)
    return generar_devolucion_pedagogica(payload)


# =============================================================================
# SANEADO DE TEXTO (las fuentes Type1 de FPDF solo soportan Latin-1)
# =============================================================================
def test_txt_elimina_caracteres_fuera_de_latin1():
    saneado = rg._txt("Diagnóstico ✅ con emoji — raya – y flecha →")
    assert saneado.encode("latin-1")           # no lanza UnicodeEncodeError
    assert "✅" not in saneado
    assert "—" not in saneado and "-" in saneado
    assert "->" in saneado


def test_txt_y_num_toleran_none_y_basura():
    assert rg._txt(None) == "N/A"
    assert rg._txt("   ") == "N/A"
    assert rg._txt(12.5) == "12.5"
    assert rg._num(None) == 0.0
    assert rg._num("no soy número") == 0.0
    assert rg._num("3.5") == 3.5


# =============================================================================
# PDF DE EVOLUCIÓN
# =============================================================================
def test_crear_reporte_evolucion_pdf_genera_bytes_validos(conn_semillada):
    from core.evaluador_diagnostico import obtener_datos_evolucion

    datos = obtener_datos_evolucion(1, 2, conn_semillada)
    buffer = io.BytesIO()
    rg.crear_reporte_evolucion_pdf(
        datos["datos_iniciales"], datos["datos_finales"], buffer
    )
    contenido = buffer.getvalue()
    assert contenido.startswith(b"%PDF")
    assert len(contenido) > 800


def test_crear_reporte_evolucion_pdf_con_devolucion_no_rompe_por_unicode(conn_semillada):
    """La devolución de un LLM puede traer emoji y tipografía exótica."""
    from core.evaluador_diagnostico import obtener_datos_evolucion

    devolucion = _devolucion_de_prueba(conn_semillada)
    devolucion["resumen_ejecutivo"] = "⚠️ Resumen con emoji — y comillas “curvas” ✅"
    devolucion["puntos_fuertes"] = ["🎯 Fortaleza 1 → criterio", "Fortaleza 2 …"]
    devolucion["vicios_criticos"] = ["▼ Vicio crítico 1"]
    devolucion["plan_accion_recomendado"] = ["🎯 Módulo 1 → criterio", "Módulo 2 …"]

    datos = obtener_datos_evolucion(1, 2, conn_semillada)
    buffer = io.BytesIO()
    rg.crear_reporte_evolucion_pdf(
        datos["datos_iniciales"], datos["datos_finales"], buffer, devolucion=devolucion
    )
    assert buffer.getvalue().startswith(b"%PDF")


def test_crear_reporte_evolucion_pdf_tolerante_a_datos_vacios(tmp_path):
    ruta = tmp_path / "evolucion_vacia.pdf"
    rg.crear_reporte_evolucion_pdf({}, {}, str(ruta))
    assert ruta.exists() and ruta.stat().st_size > 0


# =============================================================================
# M4 — TEXTO LARGO QUE DESBORDA EL ANCHO ÚTIL DE LA PÁGINA (~190 mm)
# =============================================================================
def test_foco_prioritario_largo_no_desborda_y_genera_pdf_valido(conn_semillada):
    """M4: ``foco_prioritario`` kilométrico se envuelve (multi_cell) sin reventar."""
    from core.evaluador_diagnostico import obtener_datos_evolucion

    devolucion = _devolucion_de_prueba(conn_semillada)
    devolucion["foco_prioritario"] = (
        "Consolidar la anticipación en cruces ciegos, reducir la velocidad de "
        "traslado con horquilla elevada y eliminar los microajustes de torre en "
        "apilados de precisión " * 6
    )
    devolucion["provider"] = "openrouter"
    devolucion["modelo"] = "proveedor/modelo-de-nombre-desmesuradamente-largo " * 8

    datos = obtener_datos_evolucion(1, 2, conn_semillada)
    buffer = io.BytesIO()
    rg.crear_reporte_evolucion_pdf(
        datos["datos_iniciales"], datos["datos_finales"], buffer, devolucion=devolucion
    )
    contenido = buffer.getvalue()
    assert contenido.startswith(b"%PDF")
    assert len(contenido) > 800


def test_celda_de_ancho_completo_envuelve_cuando_no_cabe_en_la_pagina():
    """M4: ``_celda`` delega en ``_parrafo`` solo si el texto excede el ancho útil."""
    pdf = rg.PDF()
    pdf.add_page()
    pdf.set_font(rg.FUENTE_PDF, "", 11)

    texto_largo = "Texto que se desbordaría del papel " * 20
    assert pdf.get_string_width(rg._txt(texto_largo)) > rg._ancho_util(pdf)
    rg._celda(pdf, texto_largo)          # no lanza ni desborda: envuelve

    # Las celdas de tabla (ancho explícito) conservan su geometría: el cursor
    # queda a la derecha de la celda, no en el margen izquierdo.
    rg._celda(pdf, "Celda", alto=8, ancho=40, borde=1, salto=False)
    assert pdf.get_x() > pdf.l_margin

    buffer = io.BytesIO()
    pdf.output(buffer)
    assert buffer.getvalue().startswith(b"%PDF")


def test_generar_pdf_evolucion_devuelve_bytes_desde_ids_de_bd(conn_semillada):
    contenido = rg.generar_pdf_evolucion(
        1, 2, conn_semillada, devolucion=_devolucion_de_prueba(conn_semillada)
    )
    assert isinstance(contenido, bytes)
    assert contenido.startswith(b"%PDF")


def test_generar_pdf_evolucion_con_sesion_inexistente_no_lanza(conn_semillada):
    contenido = rg.generar_pdf_evolucion(1, 999, conn_semillada)
    assert contenido.startswith(b"%PDF")


# =============================================================================
# PDF DE DIAGNÓSTICO INDIVIDUAL
# =============================================================================
def test_generar_pdf_diagnostico_desde_id_de_sesion(conn_semillada):
    contenido = rg.generar_pdf_diagnostico(
        1, conn_semillada, devolucion=_devolucion_de_prueba(conn_semillada)
    )
    assert isinstance(contenido, bytes)
    assert contenido.startswith(b"%PDF")


def test_crear_reporte_pdf_mantiene_el_contrato_legacy(tmp_path):
    """``app.py`` sigue llamando a ``crear_reporte_pdf(datos_analisis, ruta)``."""
    datos_analisis = {
        "info_sesion": {
            "nombre_operador": "Operario Legacy",
            "nombre_clase": "Clase B",
            "puntaje_final": 42.5,
        },
        "perfil_predicho": "apurado",
        "ruta_recomendada": {"titulo": "Ruta X", "ejercicios": ["1.1", "2.2"]},
        "analisis_comportamiento": {
            "frenadas_bruscas": 7,
            "volantazos": 3,
            "aceleraciones_bruscas": 2,
            "ajustes_elevacion": 15,
            "metricas_velocidad": {"velocidad_maxima": 9.8, "velocidad_media": 4.1},
        },
    }
    ruta = tmp_path / "individual.pdf"
    rg.crear_reporte_pdf(datos_analisis, str(ruta))
    assert ruta.exists() and ruta.read_bytes().startswith(b"%PDF")


def test_crear_reporte_pdf_omite_metricas_ausentes(tmp_path):
    """No se imprimen ceros inventados para métricas que el origen no calcula."""
    datos_analisis = {
        "info_sesion": {"nombre_operador": "Sin telemetría"},
        "perfil_predicho": None,
        "analisis_comportamiento": {"frenadas_bruscas": 4},
    }
    ruta = tmp_path / "parcial.pdf"
    rg.crear_reporte_pdf(datos_analisis, str(ruta))
    assert ruta.exists() and ruta.read_bytes().startswith(b"%PDF")


def test_pdf_output_acepta_buffer_y_ruta(conn_semillada, tmp_path):
    from core.evaluador_diagnostico import obtener_datos_documento_sesion

    inicial = obtener_datos_documento_sesion(1, conn_semillada)
    final = obtener_datos_documento_sesion(2, conn_semillada)

    buffer = io.BytesIO()
    rg.crear_reporte_evolucion_pdf(inicial, final, buffer)
    assert len(buffer.getvalue()) > 0

    ruta = tmp_path / "desde_ruta.pdf"
    rg.crear_reporte_evolucion_pdf(inicial, final, str(ruta))
    assert ruta.stat().st_size > 0
