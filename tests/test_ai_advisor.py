# tests/test_ai_advisor.py
# Verificación del asesor pedagógico LLM-ready (ruta local determinista + routing
# de proveedores OpenAI-compatible y degradación elegante ante fallos).

import json
import sys
import types

import pytest
import requests
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

# Contrato canónico de 11 claves (fuente única de verdad compartida con la UI).
CLAVES_CONTRATO = {
    # Narrativas (LLM; derivadas del motor local en fallback):
    "resumen_ejecutivo", "puntos_fuertes", "vicios_criticos", "plan_accion_recomendado",
    # Autoritativas del motor:
    "nivel_riesgo", "foco_prioritario",
    # Meta:
    "provider", "modelo", "generado_por_llm", "metricas_interpretadas", "advertencias",
}

# Respuesta LLM mínima y conforme al contrato (solo las 4 claves narrativas).
_RESPUESTA_LLM_VALIDA = json.dumps({
    "resumen_ejecutivo": "Diagnóstico sintético redactado por el modelo.",
    "puntos_fuertes": ["Fortaleza 1", "Fortaleza 2"],
    "vicios_criticos": ["Vicio crítico 1"],
    "plan_accion_recomendado": ["Módulo de simulador 1", "Módulo 2"],
}, ensure_ascii=False)


def _instalar_requests_falso(monkeypatch, capturado: dict, contenido: str) -> None:
    """Sustituye ``requests`` en ``sys.modules`` por un doble que captura la llamada.

    El caller del asesor hace ``import requests`` de forma perezosa, por lo que
    resuelve el doble instalado aquí y permite inspeccionar URL/headers/json.
    """
    modulo = types.ModuleType("requests")

    class _RespuestaFalsa:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": contenido}}]}

    def post(url, headers=None, json=None, timeout=None):
        capturado["url"] = url
        capturado["headers"] = headers
        capturado["json"] = json
        capturado["timeout"] = timeout
        return _RespuestaFalsa()

    modulo.post = post
    monkeypatch.setitem(sys.modules, "requests", modulo)


# =============================================================================
# CONTRATO DE SALIDA
# =============================================================================
def test_devolucion_mock_cumple_el_contrato(conn_semillada):
    payload = obtener_payload_para_llm(1, conn_semillada, 2)
    resultado = advisor.generar_devolucion_pedagogica(payload)

    assert set(resultado) == CLAVES_CONTRATO
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert resultado["generado_por_llm"] is False
    assert isinstance(resultado["resumen_ejecutivo"], str) and resultado["resumen_ejecutivo"]
    assert isinstance(resultado["puntos_fuertes"], list)
    assert all(isinstance(f, str) and f for f in resultado["puntos_fuertes"])
    assert all(isinstance(v, str) and v for v in resultado["vicios_criticos"])
    assert all(isinstance(a, str) and a for a in resultado["plan_accion_recomendado"])
    assert isinstance(resultado["foco_prioritario"], str) and resultado["foco_prioritario"]
    assert resultado["nivel_riesgo"] in (RIESGO_ALTO, RIESGO_MEDIO, RIESGO_BAJO)
    # Salida JSON-serializable de punta a punta (contrato para una API REST).
    json.dumps(resultado, ensure_ascii=False)


def test_devolucion_mock_es_determinista(conn_semillada):
    payload = obtener_payload_para_llm(1, conn_semillada, 2)
    primera = advisor.generar_devolucion_pedagogica(payload)
    segunda = advisor.generar_devolucion_pedagogica(payload)
    assert primera["resumen_ejecutivo"] == segunda["resumen_ejecutivo"]
    assert primera["puntos_fuertes"] == segunda["puntos_fuertes"]
    assert primera["vicios_criticos"] == segunda["vicios_criticos"]
    assert primera["plan_accion_recomendado"] == segunda["plan_accion_recomendado"]


