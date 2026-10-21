import json
from collections import deque
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from motor import mapa_grande as mg
from motor import mapas, qwen
from motor.proyectos import cargar

RAIZ = Path(__file__).resolve().parent.parent


def paleta():
    estilo = json.loads((RAIZ / "presets" / "criaturas-64" / "estilo.json").read_text())
    return mapas.paleta_lab(estilo["paleta"])


def conectado(suelo: np.ndarray) -> bool:
    celdas = {(y, x) for y, x in zip(*np.nonzero(suelo), strict=True)}
    inicio = next(iter(celdas))
    visto, cola = {inicio}, deque([inicio])
    while cola:
        y, x = cola.popleft()
        for v in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if v in celdas and v not in visto:
                visto.add(v)
                cola.append(v)
    return visto == celdas


@pytest.mark.parametrize("semilla", [0, 3, 11, 42])
def test_plano_conectado_con_borde_de_roca_y_sin_solapes(semilla):
    suelo, salas = mg.plano_mazmorra(64, 48, semilla)
    assert len(salas) >= 4 and conectado(suelo)
    assert not suelo[:3].any() and not suelo[-3:].any() and not suelo[:, :3].any() and not suelo[:, -3:].any()
    for i, (x, y, w, h) in enumerate(salas):
        for x2, y2, w2, h2 in salas[i + 1:]:
            assert x > x2 + w2 + 4 or x2 > x + w + 4 or y > y2 + h2 + 4 or y2 > y + h + 4


def test_plano_es_determinista():
    a, _ = mg.plano_mazmorra(48, 40, 5)
    b, _ = mg.plano_mazmorra(48, 40, 5)
    assert (a == b).all() and not (a == mg.plano_mazmorra(48, 40, 6)[0]).all()


def test_trazados_son_periodicos():
    for im in (mg.trazado_losas(4, 1, 256), mg.trazado_ladrillos(2, 256)):
        a = np.asarray(im, dtype=float)
        pares = abs(a[:, 0] - a[:, -1]).mean() + abs(a[0] - a[-1]).mean()
        vecinas = abs(a[:, 100] - a[:, 101]).mean() + abs(a[100] - a[101]).mean()
        assert pares < vecinas * 3 + 8  # el borde opuesto es tan continuo como dos columnas contiguas


def test_agua_procedural_periodica_y_con_ondas():
    a = mg.agua_procedural((26, 64, 56), 1, 128).astype(float)
    assert a.shape == (128, 128, 3) and a.std() > 3
    assert abs(a[:, 0] - a[:, -1]).mean() < 25


def superficies_falsas():
    r = np.random.default_rng(0)
    pal, _ = paleta()
    return {n: pal[r.integers(20, 40, (mg.PERIODO, mg.PERIODO))].astype(np.uint8)
            for n in ("suelo", "muro", "agua", "musgo")}


def test_pintar_solo_usa_la_paleta_y_pone_muros_alrededor():
    pal, lab = paleta()
    suelo, salas = mg.plano_mazmorra(40, 32, 2, 5)
    im = mg.pintar(suelo, salas, superficies_falsas(), pal, lab, 2)
    assert im.size == (40 * 32, 32 * 32)
    usados = {tuple(c) for c in np.asarray(im).reshape(-1, 3)}
    assert usados <= {tuple(c) for c in pal}  # (en este test la paleta es la del proyecto sola)
    # un píxel de suelo y otro de roca lejana son distintos
    y, x = (int(v) for v in np.argwhere(suelo)[0])
    assert im.getpixel((x * 32 + 16, y * 32 + 16)) != im.getpixel((2, 2))


def test_tmx_tiene_mapa_y_colision():
    suelo = np.zeros((4, 5), bool)
    suelo[1:3, 1:4] = True
    x = mg.tmx("mapa.png", "colision.png", 5, 4, ~suelo)
    assert 'width="5" height="4"' in x and 'name="mapa"' in x and 'name="colision"' in x
    assert 'firstgid="21"' in x  # 5×4 baldosas del mapa + 1
    fila_media = x.split("<data")[2].split(">", 1)[1].split("\n")[2]  # capa de colisión, fila 1
    assert fila_media == "21,0,0,0,21,"


