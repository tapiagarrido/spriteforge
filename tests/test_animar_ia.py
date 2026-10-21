import numpy as np
from PIL import Image, ImageDraw

from motor import animar_ia


def base_falsa(carpeta, con_boceto=True):
    ia = Image.new("RGB", (256, 256), "white")
    if con_boceto:
        boceto = Image.new("L", (256, 256), 0)
        ImageDraw.Draw(boceto).ellipse([60, 80, 200, 240], outline=255, width=4)
        boceto.save(carpeta / "boceto_limpio.png")
    original = carpeta / "boceto_limpio.png"
    return animar_ia.Base(carpeta, 1, {}, ia, np.zeros((256, 256), bool), Image.new("RGBA", (64, 64)),
                          carpeta, 0.25, [], original if con_boceto else None)


def test_sin_pose_dibujada_guia_el_boceto_original(tmp_path):
    """Sin guía, la IA convertía criaturas no humanoides (un gusano, una bestia
    agazapada) en un humanoide genérico: el boceto original fija el cuerpo."""
    base = base_falsa(tmp_path)
    control, fuerza, fin, identidad = animar_ia.guia_de_pose(base)
    original = np.array(Image.open(tmp_path / "boceto_limpio.png"))
    assert np.array_equal(np.array(control.convert("L")), original)
    assert 0 < fuerza < 0.75 and fin < 0.5  # poca fuerza y solo al principio: la postura puede cambiar
    assert identidad == 0.6


def test_pose_dibujada_manda(tmp_path):
    base = base_falsa(tmp_path)
    pose = Image.new("L", (300, 300), 255)
    d = ImageDraw.Draw(pose)  # una figura sencilla (las rectas largas se toman por bordes de papel)
    d.ellipse([110, 40, 170, 100], outline=0, width=6)
    d.arc([60, 90, 240, 280], 200, 340, fill=0, width=6)
    control, fuerza, fin, _ = animar_ia.guia_de_pose(base, boceto_pose=pose)
    assert control.size == (256, 256) and (fuerza, fin) == (0.75, 0.8)
    original = np.array(Image.open(tmp_path / "boceto_limpio.png"))
    assert not np.array_equal(np.array(control.convert("L")), original)


def test_resultado_antiguo_sin_boceto(tmp_path):
    base = base_falsa(tmp_path, con_boceto=False)
    control, fuerza, _, identidad = animar_ia.guia_de_pose(base, fuerza_identidad=0.7)
    assert fuerza == 0.0 and not np.array(control).any() and identidad == 0.7


def test_cada_accion_tiene_su_semilla():
    semillas = {animar_ia.semilla_de_accion(7, a.prompt) for a in animar_ia.ACCIONES.values()}
    assert len(semillas) == len(animar_ia.ACCIONES)
    assert animar_ia.semilla_de_accion(7, "x") == animar_ia.semilla_de_accion(7, "x")  # reproducible


def test_pasos_qwen_pide_dos_zancadas_opuestas(monkeypatch):
    from PIL import Image

    from motor import animar_ia

    pedidos = []

    def falsa(base, accion, url, semilla=0, boceto_pose=None, texto=None):
        pedidos.append((semilla, texto))
        return Image.new("RGB", (4, 4)), Image.new("RGBA", (4, 4))

    monkeypatch.setattr(animar_ia, "clave_qwen", falsa)
    for direccion in ("derecha", "abajo", "arriba", None):
        pedidos.clear()
        a, b = animar_ia.pasos_qwen(object(), "http://x", 10, direccion)
        assert [p[0] for p in pedidos] == [10, 11] and pedidos[0][1] != pedidos[1][1]
    assert "toward the viewer" in animar_ia.PASOS["abajo"][0]
    assert "away from the viewer" in animar_ia.PASOS["arriba"][0]
    textos = [t.lower() for par in animar_ia.PASOS.values() for t in par]
    assert all("weapon" not in t and "sword" not in t for t in textos)