def test_devolucion_mock_no_requiere_api_key(conn_semillada, monkeypatch):
    monkeypatch.delenv(advisor.ENV_API_KEY, raising=False)
    monkeypatch.delenv(advisor.ENV_API_KEY_LEGADO, raising=False)
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
    narracion = resultado["resumen_ejecutivo"] + " ".join(resultado["vicios_criticos"])
    assert "Alumno de Prueba" in resultado["resumen_ejecutivo"]
    assert resultado["nivel_riesgo"] == RIESGO_ALTO
    assert "2" in narracion                    # colisiones netas
    assert "6" in narracion                    # frenadas bruscas

    vicios = " ".join(resultado["vicios_criticos"]).lower()
    assert "frenadas bruscas" in vicios        # vicio de frenadas (banda moderada)
    assert "volante" in vicios or "direcci" in vicios
    assert "horquilla" in vicios               # vicio normativo crítico

    plan = " ".join(resultado["plan_accion_recomendado"]).lower()
    assert "horquilla" in plan


def test_mock_referencia_la_sesion_mas_reciente_en_comparativas(conn_semillada):
    """Con Pre/Post, el resumen describe el estado ACTUAL (sesión Post)."""
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2)
    )
    assert "MEJORA SIGNIFICATIVA" in resultado["resumen_ejecutivo"]
    assert resultado["nivel_riesgo"] == RIESGO_BAJO      # la sesión final está limpia
    assert any("no se detectaron vicios" in v.lower()
               for v in resultado["vicios_criticos"])
    assert any("consolidar" in a.lower() for a in resultado["plan_accion_recomendado"])


def test_mock_narra_la_regresion_cuando_post_empeora(conn_semillada):
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(2, conn_semillada, 1)
    )
    assert resultado["nivel_riesgo"] == RIESGO_ALTO
    assert "REGRESIÓN DETECTADA" in resultado["resumen_ejecutivo"]
    assert any("regresión" in a.lower() or "prisa" in a.lower()
               for a in resultado["plan_accion_recomendado"])


def test_mock_para_sesion_limpia_no_inventa_vicios(conn_semillada):
    payload = obtener_payload_para_llm(2, conn_semillada)
    resultado = advisor.generar_devolucion_pedagogica(payload)
    assert resultado["nivel_riesgo"] == RIESGO_BAJO
    assert any("no se detectaron vicios" in v.lower() for v in resultado["vicios_criticos"])
    assert any("afianzar" in a.lower() for a in resultado["plan_accion_recomendado"])


def test_mock_deriva_puntos_fuertes_de_una_sesion_limpia(conn_semillada):
    """Una sesión final limpia aporta fortalezas observables y reales."""
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2)
    )
    fuertes = " ".join(resultado["puntos_fuertes"]).lower()
    assert resultado["puntos_fuertes"]                  # no vacía en una comparativa con mejoras
    assert "mejora" in fuertes or "colisiones" in fuertes or "horquilla" in fuertes


def test_mock_advierte_cobertura_insuficiente(conn):
    insertar_sesion_completa(conn, 20, puntaje_final=80.0)
    conn.execute("DELETE FROM Telemetria WHERE id_sesion_fk = 20")
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(20, conn)
    )
    assert "provisional" in resultado["resumen_ejecutivo"].lower()
    assert any("provisional" in v.lower() or "ausentes" in v.lower()
               for v in resultado["vicios_criticos"])


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
    assert "MEJORA SIGNIFICATIVA" in resultado["resumen_ejecutivo"]


def test_payload_vacio_devuelve_respuesta_generica_con_advertencia():
    resultado = advisor.generar_devolucion_pedagogica({})
    assert set(resultado) == CLAVES_CONTRATO
    assert resultado["advertencias"]
    assert resultado["nivel_riesgo"] == RIESGO_MEDIO


# =============================================================================
# CONFIGURACIÓN DE ENTORNO (transición AI_* con respaldo legado TITAN_LLM_*)
# =============================================================================
def test_leer_entorno_usa_el_nombre_legado_como_respaldo(monkeypatch):
    monkeypatch.delenv(advisor.ENV_API_KEY, raising=False)
    monkeypatch.setenv(advisor.ENV_API_KEY_LEGADO, "clave-antigua")
    assert advisor._leer_entorno(advisor.ENV_API_KEY, advisor.ENV_API_KEY_LEGADO) == \
        "clave-antigua"
    # La variable primaria tiene prioridad sobre la legada.
    monkeypatch.setenv(advisor.ENV_API_KEY, "clave-nueva")
    assert advisor._leer_entorno(advisor.ENV_API_KEY, advisor.ENV_API_KEY_LEGADO) == \
        "clave-nueva"


