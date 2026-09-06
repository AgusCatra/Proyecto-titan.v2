# tests/test_api.py
# Proyecto Titán — Pruebas de contrato de la capa REST (FastAPI).
#
# Estrategia:
#   * Se ejercita la aplicación real ``api.main:app`` con ``fastapi.testclient``.
#   * La única dependencia que se sustituye es ``api.deps.get_db_path`` (devuelve
#     una RUTA), de modo que se construye un ARCHIVO SQLite temporal semillado con
#     los helpers de ``conftest`` (``crear_bd`` + ``insertar_sesion_completa``).
#     Nunca se toca la BD productiva ``database/titan.db``.
#   * No se enumeran ``app.routes``: en fastapi 0.141.1 los routers incluidos son
#     objetos perezosos ``_IncludedRouter`` cuyo ``.path`` lanza AttributeError.
#     Cuando hace falta la lista de rutas se usa ``app.openapi()["paths"]``.
#
# Las dos sesiones semilladas comparten las 6 señales canónicas pero tienen
# DURACIONES distintas (120 s vs 100 s) para que la normalización a progreso
# (0 -> 100 %) sea verificable de forma independiente del tiempo absoluto.

import json

import pytest

# --- Guardas de colección: si faltan las dependencias web, se OMITE el módulo ---
fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from conftest import crear_bd, insertar_sesion_completa  # noqa: E402

from api.main import API_VERSION, app  # noqa: E402
from api.deps import get_db_path  # noqa: E402
from api.schemas import (  # noqa: E402
    EJE_X_PROGRESO,
    CompararDeltaResponse,
    DevolucionResponse,
    DiagnosticoResponse,
    HealthResponse,
    MetricasCalculadas,
    Session,
    TelemetriaResponse,
)
from core.evaluador_diagnostico import (  # noqa: E402
    DICTAMEN_APTO,
    DICTAMEN_NO_APTO_CRITICO,
    DICTAMEN_OBSERVADO,
    SENALES_CANONICAS,
    VEREDICTO_MEJORA_SIGNIFICATIVA,
)

# Literales de dictamen construidos desde las constantes de core (única fuente).
DICTAMENES_VALIDOS = {DICTAMEN_APTO, DICTAMEN_OBSERVADO, DICTAMEN_NO_APTO_CRITICO}

# Las 18 métricas físicas del contrato de ``MetricasCalculadas``.
CAMPOS_METRICAS = set(MetricasCalculadas.model_fields.keys())

# Claves del contrato de salida de la devolución pedagógica.
CLAVES_DEVOLUCION = {
    "resumen_ejecutivo",
    "puntos_fuertes",
    "vicios_criticos",
    "plan_accion_recomendado",
    "nivel_riesgo",
    "foco_prioritario",
}
CLAVES_META_DEVOLUCION = {"provider", "modelo", "generado_por_llm", "advertencias"}

# Claves ``*_pct`` de los deltas (Optional[float]; NUNCA se excluyen del payload).
CLAVES_PCT = (
    "duracion_pct",
    "colisiones_pct",
    "frenadas_bruscas_pct",
    "volantazos_pct",
    "suavidad_pct",
    "puntaje_depurado_pct",
)


# =============================================================================
# FIXTURES
# =============================================================================
@pytest.fixture()
def bd_semillada(tmp_path):
    """Crea un ARCHIVO SQLite temporal con dos sesiones comparables.

    Sesión 1 (Pre): severa — 6 frenadas, 8 volantazos, 2 colisiones, horquilla
    alta, puntaje 40 y -10 de penalizaciones (duración 120 s).
    Sesión 2 (Post): limpia — 2 frenadas, 2 volantazos, 0 colisiones, puntaje 70
    y -2 de penalizaciones (duración 100 s).

    Devuelve la RUTA (str) al archivo, que es lo que consume ``get_db_path``.
    """
    db_path = tmp_path / "api_test.db"
    conn = crear_bd(str(db_path))
    try:
        insertar_sesion_completa(
            conn,
            1,
            frenadas=6,
            volantazos=8,
            colisiones=2,
            altura_horquilla=0.60,
            velocidad=5.0,
            puntaje_final=40.0,
            penalizaciones=-10.0,
            duracion_segundos=120,
            perfil="Apurado",
        )
        insertar_sesion_completa(
            conn,
            2,
            frenadas=2,
            volantazos=2,
            colisiones=0,
            altura_horquilla=0.10,
            velocidad=5.0,
            puntaje_final=70.0,
            penalizaciones=-2.0,
            duracion_segundos=100,
            perfil="Eficiente",
        )
    finally:
        conn.close()
    return str(db_path)


