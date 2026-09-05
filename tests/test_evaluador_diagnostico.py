# tests/test_evaluador_diagnostico.py
# Verificación del motor de evaluación pedagógica y del cálculo delta.

import json

from conftest import insertar_sesion_completa, insertar_telemetria, serie_plana

from core import evaluador_diagnostico as ev


# =============================================================================
# REGLAS PURAS (umbrales y porcentajes)
# =============================================================================
def test_delta_pct_cubre_division_por_cero():
    assert ev._delta_pct(0.0, 0.0) == 0.0
    assert ev._delta_pct(5.0, 0.0) is None          # porcentaje no definido
    assert ev._delta_pct(15.0, 10.0) == 50.0
    assert ev._delta_pct(5.0, 10.0) == -50.0
    assert ev._delta_pct(-5.0, -10.0) == 50.0       # denominador en valor absoluto


def test_veredicto_por_delta_puntaje_usa_las_constantes():
    assert ev.veredicto_por_delta_puntaje(ev.UMBRAL_MEJORA_SIGNIFICATIVA + 1) == \
        ev.VEREDICTO_MEJORA_SIGNIFICATIVA
    assert ev.veredicto_por_delta_puntaje(ev.UMBRAL_MEJORA_MODERADA + 0.5) == \
        ev.VEREDICTO_MEJORA_MODERADA
    assert ev.veredicto_por_delta_puntaje(ev.UMBRAL_REGRESION - 0.5) == \
        ev.VEREDICTO_REGRESION
    assert ev.veredicto_por_delta_puntaje(0.0) == ev.VEREDICTO_ESTANCADO


def test_nivel_riesgo_por_dictamen():
    assert ev.nivel_riesgo_por_dictamen(ev.DICTAMEN_NO_APTO_CRITICO) == ev.RIESGO_ALTO
    assert ev.nivel_riesgo_por_dictamen(ev.DICTAMEN_OBSERVADO) == ev.RIESGO_MEDIO
    assert ev.nivel_riesgo_por_dictamen(ev.DICTAMEN_APTO) == ev.RIESGO_BAJO
    assert ev.nivel_riesgo_por_dictamen("cualquier otra cosa") == ev.RIESGO_MEDIO


def test_puntaje_depurado_derivado():
    assert ev._puntaje_depurado_derivado(40.0, -10.0) == 30.0
    assert ev._puntaje_depurado_derivado(0, 0) == 0.0


# =============================================================================
# DIAGNÓSTICO DE ADMISIÓN
# =============================================================================
def test_sesion_inexistente_no_lanza_y_degrada_a_observado(conn):
    resultado = ev.evaluar_diagnostico_inicial(999, conn)
    assert resultado["sugerencia_admision"] == ev.DICTAMEN_OBSERVADO
    assert resultado["nivel_riesgo"] == ev.RIESGO_MEDIO
    assert resultado["metricas_calculadas"]["sesion_existente"] is False
    assert resultado["hallazgos"][0]["codigo"] == ev.HALLAZGO_SESION_INEXISTENTE
    assert resultado["series"] == {senal: [] for senal in ev.SENALES_CANONICAS}


def test_vicios_severos_generan_dictamen_critico(conn_semillada):
    resultado = ev.evaluar_diagnostico_inicial(1, conn_semillada)
    metricas = resultado["metricas_calculadas"]

    assert metricas["colisiones_netas"] == 2
    assert metricas["frenadas_bruscas"] == 6
    assert metricas["picos_freno_alto"] == 12
    assert metricas["volantazos"] == 8
    assert metricas["traslado_horquilla_alta_segundos"] > ev.TRASLADO_ALTO_SEGUNDOS_ALTO
    assert len(metricas["senales_disponibles"]) == len(ev.SENALES_CANONICAS)

    assert resultado["sugerencia_admision"] == ev.DICTAMEN_NO_APTO_CRITICO
    assert resultado["nivel_riesgo"] == ev.RIESGO_ALTO
    assert resultado["severidad_total"] >= ev.SEVERIDAD_NO_APTO

    codigos = {h["codigo"] for h in resultado["hallazgos"]}
    assert ev.HALLAZGO_COLISIONES in codigos
    assert ev.HALLAZGO_FRENADAS_MODERADO in codigos
    assert ev.HALLAZGO_VOLANTAZOS_MODERADO in codigos
    assert ev.HALLAZGO_TRASLADO_ALTO_SEVERO in codigos


def test_sesion_limpia_es_apto_sin_hallazgos(conn_semillada):
    resultado = ev.evaluar_diagnostico_inicial(2, conn_semillada)
    metricas = resultado["metricas_calculadas"]

    assert metricas["colisiones_netas"] == 0
    assert metricas["traslado_horquilla_alta_segundos"] == 0.0
    assert resultado["hallazgos"] == []
    assert resultado["sugerencia_admision"] == ev.DICTAMEN_APTO
    assert resultado["nivel_riesgo"] == ev.RIESGO_BAJO


