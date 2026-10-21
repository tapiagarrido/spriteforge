import json

import numpy as np
import pytest
from PIL import Image

from motor import animar


def colores(im: Image.Image) -> set:
    a = np.array(im.convert("RGBA"))
    return {tuple(c) for c in a[a[..., 3] > 0][:, :3]}


@pytest.fixture
def sprite(preset_heroes):
    return Image.open(preset_heroes / "ejemplo3_mago.png").convert("RGBA")


def test_todas_las_animaciones_conservan_la_paleta(sprite):
    fig, anims = animar.animar(sprite)
    assert set(anims) == set(animar.animaciones_estandar())
    paleta = colores(sprite)
    tamano = fig.sprite.shape[1], fig.sprite.shape[0]
    for nombre, frames in anims.items():
        assert len(frames) == len(animar.animaciones_estandar()[nombre].poses)
        for f in frames:
            assert f.size == tamano
            assert colores(f) <= paleta, nombre  # ningún color nuevo
            assert set(np.unique(np.array(f)[..., 3])) <= {0, 255}  # sin semitransparencias


def test_pose_neutra_es_el_sprite_sin_cambios(sprite):
    fig, anims = animar.animar(sprite, ["ataque"])
    assert np.array_equal(np.array(anims["ataque"][0]), fig.sprite)


def test_ataque_avanza_hacia_donde_mira(sprite):
    fig, anims = animar.animar(sprite, ["ataque"], mira=1)

    def centro(f):
        return np.nonzero(np.array(f)[..., 3])[1].mean()

    assert centro(anims["ataque"][4]) > centro(anims["ataque"][0]) + 1  # impacto: adelante
    assert centro(anims["ataque"][2]) < centro(anims["ataque"][0])  # anticipación: atrás
    _, izq = animar.animar(sprite, ["ataque"], mira=-1)
    assert centro(izq["ataque"][4]) < centro(izq["ataque"][0]) - 1


def test_muerte_termina_casi_disuelto(sprite):
    _, anims = animar.animar(sprite, ["muerte"])
    opacos = [(np.array(f)[..., 3] > 0).sum() for f in anims["muerte"]]
    assert opacos[-1] < 0.1 * opacos[1]


def test_dano_tiene_destello(sprite):
    _, anims = animar.animar(sprite, ["dano"])
    assert len(colores(anims["dano"][0])) == 1  # todo del color más claro


def test_mirando_hacia():
    alfa = np.zeros((40, 40), bool)
    alfa[20:40, 10:20] = True  # cuerpo
    alfa[5:15, 18:28] = True  # cabeza adelantada a la derecha
    assert animar.mirando_hacia(alfa) == 1
    assert animar.mirando_hacia(alfa[:, ::-1]) == -1


def test_fotograma_clave_se_integra(sprite):
    clave = sprite.transpose(Image.FLIP_TOP_BOTTOM)  # cualquier dibujo del mismo tamaño
    _, anims = animar.animar(sprite, ["reposo"], claves={"mordisco": clave})
    assert set(anims) == {"reposo", "mordisco"}
    golpe = anims["mordisco"][4]
    assert colores(golpe) <= colores(clave)
    with pytest.raises(ValueError):
        animar.animar(sprite, claves={"x": clave.resize((8, 8))})


def test_exportar(tmp_path, sprite):
    r = animar.exportar(sprite, tmp_path, ["reposo", "ataque"])
    for archivo in ("hoja.png", "hoja.json", "hoja_x4.png", "animacion.json", "reposo.gif", "ataque.gif"):
        assert (tmp_path / archivo).exists(), archivo
    meta = json.loads((tmp_path / "hoja.json").read_text())
    etiquetas = {t["name"]: t for t in meta["meta"]["frameTags"]}
    assert etiquetas["reposo"]["from"] == 0 and etiquetas["ataque"]["to"] == len(meta["frames"]) - 1
    assert etiquetas["ataque"]["repeat"] == "1" and etiquetas["reposo"]["repeat"] == "0"
    hoja = Image.open(tmp_path / "hoja.png")
    ancho, alto = r["lienzo"]
    assert hoja.size == (ancho * 8, alto * 2)
    gif = Image.open(tmp_path / "ataque.gif")
    assert gif.n_frames == 8


def test_gif_conserva_colores_y_transparencia(tmp_path, sprite):
    """El GIF debe verse igual que los fotogramas (antes salía negro: se perdía la paleta)."""
    _, anims = animar.animar(sprite, ["reposo"])
    frames = anims["reposo"]
    animar.guardar_gif(frames, [100] * len(frames), tmp_path / "r.gif")
    gif = Image.open(tmp_path / "r.gif")
    # Pillow fusiona los fotogramas idénticos consecutivos (sumando su duración).
    unicos = [f for i, f in enumerate(frames) if i == 0 or f.tobytes() != frames[i - 1].tobytes()]
    assert gif.n_frames == len(unicos)
    for i, f in enumerate(unicos):
        gif.seek(i)
        esperado, obtenido = np.array(f.convert("RGBA")), np.array(gif.convert("RGBA"))
        visible = esperado[..., 3] > 0
        assert np.array_equal(obtenido[..., 3] > 0, visible)
        assert np.array_equal(obtenido[visible][:, :3], esperado[visible][:, :3])


