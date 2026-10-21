from pathlib import Path

import pytest
from PIL import Image

RAIZ = Path(__file__).resolve().parent.parent
PRESET_HEROES = RAIZ / "presets" / "heroes-mazmorra"
BOCETOS = RAIZ / "ejemplos" / "bocetos"


@pytest.fixture(autouse=True)
def config_aislada(tmp_path, monkeypatch):
    """Ningún test lee ni escribe la configuración real del usuario (~/.config)."""
    monkeypatch.setenv("FORJA_CONFIG", str(tmp_path / "config" / "config.json"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_URL", raising=False)
    from motor import proyectos
    monkeypatch.setattr(proyectos, "ARCHIVO_PLANTILLAS", tmp_path / "plantillas" / "tipos.json")


@pytest.fixture(autouse=True)
def traductor_falso(monkeypatch):
    """Sin el modelo de traducción descargado (p. ej. en CI), `traducir` devuelve el texto tal cual."""
    from motor import traducir
    from motor.modelos import CATALOGO

    if not CATALOGO["traductor"].descargado():
        monkeypatch.setattr(traducir, "traducir", lambda texto: texto)


@pytest.fixture
def preset_heroes() -> Path:
    return PRESET_HEROES


@pytest.fixture
def boceto_digital() -> Image.Image:
    return Image.open(BOCETOS / "guerrero_digital.png")


@pytest.fixture
def boceto_foto() -> Image.Image:
    return Image.open(BOCETOS / "guerrero_foto.jpg")


def ia_falsa(peticion):
    """Sustituto de la IA: el caballero del preset sobre el fondo pedido.

    Como hace ControlNet con la IA real, el personaje ocupa el lugar del boceto:
    se amplía (sin suavizado) para llenar la caja del trazo y se apoya abajo.
    Devuelve tantas imágenes como variantes, del tamaño pedido. Permite probar la
    cadena completa sin GPU ni modelos.
    """
    import numpy as np

    from motor.pixelizar import ampliar

    sprite = Image.open(PRESET_HEROES / "ejemplo1_caballero.png").convert("RGBA")
    sprite = sprite.crop(sprite.getbbox())
    trazo = np.array(peticion.boceto.convert("L")) > 0
    ys, xs = np.nonzero(trazo)
    ancho_caja, alto_caja = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    factor = max(1, int(min(ancho_caja / sprite.width, alto_caja / sprite.height)))
    grande = ampliar(sprite, factor)
    x = int((xs.min() + xs.max()) / 2 - grande.width / 2)
    y = int(ys.max() + 1 - grande.height)
    fondo = peticion.referencia.getpixel((0, 0))  # la rejilla usa el fondo de generación
    imagenes = []
    for _ in range(peticion.variantes):
        lienzo = Image.new("RGBA", (peticion.ancho, peticion.alto), fondo)
        lienzo.alpha_composite(grande, (max(0, x), max(0, y)))
        imagenes.append(lienzo.convert("RGB"))
    return imagenes


@pytest.fixture
def generador_falso():
    return ia_falsa
