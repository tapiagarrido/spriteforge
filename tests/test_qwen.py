import base64
import io
import json
import urllib.error

import pytest
from PIL import Image

from motor import direcciones, qwen
from motor import generar as gen

URL = "http://qwen.test:1234"


def png_b64(color="red", tamano=(64, 64)) -> str:
    b = io.BytesIO()
    Image.new("RGB", tamano, color).save(b, "PNG")
    return base64.b64encode(b.getvalue()).decode()


class Respuesta:
    def __init__(self, cuerpo: bytes):
        self.cuerpo = cuerpo

    def read(self):
        return self.cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def peticion(preset_heroes, boceto_digital):
    return gen.peticion_desde_preset(preset_heroes, boceto_digital.convert("L").resize((576, 1024)),
                                     "knight", variantes=3, semilla=5)


@pytest.fixture
def red(monkeypatch):
    """Sustituye la red: registra las peticiones y responde como sd-server."""
    registro = []

    def falsa(req, timeout=None):
        registro.append(req)
        if req.full_url.endswith("/api/health"):
            return Respuesta(json.dumps({"sd": True, "llm": True}).encode())
        return Respuesta(json.dumps({"data": [{"b64_json": png_b64()}]}).encode())

    monkeypatch.setattr(qwen.urllib.request, "urlopen", falsa)
    return registro


def campos(req) -> dict:
    """Campos de texto del multipart de una petición."""
    texto = req.data.decode("latin-1")
    salida = {}
    for trozo in texto.split("\r\n--")[0:]:
        if 'name="' in trozo and "filename=" not in trozo:
            nombre = trozo.split('name="')[1].split('"')[0]
            salida[nombre] = trozo.split("\r\n\r\n", 1)[1].strip("\r\n-")
    return salida


def test_generar_envia_boceto_referencia_prompt_y_semilla(red, peticion):
    imagenes = qwen.generar(peticion, URL)
    assert len(imagenes) == 3 and all(im.size == (576, 1024) for im in imagenes)
    assert len(red) == 3
    req = red[0]
    assert req.full_url == f"{URL}/v1/images/edits"
    assert req.headers["Content-type"].startswith("multipart/form-data; boundary=")
    assert req.data.count(b'name="image[]"') == 1  # solo el boceto: la hoja de estilo lo contamina
    c = campos(req)
    assert "knight" in c["prompt"] and "white background" in c["prompt"]
    assert c["size"] == "576x1024" and c["n"] == "1"
    extras = [json.loads(campos(r)["prompt"].split("<sd_cpp_extra_args>")[1].split("</")[0]) for r in red]
    assert [e["seed"] for e in extras] == [5, 6, 7]
    assert {e["ref_image_args"] for e in extras} == {qwen.REF_ARGS}


def test_la_url_acepta_barra_final_y_usa_la_de_serie(red, peticion):
    peticion.variantes = 1
    qwen.generar(peticion, URL + "/")
    qwen.generar(peticion)
    assert red[0].full_url == f"{URL}/v1/images/edits"
    assert red[1].full_url == f"{qwen.URL_POR_DEFECTO}/v1/images/edits"


def test_editar_sin_imagenes_da_error_claro():
    with pytest.raises(qwen.ErrorQwen, match="al menos una imagen"):
        qwen.editar([], "x", URL, 64, 64)


def test_respuesta_sin_imagen(monkeypatch):
    monkeypatch.setattr(qwen.urllib.request, "urlopen", lambda *a, **k: Respuesta(b'{"data": []}'))
    with pytest.raises(qwen.ErrorQwen, match="no devolvió ninguna imagen"):
        qwen.editar([Image.new("RGB", (8, 8))], "x", URL, 64, 64)


def test_servidor_caido_explica_como_arrancarlo(monkeypatch):
    def falla(*a, **k):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(qwen.urllib.request, "urlopen", falla)
    with pytest.raises(qwen.ErrorQwen, match="servidor Qwen-Image"):
        qwen.editar([Image.new("RGB", (8, 8))], "x", URL, 64, 64)


def test_error_http_del_servidor(monkeypatch):
    def falla(*a, **k):
        raise urllib.error.HTTPError(URL, 500, "err", {}, io.BytesIO(b'{"error": "boom"}'))

    monkeypatch.setattr(qwen.urllib.request, "urlopen", falla)
    with pytest.raises(qwen.ErrorQwen, match="HTTP 500. boom"):
        qwen.editar([Image.new("RGB", (8, 8))], "x", URL, 64, 64)


def test_estado(red):
    assert qwen.estado(URL) == {"sd": True, "llm": True}


def test_vista_qwen_manda_la_variante_y_la_instruccion(monkeypatch):
    pedidos = []

    def falso(im, txt, url, an, al, s, ref_args=None):
        pedidos.append((im, txt, url, an, al, s))
        return Image.new("RGB", (an, al), "white")

    monkeypatch.setattr(qwen, "editar", falso)

    class Base:
        ia = Image.new("RGB", (576, 1024), "white")

    from motor import animar_ia
    monkeypatch.setattr(animar_ia, "pixelizar_como_base", lambda base, ia: ia)
    ia, sprite = direcciones.vista_qwen(Base, "arriba", URL, 9)
    (imagenes, texto, url, an, al, semilla), = pedidos
    assert len(imagenes) == 1 and "from behind" in texto and (url, an, al, semilla) == (URL, 576, 1024, 9)
    assert ia.size == sprite.size == (576, 1024)