def test_constantes_de_entorno_apuntan_a_ai(monkeypatch):
    assert advisor.ENV_API_KEY == "AI_API_KEY"
    assert advisor.ENV_MODEL == "AI_MODEL"
    assert advisor.ENV_BASE_URL == "AI_BASE_URL"


# =============================================================================
# PROVEEDORES REMOTOS (nunca se llama a la red real en la suite)
# =============================================================================
def test_proveedor_desconocido_degrada_a_mock(monkeypatch):
    monkeypatch.delenv(advisor.ENV_API_KEY, raising=False)
    monkeypatch.delenv(advisor.ENV_API_KEY_LEGADO, raising=False)
    # "gemini" ya es un proveedor soportado: se usa una cadena genuinamente ajena.
    resultado = advisor.generar_devolucion_pedagogica({}, provider="azure_inexistente")
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert any("no soportado" in a for a in resultado["advertencias"])


def test_proveedor_remoto_sin_api_key_degrada_a_mock(monkeypatch):
    monkeypatch.delenv(advisor.ENV_API_KEY, raising=False)
    monkeypatch.delenv(advisor.ENV_API_KEY_LEGADO, raising=False)
    resultado = advisor.generar_devolucion_pedagogica({}, provider=advisor.PROVIDER_OPENAI)
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert resultado["generado_por_llm"] is False
    assert any(advisor.ENV_API_KEY in a for a in resultado["advertencias"])


def test_fallo_del_proveedor_remoto_degrada_a_mock(conn_semillada, monkeypatch):
    """Un error de red/auth/cuota NO rompe la app: se usa la devolución local."""
    def explota(*args, **kwargs):
        raise RuntimeError("sin conexión de prueba")

    monkeypatch.setattr(advisor, "_llamar_openai_compatible", explota)
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2),
        api_key="clave-de-prueba",
        provider=advisor.PROVIDER_OPENAI,
    )
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert resultado["generado_por_llm"] is False
    assert any("falló" in a for a in resultado["advertencias"])


def test_timeout_del_proveedor_degrada_a_mock(conn_semillada, monkeypatch):
    """Un ``requests.exceptions.Timeout`` degrada al mock sin lanzar."""
    def expira(*args, **kwargs):
        raise requests.exceptions.Timeout("tiempo de espera agotado")

    monkeypatch.setattr(advisor, "_llamar_openai_compatible", expira)
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2),
        api_key="clave-de-prueba",
        provider=advisor.PROVIDER_OPENAI,
    )
    assert set(resultado) == CLAVES_CONTRATO
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert resultado["generado_por_llm"] is False
    assert any("Timeout" in a or "falló" in a for a in resultado["advertencias"])


def test_respuesta_no_json_degrada_a_mock(conn_semillada, monkeypatch):
    """Una respuesta que no es JSON degrada al generador local sin lanzar."""
    monkeypatch.setattr(
        advisor, "_llamar_openai_compatible",
        lambda *a, **k: "Lo siento, no puedo procesar esa solicitud.",
    )
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2),
        api_key="clave-de-prueba",
        provider=advisor.PROVIDER_OPENAI,
    )
    assert set(resultado) == CLAVES_CONTRATO
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert resultado["generado_por_llm"] is False
    assert any("falló" in a for a in resultado["advertencias"])


