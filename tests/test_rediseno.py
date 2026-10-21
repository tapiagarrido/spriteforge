import json

import numpy as np
import pytest
from PIL import Image

from motor import rediseno as rd


def sprite_rata():
    """Sprite de 12×8: cuerpo marrón y gris, ojos verdes, contorno casi negro, fondo transparente."""
    a = np.zeros((8, 12, 4), np.uint8)
    a[2:7, 2:10] = (150, 110, 70, 255)  # pelaje marrón
    a[4:6, 2:6] = (120, 120, 130, 255)  # pelaje gris
    a[3, 3] = a[3, 5] = (40, 200, 60, 255)  # ojos / runas verdes
    a[1, 2:10] = a[7, 2:10] = (20, 18, 22, 255)  # contorno
    return Image.fromarray(a, "RGBA")


def matiz(rgb):
    import colorsys

    return colorsys.rgb_to_hsv(*(np.array(rgb[:3]) / 255))[0] * 360


def test_tenir_hacia_azul_solo_el_pelaje():
    sp = sprite_rata()
    paleta = rd.colores_de(sp)
    pelo = ["#966e46", "#787882"]
    ajuste = rd.ajuste_hacia(sp, "#3a6fd8", pelo)
    r = np.array(rd.recolorear(sp, ajuste, paleta))
    assert 200 <= matiz(r[5, 7]) <= 230 and 200 <= matiz(r[5, 3]) <= 230  # el pelaje, azul
    assert np.array_equal(r[3, 3], np.array(sp)[3, 3])  # los ojos verdes, intactos
    assert np.array_equal(r[1], np.array(sp)[1])  # el contorno, intacto
    assert np.array_equal(r[..., 3], np.array(sp)[..., 3])  # la silueta no cambia
    luz_antes = np.array(sp)[5, 7, :3].mean()
    assert abs(r[5, 7, :3].mean() - luz_antes) < 60  # conserva claros y oscuros


def test_girar_conserva_la_variedad_y_todo_sin_seleccion():
    sp = sprite_rata()
    paleta = rd.colores_de(sp)
    ajuste = rd.ajuste_hacia(sp, "#3a6fd8", None, modo="girar")
    r = np.array(rd.recolorear(sp, ajuste, paleta))
    assert not np.array_equal(r[3, 3], np.array(sp)[3, 3])  # sin selección cambia todo, ojos incluidos
    assert abs(matiz(r[5, 7]) - matiz(r[3, 3])) > 20  # pero los tonos siguen distintos entre sí


def test_identidad_y_validacion():
    sp = sprite_rata()
    assert rd.recolorear(sp, rd.Ajuste(), rd.colores_de(sp)).tobytes() == sp.tobytes()
    for malo in (rd.Ajuste(tono=300), rd.Ajuste(saturacion=5), rd.Ajuste(brillo=0.1)):
        with pytest.raises(ValueError):
            malo.validar()
    with pytest.raises(ValueError, match="al menos un color"):
        rd.ajuste_hacia(sp, "#3a6fd8", ["#010203"])


def test_recolorear_imagen_de_la_ia_respeta_el_fondo():
    ia = Image.new("RGB", (30, 30), (255, 255, 255))
    ia.paste((150, 110, 70), (8, 8, 22, 22))
    sp = sprite_rata()
    mascara = np.zeros((30, 30), bool)
    mascara[8:22, 8:22] = True
    ajuste = rd.ajuste_hacia(sp, "#3a6fd8", ["#966e46", "#787882"])
    r = np.array(rd.recolorear(ia, ajuste, rd.colores_de(sp), mascara))
    assert (r[0, 0] == 255).all()  # el fondo blanco no se toca
    assert 200 <= matiz(r[15, 15]) <= 230


def test_ajustar_a_la_paleta_del_estilo():
    sp = sprite_rata()
    paleta = ["#000000", "#ffffff", "#3050c0", "#2060ff"]
    r = np.array(rd.ajustar_a_paleta(sp, paleta))
    usados = {tuple(c[:3]) for c in r[r[..., 3] > 0]}
    assert usados <= {(0, 0, 0), (255, 255, 255), (48, 80, 192), (32, 96, 255)}
    assert np.array_equal(r[..., 3], np.array(sp)[..., 3])


def test_anadir_variante(tmp_path):
    (tmp_path / "info.json").write_text(json.dumps({"variantes": [
        {"indice": 1, "semilla": 1, "ia": "ia_01.png", "sprite": "sprite_01.png",
         "sprite_vista": "sprite_01_x8.png", "colores": []},
        {"indice": 4, "semilla": 4, "ia": "ia_04.png", "sprite": "sprite_04.png",
         "sprite_vista": "sprite_04_x8.png", "colores": []}]}))
    sp = sprite_rata()
    e = rd.anadir_variante(tmp_path, sp, Image.new("RGB", (64, 64), "white"),
                           {"variante": 4, "tipo": "color"}, "  Rata azul  ", semilla=9)
    assert e["indice"] == 5 and e["nombre"] == "Rata azul" and e["semilla"] == 9
    for archivo in ("ia_05.png", "sprite_05.png", "sprite_05_x8.png"):
        assert (tmp_path / archivo).exists()
    info = json.loads((tmp_path / "info.json").read_text())
    assert [v["indice"] for v in info["variantes"]] == [1, 4, 5]
    assert info["variantes"][-1]["origen"] == {"variante": 4, "tipo": "color"}
    assert set(info["variantes"][-1]["colores"]) == set(rd.colores_de(sp))
