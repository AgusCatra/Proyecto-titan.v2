# tests/test_ai_advisor.py
# Verificación del asesor pedagógico LLM-ready (ruta local determinista).

import json

import pytest
from conftest import insertar_sesion_completa

from core import ai_advisor as advisor
from core.evaluador_diagnostico import (
    RIESGO_ALTO,
    RIESGO_BAJO,
    RIESGO_MEDIO,
    comparar_sesiones_delta,
    evaluar_diagnostico_inicial,
    obtener_payload_para_llm,
)

CLAVES_CONTRATO = {
    "provider", "modelo", "generado_por_llm", "diagnostico", "vicios_operativos",
    "plan_de_accion", "foco_prioritario", "nivel_riesgo", "metricas_interpretadas",
    "advertencias",
}


# =============================================================================
# CONTRATO DE SALIDA
# =============================================================================
def test_devolucion_mock_cumple_el_contrato(conn_semillada):
    payload = obtener_payload_para_llm(1, conn_semillada, 2)
    resultado = advisor.generar_devolucion_pedagogica(payload)

    assert set(resultado) == CLAVES_CONTRATO
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert resultado["generado_por_llm"] is False
    assert isinstance(resultado["diagnostico"], str) and resultado["diagnostico"]
    assert all(isinstance(v, str) and v for v in resultado["vicios_operativos"])
    assert all(isinstance(a, str) and a for a in resultado["plan_de_accion"])
    assert resultado["nivel_riesgo"] in (RIESGO_ALTO, RIESGO_MEDIO, RIESGO_BAJO)
    # Salida JSON-serializable de punta a punta (contrato para una API REST).
    json.dumps(resultado, ensure_ascii=False)


def test_devolucion_mock_es_determinista(conn_semillada):
    payload = obtener_payload_para_llm(1, conn_semillada, 2)
    primera = advisor.generar_devolucion_pedagogica(payload)
    segunda = advisor.generar_devolucion_pedagogica(payload)
    assert primera["diagnostico"] == segunda["diagnostico"]
    assert primera["vicios_operativos"] == segunda["vicios_operativos"]
    assert primera["plan_de_accion"] == segunda["plan_de_accion"]


def test_devolucion_mock_no_requiere_api_key(conn_semillada, monkeypatch):
    monkeypatch.delenv(advisor.ENV_API_KEY, raising=False)
    payload = obtener_payload_para_llm(1, conn_semillada, 2)
    resultado = advisor.generar_devolucion_pedagogica(payload)
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert resultado["advertencias"] == []


# =============================================================================
# INTERPRETACIÓN DE LAS MÉTRICAS FÍSICAS
# =============================================================================
def test_mock_interpreta_volantazos_frenadas_colisiones_y_horquilla_alta(conn_semillada):
    """Sesión severa como referencia: narra cada vicio con su evidencia numérica."""
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada)
    )
    narracion = resultado["diagnostico"] + " ".join(resultado["vicios_operativos"])
    assert "Alumno de Prueba" in resultado["diagnostico"]
    assert resultado["nivel_riesgo"] == RIESGO_ALTO
    assert "2" in narracion                    # colisiones netas
    assert "6" in narracion                    # frenadas bruscas

    vicios = " ".join(resultado["vicios_operativos"]).lower()
    assert "anticipaci" in vicios              # frenada sin anticipación
    assert "volante" in vicios or "direcci" in vicios
    assert "horquilla" in vicios               # vicio normativo crítico

    plan = " ".join(resultado["plan_de_accion"]).lower()
    assert "horquilla" in plan


def test_mock_referencia_la_sesion_mas_reciente_en_comparativas(conn_semillada):
    """Con Pre/Post, el diagnóstico describe el estado ACTUAL (sesión Post)."""
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2)
    )
    assert "MEJORA SIGNIFICATIVA" in resultado["diagnostico"]
    assert resultado["nivel_riesgo"] == RIESGO_BAJO      # la sesión final está limpia
    assert any("no se detectaron vicios" in v.lower()
               for v in resultado["vicios_operativos"])
    assert any("consolidar" in a.lower() for a in resultado["plan_de_accion"])


def test_mock_narra_la_regresion_cuando_post_empeora(conn_semillada):
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(2, conn_semillada, 1)
    )
    assert resultado["nivel_riesgo"] == RIESGO_ALTO
    assert "REGRESIÓN DETECTADA" in resultado["diagnostico"]
    assert any("regresión" in a.lower() or "prisa" in a.lower()
               for a in resultado["plan_de_accion"])


def test_mock_para_sesion_limpia_no_inventa_vicios(conn_semillada):
    payload = obtener_payload_para_llm(2, conn_semillada)
    resultado = advisor.generar_devolucion_pedagogica(payload)
    assert resultado["nivel_riesgo"] == RIESGO_BAJO
    assert any("no se detectaron vicios" in v.lower() for v in resultado["vicios_operativos"])
    assert any("afianzar" in a.lower() for a in resultado["plan_de_accion"])


def test_mock_advierte_cobertura_insuficiente(conn):
    insertar_sesion_completa(conn, 20, puntaje_final=80.0)
    conn.execute("DELETE FROM Telemetria WHERE id_sesion_fk = 20")
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(20, conn)
    )
    assert "provisional" in resultado["diagnostico"].lower()
    assert any("provisional" in v.lower() or "ausentes" in v.lower()
               for v in resultado["vicios_operativos"])


