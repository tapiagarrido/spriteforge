import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from motor import segmentar
from motor.boceto import _otsu, a_tinta, limpiar_boceto, quitar_motas


def test_otsu_separa_dos_grupos():
    valores = np.concatenate([np.full(500, 40), np.full(500, 220)])
    assert 40 <= _otsu(valores) < 220


def test_otsu_con_blanco_y_negro_puros():
    """Sin grises intermedios todos los cortes empatan: el umbral queda en medio
    (antes salía 0 y un dibujo en blanco y negro puro se quedaba sin trazos)."""
    valores = np.concatenate([np.full(100, 0), np.full(900, 255)])
    assert 100 <= _otsu(valores) <= 155


def test_dibujo_en_blanco_y_negro_puro():
    img = Image.new("L", (300, 300), 255)
    d = ImageDraw.Draw(img)
    d.ellipse([110, 40, 170, 100], outline=0, width=6)
    d.arc([60, 90, 240, 280], 200, 340, fill=0, width=6)
    assert a_tinta(img).mean() > 0.01
    assert (np.array(limpiar_boceto(img, 256, 256)) > 0).any()


def test_quitar_motas():
    tinta = np.zeros((50, 50), bool)
    tinta[10:40, 20] = True  # trazo largo
    tinta[5, 5] = True  # mota
    limpia = quitar_motas(tinta, area_min=5)
    assert limpia[10:40, 20].all() and not limpia[5, 5]


@pytest.mark.parametrize("nombre", ["boceto_digital", "boceto_foto"])
def test_limpiar_produce_trazo_blanco_sobre_negro(nombre, request):
    limpio = limpiar_boceto(request.getfixturevalue(nombre), 576, 1024)
    a = np.array(limpio)
    assert limpio.mode == "L" and limpio.size == (576, 1024)
    assert set(np.unique(a)) <= {0, 255}
    assert 0.01 < (a == 255).mean() < 0.25  # hay trazo, pero es línea, no relleno
    ys, xs = np.nonzero(a)
    assert ys.max() > 1024 * 0.85  # apoyado abajo
    assert abs((xs.min() + xs.max()) / 2 - 288) < 60  # centrado


def test_foto_y_digital_dan_lo_mismo(boceto_digital, boceto_foto):
    """Casi todo trazo de uno queda cerca (< ~8 px) de un trazo del otro.

    No se compara píxel a píxel: la foto está girada 3° y las líneas son finas.
    """
    a = limpiar_boceto(boceto_digital, 288, 512)
    b = limpiar_boceto(boceto_foto, 288, 512)
    cerca_de_b = np.array(b.filter(ImageFilter.MaxFilter(17))) > 0
    cerca_de_a = np.array(a.filter(ImageFilter.MaxFilter(17))) > 0
    a, b = np.array(a) > 0, np.array(b) > 0
    assert (a & cerca_de_b).sum() / a.sum() > 0.85
    assert (b & cerca_de_a).sum() / b.sum() > 0.85


def test_papel_con_sombra_no_es_trazo():
    y, x = np.mgrid[0:300, 0:300]
    papel = (240 - 0.4 * x - 0.2 * y).astype(np.uint8)  # sombra fuerte, sin dibujo
    img = Image.fromarray(np.stack([papel] * 3, -1))
    ImageDraw.Draw(img).line([(100, 50), (100, 250)], fill=(60, 60, 60), width=4)
    tinta = a_tinta(img)
    assert tinta[150, 100]
    assert tinta.mean() < 0.05


def test_boceto_vacio_falla():
    with pytest.raises(ValueError):
        limpiar_boceto(Image.new("RGB", (200, 200), "white"), 256, 256)


def ilustracion_sintetica() -> Image.Image:
    """Criatura rellena y sombreada sobre fondo gris, que llega casi a los bordes,
    pegada en un lienzo con márgenes blancos (como al importar en la app)."""
    lienzo = Image.new("RGB", (576, 1024), "white")
    escena = Image.new("RGB", (576, 560), (138, 138, 138))
    d = ImageDraw.Draw(escena)
    d.ellipse([30, 120, 546, 470], fill=(70, 50, 50))  # cuerpo relleno, casi a lo ancho
    d.ellipse([200, 40, 380, 200], fill=(85, 60, 60))  # cabeza
    for x in (60, 180, 380, 500):
        d.rectangle([x, 420, x + 30, 555], fill=(60, 45, 45))  # patas hasta el borde
    d.ellipse([250, 90, 330, 150], fill=(180, 60, 70))  # boca
    lienzo.paste(escena, (0, 232))
    return lienzo


def test_detecta_ilustraciones_y_bocetos(boceto_digital, boceto_foto):
    from motor.boceto import es_ilustracion

    assert es_ilustracion(ilustracion_sintetica())
    assert not es_ilustracion(boceto_digital)
    assert not es_ilustracion(boceto_foto)


