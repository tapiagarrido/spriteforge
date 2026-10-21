import json
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from PIL import Image

from motor import mapas
from tests.conftest import PRESET_HEROES


def textura_periodica(ancho, alto, semilla=0, x=True, y=True):
    """Textura con periodo exacto (suma de senos de periodo entero) y algo de color."""
    rng = np.random.default_rng(semilla)
    yy, xx = np.mgrid[0:alto, 0:ancho]
    v = np.zeros((alto, ancho))
    for _ in range(4):
        kx, ky = rng.integers(1, 6, 2)
        v += np.sin(2 * np.pi * (kx * xx / ancho * x + ky * yy / alto * y) + rng.uniform(0, 6))
    v = (v - v.min()) / (np.ptp(v) + 1e-9)
    base = np.array(rng.integers(40, 200, 3))
    rgb = np.clip(base[None, None, :] * (0.5 + v[..., None]), 0, 255).astype(np.uint8)
    if not y:  # capa de fondo: cielo blanco arriba, silueta abajo
        perfil = (alto * (0.4 + 0.2 * np.sin(2 * np.pi * 2 * xx[0] / ancho))).astype(int)
        for col in range(ancho):
            rgb[:perfil[col], col] = 255
    return Image.fromarray(rgb)


def generador_falso(prompt, negativo, n, semilla, ancho, alto, x=True, y=True, control=None):
    return [textura_periodica(ancho, alto, semilla + i, x, y) for i in range(n)]


@pytest.fixture
def paleta():
    estilo = json.loads((PRESET_HEROES / "estilo.json").read_text())
    return mapas.paleta_lab(estilo["paleta"])


def es_periodica(a: np.ndarray, eje: int) -> bool:
    """El salto entre el último y el primer píxel no es mayor que los saltos internos."""
    a = a.astype(float)
    saltos = np.abs(np.diff(a, axis=eje)).mean(axis=tuple(i for i in range(a.ndim) if i != eje))
    vuelta = np.abs(np.take(a, 0, axis=eje) - np.take(a, -1, axis=eje)).mean()
    return vuelta <= saltos.max() * 1.05 + 1


def test_redimensionar_conserva_periodicidad():
    tex = textura_periodica(768, 768, 3)
    pequena = np.array(mapas.redimensionar_periodico(tex, 32, 32))
    assert pequena.shape == (32, 32, 3)
    assert es_periodica(pequena, 0) and es_periodica(pequena, 1)


def test_baldosa_usa_la_paleta_y_pocos_colores(paleta):
    rgb, lab = paleta
    b = mapas.a_baldosa(textura_periodica(768, 768, 1), 32, rgb, lab)
    colores = {tuple(c) for c in b.reshape(-1, 3)}
    assert colores <= {tuple(c) for c in rgb} and len(colores) <= mapas.COLORES_MATERIAL


def test_variantes_comparten_borde(paleta):
    rgb, lab = paleta
    tiles = [mapas.a_baldosa(textura_periodica(768, 768, s), 32, rgb, lab) for s in range(4)]
    variantes = mapas.variantes_intercambiables(tiles[0], tiles[1:])
    for v in variantes[1:]:
        for borde in (np.s_[0], np.s_[-1], np.s_[:, 0], np.s_[:, -1]):
            assert np.array_equal(v[borde], variantes[0][borde])
        assert not np.array_equal(v, variantes[0])  # el interior cambia


def test_transiciones_casan_con_sus_vecinas(paleta):
    rgb, lab = paleta
    a = mapas.a_baldosa(textura_periodica(768, 768, 1), 32, rgb, lab)
    b = mapas.a_baldosa(textura_periodica(768, 768, 2), 32, rgb, lab)
    tiles = mapas.transiciones(a, b, semilla=5)
    assert len(tiles) == 16
    assert np.array_equal(tiles[(0, 0, 0, 0)], a) and np.array_equal(tiles[(1, 1, 1, 1)], b)
    ruido = mapas.ruido_periodico(32, 5)
    for izq in mapas.ESQUINAS:  # borde derecho de una = borde izquierdo de la de al lado
        for der in mapas.ESQUINAS:
            if (izq[1], izq[2]) != (der[0], der[3]):
                continue
            mi = mapas.mascara_esquinas(izq, 32, ruido)
            md = mapas.mascara_esquinas(der, 32, ruido)
            assert np.array_equal(mi[:, -1], md[:, 0])


