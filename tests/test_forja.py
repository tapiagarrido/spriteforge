import json
from datetime import datetime

from PIL import Image

from motor.forja import forjar, nombre_carpeta
from motor.pixelizar import coincidencia


def test_nombre_carpeta():
    n = nombre_carpeta("Caballero ÁRBOL con espada!!", datetime(2026, 10, 16, 15, 30, 5))
    assert n == "2026-10-16_153005_caballero-arbol-con-espada"
    assert nombre_carpeta("¿?", datetime(2026, 1, 1)).endswith("_personaje")


def test_cadena_completa_con_ia_falsa(tmp_path, preset_heroes, boceto_foto, generador_falso):
    r = forjar(boceto_foto, preset_heroes, "knight", variantes=2, semilla=7,
               generador=generador_falso, carpeta_salidas=tmp_path, segmentar=False)

    archivos = {p.name for p in r.carpeta.iterdir()}
    assert {"boceto.png", "boceto_limpio.png", "referencia.png", "info.json",
            "ia_01.png", "sprite_01.png", "sprite_01_x8.png", "ia_02.png", "sprite_02.png"} <= archivos

    info = json.loads((r.carpeta / "info.json").read_text())
    assert info["semilla"] == 7 and [v["semilla"] for v in info["variantes"]] == [7, 8]
    assert "knight" in info["prompt"] and {"limpiar", "ia", "pixelizar"} <= set(info["tiempos_s"])
    assert info["prompt_traducido"] is None  # ya estaba en inglés

    # La IA falsa devuelve el caballero del preset: el sprite final debe recuperarlo.
    sprite = Image.open(r.carpeta / "sprite_01.png")
    assert sprite.size == (16, 28)
    original = Image.open(preset_heroes / "ejemplo1_caballero.png")
    assert coincidencia(original, sprite) > 0.9


def test_opciones_de_pixelizado_se_aplican(tmp_path, preset_heroes, boceto_digital, generador_falso):
    r = forjar(boceto_digital, preset_heroes, "knight", variantes=1, alto_figura=14, max_colores=4,
               generador=generador_falso, carpeta_salidas=tmp_path, segmentar=False)
    assert len(r.variantes[0].colores) <= 4
    assert r.info["pixelizado"] == {"alto_figura": 14, "max_colores": 4, "separacion": "color"}


def test_proyecto_aporta_preset_y_ambientacion(tmp_path, boceto_digital, generador_falso):
    proyecto = {"id": "oscuro", "preset": "heroes-mazmorra", "ambientacion": "dark gothic dungeon",
                "negativo": "bright colors", "parecido_boceto": 0.2}
    r = forjar(boceto_digital, None, "knight", proyecto=proyecto, variantes=1,
               generador=generador_falso, carpeta_salidas=tmp_path, segmentar=False)
    assert r.info["proyecto"] == "oscuro" and r.info["preset"] == "heroes-mazmorra"
    assert "dark gothic dungeon" in r.info["prompt"]
    assert r.info["prompt_negativo"].startswith("bright colors")
    assert r.info["fin_boceto"] < 0.5


def test_carpetas_no_se_pisan(tmp_path):
    from motor.forja import carpeta_libre

    a = carpeta_libre(tmp_path, "2026-10-16_120000_knight")
    b = carpeta_libre(tmp_path, "2026-10-16_120000_knight")
    assert a != b and a.is_dir() and b.name.endswith("_2")


def test_tipo_con_parecido_propio_y_sin_ambientacion(tmp_path, boceto_digital, generador_falso):
    """Efectos: el boceto como guía suelta (parecido propio del tipo) y sin ambientación."""
    fuego = {"id": "fuego", "nombre": "Fuego", "prompt": "fireball", "negativo": "",
             "parecido_boceto": 0.15, "usar_ambientacion": False}
    proyecto = {"id": "oscuro", "preset": "heroes-mazmorra", "ambientacion": "green runes glow",
                "parecido_boceto": 0.8, "tipos": [fuego]}
    r = forjar(boceto_digital, None, "", proyecto=proyecto, tipo="fuego", variantes=1, traducir=False,
               generador=generador_falso, carpeta_salidas=tmp_path, segmentar=False)
    assert "green runes glow" not in r.info["prompt"] and "fireball" in r.info["prompt"]
    assert r.info["fin_boceto"] < 0.45  # el del tipo (0.15), no el del proyecto (0.8)
    # Un parecido pedido explícitamente (el deslizador de la app) manda sobre el del tipo.
    r = forjar(boceto_digital, None, "", proyecto=proyecto, tipo="fuego", variantes=1, traducir=False,
               parecido_boceto=0.9, generador=generador_falso, carpeta_salidas=tmp_path, segmentar=False)
    assert r.info["fin_boceto"] > 0.85
