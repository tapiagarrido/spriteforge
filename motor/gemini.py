"""Generación con la API de Gemini (opcional, externa y de pago).

Sustituye solo el paso de la IA local: Gemini recibe el boceto como guía de pose y
silueta, la rejilla de ejemplos del preset como referencia de estilo, la
descripción del personaje y la ambientación del proyecto. Lo demás sigue siendo
local: recorte (segmentador) y pixelizado con la paleta exacta del preset, así
los personajes son coherentes con los generados en local.

Privacidad y coste: el boceto, la referencia y los textos se envían a Google; cada
imagen cuesta según la tarifa de tu cuenta. Las imágenes de Gemini incluyen la
marca de agua invisible SynthID.

Solo usa la biblioteca estándar (urllib). La clave va en la cabecera
`x-goog-api-key`, nunca en la URL.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

BASE = "https://generativelanguage.googleapis.com"
VERSIONES = ("v1beta", "v1")
# Preferencia si la cuenta tiene varios; si no, el primero disponible con "image".
MODELOS_PREFERIDOS = ("gemini-3.1-flash-image", "gemini-nano-banana-2.1", "gemini-2.5-flash-image",
                      "gemini-3-pro-image", "gemini-3-pro-image-preview")
PROPORCIONES = {"1:1": 1.0, "3:4": 0.75, "2:3": 0.667, "9:16": 0.5625, "4:5": 0.8}
TIEMPO_MAX = 120  # s por imagen
PARALELO = 4


class ErrorGemini(RuntimeError):
    """Error de la API, con un mensaje apto para mostrar al usuario."""


# --- HTTP ------------------------------------------------------------------------

def _peticion(metodo: str, ruta: str, api_key: str, cuerpo: dict | None = None) -> dict:
    datos = None if cuerpo is None else json.dumps(cuerpo).encode()
    req = urllib.request.Request(f"{BASE}/{ruta}", data=datos, method=metodo,
                                 headers={"x-goog-api-key": api_key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIEMPO_MAX) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            detalle = json.loads(e.read()).get("error", {}).get("message", "")
        except (json.JSONDecodeError, AttributeError, OSError):
            detalle = ""
        raise ErrorGemini(_mensaje_http(e.code, detalle)) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise ErrorGemini(f"No se pudo conectar con Gemini: {e}") from None


def _mensaje_http(codigo: int, detalle: str) -> str:
    if codigo in (401, 403):
        return f"Gemini rechazó la clave (HTTP {codigo}). Revísala en Ajustes. {detalle}".strip()
    if codigo == 429:
        return f"Gemini: cuota o límite de uso alcanzado (HTTP 429). {detalle}".strip()
    if codigo == 404:
        return f"Gemini: modelo o ruta no encontrada (HTTP 404). {detalle}".strip()
    return f"Gemini respondió con error HTTP {codigo}. {detalle}".strip()


# --- modelos ---------------------------------------------------------------------

def listar_modelos(api_key: str) -> list[str]:
    """Modelos de la cuenta que generan imágenes (nombre contiene 'image')."""
    datos = _peticion("GET", "v1beta/models?pageSize=1000", api_key)
    modelos = []
    for m in datos.get("models", []):
        nombre = m.get("name", "").removeprefix("models/")
        if "image" in nombre and "generateContent" in m.get("supportedGenerationMethods", []):
            modelos.append(nombre)
    preferidos = [m for m in MODELOS_PREFERIDOS if m in modelos]
    return preferidos + sorted(m for m in modelos if m not in preferidos)


def modelo_por_defecto(api_key: str) -> str:
    modelos = listar_modelos(api_key)
    if not modelos:
        raise ErrorGemini("Tu clave no tiene acceso a ningún modelo de imagen de Gemini.")
    return modelos[0]


# --- generación ------------------------------------------------------------------

def proporcion_cercana(ancho: int, alto: int) -> str:
    objetivo = ancho / alto
    return min(PROPORCIONES, key=lambda k: abs(PROPORCIONES[k] - objetivo))


def instrucciones(peticion) -> str:
    """Texto para Gemini a partir de la petición (mismo prompt que la IA local)."""
    return (
        "Create one pixel art game sprite of a single character.\n"
        "Image 1 is my rough sketch: use it as a loose guide for the pose, silhouette and "
        "proportions; it does not need to be traced exactly.\n"
        "Image 2 is a style reference sheet: match its pixel art rendering, outline and shading "
        "style, but do not copy its characters.\n"
        f"Description and art direction: {peticion.prompt}.\n"
        "Requirements: full body, centered, plain pure white background, no ground, no shadow, "
        "no text, no frame, only one character.\n"
        f"Avoid: {peticion.prompt_negativo}."
    )


def _png_b64(imagen: Image.Image) -> str:
    b = io.BytesIO()
    imagen.save(b, "PNG")
    return base64.b64encode(b.getvalue()).decode()


def _extraer_imagen(respuesta: dict) -> Image.Image:
    textos = []
    for cand in respuesta.get("candidates", []):
        for parte in cand.get("content", {}).get("parts", []):
            datos = parte.get("inlineData") or parte.get("inline_data")
            if datos and datos.get("data"):
                return Image.open(io.BytesIO(base64.b64decode(datos["data"]))).convert("RGB")
            if parte.get("text"):
                textos.append(parte["text"])
        if cand.get("finishReason") not in (None, "STOP"):
            textos.append(f"(finishReason: {cand['finishReason']})")
    bloqueo = respuesta.get("promptFeedback", {}).get("blockReason")
    if bloqueo:
        textos.append(f"(bloqueado: {bloqueo})")
    raise ErrorGemini("Gemini no devolvió ninguna imagen. " + " ".join(textos)[:300])


def generar_una(peticion, api_key: str, modelo: str, semilla: int) -> Image.Image:
    boceto = ImageOps.invert(peticion.boceto.convert("L")).convert("RGB")  # trazo negro sobre blanco
    partes = [
        {"text": instrucciones(peticion)},
        {"inline_data": {"mime_type": "image/png", "data": _png_b64(boceto)}},
        {"inline_data": {"mime_type": "image/png", "data": _png_b64(peticion.referencia)}},
    ]
    config = {"responseModalities": ["TEXT", "IMAGE"], "seed": semilla,
              "imageConfig": {"aspectRatio": proporcion_cercana(peticion.ancho, peticion.alto)}}
    ultimo_error = None
    # La API ha cambiado de versión y de nombres de campo: se prueba la forma
    # completa y, si la rechaza, una mínima sin opciones de imagen.
    for version in VERSIONES:
        for cfg in (config, {"responseModalities": ["TEXT", "IMAGE"]}):
            try:
                r = _peticion("POST", f"{version}/models/{modelo}:generateContent", api_key,
                              {"contents": [{"role": "user", "parts": partes}], "generationConfig": cfg})
                imagen = _extraer_imagen(r)
                # Al tamaño de generación local: así la silueta del boceto coincide.
                return imagen.resize((peticion.ancho, peticion.alto), Image.LANCZOS)
            except ErrorGemini as e:
                ultimo_error = e
                if "HTTP 400" not in str(e) and "HTTP 404" not in str(e):
                    raise
    raise ultimo_error


def editar(imagenes: list[Image.Image], instruccion: str, api_key: str, modelo: str, ancho: int, alto: int,
           semilla: int = 0) -> Image.Image:
    """Imagen nueva a partir de imágenes de entrada + instrucción (p. ej. "el mismo
    personaje visto de espaldas"). Se devuelve a `ancho`×`alto`."""
    partes = [{"text": instruccion}] + [
        {"inline_data": {"mime_type": "image/png", "data": _png_b64(im.convert("RGB"))}} for im in imagenes]
    config = {"responseModalities": ["TEXT", "IMAGE"], "seed": semilla,
              "imageConfig": {"aspectRatio": proporcion_cercana(ancho, alto)}}
    ultimo_error = None
    for version in VERSIONES:
        for cfg in (config, {"responseModalities": ["TEXT", "IMAGE"]}):
            try:
                r = _peticion("POST", f"{version}/models/{modelo}:generateContent", api_key,
                              {"contents": [{"role": "user", "parts": partes}], "generationConfig": cfg})
                return _extraer_imagen(r).resize((ancho, alto), Image.LANCZOS)
            except ErrorGemini as e:
                ultimo_error = e
                if "HTTP 400" not in str(e) and "HTTP 404" not in str(e):
                    raise
    raise ultimo_error


def generar(peticion, api_key: str, modelo: str | None = None) -> list[Image.Image]:
    """`peticion.variantes` imágenes, pedidas en paralelo (una petición por imagen)."""
    if not api_key:
        raise ErrorGemini("Falta la clave de la API de Gemini (Ajustes).")
    modelo = modelo or modelo_por_defecto(api_key)
    log.info("Gemini: %d imágenes con %s", peticion.variantes, modelo)
    semillas = [peticion.semilla + i for i in range(peticion.variantes)]
    with ThreadPoolExecutor(min(PARALELO, peticion.variantes)) as ex:
        return list(ex.map(lambda s: generar_una(peticion, api_key, modelo, s), semillas))
