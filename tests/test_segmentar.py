import numpy as np
import pytest
from PIL import Image

from motor import segmentar
from motor.pixelizar import Opciones, ampliar, coincidencia, pixelizar

pytestmark = pytest.mark.skipif(not segmentar.disponible(), reason="segmentador no instalado")


def test_figura_oscura_sobre_fondo_oscuro(preset_heroes):
    """El caso que la separación por color no resuelve: figura y fondo de tonos parecidos."""
    sprite = Image.open(preset_heroes / "ejemplo1_caballero.png").convert("RGBA")
    grande = ampliar(sprite, 24)
    lienzo = Image.new("RGBA", (576, 1024), (52, 56, 64, 255))
    lienzo.alpha_composite(grande, ((576 - grande.width) // 2, 1024 - 80 - grande.height))
    imagen = lienzo.convert("RGB")

    m = segmentar.mascara(imagen)
    assert m.shape == (1024, 576)
    assert not m[:50].any() and not m[:, :40].any()  # el fondo no es figura
    alfa_figura = np.zeros((1024, 576), bool)  # dónde está realmente el caballero
    y0 = 1024 - 80 - grande.height
    x0 = (576 - grande.width) // 2
    alfa_figura[y0:y0 + grande.height, x0:x0 + grande.width] = np.array(grande.split()[-1]) > 0
    acierto = (m & alfa_figura).sum() / alfa_figura.sum()
    assert acierto > 0.9

    res = pixelizar(imagen, Opciones.desde_preset(preset_heroes), mascara=m)
    assert coincidencia(sprite, res.imagen) > 0.8