def test_cobertura_insuficiente_nunca_emite_apto(conn):
    """Con menos señales de las críticas, la ausencia de vicios NO es buena conducción."""
    ev_id = insertar_sesion_completa(conn, 7, puntaje_final=99.0, colisiones=0)
    conn.execute("DELETE FROM Telemetria WHERE id_sesion_fk = ?", (ev_id["id_sesion"],))
    insertar_telemetria(conn, 7, "Speed In Km/h", serie_plana(4.0))
    insertar_telemetria(conn, 7, "Acceleration Pad", serie_plana(1.0))

    resultado = ev.evaluar_diagnostico_inicial(7, conn)
    assert len(resultado["metricas_calculadas"]["senales_disponibles"]) == 2
    assert resultado["sugerencia_admision"] == ev.DICTAMEN_OBSERVADO
    assert resultado["hallazgos"][0]["codigo"] == ev.HALLAZGO_COBERTURA_INSUFICIENTE
    assert "Telemetría insuficiente" in resultado["justificacion"]


def test_serie_en_cero_es_dato_legitimo(conn):
    """Un flatline en 0.0 cuenta como señal presente (regla de negocio)."""
    insertar_sesion_completa(conn, 8, frenadas=0, volantazos=0, velocidad=0.0)
    resultado = ev.evaluar_diagnostico_inicial(8, conn)
    assert len(resultado["metricas_calculadas"]["senales_disponibles"]) == 6


# =============================================================================
# COMPARATIVA DELTA (PRE / POST)
# =============================================================================
def test_delta_pre_post_calcula_valores_exactos(conn_semillada):
    resultado = ev.comparar_sesiones_delta(1, 2, conn_semillada)
    deltas = resultado["deltas"]

    assert resultado["pre"]["puntaje_depurado"] == 30.0
    assert resultado["post"]["puntaje_depurado"] == 68.0
    assert deltas["puntaje_depurado"] == 38.0
    assert deltas["puntaje_depurado_pct"] == 126.67
    assert deltas["colisiones"] == -2
    assert deltas["colisiones_pct"] == -100.0
    assert deltas["frenadas_bruscas"] == -4
    assert deltas["frenadas_bruscas_pct"] == -66.67
    assert deltas["volantazos"] == -6
    assert deltas["volantazos_pct"] == -75.0
    assert deltas["suavidad"] == -10
    assert deltas["suavidad_pct"] == -71.43
    assert deltas["duracion_seg"] == -20.0
    assert deltas["duracion_pct"] == -16.67

    assert resultado["veredicto_evolucion"] == ev.VEREDICTO_MEJORA_SIGNIFICATIVA
    assert resultado["retrocesos"] == []
    assert len(resultado["mejoras"]) == 6
    assert {m["metrica"] for m in resultado["mejoras"]} == {
        "Puntaje depurado", "Colisiones", "Frenadas bruscas",
        "Volantazos", "Suavidad (frenadas + volantazos)", "Duración (s)",
    }


def test_delta_devuelve_datos_nativos_serializables(conn_semillada):
    resultado = ev.comparar_sesiones_delta(1, 2, conn_semillada)
    assert "resumen_evolucion" not in resultado      # se retiró el texto de consola
    serializado = json.loads(json.dumps(resultado["deltas"]))
    assert serializado["puntaje_depurado"] == 38.0
    json.dumps(resultado["mejoras"], ensure_ascii=False)


def test_delta_regresion_se_clasifica_como_retroceso(conn_semillada):
    resultado = ev.comparar_sesiones_delta(2, 1, conn_semillada)   # Post -> Pre
    assert resultado["deltas"]["puntaje_depurado"] == -38.0
    assert resultado["veredicto_evolucion"] == ev.VEREDICTO_REGRESION
    assert resultado["mejoras"] == []
    assert len(resultado["retrocesos"]) == 6


def test_delta_con_puntajes_cero_no_divide_por_cero(conn):
    insertar_sesion_completa(conn, 3, puntaje_final=0.0, penalizaciones=0.0)
    insertar_sesion_completa(conn, 4, puntaje_final=0.0, penalizaciones=0.0)
    resultado = ev.comparar_sesiones_delta(3, 4, conn)
    assert "error" not in resultado
    assert resultado["deltas"]["puntaje_depurado"] == 0.0
    assert resultado["deltas"]["puntaje_depurado_pct"] == 0.0
    assert resultado["veredicto_evolucion"] == ev.VEREDICTO_ESTANCADO
    assert resultado["mejoras"] == [] and resultado["retrocesos"] == []