def test_forjar_mapa_grande_reutiliza_las_superficies_sin_llamar_a_qwen(tmp_path, monkeypatch):
    sup = tmp_path / "sup"
    sup.mkdir()
    for n, a in superficies_falsas().items():
        Image.fromarray(a).save(sup / f"{n}.png")

    def prohibido(*a, **k):
        raise AssertionError("no debe llamar a Qwen")

    monkeypatch.setattr(qwen, "editar", prohibido)
    monkeypatch.setattr(qwen, "texto_a_imagen", prohibido)
    pr = cargar("mazmorra-oscura")
    info = mg.forjar_mapa_grande("catacumbas", pr, RAIZ / "presets" / pr["preset"], tmp_path / "out",
                                 ancho=40, alto=32, semilla=4, superficies=sup, nombre="prueba",
                                 decorar=False)
    d = tmp_path / "out" / "prueba"
    assert info["pixeles"] == [1280, 1024] and Image.open(d / "mapa.png").size == (1280, 1024)
    assert (d / "mapa.tmx").exists() and (d / "colision.png").exists()
    plano = json.loads((d / "plano.json").read_text())
    assert len(plano["suelo"]) == 32 and len(plano["suelo"][0]) == 40


def test_validaciones(tmp_path):
    pr = cargar("mazmorra-oscura")
    base = (pr, RAIZ / "presets" / pr["preset"], tmp_path)
    with pytest.raises(ValueError, match="sin definir"):
        mg.forjar_mapa_grande("nada", *base)
    with pytest.raises(ValueError, match="entre 24 y 160"):
        mg.forjar_mapa_grande("catacumbas", *base, ancho=10)


def test_trazado_de_ondas_es_periodico_y_con_relieve():
    a = np.asarray(mg.trazado_ondas(2, 256), dtype=float)
    assert a.std() > 15 and a.shape == (256, 256, 3)
    assert abs(a[:, 0] - a[:, -1]).mean() < 25 and abs(a[0] - a[-1]).mean() < 25


def test_superficie_agua_usa_la_rampa_y_casi_todo_es_agua_oscura():
    ruido = np.random.default_rng(1).integers(0, 256, (256, 256, 3)).astype(np.uint8)
    s = mg.superficie_agua(Image.fromarray(ruido))
    colores, cuenta = np.unique(s.reshape(-1, 3), axis=0, return_counts=True)
    assert s.shape == (mg.PERIODO_AGUA, mg.PERIODO_AGUA, 3)
    assert {tuple(c) for c in colores} <= {tuple(c) for c in mg.RAMPA_AGUA}
    assert cuenta.max() / cuenta.sum() > 0.6  # el tono dominante (azul oscuro) es la mayoría
    azul = np.array(mg.RAMPA_AGUA)
    assert (azul[:, 2] > azul[:, 0]).all() and (azul[:, 2] > azul[:, 1]).all()  # todos los tonos son azulados


def test_el_mapa_solo_usa_la_paleta_del_proyecto_mas_los_azules_del_agua(tmp_path):
    sup = tmp_path / "sup"
    sup.mkdir()
    for n, a in superficies_falsas().items():
        Image.fromarray(a).save(sup / f"{n}.png")
    pr = cargar("mazmorra-oscura")
    mg.forjar_mapa_grande("catacumbas", pr, RAIZ / "presets" / pr["preset"], tmp_path / "o",
                          ancho=40, alto=32, semilla=2, superficies=sup, nombre="m", decorar=False)
    pal, _ = paleta()
    permitidos = {tuple(c) for c in pal} | {tuple(c) for c in mg.RAMPA_AGUA}
    usados = np.asarray(Image.open(tmp_path / "o" / "m" / "mapa.png")).reshape(-1, 3)
    assert {tuple(c) for c in usados} <= permitidos


def test_todos_los_ambientes_tienen_trazados_validos():
    for amb, cfg in mg.AMBIENTES.items():
        for parte in ("suelo", "muro"):
            im = mg.trazado(cfg[parte], 1)  # no lanza (las hiladas dividen el lado) y es cuadrado
            assert im.size == (mg.FUENTE, mg.FUENTE), (amb, parte)
        assert len(cfg["liquido"]["rampa"]) == len(cfg["liquido"]["cortes"]) + 1, amb
        assert set(cfg) >= {"tono", "suelo", "muro", "cobertura", "liquido", "charcos", "umbral"}


def test_ladrillos_que_no_dividen_el_lado_se_rechazan():
    import pytest

    with pytest.raises(ValueError, match="no dividen"):
        mg.trazado_ladrillos(0, 1024, filas=5, columnas=3)