def test_plataformas(paleta):
    rgb, lab = paleta
    sup = mapas.a_baldosa(textura_periodica(768, 768, 1), 32, rgb, lab)
    rel = mapas.a_baldosa(textura_periodica(768, 768, 2), 32, rgb, lab)
    bloques = mapas.plataformas(sup, rel)
    assert len(bloques) == 15 and (0, 0, 0, 0) not in bloques
    lleno = bloques[(1, 1, 1, 1)]
    assert lleno.shape == (32, 32, 4) and (lleno[..., 3] == 255).all()
    mitad = bloques[(0, 0, 1, 1)]  # suelo abajo, aire arriba
    assert (mitad[:8, :, 3] == 0).all() and (mitad[-8:, :, 3] == 255).all()
    # La superficie está justo debajo del aire; más abajo, el relleno.
    fila_superficie = int(np.argmax(mitad[:, 16, 3] > 0)) + 2
    assert tuple(mitad[fila_superficie, 16, :3]) in {tuple(c) for c in sup.reshape(-1, 3)} | {
        tuple(mapas.oscuro_de(rel))}


def test_perfil_periodico_y_en_su_franja():
    for tipo, (arriba, abajo) in mapas.PERFILES.items():
        filas = mapas.perfil(1344, 576, tipo, semilla=3)
        assert filas.min() >= round(arriba * 576) - 1 and filas.max() <= round(abajo * 576) + 1
        saltos = np.abs(np.diff(filas))
        assert abs(int(filas[0]) - int(filas[-1])) <= saltos.max() + 1  # enlaza al repetirse
    boceto = np.array(mapas.boceto_perfil(filas, 576))
    assert boceto[filas[100], 100] == 255 and boceto[0, 100] == 0


def test_capa_de_fondo_recortada_por_el_perfil(paleta):
    rgb, lab = paleta
    im = textura_periodica(1344, 576, 4, y=False)
    filas = mapas.perfil(1344, 576, "montanas", semilla=1)
    capa = mapas.capa_fondo(im, 288, rgb, lab, filas)
    assert capa.shape == (288, 672, 4)
    col = 300  # en la capa reducida, la silueta está a la mitad de altura del perfil
    corte = round(filas[col * 2] / 2)
    assert (capa[:corte - 1, col, 3] == 0).all() and (capa[corte + 1:, col, 3] == 255).all()
    cielo = mapas.capa_fondo(im, 288, rgb, lab)
    assert (cielo[..., 3] == 255).all()


def test_forjar_mapa_cenital(tmp_path):
    proyecto = {"id": "p", "nombre": "Prueba", "perspectiva": "cenital", "baldosa": 16,
                "ambientacion": "dark dungeon"}
    info = mapas.forjar_mapa(proyecto, PRESET_HEROES, tmp_path, generador=generador_falso,
                             materiales={"a": "grass", "b": "dirt"}, variantes=3, traducir=False)
    carpeta = tmp_path / "mapas" / info["carpeta"]
    assert info["baldosas"] == 16 + 2 + 2  # transiciones + variantes de cada suelo puro
    hoja = Image.open(carpeta / "tileset.png")
    assert hoja.size == (8 * 16, 3 * 16)
    raiz = ET.parse(carpeta / "tileset.tsx").getroot()
    assert raiz.get("tilewidth") == "16" and raiz.find("image").get("source") == "tileset.png"
    wang = raiz.find("wangsets/wangset")
    assert wang.get("type") == "corner" and len(wang.findall("wangcolor")) == 2
    ids = [t.get("wangid") for t in wang.findall("wangtile")]
    assert len(ids) == 20 and ids.count("0,1,0,1,0,1,0,1") == 3  # A puro y sus 2 variantes
    assert (carpeta / "mapa_prueba.png").exists()
    assert "grass" in info["prompts"]["a"] and "top-down" in info["prompts"]["a"]


def test_forjar_mapa_lateral(tmp_path):
    proyecto = {"id": "p", "nombre": "Prueba", "perspectiva": "lateral", "baldosa": 16}
    info = mapas.forjar_mapa(proyecto, PRESET_HEROES, tmp_path, generador=generador_falso,
                             materiales={"superficie": "grass", "relleno": "dirt", "cielo": "sunset"},
                             variantes=2, traducir=False)
    carpeta = tmp_path / "mapas" / info["carpeta"]
    assert info["baldosas"] == 15 + 1
    for capa in ("cielo", "lejos", "cerca"):
        assert Image.open(carpeta / f"fondo_{capa}.png").height == 16 * 9
    wang = ET.parse(carpeta / "tileset.tsx").getroot().find("wangsets/wangset")
    assert len(wang.findall("wangcolor")) == 1  # el aire no tiene color
    assert Image.open(carpeta / "mapa_prueba.png").size == (20 * 16, 9 * 16)


