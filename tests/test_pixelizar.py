import numpy as np
import pytest
from PIL import Image

from motor.color import hex_a_rgb, rgb_a_lab
from motor.pixelizar import (
    Opciones,
    ampliar,
    coincidencia,
    limitar_colores,
    mascara_figura,
    pixelizar,
    tamano_destino,
)


def sobre_fondo(sprite: Image.Image, factor: int, fondo="#0000f0", lado=512) -> Image.Image:
    """Sprite ampliado y centrado sobre un fondo liso, sin transparencia."""
    grande = ampliar(sprite.convert("RGBA"), factor)
    lienzo = Image.new("RGBA", (lado, lado), fondo)
    lienzo.alpha_composite(grande, ((lado - grande.width) // 2, (lado - grande.height) // 2))
    return lienzo.convert("RGB")


@pytest.fixture
def op(preset_heroes):
    return Opciones.desde_preset(preset_heroes)


def test_tamano_destino_conserva_proporcion():
    assert tamano_destino(100, 200, 16, 28) == (14, 28)
    assert tamano_destino(300, 100, 16, 28) == (16, 5)


def test_sprite_ampliado_limpio_se_recupera(preset_heroes, op):
    original = Image.open(preset_heroes / "ejemplo1_caballero.png")
    res = pixelizar(sobre_fondo(original, 16), op)
    assert res.imagen.size == (16, 28)
    assert coincidencia(original, res.imagen) >= 0.9


def test_cumple_el_preset(preset_heroes, op):
    original = Image.open(preset_heroes / "ejemplo3_mago.png")
    res = pixelizar(sobre_fondo(original, 15), op)
    a = np.array(res.imagen)
    solido = a[..., 3] > 0

    assert len(res.colores) <= op.max_colores
    assert set(res.colores) <= {c.lower() for c in op.paleta} | {op.color_contorno}
    assert set(np.unique(a[..., 3])) <= {0, 255}  # sin semitransparencias
    assert solido[-1].any()  # apoyado abajo

    # Todo píxel del borde de la silueta es contorno.
    p = np.pad(solido, 1)
    borde = solido & ~(p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:])
    assert (a[borde][:, :3] == hex_a_rgb(op.color_contorno)).all()


def test_usa_alfa_si_existe(preset_heroes):
    op = Opciones.desde_preset(preset_heroes, alto_figura=16)  # la elfa es más baja
    original = Image.open(preset_heroes / "ejemplo2_elfa.png")
    res = pixelizar(ampliar(original, 12), op)  # RGBA con fondo transparente
    assert res.tamano_figura == (15, 16)
    assert coincidencia(original, res.imagen) >= 0.9


def test_alto_figura_del_preset(op):
    assert op.alto_figura == 20


def test_coincidencia_ignora_desplazamiento(preset_heroes):
    original = Image.open(preset_heroes / "ejemplo1_caballero.png")
    movido = Image.new("RGBA", original.size)
    movido.paste(original, (1, 0))
    assert coincidencia(original, movido) == 1.0


def test_sin_contorno(preset_heroes):
    op = Opciones.desde_preset(preset_heroes, contorno=False)
    original = Image.open(preset_heroes / "ejemplo1_caballero.png")
    res = pixelizar(sobre_fondo(original, 16), op)
    assert len(res.colores) <= op.max_colores


def test_imagen_sin_figura_falla(op):
    with pytest.raises(ValueError):
        pixelizar(Image.new("RGB", (64, 64), "#336699"), op)


def test_mascara_respeta_interior_del_color_del_fondo():
    img = np.full((60, 60, 3), 255, np.uint8)  # fondo blanco
    img[10:50, 10:50] = (30, 30, 30)  # figura oscura...
    img[25:35, 25:35] = 255  # ...con un "ojo" blanco dentro
    m = mascara_figura(img)
    assert m[30, 30]  # el ojo es figura, no fondo
    assert not m[2, 2]


def test_mascara_ignora_ruido_del_fondo():
    rng = np.random.default_rng(1)
    img = np.clip(rng.normal(128, 8, (300, 300, 3)), 0, 255).astype(np.uint8)
    img[100:200, 120:180] = (200, 40, 40)
    ys, xs = np.where(mascara_figura(img))
    assert (ys.min(), xs.min()) >= (95, 115) and (ys.max(), xs.max()) <= (205, 185)


def test_limitar_colores_protege_contorno():
    paleta_lab = rgb_a_lab(np.array([[34, 34, 34], [200, 0, 0], [210, 10, 10], [0, 0, 200]]))
    indices = np.array([[0, 1, 1, 1, 2, 2, 3, 3, 3, 3]])
    opaco = np.ones_like(indices, bool)
    salida = limitar_colores(indices, opaco, paleta_lab, maximo=3, proteger=0)
    assert set(np.unique(salida)) == {0, 1, 3}  # el 2 se fusiona con su vecino rojo; el 0 sigue


def test_alto_exacto_respeta_la_altura_en_figuras_esbeltas():
    """Un personaje esbelto puede usar todo el alto del lienzo (regla pensada para personajes);
    una runa o una roca, no: deben medir lo que se pidió estén o no en una piedra alta."""
    from pathlib import Path

    import numpy as np
    from PIL import Image

    from motor.pixelizar import Opciones, pixelizar

    preset = Path(__file__).resolve().parent.parent / "presets" / "criaturas-64"
    img = Image.new("RGB", (400, 600), "white")
    a = np.array(img)
    a[100:560, 140:260] = (40, 90, 200)  # figura alta y estrecha (aspecto 0.26)
    img = Image.fromarray(a)
    altos = {}
    for exacto in (False, True):
        op = Opciones.desde_preset(preset, alto_figura=44, max_colores=8, alto_exacto=exacto)
        px = np.array(pixelizar(img, op).imagen)[..., 3] > 0
        altos[exacto] = int(np.ptp(np.nonzero(px)[0]) + 1)
    assert altos[False] > 44 and altos[True] <= 44