@pytest.mark.skipif(not segmentar.disponible(), reason="necesita el segmentador (BiRefNet) descargado")
def test_ilustracion_no_se_corta_en_franjas():
    """Regresión: el filtro de líneas rectas borraba filas enteras de un cuerpo relleno."""
    limpio = np.array(limpiar_boceto(ilustracion_sintetica(), 768, 768)) > 0
    ys, xs = np.nonzero(limpio)
    assert ys.max() - ys.min() > 768 * 0.6  # la figura conserva su altura
    filas = limpio[ys.min():ys.max() + 1].any(1)
    hueco_max = max((len(h) for h in "".join("1" if f else "0" for f in filas).split("1")), default=0)
    assert hueco_max < 768 * 0.05  # sin franjas vacías que partan la figura
    # Los bordes de los márgenes blancos no deben quedar como líneas largas.
    assert (limpio.mean(1) < 0.6).all()


def test_lineas_rectas_finas_se_borran_pero_no_los_rellenos():
    from motor.boceto import quitar_lineas_rectas

    t = np.zeros((300, 300), bool)
    t[50, 10:290] = True  # línea fina (borde del papel)
    t[150:250, 20:280] = True  # zona rellena ancha
    limpio = quitar_lineas_rectas(t)
    assert not limpio[50].any()
    assert limpio[150:250, 20:280].mean() > 0.9


def test_adelgazar_deja_lineas_de_un_pixel():
    from motor.boceto import adelgazar

    m = np.zeros((60, 100), bool)
    m[25:35, 10:90] = True  # barra de 10 px de grosor
    esqueleto = adelgazar(m)
    assert esqueleto.any()
    assert esqueleto.sum(0)[20:80].max() == 1  # cada columna central: 1 px
    assert esqueleto[:, 20:80].any(0).all()  # sin cortes


def test_simplificar_funde_trazos_paralelos():
    from motor.boceto import componentes, simplificar_trazos

    t = np.zeros((300, 300), bool)
    for y in (100, 104, 108):  # tres trazos casi superpuestos, como al bocetar
        t[y:y + 2, 40:260] = True
    simple = simplificar_trazos(t, 0.5)
    assert len(componentes(simple)) == 1
    assert simple.sum(0)[60:240].max() == 1  # una sola línea


def test_simplificar_cero_no_cambia_nada(boceto_foto):
    from motor.boceto import simplificar_trazos

    t = np.zeros((50, 50), bool)
    t[10:40, 20] = True
    assert np.array_equal(simplificar_trazos(t, 0), t)
    a = np.array(limpiar_boceto(boceto_foto, 288, 512, simplificar=0)) > 0
    b = np.array(limpiar_boceto(boceto_foto, 288, 512, simplificar=0.3)) > 0
    assert a.any() and b.any() and not np.array_equal(a, b)


def test_foto_con_fondo_oscuro_y_anillas():
    """Cuaderno fotografiado sobre fondo negro, con anillas arriba y dibujo a lápiz
    suave: el fondo no debe decidir el umbral (antes el lápiz se tomaba por papel y
    solo quedaban las anillas)."""
    # Como llega del lienzo: foto encajada con márgenes blancos arriba y abajo; a los
    # lados se ve la mesa negra (~15 % de la imagen, no basta para ser "ilustración").
    img = Image.new("RGB", (400, 640), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 50, 399, 590], fill=(5, 5, 5))  # foto: mesa negra
    d.rectangle([32, 64, 367, 590], fill=(232, 230, 226))  # papel
    for x in range(50, 350, 24):  # anillas pegadas al borde superior del papel
        d.rectangle([x, 56, x + 8, 80], fill=(20, 20, 20))
    d.ellipse([120, 220, 280, 460], outline=(185, 185, 182), width=3)  # lápiz suave
    d.line([200, 460, 200, 560], fill=(185, 185, 182), width=3)
    limpio = np.array(limpiar_boceto(img, 400, 640, simplificar=0)) > 0
    ys, xs = np.nonzero(limpio)
    assert limpio.mean() > 0.01, "se perdió el dibujo a lápiz"
    # Encuadrado: el dibujo (óvalo + palo, más alto que ancho) ocupa el lienzo; las
    # anillas habrían añadido una franja ancha y baja.
    assert (ys.max() - ys.min()) > 1.3 * (xs.max() - xs.min())


def test_ilustracion_se_puede_forzar_a_lineas():
    """Un dibujo de líneas finas reducido a 512 px puede confundirse con una ilustración (y pasar
    por el segmentador, lento y que pierde ramas finas): quien sabe que son líneas lo indica."""
    img = Image.new("RGB", (576, 1024), "white")
    d = ImageDraw.Draw(img)
    d.ellipse([100, 300, 480, 700], outline="black", width=7)
    d.line([(290, 700), (290, 950)], fill="black", width=7)
    lineas = limpiar_boceto(img, 256, 256, ilustracion=False, simplificar=0)
    assert (np.array(lineas) > 0).any()
    assert limpiar_boceto(img, 256, 256, ilustracion=False).size == (256, 256)