def test_forjar_mapa_valida(tmp_path):
    cenital = {"perspectiva": "cenital", "baldosa": 16}
    with pytest.raises(ValueError, match="segundo suelo"):
        mapas.forjar_mapa(cenital, PRESET_HEROES, tmp_path, generador=generador_falso,
                          materiales={"a": "grass"}, traducir=False)
    with pytest.raises(ValueError, match="no válidas"):
        mapas.forjar_mapa(cenital, PRESET_HEROES, tmp_path, generador=generador_falso,
                          materiales={"a": "grass"}, piezas=["fondo"], traducir=False)
    # Solo suelos: sin Wang set, baldosas sueltas.
    info = mapas.forjar_mapa(cenital, PRESET_HEROES, tmp_path, generador=generador_falso,
                             materiales={"a": "grass"}, piezas=["suelos"], variantes=2, traducir=False)
    assert info["baldosas"] == 2 and "mapa_prueba" not in info["archivos"]


def test_mosaico_hace_periodica_la_convolucion():
    torch = pytest.importorskip("torch")
    conv = torch.nn.Conv2d(1, 1, 3, padding=1, bias=False)
    torch.nn.init.ones_(conv.weight)
    x = torch.zeros(1, 1, 4, 4)
    x[0, 0, :, 0] = 1  # columna izquierda
    with mapas.mosaico(conv, x=True, y=False):
        y = conv(x)
    assert y[0, 0, 1, -1] > 0  # la columna derecha "ve" la izquierda
    assert conv(x)[0, 0, 1, -1] == 0  # restaurado al salir


# --- legibilidad: que los personajes no se pierdan sobre el suelo ---------------------------

def hue(rgb):
    import colorsys

    return colorsys.rgb_to_hsv(*(np.array(rgb[:3], float) / 255))[0] * 360


def test_suavizar_comprime_la_luz_y_conserva_el_matiz():
    colores = np.array([[20, 90, 30], [60, 200, 70], [30, 40, 150], [190, 40, 40]], np.uint8)
    suave = mapas.suavizar(colores, 0.8)
    bajo, alto = mapas.banda_suelo(0.8)
    from motor.color import rgb_a_lab

    luz = rgb_a_lab(suave)[:, 0]
    assert luz.min() >= bajo - 2 and luz.max() <= alto + 2  # dentro de la franja del suelo
    assert np.ptp(rgb_a_lab(suave)[:, 0]) < np.ptp(rgb_a_lab(colores.astype(float))[:, 0])
    for antes, despues in zip(colores, suave, strict=True):
        diferencia = abs((hue(antes) - hue(despues) + 180) % 360 - 180)
        assert diferencia < 15  # el verde sigue verde, el azul azul y el rojo rojo
    assert np.array_equal(mapas.suavizar(colores, 0), colores.astype(float))  # nivel 0: tal cual


def test_paleta_del_suelo_es_la_del_estilo_apagada(paleta):
    rgb, _ = paleta
    apagada, lab = mapas.paleta_suelo(rgb, 0.7)
    bajo, alto = mapas.banda_suelo(0.7)
    assert lab[:, 0].min() >= bajo - 2 and lab[:, 0].max() <= alto + 2
    assert len(apagada) <= len(rgb) and len(np.unique(apagada, axis=0)) == len(apagada)
    # Conserva las gamas: sigue habiendo verdes y azules, no todo marrón.
    matices = {round(hue(c) / 60) % 6 for c in apagada if max(c) - min(c) > 25}
    assert len(matices) >= 3
    assert mapas.paleta_suelo(rgb, 0)[0] is rgb


def sprite_vivo():
    a = np.zeros((16, 12, 4), np.uint8)
    a[2:14, 2:10] = (230, 190, 60, 255)  # cuerpo claro y vivo
    a[2:14, 2] = a[2:14, 9] = a[2, 2:10] = a[13, 2:10] = (12, 10, 14, 255)  # contorno casi negro
    return Image.fromarray(a, "RGBA")


def test_camuflaje_mide_si_el_personaje_se_parece_al_suelo():
    sp = sprite_vivo()
    suelo_igual = np.full((4, 4, 3), (230, 190, 60), np.uint8)
    suelo_distinto = np.full((4, 4, 3), (70, 80, 90), np.uint8)
    igual = mapas.camuflaje([suelo_igual], [sp])
    distinto = mapas.camuflaje([suelo_distinto], [sp])
    # Se cuentan colores distintos: el amarillo del cuerpo se pierde (1 de 2), el contorno negro no.
    assert igual["medio"] == 0.5 and distinto["medio"] == 0.0
    assert mapas.camuflaje([suelo_igual], []) == {"medio": 0.0, "peor": 0.0, "personajes": 0}
    mezcla = mapas.camuflaje([suelo_igual], [sp, Image.new("RGBA", (4, 4), (0, 0, 0, 0))])
    assert mezcla["personajes"] == 1  # un sprite vacío no cuenta