def test_mock_acepta_metricas_planas_de_una_sesion(conn_semillada):
    """Tolerancia: también acepta ``metricas_calculadas`` sin el payload completo."""
    metricas = evaluar_diagnostico_inicial(1, conn_semillada)["metricas_calculadas"]
    resultado = advisor.generar_devolucion_pedagogica(metricas)
    assert set(resultado) == CLAVES_CONTRATO
    assert resultado["metricas_interpretadas"]["metricas"]["colisiones_netas"] == 2


def test_mock_acepta_la_comparativa_delta(conn_semillada):
    """Tolerancia: también acepta la salida de ``comparar_sesiones_delta``."""
    comparativa = comparar_sesiones_delta(1, 2, conn_semillada)
    resultado = advisor.generar_devolucion_pedagogica(comparativa)
    assert resultado["metricas_interpretadas"]["evolucion"]["veredicto"] == \
        "MEJORA SIGNIFICATIVA"
    assert "MEJORA SIGNIFICATIVA" in resultado["diagnostico"]


def test_payload_vacio_devuelve_respuesta_generica_con_advertencia():
    resultado = advisor.generar_devolucion_pedagogica({})
    assert set(resultado) == CLAVES_CONTRATO
    assert resultado["advertencias"]
    assert resultado["nivel_riesgo"] == RIESGO_MEDIO


# =============================================================================
# PROVEEDORES REMOTOS (nunca se llama a la red en la suite)
# =============================================================================
def test_proveedor_desconocido_degrada_a_mock(monkeypatch):
    monkeypatch.delenv(advisor.ENV_API_KEY, raising=False)
    resultado = advisor.generar_devolucion_pedagogica({}, provider="gemini")
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert any("no soportado" in a for a in resultado["advertencias"])


def test_proveedor_remoto_sin_api_key_degrada_a_mock(monkeypatch):
    monkeypatch.delenv(advisor.ENV_API_KEY, raising=False)
    resultado = advisor.generar_devolucion_pedagogica({}, provider=advisor.PROVIDER_OPENAI)
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert resultado["generado_por_llm"] is False
    assert any(advisor.ENV_API_KEY in a for a in resultado["advertencias"])


def test_fallo_del_proveedor_remoto_degrada_a_mock(conn_semillada, monkeypatch):
    """Un error de red/auth/cuota NO rompe la app: se usa la devolución local."""
    def explota(*args, **kwargs):
        raise RuntimeError("sin conexión de prueba")

    monkeypatch.setattr(advisor, "_llamar_openai", explota)
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2),
        api_key="clave-de-prueba",
        provider=advisor.PROVIDER_OPENAI,
    )
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert resultado["generado_por_llm"] is False
    assert any("falló" in a for a in resultado["advertencias"])


def test_respuesta_llm_se_parsea_y_valida_contra_el_contrato(conn_semillada, monkeypatch):
    respuesta_llm = json.dumps({
        "diagnostico": "Diagnóstico redactado por el modelo.",
        "vicios_operativos": ["Vicio 1", "Vicio 2"],
        "plan_de_accion": ["Acción 1", "Acción 2", "Acción 3"],
        "foco_prioritario": "Prioridad única.",
        "nivel_riesgo": "alto",
    }, ensure_ascii=False)

    monkeypatch.setattr(advisor, "_llamar_openai", lambda *a, **k: respuesta_llm)
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2),
        api_key="clave-de-prueba",
        provider=advisor.PROVIDER_OPENAI,
    )
    assert resultado["generado_por_llm"] is True
    assert resultado["provider"] == advisor.PROVIDER_OPENAI
    assert resultado["diagnostico"] == "Diagnóstico redactado por el modelo."
    assert resultado["vicios_operativos"] == ["Vicio 1", "Vicio 2"]
    assert resultado["nivel_riesgo"] == RIESGO_ALTO


def test_riesgo_fuera_de_contrato_se_sustituye_por_el_del_motor(conn_semillada, monkeypatch):
    respuesta_llm = "```json\n" + json.dumps({
        "diagnostico": "Texto.",
        "vicios_operativos": ["Vicio"],
        "plan_de_accion": ["Acción"],
        "foco_prioritario": "Foco",
        "nivel_riesgo": "CRITICISIMO",
    }, ensure_ascii=False) + "\n```"

    monkeypatch.setattr(advisor, "_llamar_openai", lambda *a, **k: respuesta_llm)
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(2, conn_semillada, 1),
        api_key="clave-de-prueba",
        provider=advisor.PROVIDER_OPENAI,
    )
    assert resultado["nivel_riesgo"] == RIESGO_ALTO     # impuesto por el motor
    assert any("fuera de contrato" in a for a in resultado["advertencias"])


@pytest.mark.parametrize("texto", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    'Aquí está:\n{"a": 1}\nEspero que sirva.',
])
def test_extraer_json_tolerante(texto):
    assert advisor._extraer_json(texto) == {"a": 1}


def test_extraer_json_rechaza_respuestas_invalidas():
    with pytest.raises(ValueError):
        advisor._extraer_json("")
    with pytest.raises(ValueError):
        advisor._extraer_json("sin json aquí")


def test_system_prompt_del_instructor_esta_calibrado():
    prompt = advisor.SYSTEM_PROMPT_INSTRUCTOR
    for termino in (
        "instructor", "volantazos", "frenadas", "colisiones", "horquilla",
        "delta", "JSON", "No inventes",
    ):
        assert termino.lower() in prompt.lower()