def test_respuesta_json_incompleta_se_rellena_con_mock(conn_semillada, monkeypatch):
    """Un JSON válido pero fuera de esquema cumple el contrato por relleno local."""
    monkeypatch.setattr(
        advisor, "_llamar_openai_compatible",
        lambda *a, **k: json.dumps(
            {"resumen_ejecutivo": "Solo vino el resumen del modelo."}, ensure_ascii=False
        ),
    )
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2),
        api_key="clave-de-prueba",
        provider=advisor.PROVIDER_OPENAI,
    )
    assert set(resultado) == CLAVES_CONTRATO           # 11 claves garantizadas
    assert resultado["generado_por_llm"] is True
    assert resultado["resumen_ejecutivo"] == "Solo vino el resumen del modelo."
    # Las claves narrativas ausentes se rellenan desde el generador local.
    assert resultado["vicios_criticos"]
    assert resultado["plan_accion_recomendado"]
    assert resultado["nivel_riesgo"] in (RIESGO_ALTO, RIESGO_MEDIO, RIESGO_BAJO)


def test_respuesta_llm_se_parsea_y_valida_contra_el_contrato(conn_semillada, monkeypatch):
    monkeypatch.setattr(advisor, "_llamar_openai_compatible", lambda *a, **k: _RESPUESTA_LLM_VALIDA)
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2),
        api_key="clave-de-prueba",
        provider=advisor.PROVIDER_OPENAI,
    )
    assert resultado["generado_por_llm"] is True
    assert resultado["provider"] == advisor.PROVIDER_OPENAI
    assert set(resultado) == CLAVES_CONTRATO           # M1: exactamente las 11 claves
    assert resultado["resumen_ejecutivo"] == "Diagnóstico sintético redactado por el modelo."
    assert resultado["vicios_criticos"] == ["Vicio crítico 1"]
    assert resultado["puntos_fuertes"] == ["Fortaleza 1", "Fortaleza 2"]
    # Clave autoritativa del motor: la sesión final está limpia -> riesgo bajo.
    assert resultado["nivel_riesgo"] == RIESGO_BAJO
    assert isinstance(resultado["foco_prioritario"], str) and resultado["foco_prioritario"]


def test_riesgo_fuera_de_contrato_se_sustituye_por_el_del_motor(conn_semillada, monkeypatch):
    respuesta_llm = "```json\n" + json.dumps({
        "resumen_ejecutivo": "Texto.",
        "puntos_fuertes": ["F"],
        "vicios_criticos": ["Vicio"],
        "plan_accion_recomendado": ["Acción"],
        "nivel_riesgo": "CRITICISIMO",
    }, ensure_ascii=False) + "\n```"

    monkeypatch.setattr(advisor, "_llamar_openai_compatible", lambda *a, **k: respuesta_llm)
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(2, conn_semillada, 1),
        api_key="clave-de-prueba",
        provider=advisor.PROVIDER_OPENAI,
    )
    assert resultado["nivel_riesgo"] == RIESGO_ALTO     # impuesto por el motor
    assert any("fuera de contrato" in a for a in resultado["advertencias"])


# =============================================================================
# ROUTING DE PROVEEDORES OPENAI-COMPATIBLES (openai / openrouter / gemini)
# =============================================================================
@pytest.mark.parametrize("proveedor,url_esperada", [
    (advisor.PROVIDER_OPENAI, "https://api.openai.com/v1/chat/completions"),
    (advisor.PROVIDER_OPENROUTER, "https://openrouter.ai/api/v1/chat/completions"),
    (
        advisor.PROVIDER_GEMINI,
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    ),
])
def test_proveedores_compatibles_enrutan_al_mismo_caller(monkeypatch, proveedor, url_esperada):
    monkeypatch.delenv(advisor.ENV_BASE_URL, raising=False)
    monkeypatch.delenv(advisor.ENV_MODEL, raising=False)
    monkeypatch.delenv(advisor.ENV_MODEL_LEGADO, raising=False)
    capturado: dict = {}
    _instalar_requests_falso(monkeypatch, capturado, _RESPUESTA_LLM_VALIDA)

    resultado = advisor.generar_devolucion_pedagogica({}, api_key="clave-k", provider=proveedor)

    assert resultado["generado_por_llm"] is True
    assert resultado["provider"] == proveedor
    assert capturado["url"] == url_esperada
    assert capturado["headers"]["Authorization"] == "Bearer clave-k"
    assert capturado["json"]["model"] == advisor.MODELO_POR_DEFECTO[proveedor]
    assert capturado["json"]["messages"][0]["role"] == "system"
    assert capturado["json"]["messages"][1]["role"] == "user"
    # M2: temperatura determinista + max_completion_tokens (no max_tokens) + JSON.
    assert capturado["json"]["temperature"] == advisor.TEMPERATURA_RESPUESTA
    assert capturado["json"]["max_completion_tokens"] == advisor.MAX_TOKENS_RESPUESTA
    assert "max_tokens" not in capturado["json"]
    assert capturado["json"]["response_format"] == {"type": "json_object"}
    assert capturado["timeout"] == advisor.TIMEOUT_SEGUNDOS