def test_escena_con_personajes_cenital_y_lateral():
    mapa = Image.new("RGBA", (16 * 32, 10 * 32), (60, 60, 60, 255))
    sp = sprite_vivo()
    for perspectiva in ("cenital", "lateral"):
        escena = mapas.escena_con_personajes(mapa, [sp, sp, sp], perspectiva, 32)
        assert escena.size == mapa.size
        a = np.array(escena)
        assert (a[..., :3] == (230, 190, 60)).all(-1).sum() == 3 * 10 * 6  # los tres, enteros y pegados
    assert mapas.escena_con_personajes(mapa, [], "cenital", 32).tobytes() == mapa.tobytes()


def test_agua_procedural():
    assert mapas.es_agua("agua azul clara con ondas") and mapas.es_agua("charcos de agua")
    assert not mapas.es_agua("hielo azul") and not mapas.es_agua("lava brillante")
    assert not mapas.es_agua("pasto oscuro")
    clara = np.array(mapas.textura_agua("agua clara", 768, 1)).astype(float)
    oscura = np.array(mapas.textura_agua("agua oscura", 768, 1)).astype(float)
    assert oscura.mean() < clara.mean()
    assert 180 <= hue(clara.reshape(-1, 3).mean(0)) <= 240  # azul
    salto = np.abs(clara[:, 0] - clara[:, -1]).mean()
    assert salto < np.abs(np.diff(clara, axis=1)).mean() * 2  # sin costura


def test_forjar_mapa_elige_el_nivel_y_hace_la_escena(tmp_path):
    proyecto = {"id": "p", "nombre": "Prueba", "perspectiva": "cenital", "baldosa": 16}
    personajes = [sprite_vivo(), sprite_vivo()]
    kw = dict(generador=generador_falso, materiales={"a": "grass", "b": "dirt"}, variantes=2,
              traducir=False)
    auto = mapas.forjar_mapa(proyecto, PRESET_HEROES, tmp_path, personajes=personajes,
                             piso="catacumbas", **kw)
    leg = auto["legibilidad"]
    assert leg["automatica"] and leg["nivel"] in mapas.NIVELES_AUTO and leg["camuflaje"]["personajes"] == 2
    assert "escena_prueba" in auto["archivos"] and auto["piso"] == "catacumbas"
    assert (tmp_path / "mapas" / auto["carpeta"] / "escena_prueba_x2.png").exists()
    assert auto["carpeta"].endswith("prueba-catacumbas")
    # Fijo: se respeta; sin personajes: nivel por defecto y sin escena.
    fijo = mapas.forjar_mapa(proyecto, PRESET_HEROES, tmp_path, personajes=personajes, legibilidad=0.2, **kw)
    assert fijo["legibilidad"]["nivel"] == 0.2 and not fijo["legibilidad"]["automatica"]
    sin = mapas.forjar_mapa(proyecto, PRESET_HEROES, tmp_path, **kw)
    assert sin["legibilidad"]["nivel"] == mapas.NIVEL_POR_DEFECTO and "escena_prueba" not in sin["archivos"]
    with pytest.raises(ValueError, match="legibilidad"):
        mapas.forjar_mapa(proyecto, PRESET_HEROES, tmp_path, legibilidad=1.5, **kw)


def test_la_legibilidad_reduce_el_camuflaje(tmp_path):
    proyecto = {"id": "p", "nombre": "Prueba", "perspectiva": "cenital", "baldosa": 16}
    kw = dict(generador=generador_falso, materiales={"a": "grass", "b": "dirt"}, variantes=2,
              traducir=False, personajes=[sprite_vivo()])
    sin = mapas.forjar_mapa(proyecto, PRESET_HEROES, tmp_path, legibilidad=0.0, **kw)
    con = mapas.forjar_mapa(proyecto, PRESET_HEROES, tmp_path, legibilidad=1.0, **kw)
    assert con["legibilidad"]["camuflaje"]["medio"] <= sin["legibilidad"]["camuflaje"]["medio"]
    assert con["legibilidad"]["luminosidad_suelo"] <= sin["legibilidad"]["luminosidad_suelo"] + 8


def test_muestras_de_personajes(tmp_path):
    for nombre, proyecto in (("a", "p"), ("b", "p"), ("c", "otro")):
        d = tmp_path / nombre
        d.mkdir()
        sprite_vivo().save(d / "sprite_01.png")
        info = {"proyecto": proyecto, "variantes": [{"sprite": "sprite_01.png"}]}
        (d / "info.json").write_text(json.dumps(info))
    assert len(mapas.muestras_de_personajes(tmp_path, "p")) == 2
    assert len(mapas.muestras_de_personajes(tmp_path, "p", maximo=1)) == 1
    assert mapas.muestras_de_personajes(tmp_path, None) == []