@pytest.fixture()
def client(bd_semillada):
    """``TestClient`` con ``get_db_path`` apuntando a la BD temporal semillada.

    El ``dependency_overrides`` se limpia SIEMPRE en el teardown para no filtrar
    la sustitución a otras pruebas de la suite.
    """
    app.dependency_overrides[get_db_path] = lambda: bd_semillada
    try:
        with TestClient(app) as manejador:
            yield manejador
    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 0) RUTAS REGISTRADAS (vía OpenAPI, sin enumerar app.routes)
# =============================================================================
def test_rutas_registradas_en_openapi():
    """El contrato expone todas las rutas documentadas (QUICK de humo estructural)."""
    rutas = set(app.openapi()["paths"].keys())
    esperadas = {
        "/health",
        "/api/v1/sesiones",
        "/api/v1/sesiones/{session_id}/telemetria",
        "/api/v1/sesiones/ingestar",
        "/api/v1/evaluar-admision",
        "/api/v1/comparar-delta",
        "/api/v1/devolucion-pedagogica",
    }
    assert esperadas.issubset(rutas)


def test_ruta_upload_multipart_presente_en_openapi():
    """R1: la variante multipart de evaluar-admision está documentada en OpenAPI.

    Prueba LIGERA: solo verifica el registro de la ruta (no sube un PDF real ni
    ejecuta el OCR/pipeline). ``python-multipart`` está instalado en el entorno de
    test, de modo que la guarda de disponibilidad registra la ruta.
    """
    assert "/api/v1/evaluar-admision/upload" in app.openapi()["paths"]


# =============================================================================
# 1) HEALTH
# =============================================================================
def test_health_ok(client):
    """/health -> 200 con estado ok, BD disponible, >=2 sesiones y versión."""
    respuesta = client.get("/health")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()

    # Valida contra el modelo de respuesta (permisivo).
    modelo = HealthResponse(**cuerpo)
    assert modelo.status == "ok"
    assert modelo.db_disponible is True
    assert modelo.sesiones >= 2
    assert modelo.version == API_VERSION
    assert "version" in cuerpo


# =============================================================================
# 2) VALIDACIONES 422 (modelos de petición estrictos)
# =============================================================================
def test_evaluar_admision_sin_session_id_422(client):
    """Falta el campo obligatorio ``session_id`` -> 422."""
    respuesta = client.post("/api/v1/evaluar-admision", json={})
    assert respuesta.status_code == 422


def test_evaluar_admision_session_id_no_entero_422(client):
    """``session_id`` no entero -> 422."""
    respuesta = client.post(
        "/api/v1/evaluar-admision", json={"session_id": "no-soy-un-int"}
    )
    assert respuesta.status_code == 422


def test_peticion_con_campo_desconocido_422(client):
    """``extra='forbid'``: una clave ajena en la petición -> 422."""
    respuesta = client.post(
        "/api/v1/evaluar-admision",
        json={"session_id": 1, "campo_inventado": "valor"},
    )
    assert respuesta.status_code == 422


def test_comparar_delta_mismas_sesiones_422(client):
    """``session_id_pre == session_id_post`` viola el validador -> 422."""
    respuesta = client.post(
        "/api/v1/comparar-delta",
        json={"session_id_pre": 1, "session_id_post": 1},
    )
    assert respuesta.status_code == 422


def test_devolucion_provider_invalido_422(client):
    """Un ``provider`` fuera de ``PROVEEDORES_SOPORTADOS`` -> 422."""
    respuesta = client.post(
        "/api/v1/devolucion-pedagogica",
        json={"session_id_pre": 1, "provider": "azure_x"},
    )
    assert respuesta.status_code == 422


def test_devolucion_sin_fuente_de_datos_422(client):
    """Ni ``session_id_pre`` ni ``payload_metricas`` -> 422."""
    respuesta = client.post("/api/v1/devolucion-pedagogica", json={})
    assert respuesta.status_code == 422