def test_rediseno_qwen_edita_la_variante_y_pixeliza(monkeypatch):
    from motor import rediseno

    pedidos = []
    monkeypatch.setattr(qwen, "editar", lambda im, txt, url, an, al, s: pedidos.append((len(im), txt, url, s))
                        or Image.new("RGB", (an, al), "white"))
    monkeypatch.setattr(rediseno, "pixelizar_rediseno", lambda base, ia: ia)

    class Base:
        ia = Image.new("RGB", (576, 1024), "white")

    ia, sprite = rediseno.rediseno_qwen(Base, "an iron helmet", URL, 3)
    (n, texto, url, semilla), = pedidos
    assert n == 1 and "iron helmet" in texto and (url, semilla) == (URL, 3) and ia is sprite


def test_clave_qwen_pide_la_pose_y_pixeliza(monkeypatch):
    from motor import animar_ia

    pedidos = []

    def falso(im, txt, url, an, al, s, ref_args=None):
        pedidos.append((len(im), txt, url))
        return Image.new("RGB", (an, al), "white")

    monkeypatch.setattr(qwen, "editar", falso)
    monkeypatch.setattr(animar_ia, "pixelizar_como_base", lambda base, ia: ia)

    class Base:
        ia = Image.new("RGB", (576, 1024), "white")

    animar_ia.clave_qwen(Base, "ataque", URL, 1, texto="jumping with the sword raised")
    animar_ia.clave_qwen(Base, "ataque", URL, 1, boceto_pose=Image.new("L", (576, 1024)))
    (n1, t1, _), (n2, t2, _) = pedidos
    assert n1 == 1 and "jumping with the sword raised" in t1 and "Image 2" not in t1
    assert n2 == 2 and "lunging forward" in t2 and "Follow the pose of Image 2" in t2
    with pytest.raises(ValueError, match="desconocida"):
        animar_ia.clave_qwen(Base, "nada", URL)


@pytest.mark.parametrize("vista,frase", [("arriba", "from behind"), ("lado", "mid-stride"),
                                         ("abajo", "from the front")])
def test_instrucciones_de_vista_para_qwen(vista, frase):
    assert frase in direcciones.instruccion_qwen(vista, False)
    assert "Image 2" in direcciones.instruccion_qwen(vista, True)


def test_perfil_qwen_usa_frente_y_espalda_si_existe(monkeypatch, tmp_path):
    from motor import animar_ia

    pedidos = []

    def falso(im, txt, url, an, al, s, ref_args=None):
        pedidos.append((im, txt))
        return Image.new("RGB", (an, al), "white")

    monkeypatch.setattr(qwen, "editar", falso)
    monkeypatch.setattr(animar_ia, "pixelizar_como_base", lambda base, ia: ia)

    class Base:
        carpeta, indice = tmp_path, 1
        ia = Image.new("RGB", (576, 1024), "white")

    direcciones.vista_qwen(Base, "lado", URL, 1)  # sin espalda: copia comprimida
    d = direcciones.carpeta_de(tmp_path, 1)
    d.mkdir()
    Image.new("RGB", (576, 1024), "gray").save(d / "ia_arriba.png")
    direcciones.vista_qwen(Base, "lado", URL, 1)  # con espalda
    direcciones.vista_qwen(Base, "lado", URL, 1, boceto=Image.new("L", (576, 1024)))
    (sin, t_sin), (con, t_con), (con_b, t_b) = pedidos
    assert len(sin) == 2 and "squeezed" in t_sin
    assert len(con) == 2 and "from behind" in t_con and "strict side profile" in t_con
    for texto in (t_sin, t_con):
        assert "do not add any weapon, shield" in texto and "sword" not in texto.lower()
    assert len(con_b) == 3 and "last image" in t_b


def test_con_original_se_envia_el_dibujo_y_no_el_contorno(red, peticion):
    original = Image.new("RGBA", (300, 200), (0, 0, 0, 0))  # transparente: debe quedar sobre blanco
    original.paste(Image.new("RGBA", (40, 40), "red"), (10, 10))
    peticion.variantes = 1
    qwen.generar(peticion, URL, original)
    parte = red[0].data.split(b"\r\n\r\n")[-1].split(b"\r\n--")[0]
    enviada = Image.open(io.BytesIO(parte))
    assert enviada.size == (peticion.ancho, peticion.alto) and enviada.getpixel((0, 0)) == (255, 255, 255)
    assert "FILLED" in campos(red[0])["prompt"]


@pytest.mark.parametrize("vista", ["abajo", "arriba", "lado"])
def test_las_vistas_de_qwen_no_inventan_equipo(vista):
    texto = direcciones.instruccion_qwen(vista, False).lower()
    assert "do not add any weapon, shield" in texto
    assert "sword" not in texto and "knight" not in texto


def test_periodizar_une_los_bordes_opuestos():
    import numpy as np

    x = np.linspace(0, 255, 64)
    original = Image.fromarray(np.tile(np.stack([x] * 3, -1)[None], (64, 1, 1)).astype("uint8"))  # degradado
    p = np.asarray(qwen.periodizar(original), dtype=float)
    o = np.asarray(original, dtype=float)

    def costura(m):  # diferencia entre el borde izquierdo y el derecho
        return abs(m[:, 0] - m[:, -1]).mean()

    assert costura(o) > 200 and costura(p) < 20
    assert qwen.periodizar(original, y=False).size == (64, 64)


def test_texturas_son_del_tamano_pedido_y_periodicas(monkeypatch):
    monkeypatch.setattr(qwen, "texto_a_imagen",
                        lambda p, u, an, al, s: Image.new("RGB", (an, al), (30, 28, 30)))
    ims = qwen.texturas("suelo", URL, 2, 5, 768, 768)
    assert len(ims) == 2 and all(i.size == (768, 768) for i in ims)
