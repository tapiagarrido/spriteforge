"""Generación con Qwen-Image (opcional, servidor propio en red local).

Tercera vía junto a la IA local (SDXL en la GPU de esta máquina) y Gemini: Qwen-Image
corre en un servidor propio (p. ej. `stable-diffusion.cpp`) que expone una API compatible con
`POST /v1/images/edits` y `GET /api/health` (ver docs/qwen.md).
Como con Gemini, solo se sustituye el paso de la IA: recorte y pixelizado con la paleta
exacta del preset siguen siendo locales.

Qwen recibe el boceto (modo edición) y el estilo del preset va en el prompt; la hoja de
ejemplos del preset NO se envía (ver `instrucciones`).
No hay clave ni coste, y nada sale de tu red; el servidor procesa una petición a la vez
(unos 50-100 s por imagen en una GPU de gama media), así que las variantes se piden en serie.

Solo usa la biblioteca estándar (urllib).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import urllib.error
import urllib.request
import uuid

import numpy as np
from PIL import Image, ImageOps

log = logging.getLogger(__name__)

URL_POR_DEFECTO = "http://127.0.0.1:1234"
TIEMPO_MAX = 1800  # s por imagen: la primera carga el modelo y las peticiones se encolan
TIEMPO_ESTADO = 5
# Referencia a menor resolución para el VAE: mismo resultado con un boceto y ~40 % más rápido.
REF_ARGS = "vae_input_max_pixels=262144"


class ErrorQwen(RuntimeError):
    """Error del servidor Qwen, con un mensaje apto para mostrar al usuario."""


def _base(url: str) -> str:
    return (url or URL_POR_DEFECTO).rstrip("/")


# --- HTTP ------------------------------------------------------------------------

def _multipart(campos: dict[str, str], archivos: list[bytes]) -> tuple[bytes, str]:
    limite = "----forja" + uuid.uuid4().hex
    partes = [f'--{limite}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
              for k, v in campos.items()]
    for i, datos in enumerate(archivos):
        partes.append((f'--{limite}\r\nContent-Disposition: form-data; name="image[]"; '
                       f'filename="ref{i}.png"\r\nContent-Type: image/png\r\n\r\n').encode()
                      + datos + b"\r\n")
    partes.append(f"--{limite}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={limite}"


def _peticion(ruta: str, url: str, cuerpo: bytes | None = None, tipo: str | None = None,
              tiempo: float = TIEMPO_MAX) -> dict:
    req = urllib.request.Request(f"{_base(url)}{ruta}", data=cuerpo,
                                 headers={"Content-Type": tipo} if tipo else {})
    try:
        with urllib.request.urlopen(req, timeout=tiempo) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            detalle = json.loads(e.read()).get("error", "")
        except (json.JSONDecodeError, AttributeError, OSError):
            detalle = ""
        raise ErrorQwen(f"Qwen respondió con error HTTP {e.code}. {detalle} Revisa los logs de "
                        "tu servidor Qwen-Image.".strip()) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise ErrorQwen(f"No se pudo conectar con Qwen en {_base(url)}: {e}. Comprueba que tu "
                        "servidor Qwen-Image esté arrancado y que la dirección sea correcta.") from None


def estado(url: str) -> dict:
    """{"sd": bool, "llm": bool} del puente; ErrorQwen si no responde."""
    return _peticion("/api/health", url, tiempo=TIEMPO_ESTADO)


def _png(imagen: Image.Image) -> bytes:
    b = io.BytesIO()
    imagen.save(b, "PNG")
    return b.getvalue()


# --- generación ------------------------------------------------------------------

def editar(imagenes: list[Image.Image], instruccion: str, url: str, ancho: int, alto: int,
           semilla: int = 0, ref_args: str | None = None) -> Image.Image:
    """Imagen nueva a partir de imágenes de entrada + instrucción, de `ancho`×`alto`.

    Misma firma que `gemini.editar` (sin clave ni modelo). La semilla viaja en el bloque
    `<sd_cpp_extra_args>` del prompt: sd-server usa una fija si no se indica. `ref_args` son
    opciones de las imágenes de referencia (p. ej. su resolución máxima en el VAE).
    """
    if not imagenes:
        raise ErrorQwen("Hace falta al menos una imagen de entrada.")
    extra = json.dumps({"seed": int(semilla), **({"ref_image_args": ref_args} if ref_args else {})})
    campos = {"prompt": f"{instruccion}<sd_cpp_extra_args>{extra}</sd_cpp_extra_args>",
              "size": f"{ancho}x{alto}", "n": "1", "output_format": "png"}
    cuerpo, tipo = _multipart(campos, [_png(im.convert("RGB")) for im in imagenes])
    r = _peticion("/v1/images/edits", url, cuerpo, tipo)
    try:
        datos = base64.b64decode(r["data"][0]["b64_json"])
        imagen = Image.open(io.BytesIO(datos)).convert("RGB")
    except (KeyError, IndexError, TypeError, ValueError, OSError):
        raise ErrorQwen("Qwen no devolvió ninguna imagen.") from None
    return imagen.resize((ancho, alto), Image.LANCZOS)


def instrucciones(peticion) -> str:
    """Texto para Qwen: el dibujo se convierte en el sprite. `peticion.prompt` ya trae el estilo
    del preset (pixel art, contorno grueso, colores planos...).

    Medido con Qwen-Image 2.1: en modo edición conserva la estructura de la primera imagen y no
    hay prompt que lo evite (devuelve el dibujo coloreado). Y si se le añade la hoja de ejemplos
    del preset como referencia, copia sus personajes en el resultado. Por eso solo se envía el
    dibujo, y se pide cambiar el trazo por contorno de píxeles y rellenar el cuerpo de color
    (sin esto, un dibujo a tinta se queda en un contorno vacío)."""
    return (
        "Turn this drawing into a finished pixel art game sprite of the same character, with the same "
        "pose and proportions. Redraw it as blocky pixel art with big visible square pixels, a thick "
        "dark pixel outline, flat cel shading and a small palette, with the whole body FILLED with solid "
        "colors, not just outlines.\n"
        f"Description and art direction: {peticion.prompt}.\n"
        "Requirements: full body, centered, plain pure white background, no ground, no shadow, "
        "no text, no frame, only one character.\n"
        f"Avoid: {peticion.prompt_negativo}."
    )


def _ajustar(imagen: Image.Image, ancho: int, alto: int) -> Image.Image:
    """El dibujo original entero, centrado sobre blanco y a `ancho`×`alto`."""
    if imagen.mode in ("RGBA", "LA", "P"):
        imagen = imagen.convert("RGBA")
        fondo = Image.new("RGBA", imagen.size, "white")
        imagen = Image.alpha_composite(fondo, imagen)
    imagen = ImageOps.contain(imagen.convert("RGB"), (ancho, alto), Image.LANCZOS)
    lienzo = Image.new("RGB", (ancho, alto), "white")
    lienzo.paste(imagen, ((ancho - imagen.width) // 2, (alto - imagen.height) // 2))
    return lienzo


def texto_a_imagen(prompt: str, url: str, ancho: int, alto: int, semilla: int = 0) -> Image.Image:
    """Imagen nueva solo a partir de texto (sin imágenes de entrada), de `ancho`×`alto`.
    Se usa para escenas enteras (salas de mapa), donde Qwen dibuja muy bien el conjunto."""
    extra = json.dumps({"seed": int(semilla)})
    cuerpo = json.dumps({"prompt": f"{prompt}<sd_cpp_extra_args>{extra}</sd_cpp_extra_args>",
                         "size": f"{ancho}x{alto}", "n": 1, "output_format": "png"}).encode()
    r = _peticion("/v1/images/generations", url, cuerpo, "application/json")
    try:
        return Image.open(io.BytesIO(base64.b64decode(r["data"][0]["b64_json"]))).convert("RGB")
    except (KeyError, IndexError, TypeError, ValueError, OSError):
        raise ErrorQwen("Qwen no devolvió ninguna imagen.") from None


def generar_una(peticion, url: str, semilla: int, original: Image.Image | None = None) -> Image.Image:
    """Con `original` (el dibujo o foto tal cual) Qwen entiende el dibujo; el contorno limpiado de
    `peticion.boceto` es solo el plan B: en una ilustración a tinta queda roto y Qwen lo calca."""
    if original is not None:
        entrada = _ajustar(original, peticion.ancho, peticion.alto)
    else:
        entrada = ImageOps.invert(peticion.boceto.convert("L")).convert("RGB")  # trazo negro sobre blanco
    return editar([entrada], instrucciones(peticion), url, peticion.ancho, peticion.alto, semilla,
                  ref_args=REF_ARGS)


def generar(peticion, url: str | None = None, original: Image.Image | None = None) -> list[Image.Image]:
    """`peticion.variantes` imágenes, una tras otra (el servidor solo atiende una a la vez)."""
    url = url or URL_POR_DEFECTO
    log.info("Qwen: %d imágenes en %s", peticion.variantes, _base(url))
    return [generar_una(peticion, url, peticion.semilla + i, original) for i in range(peticion.variantes)]


def realzar(rgb: np.ndarray) -> np.ndarray:
    """Aclara una textura oscura y aviva su color (ver `motor.escenas.realzar`, mismo motivo)."""
    from motor import escenas

    return escenas.realzar(rgb)


# --- texturas repetibles ---------------------------------------------------------------

def periodizar(imagen: Image.Image, x: bool = True, y: bool = True, banda: float = 0.3) -> Image.Image:
    """Hace que una imagen se repita sin costuras en x y/o y.

    Qwen-Image no tiene modo de mosaico: el borde derecho no casa con el izquierdo. Se mezcla la
    imagen con ella misma desplazada media vuelta, con un peso que vale 1 en el centro y 0 en los
    bordes: así los bordes salen de zonas contiguas (continuos entre sí) y solo se mezcla una banda
    de `banda` (fracción del lado) junto a cada borde, para que el resto conserve todo su detalle."""
    a = np.asarray(imagen.convert("RGB"), dtype=float)
    alto, ancho = a.shape[:2]

    def peso(n: int) -> np.ndarray:
        d = np.minimum(np.arange(n), n - 1 - np.arange(n)) / (n * banda)  # 0 en el borde, 1 a `banda` de él
        d = np.clip(d, 0, 1)
        return d * d * (3 - 2 * d)

    w = np.ones((alto, ancho))
    desplazada = a
    if x:
        w = w * peso(ancho)[None, :]
        desplazada = np.roll(desplazada, ancho // 2, axis=1)
    if y:
        w = w * peso(alto)[:, None]
        desplazada = np.roll(desplazada, alto // 2, axis=0)
    salida = a * w[..., None] + desplazada * (1 - w[..., None])
    return Image.fromarray(np.clip(salida, 0, 255).astype(np.uint8))


def texturas(prompt: str, url: str, n: int, semilla: int, ancho: int, alto: int, x: bool = True,
             y: bool = True) -> list[Image.Image]:
    """`n` texturas repetibles de `ancho`×`alto` a partir de texto (ver `periodizar`).
    Misma forma que el generador de texturas de `motor.mapas.forjar_mapa`."""
    lado = max(ancho, alto)
    pedido = (f"{prompt}. Top-down flat orthographic view filling the whole image edge to edge, sharp "
              "clearly "
              "defined details, large readable pieces, dark outlines, no border, no frame, no objects "
              "standing out, no characters, no text.")
    imagenes = []
    for i in range(n):
        im = texto_a_imagen(pedido, url, 1024, 1024, semilla + i)
        im = periodizar(im, x, y).resize((ancho, alto), Image.LANCZOS if lado < 1024 else Image.BOX)
        imagenes.append(Image.fromarray(np.clip(realzar(np.asarray(im, float)), 0, 255).astype(np.uint8)))
    return imagenes