def test_ai_base_url_sobrescribe_el_endpoint(monkeypatch):
    monkeypatch.setenv(advisor.ENV_BASE_URL, "https://mi-proxy.local/v1/")
    capturado: dict = {}
    _instalar_requests_falso(monkeypatch, capturado, _RESPUESTA_LLM_VALIDA)

    advisor.generar_devolucion_pedagogica(
        {}, api_key="clave-k", provider=advisor.PROVIDER_OPENROUTER
    )
    # La URL base del entorno tiene prioridad sobre el valor por defecto del proveedor.
    assert capturado["url"] == "https://mi-proxy.local/v1/chat/completions"


def test_url_base_por_defecto_segun_proveedor(monkeypatch):
    monkeypatch.delenv(advisor.ENV_BASE_URL, raising=False)
    assert advisor._url_base_para(advisor.PROVIDER_OPENAI) == "https://api.openai.com/v1"
    assert advisor._url_base_para(advisor.PROVIDER_OPENROUTER) == "https://openrouter.ai/api/v1"
    assert advisor._url_base_para(advisor.PROVIDER_GEMINI).endswith("/v1beta/openai")


def test_proveedores_soportados_incluye_los_cinco():
    assert set(advisor.PROVEEDORES_SOPORTADOS) == {
        advisor.PROVIDER_MOCK,
        advisor.PROVIDER_OPENAI,
        advisor.PROVIDER_ANTHROPIC,
        advisor.PROVIDER_OPENROUTER,
        advisor.PROVIDER_GEMINI,
    }


# =============================================================================
# PARSEO TOLERANTE DE JSON
# =============================================================================
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


# =============================================================================
# SYSTEM PROMPT DEL INSTRUCTOR
# =============================================================================
def test_system_prompt_del_instructor_esta_calibrado():
    prompt = advisor.SYSTEM_PROMPT_INSTRUCTOR
    bajo = prompt.lower()
    # Rol del instructor senior de simulación y seguridad operativa.
    assert "instructor senior de simulación y seguridad operativa" in bajo
    assert "autoelevadores" in bajo
    # Regla de oro: no inventar cifras ni recalcular métricas.
    assert "regla de oro" in bajo
    assert "no inventes" in bajo
    # Salida JSON estricta con exactamente las 4 claves narrativas.
    assert "json" in bajo
    for clave in (
        "resumen_ejecutivo", "puntos_fuertes", "vicios_criticos", "plan_accion_recomendado",
    ):
        assert clave in bajo
    # Vocabulario del dominio operativo.
    for termino in ("volantazos", "frenadas", "colisiones", "horquilla", "delta"):
        assert termino in bajo
    # m9: endurecimiento contra inyección indirecta de prompt.
    assert advisor.DELIMITADOR_DATOS_INICIO in prompt
    assert advisor.DELIMITADOR_DATOS_FIN in prompt
    assert "seguridad del prompt" in bajo
    assert "nunca instrucciones" in bajo
    # La plantilla de usuario envuelve el payload entre los delimitadores de datos.
    assert advisor.DELIMITADOR_DATOS_INICIO in advisor.USER_PROMPT_PLANTILLA
    assert advisor.DELIMITADOR_DATOS_FIN in advisor.USER_PROMPT_PLANTILLA