def test_devolucion_api_key_no_se_filtra_en_422(client):
    """K-C1: un 422 de validación NUNCA debe filtrar la ``api_key`` del cuerpo.

    ``DevolucionRequest`` admite ``api_key`` opcional y exige ``session_id_pre`` o
    ``payload_metricas``. Al enviar SOLO la ``api_key`` (sin fuente de datos) falla
    el ``model_validator`` y el manejador por defecto de FastAPI incluiría el
    ``input`` crudo (el secreto) en el detalle del 422. El manejador registrado en
    ``api.main`` lo sanea: aquí se verifica que el secreto no aparece NI en el texto
    NI en el JSON de la respuesta.
    """
    secreto = "sk-SUPERSECRET-abc123"
    respuesta = client.post(
        "/api/v1/devolucion-pedagogica", json={"api_key": secreto}
    )
    assert respuesta.status_code == 422
    # Ausente del cuerpo de texto...
    assert secreto not in respuesta.text
    # ...y del JSON serializado (el ``input`` crudo fue eliminado de cada error).
    assert secreto not in json.dumps(respuesta.json())


# =============================================================================
# 3) EVALUAR-ADMISION
# =============================================================================
def test_evaluar_admision_sesion_semillada(client):
    """Sesión 1 -> 200; valida DiagnosticoResponse y las 18 métricas."""
    respuesta = client.post("/api/v1/evaluar-admision", json={"session_id": 1})
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()

    modelo = DiagnosticoResponse(**cuerpo)
    assert modelo.semaforo in DICTAMENES_VALIDOS
    assert modelo.metricas.sesion_existente is True

    # Las 18 métricas físicas presentes en el payload.
    assert set(cuerpo["metricas"].keys()) == CAMPOS_METRICAS
    assert cuerpo["metricas"]["sesion_existente"] is True
    assert cuerpo["semaforo"] in DICTAMENES_VALIDOS

    # Por defecto (incluir_series=False) la clave ``series`` se OMITE de verdad.
    assert "series" not in cuerpo


def test_evaluar_admision_sesion_inexistente_404(client):
    """Una sesión que no existe -> 404."""
    respuesta = client.post("/api/v1/evaluar-admision", json={"session_id": 9999})
    assert respuesta.status_code == 404


