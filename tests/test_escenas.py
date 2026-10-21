import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

from motor import escenas, mapas
from motor.proyectos import cargar

RAIZ = Path(__file__).resolve().parent.parent


def sala_falsa(prompt, ancho, alto, semilla):
    """Sala oscura de Qwen: muros, suelo gris y un charco verde muy oscuro."""
    a = np.full((alto, ancho, 3), (30, 28, 30), np.uint8)
    a[: alto // 8] = (12, 12, 14)
    a[alto // 2: alto // 2 + alto // 4, ancho // 4: ancho // 2] = (14, 36, 22)
    return Image.fromarray(a)


def proyecto(tmp_path):
    return {"id": "p", "nombre": "Horror grotesco", "preset": "criaturas-64", "baldosa": 32}


def test_ajustar_cubre_y_recorta():
    im = Image.new("RGB", (1216, 832), "red")
    assert escenas.ajustar(im, 768, 512).size == (768, 512)
    assert escenas.ajustar(im, 512, 512).size == (512, 512)


def test_realzar_aclara_una_sala_oscura_y_no_oscurece_una_clara():
    oscura = np.full((8, 8, 3), (20, 30, 24), float)
    clara = np.full((8, 8, 3), (200, 210, 190), float)
    assert escenas.realzar(oscura).mean() > oscura.mean() * 1.5
    assert abs(escenas.realzar(clara).mean() - clara.mean()) < 25  # solo cambia el color


def test_el_agua_oscura_no_cae_en_el_negro_de_la_paleta():
    estilo = json.loads((RAIZ / "presets" / "criaturas-64" / "estilo.json").read_text())
    pal, lab = mapas.paleta_lab(estilo["paleta"])
    s = np.array(escenas.a_paleta(sala_falsa("", 128, 128, 0), pal, lab))
    agua = s[70, 40]
    assert agua[1] > agua[0] and agua[1] > agua[2]  # sigue siendo verdosa
    assert agua.max() > 30  # y no es negro


def test_tmx_numera_las_baldosas_por_filas():
    x = escenas.tmx("sala_1.png", 3, 2, 32)
    assert 'width="3" height="2" tilewidth="32"' in x and 'columns="3"' in x
    assert '<image source="sala_1.png" width="96" height="64"/>' in x
    assert re.search(r"1,2,3,\n4,5,6\n", x)


def test_forjar_sala_guarda_imagen_tmx_e_info(tmp_path):
    pr = cargar("horror-grotesco")
    info = escenas.forjar_sala(pr, RAIZ / "presets" / pr["preset"], tmp_path, "catacumbas con moho",
                               sala_falsa, forma="ancha", variantes=2, semilla=3, traducir=False)
    carpeta = tmp_path / "mapas" / info["carpeta"]
    im = Image.open(carpeta / "sala_1.png")
    assert im.size == (24 * 32, 16 * 32) and Image.open(carpeta / "sala_1_x2.png").size == (1536, 1024)
    assert (carpeta / "sala_2.tmx").exists() and (carpeta / "ia" / "sala_2.png").exists()
    assert info["salas"] == [1, 2] and info["archivos"]["mapa_prueba_x2"] == "sala_1_x2.png"
    assert "catacumbas con moho" in info["prompt"] and "evenly lit" in info["prompt"]
    guardado = json.loads((carpeta / "info.json").read_text())
    assert guardado["materiales"] == {"sala": "catacumbas con moho"} and guardado["baldosas"] == 24 * 16
    # solo colores de la paleta del proyecto
    estilo = json.loads((RAIZ / "presets" / "criaturas-64" / "estilo.json").read_text())
    paleta = {tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in estilo["paleta"]}
    assert {tuple(c) for c in np.array(im).reshape(-1, 3)} <= paleta


def test_forjar_sala_valida(tmp_path):
    pr = cargar("horror-grotesco")
    base = (pr, RAIZ / "presets" / pr["preset"], tmp_path)
    for descripcion, extra, texto in (("", {}, "Describe"), ("x", {"forma": "enorme"}, "Forma"),
                                      ("x", {"detalle": "raro"}, "Detalle")):
        try:
            escenas.forjar_sala(*base, descripcion, sala_falsa, traducir=False, **extra)
        except ValueError as e:
            assert texto in str(e)
        else:
            raise AssertionError(texto)


def test_forjar_sala_en_una_carpeta_con_nombre(tmp_path):
    pr = cargar("horror-grotesco")
    info = escenas.forjar_sala(pr, RAIZ / "presets" / pr["preset"], tmp_path, "cripta", sala_falsa,
                               variantes=1, traducir=False, destino=tmp_path / "salas" / "cripta-helada")
    assert info["carpeta"] == "cripta-helada"
    assert (tmp_path / "salas" / "cripta-helada" / "sala_1.png").exists()
