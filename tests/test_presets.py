import json
import shutil

import numpy as np
from PIL import Image

from motor.presets import analizar, analizar_sprite, escala_pixel, vista_previa


def test_escala_pixel_detecta_ampliacion():
    base = np.random.default_rng(0).integers(0, 255, (7, 5, 4), dtype=np.uint8)
    ampliada = np.repeat(np.repeat(base, 4, 0), 4, 1)
    assert escala_pixel(base) == 1
    assert escala_pixel(ampliada) == 4


def test_analizar_sprite_heroe(preset_heroes):
    d = analizar_sprite(preset_heroes / "ejemplo1_caballero.png")
    assert d["lienzo"] == [16, 28]
    assert d["contorno"]["tiene"] is True
    assert d["contorno"]["grosor"] == 1
    assert 4 <= d["colores"] <= 10


def test_analizar_sprite_ampliado_da_lo_mismo(tmp_path, preset_heroes):
    original = Image.open(preset_heroes / "ejemplo2_elfa.png")
    original.resize((original.width * 4, original.height * 4), Image.NEAREST).save(tmp_path / "x4.png")
    a = analizar_sprite(preset_heroes / "ejemplo2_elfa.png")
    b = analizar_sprite(tmp_path / "x4.png")
    assert b["escala_pixel_origen"] == 4
    assert (a["lienzo"], a["paleta"], a["contorno"]) == (b["lienzo"], b["paleta"], b["contorno"])


def test_analizar_conserva_campos_editados(tmp_path, preset_heroes):
    for p in preset_heroes.glob("ejemplo*.png"):
        shutil.copy(p, tmp_path)
    (tmp_path / "estilo.json").write_text(json.dumps({
        "nombre": "Mío", "fuente": {"pack": "x"}, "generacion": {"fuerza_estilo": 0.42}}))

    estilo = analizar(tmp_path, nombre=None, paleta_de=None)

    assert estilo["nombre"] == "Mío"
    assert estilo["fuente"] == {"pack": "x"}
    assert estilo["generacion"] == {"fuerza_estilo": 0.42}
    assert estilo["sprite"]["ancho"] == 16 and estilo["sprite"]["alto"] == 28
    assert estilo["paleta"] == estilo["paleta_ejemplos"]
    assert estilo["fondo_generacion"] == "#ffffff"
    assert len(estilo["ejemplos"]) == 3


def test_vista_previa(tmp_path, preset_heroes):
    for p in list(preset_heroes.glob("ejemplo*.png")) + [preset_heroes / "estilo.json"]:
        shutil.copy(p, tmp_path)
    salida = vista_previa(tmp_path)
    assert salida.exists() and Image.open(salida).width > 300


def test_reducir_paleta_elige_colores_reales_y_respeta_el_maximo():
    from collections import Counter

    from motor.presets import reducir_paleta

    # Dos familias (rojos y azules) con muchos matices: deben quedar representadas.
    cuenta = Counter({(200 + i, 20, 20): 100 - i for i in range(20)})
    cuenta.update({(20, 20, 200 + i): 50 for i in range(20)})
    cuenta[(250, 250, 250)] = 1  # raro pero muy distinto
    reducida = reducir_paleta(cuenta, 4)
    assert len(reducida) <= 4
    assert set(reducida) <= set(cuenta)  # nada inventado
    assert any(r > 150 and b < 100 for r, _, b in reducida)  # algún rojo
    assert any(b > 150 and r < 100 for r, _, b in reducida)  # algún azul


def test_presets_incluidos_son_coherentes():
    import json

    from motor.modelos import RAIZ

    for archivo in (RAIZ / "presets").glob("*/estilo.json"):
        e = json.loads(archivo.read_text())
        assert e["sprite"]["alto_figura"] <= e["sprite"]["alto"]
        assert e["colores_por_sprite"]["objetivo"] <= len(e["paleta"])
        for ej in e["ejemplos"]:
            assert (archivo.parent / ej).exists()
