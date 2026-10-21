import json
import time

import numpy as np

from motor import generar as gen


def test_tamano_generacion_respeta_proporcion_y_multiplos():
    ancho, alto = gen.tamano_generacion(16, 28, 768)
    assert ancho % 64 == 0 and alto % 64 == 0
    assert abs(ancho / alto - 16 / 28) < 0.08
    assert 0.8 < ancho * alto / 768**2 < 1.2
    assert gen.tamano_generacion(16, 16, 512) == (512, 512)


def test_construir_prompt_incluye_fondo(preset_heroes):
    estilo = json.loads((preset_heroes / "estilo.json").read_text())
    positivo, negativo = gen.construir_prompt(estilo, "  knight with red armor ")
    assert positivo.startswith("pixel art, plain white background, knight with red armor, ")
    assert positivo.endswith(estilo["generacion"]["prompt_base"].split(", ")[-1])
    assert "blue" not in positivo  # nombrar un color de fondo tiñe al personaje
    assert negativo == estilo["generacion"]["prompt_negativo"]


def test_rejilla_referencia_es_cuadrada_con_fondo(preset_heroes):
    estilo = json.loads((preset_heroes / "estilo.json").read_text())
    ref = gen.rejilla_referencia(preset_heroes, estilo, lado=512)
    assert ref.size == (512, 512)
    assert "#{:02x}{:02x}{:02x}".format(*ref.getpixel((0, 0))) == estilo["fondo_generacion"]
    assert len(np.unique(np.array(ref).reshape(-1, 3), axis=0)) > 10  # contiene los ejemplos


def test_peticion_desde_preset_usa_valores_y_cambios(preset_heroes, boceto_digital):
    p = gen.peticion_desde_preset(preset_heroes, boceto_digital.convert("L"), "mage",
                                  variantes=2, fuerza_estilo=0.45, semilla=None)
    assert p.variantes == 2 and p.fuerza_estilo == 0.45
    assert p.semilla == 0  # None no pisa el valor por defecto
    assert (p.fuerza_boceto, p.fin_boceto) == gen.controlnet_desde_parecido(0.5)
    assert (p.ancho, p.alto) == boceto_digital.size


def test_parecido_del_proyecto_y_explicito(preset_heroes, boceto_digital):
    proyecto = {"parecido_boceto": 1.0}
    p = gen.peticion_desde_preset(preset_heroes, boceto_digital.convert("L"), "x", proyecto=proyecto)
    assert (p.fuerza_boceto, p.fin_boceto) == (1.0, 1.0)
    p = gen.peticion_desde_preset(preset_heroes, boceto_digital.convert("L"), "x", proyecto=proyecto,
                                  parecido_boceto=0.0)
    assert (p.fuerza_boceto, p.fin_boceto) == (0.35, 0.3)


def test_controlnet_desde_parecido_es_monotono():
    valores = [gen.controlnet_desde_parecido(p / 10) for p in range(11)]
    assert valores == sorted(valores)
    assert gen.controlnet_desde_parecido(5) == (1.0, 1.0)  # se acota a [0, 1]


def test_prompt_con_proyecto(preset_heroes):
    estilo = json.loads((preset_heroes / "estilo.json").read_text())
    proyecto = {"ambientacion": "dark gothic dungeon, grim", "negativo": "bright colors, cute"}
    positivo, negativo = gen.construir_prompt(estilo, "knight", proyecto)
    assert positivo.startswith("pixel art, plain white background, knight, dark gothic dungeon, grim")
    assert negativo.startswith("bright colors, cute, ")
    assert gen.contar_tokens(positivo) <= gen.LIMITE_TOKENS
    assert gen.contar_tokens(negativo) <= gen.LIMITE_TOKENS


def test_prompt_largo_suelta_estilo_pero_no_lo_importante(preset_heroes):
    estilo = json.loads((preset_heroes / "estilo.json").read_text())
    descripcion = ", ".join(["huge armored knight with a long flowing red cape"] * 3)
    proyecto = {"ambientacion": "dark gothic dungeon, grim and eerie mood"}
    positivo, _ = gen.construir_prompt(estilo, descripcion, proyecto)
    assert descripcion in positivo and "dark gothic dungeon" in positivo
    assert gen.contar_tokens(positivo) <= gen.LIMITE_TOKENS
    assert "side view" not in positivo  # matiz final del preset, el primero en soltarse


def test_info_gpu_no_falla():
    info = gen.info_gpu()
    assert "gpu" in info


def test_gestor_carga_bajo_demanda_y_libera():
    cargas = []
    g = gen.GestorModelos(minutos_inactividad=0, cargador=lambda: cargas.append(1) or object())
    assert not g.cargada
    a = g.obtener()
    assert g.obtener() is a and len(cargas) == 1
    g.liberar()
    assert not g.cargada
    g.obtener()
    assert len(cargas) == 2


def test_liberar_suelta_tambien_las_tuberias_derivadas():
    """Las tuberías de poses (img2img) y de mapas comparten componentes con la principal:
    si quedaran referenciadas, liberar no devolvería la VRAM."""
    import gc
    import weakref

    class Tuberia:
        pass

    g = gen.GestorModelos(minutos_inactividad=0, cargador=Tuberia)
    principal = g.obtener()
    g._img2img, g._img2img_de = Tuberia(), principal
    g._texto, g._texto_de = Tuberia(), principal
    vivas = [weakref.ref(x) for x in (principal, g._img2img, g._texto)]
    del principal
    g.liberar()
    gc.collect()
    assert all(r() is None for r in vivas)


def test_gestor_libera_por_inactividad(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)  # el vigilante no espera
    g = gen.GestorModelos(minutos_inactividad=0.0001, cargador=object)
    g.obtener()
    g._ultimo_uso -= 60
    g._vigilante.join(timeout=2)
    assert not g.cargada


def test_tipo_sin_ambientacion(preset_heroes):
    """Efectos como una bola de fuego: la ambientación del mundo ("brillo verde") los teñía."""
    import json

    estilo = json.loads((preset_heroes / "estilo.json").read_text())
    proyecto = {"ambientacion": "green glowing runes, cold colors"}
    con = {"prompt": "fireball projectile"}
    sin = {**con, "usar_ambientacion": False}
    assert "green glowing runes" in gen.construir_prompt(estilo, "", proyecto, con)[0]
    positivo = gen.construir_prompt(estilo, "", proyecto, sin)[0]
    assert "green glowing runes" not in positivo and "fireball projectile" in positivo
    assert gen.previsualizar(estilo, "", proyecto, sin)["ambientacion_fuera"] == []