# =============================================================================
# C1 — LA FUNCIÓN PÚBLICA NUNCA LANZA
# =============================================================================
@pytest.mark.parametrize("advertencias_llm", ["una cadena", {"a": 1}])
def test_advertencias_no_lista_del_llm_no_rompe_el_contrato(
    conn_semillada, monkeypatch, advertencias_llm
):
    """C1a: una ``advertencias`` str/dict del LLM no provoca TypeError ni lanza."""
    respuesta = json.dumps({
        "resumen_ejecutivo": "Texto del modelo.",
        "puntos_fuertes": ["F1"],
        "vicios_criticos": ["V1"],
        "plan_accion_recomendado": ["P1"],
        "advertencias": advertencias_llm,
    }, ensure_ascii=False)
    monkeypatch.setattr(advisor, "_llamar_openai_compatible", lambda *a, **k: respuesta)
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2),
        api_key="clave-de-prueba",
        provider=advisor.PROVIDER_OPENAI,
    )
    assert set(resultado) == CLAVES_CONTRATO
    assert isinstance(resultado["advertencias"], list)
    assert all(isinstance(a, str) for a in resultado["advertencias"])


def test_red_de_seguridad_degrada_a_mock_ante_fallo_interno(conn_semillada, monkeypatch):
    """C1b: un fallo inesperado en el flujo interno NO se propaga al llamador."""
    def explota(*a, **k):
        raise RuntimeError("fallo interno inesperado")

    monkeypatch.setattr(advisor, "_normalizar_payload", explota)
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2)
    )
    assert set(resultado) == CLAVES_CONTRATO
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert resultado["generado_por_llm"] is False
    assert resultado["advertencias"]


# =============================================================================
# M1 — CONTRATO EXACTO: PODA DE CLAVES AJENAS + COTA DE CARDINALIDAD
# =============================================================================
def test_llm_con_claves_ajenas_y_sobrecardinalidad_se_poda_y_acota(
    conn_semillada, monkeypatch
):
    respuesta = json.dumps({
        "resumen_ejecutivo": "Texto del modelo.",
        "puntos_fuertes": ["F1", "F2", "F3", "F4", "F5"],
        "vicios_criticos": ["V1", "V2", "V3", "V4", "V5", "V6"],
        "plan_accion_recomendado": ["P1", "P2"],
        "diagnostico": "clave ajena al contrato",
        "extra": {"otro": 1},
    }, ensure_ascii=False)
    monkeypatch.setattr(advisor, "_llamar_openai_compatible", lambda *a, **k: respuesta)
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2),
        api_key="clave-de-prueba",
        provider=advisor.PROVIDER_OPENAI,
    )
    assert set(resultado) == CLAVES_CONTRATO          # ni "diagnostico" ni "extra"
    assert "diagnostico" not in resultado
    assert "extra" not in resultado
    assert len(resultado["vicios_criticos"]) <= advisor.MAX_VICIOS_CRITICOS
    assert len(resultado["puntos_fuertes"]) <= advisor.MAX_PUNTOS_FUERTES


# =============================================================================
# M2 — REINTENTO DE COMPATIBILIDAD ANTE HTTP 400
# =============================================================================
def _instalar_requests_falso_400(monkeypatch, llamadas: list, contenido: str) -> None:
    """Doble de ``requests`` que falla con HTTP 400 en la 1ª llamada y luego OK."""
    modulo = types.ModuleType("requests")
    excepciones = types.ModuleType("requests.exceptions")

    class HTTPError(Exception):
        def __init__(self, status_code):
            super().__init__(f"HTTP {status_code}")
            self.response = types.SimpleNamespace(status_code=status_code)

    excepciones.HTTPError = HTTPError
    modulo.exceptions = excepciones

    class _RespuestaFalsa:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": contenido}}]}

    def post(url, headers=None, json=None, timeout=None):
        llamadas.append({"url": url, "json": json})
        if len(llamadas) == 1:
            raise HTTPError(400)
        return _RespuestaFalsa()

    modulo.post = post
    monkeypatch.setitem(sys.modules, "requests", modulo)


