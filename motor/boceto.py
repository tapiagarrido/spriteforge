"""Limpieza del boceto para ControlNet (scribble).

ControlNet scribble espera trazos blancos sobre fondo negro, con grosor
uniforme. La entrada puede ser una foto de papel (con sombras y textura), un
dibujo digital en PNG (trazos opacos sobre transparente) o el lienzo de la app.

Pasos:
    1. Pasar a "tinta": transparencia si existe; si no, gris con la iluminación
       normalizada (se divide por una versión muy desenfocada, lo que elimina
       sombras y el tono del papel) y umbral de Otsu. Si el resultado son zonas
       rellenas (una ilustración sombreada, no un boceto de líneas), se convierte
       en contornos: silueta + bordes internos.
    2. Quitar motas sueltas (componentes muy pequeños) y restos de la foto: bordes
       del papel, la mesa, texto del margen o marcos (`quitar_restos_de_foto`).
    3. Recortar al dibujo y centrarlo en el lienzo de generación con margen,
       apoyado abajo como el sprite final.
    4. Engrosar los trazos a un grosor proporcional al lienzo.

Uso:
    python -m motor.boceto foto_boceto.jpg -o boceto_limpio.png --ancho 576 --alto 1024
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def _otsu(valores: np.ndarray) -> float:
    """Umbral de Otsu para valores 0..255."""
    hist = np.bincount(valores.astype(np.uint8).ravel(), minlength=256).astype(float)
    total = hist.sum()
    if total == 0:
        return 128.0
    prob = hist / total
    omega = np.cumsum(prob)
    mu = np.cumsum(prob * np.arange(256))
    varianza = (mu[-1] * omega - mu) ** 2 / np.maximum(omega * (1 - omega), 1e-12)
    # Con dos grupos sin valores intermedios (blanco y negro puros) todos los cortes
    # entre ellos empatan: se toma el centro de la meseta (argmax daría el primero, 0,
    # y con "valor < umbral" no quedaría ningún trazo).
    mejores = np.flatnonzero(varianza >= varianza.max() * (1 - 1e-9))
    return float((mejores[0] + mejores[-1] + 1) / 2)


def _triangulo(valores: np.ndarray) -> float:
    """Umbral del triángulo: para un gran pico (el papel) con una cola (el trazo), donde
    Otsu falla porque no hay dos grupos comparables (lápiz suave sobre mucho papel).
    Se ignora el 0,5 % más oscuro (restos) al trazar la recta desde el pico."""
    hist = np.bincount(np.clip(valores.astype(int), 0, 255), minlength=256).astype(float)
    pico = int(hist.argmax())
    fin = int(np.searchsorted(np.cumsum(hist), hist.sum() * 0.005))
    if fin >= pico:
        return float(pico)
    i = np.arange(fin, pico)
    dx, dy = pico - fin, hist[pico] - hist[fin]
    d = np.abs(dy * (i - fin) - dx * (hist[i] - hist[fin]))
    return float(i[d.argmax()])


def a_tinta(imagen: Image.Image) -> np.ndarray:
    """Máscara booleana de los trazos del boceto."""
    rgba = np.array(imagen.convert("RGBA"))
    alfa = rgba[..., 3]
    if (alfa < 250).mean() > 0.05:
        # Dibujo digital: trazo = opaco y no blanco (fondo blanco opaco también vale).
        gris = np.array(imagen.convert("L"), dtype=float)
        return (alfa > 64) & (gris < 200)

    gris = np.array(imagen.convert("L"), dtype=float)
    # Foto del papel sobre una mesa o fondo oscuro: ese fondo no es tinta y, si entra
    # en el umbral, el corte separa "negro frente a papel" y el lápiz se toma por papel.
    exterior = fondo_exterior(gris)
    papel = ~exterior
    if exterior.any():
        gris = np.where(exterior, np.median(gris[papel]), gris)
    radio = max(gris.shape) / 30
    fondo = np.array(Image.fromarray(gris.astype(np.uint8)).filter(ImageFilter.GaussianBlur(radio)),
                     dtype=float)
    normal = np.clip(gris / np.maximum(fondo, 1) * 255, 0, 255)
    # El papel queda saturado en ~255 tras normalizar. Con trazo oscuro y nítido basta
    # Otsu; con lápiz suave, el triángulo sobre lo que no es papel saturado capta más.
    valores = normal[papel]
    umbral = min(_otsu(valores), 200)
    sin_papel = valores[valores < 254]
    if sin_papel.size > valores.size * 0.01:
        umbral = max(umbral, min(_triangulo(sin_papel) - 8, 235))
    tinta = (normal < umbral) & papel
    if exterior.any():
        tinta = quitar_pegado(tinta, exterior)
    return tinta


EXTERIOR_OSCURO = 90  # gris por debajo del cual algo puede ser la mesa/fondo de la foto
EXTERIOR_AREA = 0.02  # fracción mínima de la imagen para considerarlo fondo y no un trazo


def fondo_exterior(gris: np.ndarray) -> np.ndarray:
    """Zonas oscuras grandes que tocan el borde de la imagen: la mesa o el fondo
    alrededor del papel en una foto (los trazos nunca son tan grandes y macizos)."""
    h, w = gris.shape
    exterior = np.zeros(gris.shape, bool)
    for ys, xs in componentes(gris < EXTERIOR_OSCURO):
        toca = ys.min() == 0 or xs.min() == 0 or ys.max() == h - 1 or xs.max() == w - 1
        macizo = len(ys) > EXTERIOR_AREA * gris.size
        if toca and macizo:
            exterior[ys, xs] = True
    return exterior


def quitar_pegado(tinta: np.ndarray, exterior: np.ndarray) -> np.ndarray:
    """Quita lo que toca el fondo exterior (anillas del cuaderno, sombra del borde del
    papel); si no quedara nada, devuelve la tinta tal cual."""
    r = max(1, round(max(tinta.shape) * 0.01))
    cerca = np.array(Image.fromarray(exterior.astype(np.uint8) * 255)
                     .filter(ImageFilter.MaxFilter(2 * r + 1))) > 0
    salida = tinta.copy()
    for ys, xs in componentes(tinta):
        if cerca[ys, xs].any():
            salida[ys, xs] = False
    return salida if salida.any() else tinta


RELLENO_ILUSTRACION = 0.25  # fracción oscura de la imagen útil: por encima, es una imagen sombreada


def a_contornos(imagen: Image.Image, tinta: np.ndarray) -> np.ndarray:
    """Convierte una imagen sombreada (zonas rellenas) en contornos para ControlNet.

    Silueta exterior + bordes internos fuertes (gradiente del gris), solo dentro
    de la figura. ControlNet scribble espera líneas: con manchas rellenas la IA
    pierde la forma.
    """
    h, w = tinta.shape
    k = max(3, int(max(h, w) * 0.006) | 1)
    masc = Image.fromarray(tinta.astype(np.uint8) * 255)
    interior = np.array(masc.filter(ImageFilter.MinFilter(k))) > 0
    silueta = tinta & ~interior

    gris = np.array(imagen.convert("L").filter(ImageFilter.GaussianBlur(1.5)), dtype=float)
    gx = np.zeros_like(gris)
    gy = np.zeros_like(gris)
    gx[:, 1:-1] = gris[:, 2:] - gris[:, :-2]
    gy[1:-1] = gris[2:] - gris[:-2]
    magnitud = np.hypot(gx, gy)
    dentro = np.array(masc.filter(ImageFilter.MinFilter(k * 2 + 1))) > 0
    if dentro.any():
        umbral = np.percentile(magnitud[dentro], 85)
        bordes = dentro & (magnitud > umbral)
    else:
        bordes = np.zeros_like(tinta)
    return silueta | bordes


def silueta_ilustracion(imagen: Image.Image, tinta: np.ndarray) -> np.ndarray:
    """Zona del sujeto en una ilustración: segmentador si está disponible; si no, la tinta."""
    from motor import segmentar

    if segmentar.disponible():
        try:
            return segmentar.mascara(imagen.convert("RGB"))
        except Exception:  # noqa: BLE001 - sin GPU/memoria: se sigue con la tinta
            pass
    return tinta


def es_ilustracion(imagen: Image.Image) -> bool:
    """True si la imagen es una ilustración sombreada (zonas rellenas), no un boceto de líneas.

    Se mira qué parte de la imagen útil (sin blancos puros: márgenes al importar)
    es claramente más oscura que su fondo (percentil 90 del gris). En un boceto, la
    tinta son líneas finas: < 10 %. En una ilustración, cuerpo y sombras rellenan
    mucho más (medido: 41 %).
    """
    rgba = np.array(imagen.convert("RGBA"))
    gris = np.array(imagen.convert("L"), dtype=float)
    util = (rgba[..., 3] > 64) & (gris < 250)
    if util.sum() < 100:
        return False
    fondo = np.percentile(gris[util], 90)
    oscuro = util & (gris < 0.75 * fondo)
    return oscuro.sum() / util.sum() > RELLENO_ILUSTRACION


def componentes(tinta: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """Componentes conexos (8-vecinos) como pares (filas, columnas)."""
    from collections import deque

    h, w = tinta.shape
    visto = np.zeros_like(tinta)
    salida = []
    for y0, x0 in zip(*np.nonzero(tinta), strict=True):
        if visto[y0, x0]:
            continue
        ys, xs = [], []
        cola = deque([(y0, x0)])
        visto[y0, x0] = True
        while cola:
            y, x = cola.popleft()
            ys.append(y)
            xs.append(x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and tinta[ny, nx] and not visto[ny, nx]:
                        visto[ny, nx] = True
                        cola.append((ny, nx))
        salida.append((np.array(ys), np.array(xs)))
    return salida


def quitar_motas(tinta: np.ndarray, area_min: int) -> np.ndarray:
    """Elimina componentes de menos de `area_min` píxeles."""
    salida = tinta.copy()
    for ys, xs in componentes(tinta):
        if len(ys) < area_min:
            salida[ys, xs] = False
    return salida


BANDA_BORDE = 0.015  # tocar esta franja exterior = borde del papel, mesa, marco
BANDA_MARGEN = 0.10  # un trazo entero dentro de esta franja = texto o bordes, no el dibujo
ALCANCE_MAX = 0.9  # un trazo que cruza casi toda la foto = borde del papel o marco
RECTA_LARGO = 0.6  # una franja estrecha con tinta en más de este alto/ancho = borde del papel
RECTA_GROSOR = 0.012  # ancho de esa franja (fracción del lado)


def quitar_lineas_rectas(tinta: np.ndarray) -> np.ndarray:
    """Borra líneas verticales u horizontales largas y rectas (bordes del papel, de la mesa).

    Se busca cada columna (y fila) cuya franja estrecha tenga tinta en más del
    `RECTA_LARGO` del alto (ancho) **y** cuyas franjas vecinas, a ambos lados,
    estén mucho más vacías: es decir, una línea fina. Un cuerpo relleno (una
    ilustración sombreada) también llena muchas filas seguidas, pero sus vecinas
    están igual de llenas y no se toca. Un trazo a mano nunca es tan largo y recto.
    Se borra solo esa franja, así los trazos pegados a la línea se conservan.
    """
    salida = tinta.copy()
    for eje in (0, 1):  # 0: líneas verticales (columnas); 1: horizontales (filas)
        t = tinta if eje == 0 else tinta.T
        largo, n = t.shape
        media = max(1, int(round(n * RECTA_GROSOR / 2)))
        presencia = np.zeros(n)
        for i in range(n):
            presencia[i] = t[:, max(0, i - media):i + media + 1].any(1).mean()
        separacion = 3 * media + 2
        objetivo = salida if eje == 0 else salida.T
        for i in np.flatnonzero(presencia >= RECTA_LARGO):
            antes = presencia[i - separacion] if i - separacion >= 0 else 0.0
            despues = presencia[i + separacion] if i + separacion < n else 0.0
            if max(antes, despues) < 0.5 * presencia[i]:
                objetivo[:, max(0, i - media - 1):i + media + 2] = False
    return salida


def quitar_restos_de_foto(tinta: np.ndarray) -> np.ndarray:
    """Elimina lo que no es el dibujo en una foto: bordes del papel, mesa, texto del margen.

    Se descarta un componente si toca el borde de la imagen, si está entero en una
    franja exterior (p. ej. una línea de texto arriba) o si cruza casi toda la
    imagen de lado a lado. Antes se borran las líneas largas y rectas
    (`quitar_lineas_rectas`), que suelen estar pegadas al dibujo. Si no queda nada, se devuelve la
    tinta original: mejor un boceto con restos que ninguno.
    """
    original = tinta
    tinta = quitar_lineas_rectas(tinta)
    h, w = tinta.shape
    bh, bw = max(1, int(h * BANDA_BORDE)), max(1, int(w * BANDA_BORDE))
    mh, mw = int(h * BANDA_MARGEN), int(w * BANDA_MARGEN)
    salida = tinta.copy()
    for ys, xs in componentes(tinta):
        y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
        toca_borde = y0 < bh or x0 < bw or y1 >= h - bh or x1 >= w - bw
        en_margen = y1 < mh or y0 >= h - mh or x1 < mw or x0 >= w - mw
        cruza = (y1 - y0) >= ALCANCE_MAX * h or (x1 - x0) >= ALCANCE_MAX * w
        if toca_borde or en_margen or cruza:
            salida[ys, xs] = False
    return salida if salida.any() else original


def adelgazar(m: np.ndarray) -> np.ndarray:
    """Esqueleto de 1 px de una máscara (algoritmo de Zhang-Suen, vectorizado)."""
    img = np.pad(m.astype(np.uint8), 1)
    while True:
        cambio = False
        for paso in (0, 1):
            p2, p3, p4 = img[:-2, 1:-1], img[:-2, 2:], img[1:-1, 2:]
            p5, p6, p7 = img[2:, 2:], img[2:, 1:-1], img[2:, :-2]
            p8, p9 = img[1:-1, :-2], img[:-2, :-2]
            vecinos = [p2, p3, p4, p5, p6, p7, p8, p9]
            b = sum(v.astype(int) for v in vecinos)
            a = sum(((vecinos[i] == 0) & (vecinos[(i + 1) % 8] == 1)).astype(int) for i in range(8))
            if paso == 0:
                c = (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                c = (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
            quitar = (img[1:-1, 1:-1] == 1) & (b >= 2) & (b <= 6) & (a == 1) & c
            if quitar.any():
                img[1:-1, 1:-1][quitar] = 0
                cambio = True
        if not cambio:
            return img[1:-1, 1:-1].astype(bool)


def simplificar_trazos(tinta: np.ndarray, fuerza: float) -> np.ndarray:
    """Convierte un boceto de trazos repetidos en líneas únicas y limpias.

    1. Cierre morfológico de radio proporcional a `fuerza` (0..1): los trazos casi
       paralelos que uno hace al bocetar (y el sombreado fino) se funden en uno.
    2. Esqueleto de 1 px por el centro de cada trazo fundido.
    3. Se quitan los restos cortos sueltos.
    El grosor uniforme se aplica después, al encajar en el lienzo.
    """
    if fuerza <= 0 or not tinta.any():
        return tinta
    r = max(1, round(fuerza * 0.015 * max(tinta.shape)))
    k = 2 * r + 1
    masc = Image.fromarray(tinta.astype(np.uint8) * 255)
    cerrado = np.array(masc.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))) > 0
    esqueleto = adelgazar(cerrado)
    return quitar_motas(esqueleto, max(4, int(max(tinta.shape) * 0.02)))


def limpiar_boceto(imagen: Image.Image, ancho: int, alto: int, margen: float = 0.08,
                   grosor: float = 0.006, trabajo: int = 512, simplificar: float = 0.3,
                   ilustracion: bool | None = None) -> Image.Image:
    """Devuelve el boceto como trazo blanco sobre negro, de ancho x alto.

    `margen` y `grosor` son fracciones del lado mayor del lienzo. `trabajo` es
    el lado mayor al que se reduce la entrada para limpiarla (fotos de móvil de
    4000 px se procesarían muy despacio y no aportan nada). `simplificar` (0..1)
    funde los trazos repetidos en líneas únicas (`simplificar_trazos`). `ilustracion` fuerza
    (True/False) el tratamiento como imagen sombreada o como dibujo de líneas; por defecto se
    detecta (`es_ilustracion`).
    """
    if max(imagen.size) > trabajo:
        f = trabajo / max(imagen.size)
        imagen = imagen.resize((max(1, round(imagen.width * f)), max(1, round(imagen.height * f))),
                               Image.LANCZOS)
    tinta = a_tinta(imagen)
    area_min = max(4, int(tinta.size * 0.00005))
    if es_ilustracion(imagen) if ilustracion is None else ilustracion:
        # Imagen sombreada (render, ilustración): el sujeto puede llegar a los bordes,
        # así que no se aplican los filtros de fotos de papel. La silueta sale del
        # segmentador si está instalado (no depende del color del fondo).
        tinta = a_contornos(imagen, silueta_ilustracion(imagen, tinta))
        tinta = quitar_motas(quitar_lineas_rectas(tinta), area_min)  # bordes de márgenes al importar
    else:
        tinta = quitar_motas(tinta, area_min)
        tinta = quitar_motas(quitar_restos_de_foto(tinta), area_min)  # restos de las líneas borradas
    if not tinta.any():
        raise ValueError("El boceto está vacío: no se encontraron trazos.")
    if simplificar > 0:
        esqueleto = simplificar_trazos(tinta, simplificar)
        if esqueleto.any():
            # 3 px antes de reescalar: una línea de 1 px se rompería al reducirla.
            tinta = np.array(Image.fromarray(esqueleto.astype(np.uint8) * 255)
                             .filter(ImageFilter.MaxFilter(3))) > 0

    ys, xs = np.nonzero(tinta)
    recorte = Image.fromarray((tinta[ys.min():ys.max() + 1, xs.min():xs.max() + 1] * 255).astype(np.uint8))

    lado = max(ancho, alto)
    m = int(lado * margen)
    escala = min((ancho - 2 * m) / recorte.width, (alto - 2 * m) / recorte.height)
    nuevo = (max(1, round(recorte.width * escala)), max(1, round(recorte.height * escala)))
    recorte = recorte.resize(nuevo, Image.BILINEAR)

    lienzo = Image.new("L", (ancho, alto), 0)
    lienzo.paste(recorte, ((ancho - nuevo[0]) // 2, alto - m - nuevo[1]))
    # Engrosar con un filtro de máximo (tamaño impar) y binarizar.
    k = max(3, int(lado * grosor) | 1)
    lienzo = lienzo.point(lambda v: 255 if v > 96 else 0).filter(ImageFilter.MaxFilter(k))
    return lienzo


def main() -> None:
    ap = argparse.ArgumentParser(prog="motor.boceto", description=__doc__.split("\n")[0])
    ap.add_argument("entrada", type=Path)
    ap.add_argument("-o", "--salida", type=Path)
    ap.add_argument("--ancho", type=int, default=576)
    ap.add_argument("--alto", type=int, default=1024)
    args = ap.parse_args()
    salida = args.salida or args.entrada.with_name(args.entrada.stem + "_limpio.png")
    limpiar_boceto(Image.open(args.entrada), args.ancho, args.alto).save(salida)
    print(salida)


if __name__ == "__main__":
    main()