def test_animaciones_desconocidas(sprite):
    with pytest.raises(ValueError):
        animar.animar(sprite, ["bailar"])


def test_pose_clave_se_voltea_si_mira_al_otro_lado():
    base = np.zeros((40, 40, 4), np.uint8)
    base[20:40, 10:20] = (200, 50, 50, 255)  # cuerpo
    base[5:15, 4:14] = (50, 200, 50, 255)  # cabeza adelantada a la izquierda
    sprite = Image.fromarray(base, "RGBA")
    clave = sprite.transpose(Image.FLIP_LEFT_RIGHT)  # la IA la dibujó mirando a la derecha
    fig, anims = animar.animar(sprite, [], claves={"ataque": clave})
    assert fig.mira == -1
    golpe = np.array(anims["ataque"][5])[..., 3] > 0  # pose clave sin estela
    assert animar.mirando_hacia(golpe) == -1


def test_ataques_procedurales_distintos(sprite):
    """Zarpazo, rugido y especial tienen movimiento propio (antes todos reutilizaban
    la embestida de "ataque")."""
    frames = {n: animar.construir(sprite, n)[2] for n in ("ataque", "zarpazo", "rugido", "especial")}
    huellas = {n: b"".join(f.tobytes() for f in fr) for n, fr in frames.items()}
    assert len(set(huellas.values())) == 4
    for fr in frames.values():  # la deformación no inventa colores
        for f in fr:
            assert colores(f) <= colores(sprite)


def test_garra_mueve_solo_la_parte_delantera_alta(sprite):
    fig = animar.preparar(sprite, mira=1)
    quieto = animar.fotograma(fig, animar.Pose())
    garra = animar.fotograma(fig, animar.Pose(garra=0.8))
    cambia = (quieto != garra).any(-1)
    ys, xs = np.nonzero(fig.sprite[..., 3] > 0)
    centro, pies = fig.centro, fig.pies
    assert cambia[:, int(centro) + 1:].sum() > 3 * cambia[:, :int(centro) - 1].sum()  # delante
    assert not cambia[pies - 1].any()  # los pies no se mueven


def test_direcciones_frente_y_espalda(sprite):
    """De frente/espaldas: caminar alterna los pies (sin tijera lateral) y el ataque va
    hacia el espectador (más grande) o hacia el fondo (más pequeño)."""
    frente = animar.animaciones_direccion("abajo")
    espalda = animar.animaciones_direccion("arriba")
    assert all(p.piernas == 0 and p.dx == 0 for p in frente["caminar"].poses)
    pasos = [p.pasos for p in frente["caminar"].poses]
    assert max(pasos) > 0 > min(pasos)
    ataque_f, ataque_e = frente["ataque"].poses, espalda["ataque"].poses
    assert max(p.sy for p in ataque_f) > 1.05 and min(p.dy for p in ataque_f) < 0
    assert min(p.sy for p in ataque_e) < 0.95 and max(p.dy for p in ataque_e) > 0
    assert animar.animaciones_direccion("derecha")["caminar"].poses[0].piernas == 0  # la estándar
    for nombre in ("reposo", "caminar", "ataque", "zarpazo", "dano", "muerte"):
        _, _, frames = animar.construir(sprite, nombre, 1, direccion="abajo")
        for f in frames:
            assert colores(f) <= colores(sprite)


def test_separar_direccion_y_orden(tmp_path, sprite):
    assert animar.separar_direccion("caminar_abajo") == ("caminar", "abajo")
    assert animar.separar_direccion("caminar") == ("caminar", None)
    assert animar.separar_direccion("mi_ataque") == ("mi_ataque", None)
    casos = (("caminar", "arriba"), ("reposo", None), ("ataque", "abajo"), ("caminar", "abajo"))
    for nombre, direccion in casos:
        _, anim, frames = animar.construir(sprite, nombre, 1, direccion=direccion)
        anim.nombre = f"{nombre}_{direccion}" if direccion else nombre
        animar.guardar_animacion(tmp_path, anim, frames, 1)
    orden = [m["nombre"] for m in animar.animaciones_guardadas(tmp_path)]
    assert orden == ["reposo", "caminar_abajo", "ataque_abajo", "caminar_arriba"]


def test_ciclo_de_caminar_alterna_los_pasos_y_la_pose_base():
    import numpy as np
    from PIL import Image

    from motor import animar

    base = Image.new("RGBA", (20, 30), (0, 0, 0, 0))
    base.paste((120, 120, 120, 255), (6, 6, 14, 30))
    a, b = base.copy(), base.copy()
    a.paste((255, 0, 0, 255), (4, 24, 8, 30))  # un pie fuera
    b.paste((0, 0, 255, 255), (12, 24, 16, 30))
    fig, anim, frames = animar.construir_caminata(base, a, b, mira=1)
    assert anim.bucle and len(frames) == 4 and anim.duraciones == [140, 100, 140, 100]
    r = [np.asarray(f) for f in frames]
    assert not np.array_equal(r[0], r[2]) and np.array_equal(r[1], r[3])
    assert (r[0][..., 0] == 255).any() and (r[2][..., 2] == 255).any()  # cada paso muestra su pie