def test_openai_reintenta_sin_campos_rechazados_en_http_400(monkeypatch):
    monkeypatch.delenv(advisor.ENV_BASE_URL, raising=False)
    llamadas: list = []
    _instalar_requests_falso_400(monkeypatch, llamadas, _RESPUESTA_LLM_VALIDA)
    resultado = advisor.generar_devolucion_pedagogica(
        {}, api_key="clave-k", provider=advisor.PROVIDER_OPENAI
    )
    assert resultado["generado_por_llm"] is True
    assert len(llamadas) == 2
    # 1er intento: cuerpo rico (response_format + max_completion_tokens).
    assert "max_completion_tokens" in llamadas[0]["json"]
    assert llamadas[0]["json"]["response_format"] == {"type": "json_object"}
    # Reintento: compatibilidad amplia (max_tokens, sin response_format).
    assert "response_format" not in llamadas[1]["json"]
    assert "max_completion_tokens" not in llamadas[1]["json"]
    assert llamadas[1]["json"]["max_tokens"] == advisor.MAX_TOKENS_RESPUESTA
    assert llamadas[1]["json"]["temperature"] == advisor.TEMPERATURA_RESPUESTA


# =============================================================================
# M6 — URL LIMPIA + CERO FUGA DE SECRETOS
# =============================================================================
def test_query_en_ai_base_url_no_corrompe_el_endpoint(monkeypatch):
    monkeypatch.setenv(
        advisor.ENV_BASE_URL, "https://proxy.local/v1?api_key=SECRETO#fragmento"
    )
    capturado: dict = {}
    _instalar_requests_falso(monkeypatch, capturado, _RESPUESTA_LLM_VALIDA)
    advisor.generar_devolucion_pedagogica(
        {}, api_key="clave-k", provider=advisor.PROVIDER_OPENROUTER
    )
    assert capturado["url"] == "https://proxy.local/v1/chat/completions"
    assert "SECRETO" not in capturado["url"]


def test_texto_de_excepcion_no_se_filtra_en_advertencias(conn_semillada, monkeypatch):
    """M6b: el texto crudo de la excepción (con URL/secreto) no llega a advertencias."""
    secreto = "sk-SUPERSECRETO"

    def explota(*a, **k):
        raise RuntimeError(
            "HTTPSConnectionPool(host='api.x', port=443): Max retries exceeded "
            f"(url: /v1/chat/completions?key={secreto})"
        )

    monkeypatch.setattr(advisor, "_llamar_openai_compatible", explota)
    resultado = advisor.generar_devolucion_pedagogica(
        obtener_payload_para_llm(1, conn_semillada, 2),
        api_key="clave-de-prueba",
        provider=advisor.PROVIDER_OPENAI,
    )
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert all(secreto not in a for a in resultado["advertencias"])
    assert any("falló" in a for a in resultado["advertencias"])


# =============================================================================
# M7 — ESQUEMA SEGURO DE LA URL BASE
# =============================================================================
def test_base_url_http_no_local_degrada_a_mock(monkeypatch):
    monkeypatch.setenv(advisor.ENV_BASE_URL, "http://api.malvado.com/v1")
    capturado: dict = {}
    _instalar_requests_falso(monkeypatch, capturado, _RESPUESTA_LLM_VALIDA)
    resultado = advisor.generar_devolucion_pedagogica(
        {}, api_key="clave-k", provider=advisor.PROVIDER_OPENAI
    )
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert resultado["generado_por_llm"] is False
    assert "url" not in capturado                     # nunca se llegó a POSTear


def test_base_url_http_localhost_si_se_permite(monkeypatch):
    monkeypatch.setenv(advisor.ENV_BASE_URL, "http://localhost:8080/v1")
    capturado: dict = {}
    _instalar_requests_falso(monkeypatch, capturado, _RESPUESTA_LLM_VALIDA)
    resultado = advisor.generar_devolucion_pedagogica(
        {}, api_key="clave-k", provider=advisor.PROVIDER_OPENAI
    )
    assert resultado["generado_por_llm"] is True
    assert capturado["url"] == "http://localhost:8080/v1/chat/completions"