# =============================================================================
# 4) COMPARAR-DELTA
# =============================================================================
def test_comparar_delta_mejora_significativa(client):
    """(1,2) -> MEJORA SIGNIFICATIVA, delta de puntaje ~38 y series normalizadas."""
    respuesta = client.post(
        "/api/v1/comparar-delta",
        json={"session_id_pre": 1, "session_id_post": 2},
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()

    # Valida contra el modelo de respuesta completo.
    CompararDeltaResponse(**cuerpo)

    assert cuerpo["veredicto_evolucion"] == VEREDICTO_MEJORA_SIGNIFICATIVA

    deltas = cuerpo["deltas"]
    # pd_pre = 40 + (-10) = 30 ; pd_post = 70 + (-2) = 68 ; delta = 38.
    assert abs(deltas["puntaje_depurado"] - 38.0) < 0.01

    # Los ``*_pct`` son Optional[float]: presentes SIEMPRE (no excluidos), None o numéricos.
    for clave in CLAVES_PCT:
        assert clave in deltas
        assert deltas[clave] is None or isinstance(deltas[clave], (int, float))

    # Eje X de progreso estable.
    assert cuerpo["eje_x"] == EJE_X_PROGRESO

    # Series normalizadas SIEMPRE presentes con las 6 señales canónicas.
    for clave_series in ("series_normalizadas_pre", "series_normalizadas_post"):
        assert clave_series in cuerpo
        assert set(cuerpo[clave_series].keys()) == set(SENALES_CANONICAS)
        for _senal, puntos in cuerpo[clave_series].items():
            if not puntos:
                continue
            # El progreso arranca cerca de 0 % y termina cerca de 100 %, con
            # independencia de la duración real (120 s vs 100 s).
            assert abs(puntos[0][0] - 0.0) < 1.0
            assert abs(puntos[-1][0] - 100.0) < 1.0


def test_comparar_delta_sesion_faltante_404(client):
    """Si falta una sesión -> 404 y el detalle incluye los ids ausentes."""
    respuesta = client.post(
        "/api/v1/comparar-delta",
        json={"session_id_pre": 1, "session_id_post": 9999},
    )
    assert respuesta.status_code == 404
    detalle = respuesta.json()["detail"]
    assert "9999" in str(detalle)
    assert "9999" in detalle.get("sesiones_faltantes", [])


def test_comparar_delta_series_raw_ausentes_por_defecto(client):
    """Sin ``incluir_series`` las series RAW ``series_pre/post`` NO aparecen."""
    respuesta = client.post(
        "/api/v1/comparar-delta",
        json={"session_id_pre": 1, "session_id_post": 2},
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert "series_pre" not in cuerpo
    assert "series_post" not in cuerpo


def test_comparar_delta_series_raw_con_incluir_series(client):
    """Con ``incluir_series=true`` las series RAW ``series_pre/post`` SÍ aparecen."""
    respuesta = client.post(
        "/api/v1/comparar-delta",
        json={"session_id_pre": 1, "session_id_post": 2, "incluir_series": True},
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert "series_pre" in cuerpo and cuerpo["series_pre"]
    assert "series_post" in cuerpo and cuerpo["series_post"]
    # Las series normalizadas siguen presentes (son independientes del flag).
    assert "series_normalizadas_pre" in cuerpo
    assert "series_normalizadas_post" in cuerpo


# =============================================================================
# 5) DEVOLUCION-PEDAGOGICA
# =============================================================================
def test_devolucion_pedagogica_mock(client):
    """(pre=1, post=2, provider mock) -> 200 con el contrato completo y sin red."""
    respuesta = client.post(
        "/api/v1/devolucion-pedagogica",
        json={"session_id_pre": 1, "session_id_post": 2},
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()

    # Valida contra el modelo de respuesta.
    DevolucionResponse(**cuerpo)

    assert CLAVES_DEVOLUCION.issubset(cuerpo.keys())
    assert CLAVES_META_DEVOLUCION.issubset(cuerpo.keys())
    # Sin API key y provider mock: generador local determinista, nunca red.
    assert cuerpo["generado_por_llm"] is False
    assert cuerpo["provider"] == "mock"
    # ``metricas_interpretadas`` se omite salvo flag explícito.
    assert "metricas_interpretadas" not in cuerpo


def test_devolucion_pedagogica_payload_directo(client):
    """Variante con ``payload_metricas`` aportado por el cliente -> 200 (sin BD)."""
    payload = {
        "frenadas_bruscas": 3,
        "volantazos": 2,
        "colisiones_netas": 1,
        "traslado_horquilla_alta_segundos": 4.0,
        "tiempo_total_segundos": 95.0,
        "perfil_operador": "Novato",
        "sugerencia_admision": DICTAMEN_OBSERVADO,
        "senales_disponibles": list(SENALES_CANONICAS),
    }
    respuesta = client.post(
        "/api/v1/devolucion-pedagogica",
        json={"payload_metricas": payload},
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    DevolucionResponse(**cuerpo)
    assert CLAVES_DEVOLUCION.issubset(cuerpo.keys())
    assert cuerpo["provider"] == "mock"
    assert cuerpo["generado_por_llm"] is False


# =============================================================================
# 6) SESIONES Y TELEMETRÍA
# =============================================================================
def test_listar_sesiones(client):
    """GET /api/v1/sesiones -> 200 con las 2 sesiones validando el modelo Session."""
    respuesta = client.get("/api/v1/sesiones")
    assert respuesta.status_code == 200
    filas = respuesta.json()
    assert isinstance(filas, list)
    assert len(filas) == 2

    sesiones = [Session(**fila) for fila in filas]
    ids = {s.id_sesion for s in sesiones}
    assert ids == {1, 2}


def test_telemetria_sesion_existente(client):
    """GET /api/v1/sesiones/1/telemetria -> 200 con series."""
    respuesta = client.get("/api/v1/sesiones/1/telemetria")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    modelo = TelemetriaResponse(**cuerpo)
    assert modelo.session_id == 1
    assert modelo.normalizar is False
    assert isinstance(cuerpo["series"], dict)


def test_telemetria_sesion_inexistente_404(client):
    """GET /api/v1/sesiones/9999/telemetria -> 404."""
    respuesta = client.get("/api/v1/sesiones/9999/telemetria")
    assert respuesta.status_code == 404