def test_delta_con_sesion_faltante_devuelve_sin_datos(conn_semillada):
    resultado = ev.comparar_sesiones_delta(1, 999, conn_semillada)
    assert resultado["error"]
    assert resultado["sesiones_faltantes"] == ["999"]
    assert resultado["veredicto_evolucion"] == ev.VEREDICTO_SIN_DATOS
    assert resultado["mejoras"] == [] and resultado["retrocesos"] == []
    assert resultado["deltas"] == ev._deltas_cero()


# =============================================================================
# PERSISTENCIA DE LA DECISIÓN DEL INSTRUCTOR
# =============================================================================
def test_decision_instructor_upsert_mantiene_una_fila(conn_semillada):
    assert ev.obtener_decision_instructor(1, conn_semillada) is None

    assert ev.guardar_decision_instructor(
        1, "Observado", "Primera nota", conn_semillada, foco_sugerido="Suavidad"
    )
    guardada = ev.obtener_decision_instructor(1, conn_semillada)
    assert guardada["veredicto"] == "Observado"
    assert guardada["notas"] == "Primera nota"
    assert guardada["foco_sugerido"] == "Suavidad"

    assert ev.guardar_decision_instructor(1, "Apto", "Nota corregida", conn_semillada)
    actualizada = ev.obtener_decision_instructor(1, conn_semillada)
    assert actualizada["veredicto"] == "Apto"
    assert actualizada["notas"] == "Nota corregida"

    filas = conn_semillada.execute(
        "SELECT COUNT(*) FROM DecisionInstructor WHERE id_sesion = 1"
    ).fetchone()[0]
    assert filas == 1


def test_obtener_decision_sin_tabla_no_escribe_nada(conn):
    """Lectura degradada: si la tabla no existe, devuelve None sin crearla."""
    conn.execute("DROP TABLE IF EXISTS DecisionInstructor")
    assert ev.obtener_decision_instructor(1, conn) is None
    tablas = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "DecisionInstructor" not in tablas


# =============================================================================
# DATOS PARA DOCUMENTOS Y PAYLOAD LLM
# =============================================================================
def test_datos_documento_sesion_sanea_nulos(conn):
    conn.execute(
        "INSERT INTO Sesiones (id_sesion, nombre_archivo_origen, nombre_operador) "
        "VALUES (5, 'sin_datos.pdf', NULL)"
    )
    conn.commit()
    datos = ev.obtener_datos_documento_sesion(5, conn)
    assert datos["existe"] is True
    assert datos["nombre_operador"] == "N/A"
    assert datos["perfil_operador"] == "N/A"
    assert datos["puntaje_final"] == 0.0
    assert datos["penalizaciones_totales"] == 0.0
    assert datos["puntaje_depurado"] == 0.0


def test_datos_documento_sesion_inexistente(conn):
    datos = ev.obtener_datos_documento_sesion(404, conn)
    assert datos["existe"] is False
    assert datos["nombre_operador"] == "N/A"


def test_datos_evolucion_incluye_comparativa(conn_semillada):
    datos = ev.obtener_datos_evolucion(1, 2, conn_semillada)
    assert set(datos) == {"datos_iniciales", "datos_finales", "comparativa"}
    assert datos["datos_iniciales"]["puntaje_depurado"] == 30.0
    assert datos["datos_finales"]["puntaje_depurado"] == 68.0
    assert datos["comparativa"]["veredicto_evolucion"] == ev.VEREDICTO_MEJORA_SIGNIFICATIVA


def test_payload_llm_es_json_serializable(conn_semillada):
    payload = ev.obtener_payload_para_llm(1, conn_semillada, 2)
    assert set(payload) == {
        "metadatos", "fisicas_clave", "metricas", "diagnostico", "evolucion"
    }
    texto = json.dumps(payload, ensure_ascii=False)   # sin ``default``: contrato JSON
    assert "MEJORA SIGNIFICATIVA" in texto
    assert payload["fisicas_clave"]["frenadas_bruscas_post"] == 2
    assert payload["diagnostico"]["hallazgos_pre"]


def test_payload_llm_sin_sesion_post_omite_evolucion(conn_semillada):
    payload = ev.obtener_payload_para_llm(1, conn_semillada)
    assert "evolucion" not in payload
    assert "advertencia" not in payload
    assert payload["metadatos"]["session_id_post"] is None
    json.dumps(payload, ensure_ascii=False)


def test_payload_llm_advierte_sesion_post_inexistente(conn_semillada):
    payload = ev.obtener_payload_para_llm(1, conn_semillada, 999)
    assert "advertencia" in payload
    assert payload["evolucion"]["veredicto"] == ev.VEREDICTO_SIN_DATOS