# =============================================================================
# m8 — ANTHROPIC HONRA LA BASE URL CONFIGURADA
# =============================================================================
def test_anthropic_honra_la_base_url_configurada(monkeypatch):
    monkeypatch.setenv(advisor.ENV_BASE_URL, "https://mi-proxy.local/anthropic")
    capturado: dict = {}
    modulo = types.ModuleType("requests")

    class _RespuestaFalsa:
        def raise_for_status(self):
            return None

        def json(self):
            return {"content": [{"text": _RESPUESTA_LLM_VALIDA}]}

    def post(url, headers=None, json=None, timeout=None):
        capturado["url"] = url
        capturado["headers"] = headers
        capturado["json"] = json
        return _RespuestaFalsa()

    modulo.post = post
    monkeypatch.setitem(sys.modules, "requests", modulo)

    resultado = advisor.generar_devolucion_pedagogica(
        {}, api_key="clave-k", provider=advisor.PROVIDER_ANTHROPIC
    )
    assert capturado["url"] == "https://mi-proxy.local/anthropic/messages"
    assert capturado["json"]["temperature"] == advisor.TEMPERATURA_RESPUESTA
    assert capturado["json"]["max_tokens"] == advisor.MAX_TOKENS_RESPUESTA
    assert resultado["provider"] == advisor.PROVIDER_ANTHROPIC
    assert resultado["generado_por_llm"] is True


# =============================================================================
# m1 / m5 / m7-test — ENTORNO Y ARGUMENTOS
# =============================================================================
def test_api_key_en_blanco_se_trata_como_ausente(monkeypatch):
    """m1: una clave de solo espacios no dispara una llamada real con 'Bearer   '."""
    monkeypatch.delenv(advisor.ENV_API_KEY, raising=False)
    monkeypatch.delenv(advisor.ENV_API_KEY_LEGADO, raising=False)
    resultado = advisor.generar_devolucion_pedagogica(
        {}, api_key="   ", provider=advisor.PROVIDER_OPENAI
    )
    assert resultado["provider"] == advisor.PROVIDER_MOCK
    assert any(advisor.ENV_API_KEY in a for a in resultado["advertencias"])


def test_modelo_legado_solo_aplica_a_proveedores_preexistentes(monkeypatch):
    """m5: TITAN_LLM_MODEL aplica a openai/anthropic, no a openrouter/gemini."""
    monkeypatch.delenv(advisor.ENV_MODEL, raising=False)
    monkeypatch.setenv(advisor.ENV_MODEL_LEGADO, "gpt-4o-mini-legado")
    assert advisor._resolver_modelo(advisor.PROVIDER_OPENAI) == "gpt-4o-mini-legado"
    assert advisor._resolver_modelo(advisor.PROVIDER_ANTHROPIC) == "gpt-4o-mini-legado"
    # openrouter/gemini ignoran el legado y usan su slug por defecto.
    assert advisor._resolver_modelo(advisor.PROVIDER_OPENROUTER) == \
        advisor.MODELO_POR_DEFECTO[advisor.PROVIDER_OPENROUTER]
    assert advisor._resolver_modelo(advisor.PROVIDER_GEMINI) == \
        advisor.MODELO_POR_DEFECTO[advisor.PROVIDER_GEMINI]


def test_ai_model_fluye_al_cuerpo_de_la_peticion(monkeypatch):
    """m7-test: AI_MODEL del entorno llega al campo ``model`` de la petición."""
    monkeypatch.delenv(advisor.ENV_BASE_URL, raising=False)
    monkeypatch.delenv(advisor.ENV_MODEL_LEGADO, raising=False)
    monkeypatch.setenv(advisor.ENV_MODEL, "mi-modelo-custom")
    capturado: dict = {}
    _instalar_requests_falso(monkeypatch, capturado, _RESPUESTA_LLM_VALIDA)
    resultado = advisor.generar_devolucion_pedagogica(
        {}, api_key="clave-k", provider=advisor.PROVIDER_OPENAI
    )
    assert capturado["json"]["model"] == "mi-modelo-custom"
    assert resultado["modelo"] == "mi-modelo-custom"
