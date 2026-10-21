import base64
import io

import pytest
from PIL import Image

from motor import gemini
from motor import generar as gen

CLAVE = "AIzaFAKE-clave-de-prueba-1234567890abcd"


def png_b64(color="red", tamano=(64, 64)) -> str:
    b = io.BytesIO()
    Image.new("RGB", tamano, color).save(b, "PNG")
    return base64.b64encode(b.getvalue()).decode()


@pytest.fixture
def peticion(preset_heroes, boceto_digital):
    return gen.peticion_desde_preset(preset_heroes, boceto_digital.convert("L").resize((576, 1024)),
                                     "knight", variantes=3, semilla=5)


@pytest.fixture
def llamadas(monkeypatch):
    """Sustituye la red: registra las peticiones y responde como la API."""
    registro = []

    def falsa(metodo, ruta, api_key, cuerpo=None):
        registro.append((metodo, ruta, api_key, cuerpo))
        if ruta.startswith("v1beta/models?"):
            return {"models": [
                {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/zz-image-exp", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/imagen-x", "supportedGenerationMethods": ["predict"]},
            ]}
        return {"candidates": [{"content": {"parts": [{"text": "ok"},
                                                      {"inlineData": {"mimeType": "image/png",
                                                                      "data": png_b64()}}]}}]}

    monkeypatch.setattr(gemini, "_peticion", falsa)
    return registro


def test_listar_modelos_filtra_y_prioriza(llamadas):
    assert gemini.listar_modelos(CLAVE) == ["gemini-2.5-flash-image", "zz-image-exp"]


def test_generar_envia_boceto_referencia_y_prompt(llamadas, peticion):
    imagenes = gemini.generar(peticion, CLAVE, "gemini-2.5-flash-image")
    assert len(imagenes) == 3 and all(im.size == (576, 1024) for im in imagenes)
    posts = [c for c in llamadas if c[0] == "POST"]
    assert len(posts) == 3
    metodo, ruta, clave, cuerpo = posts[0]
    assert ruta == "v1beta/models/gemini-2.5-flash-image:generateContent"
    assert clave == CLAVE and CLAVE not in ruta  # la clave va en cabecera, no en la URL
    partes = cuerpo["contents"][0]["parts"]
    assert "knight" in partes[0]["text"] and "white background" in partes[0]["text"]
    assert [p["inline_data"]["mime_type"] for p in partes[1:]] == ["image/png", "image/png"]
    assert cuerpo["generationConfig"]["imageConfig"]["aspectRatio"] == "9:16"
    assert sorted(c[3]["generationConfig"]["seed"] for c in posts) == [5, 6, 7]


def test_sin_imagen_da_error_claro(monkeypatch, peticion):
    monkeypatch.setattr(gemini, "_peticion", lambda *a, **k: {
        "candidates": [{"content": {"parts": [{"text": "No puedo"}]}, "finishReason": "SAFETY"}]})
    with pytest.raises(gemini.ErrorGemini, match="no devolvió ninguna imagen"):
        gemini.generar_una(peticion, CLAVE, "m", 1)


def test_reintenta_sin_opciones_si_la_api_las_rechaza(monkeypatch, peticion):
    cuerpos = []

    def falsa(metodo, ruta, api_key, cuerpo=None):
        cuerpos.append(cuerpo)
        if "imageConfig" in cuerpo["generationConfig"]:
            raise gemini.ErrorGemini("Gemini respondió con error HTTP 400. Unknown field imageConfig")
        return {"candidates": [{"content": {"parts": [{"inline_data": {"data": png_b64()}}]}}]}

    monkeypatch.setattr(gemini, "_peticion", falsa)
    assert gemini.generar_una(peticion, CLAVE, "m", 1).size == (576, 1024)
    assert "imageConfig" not in cuerpos[-1]["generationConfig"]


def test_clave_invalida_no_reintenta(monkeypatch, peticion):
    n = []

    def falsa(*a, **k):
        n.append(1)
        raise gemini.ErrorGemini(gemini._mensaje_http(403, "API key not valid"))

    monkeypatch.setattr(gemini, "_peticion", falsa)
    with pytest.raises(gemini.ErrorGemini, match="rechazó la clave"):
        gemini.generar_una(peticion, CLAVE, "m", 1)
    assert len(n) == 1


def test_proporcion_cercana():
    assert gemini.proporcion_cercana(64, 64) == "1:1"
    assert gemini.proporcion_cercana(576, 1024) == "9:16"


def test_sin_clave():
    with pytest.raises(gemini.ErrorGemini):
        gemini.generar(None, "", "m")
