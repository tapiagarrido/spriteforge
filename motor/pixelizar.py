"""Pixelizado: imagen grande -> sprite pixel art que cumple un preset.

La entrada es típicamente la salida de la IA (512-1024 px, fondo liso), pero
sirve cualquier imagen: un render, un dibujo escaneado, un sprite ampliado.
Todo corre en CPU con NumPy.

Pasos (ver docs/pixelizador.md):
    1. Separar figura y fondo (alfa si existe; si no, relleno desde los bordes).
    2. Recortar a la figura y calcular el tamaño que cabe en el lienzo del preset.
    3. Mapear cada píxel al color más cercano de la paleta (CIELAB).
    4. Reducir por "moda": cada píxel final toma el color más frecuente de su bloque.
    5. Limitar el número de colores fusionando los menos usados.
    6. Limpiar píxeles sueltos y agujeros de 1 px.
    7. Convertir el anillo exterior de la silueta en contorno y colocar en el
       lienzo (abajo-centro).

Uso:
    python -m motor.pixelizar entrada.png --preset presets/heroes-mazmorra -o sprite.png
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from motor.color import color_mas_cercano, hex_a_rgb, rgb_a_hex, rgb_a_lab

ARCHIVO_ESTILO = "estilo.json"


@dataclass
class Opciones:
    """Parámetros del pixelizado. Por defecto salen del preset."""

    ancho: int
    alto: int
    paleta: list[str]
    max_colores: int
    alto_figura: int | None = None  # alto máximo del personaje dentro del lienzo
    alto_exacto: bool = False  # respetar alto_figura también en figuras esbeltas (objetos, no personajes)
    contorno: bool = True
    grosor_contorno: int = 1
    color_contorno: str = "#222222"
    tolerancia_fondo: float = 5.0
    umbral_alfa: float = 0.5
    limpiar: bool = True
    fondo_esperado: str | None = None  # color de fondo pedido a la IA, para corregir el tinte
    escala: float | None = None  # px del sprite por px de la imagen: fija el tamaño (animación)

    @classmethod
    def desde_preset(cls, carpeta: Path, **cambios) -> Opciones:
        estilo = json.loads((Path(carpeta) / ARCHIVO_ESTILO).read_text())
        c = estilo["contorno"]
        base = dict(
            ancho=estilo["sprite"]["ancho"],
            alto=estilo["sprite"]["alto"],
            alto_figura=estilo["sprite"].get("alto_figura"),
            paleta=estilo["paleta"],
            max_colores=estilo["colores_por_sprite"]["objetivo"],
            contorno=c["tiene"],
            grosor_contorno=c["grosor"],
            color_contorno=c["color"],
            fondo_esperado=estilo.get("fondo_generacion"),
        )
        base.update({k: v for k, v in cambios.items() if v is not None})
        return cls(**base)


@dataclass
class Resultado:
    imagen: Image.Image
    colores: list[str] = field(default_factory=list)
    tamano_figura: tuple[int, int] = (0, 0)


# --- operaciones sobre máscaras booleanas -------------------------------------

def _vecinos(m: np.ndarray) -> list[np.ndarray]:
    """Las 4 vecinas (arriba, abajo, izquierda, derecha) de cada celda, con borde False."""
    p = np.pad(m, 1)
    return [p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]]


def dilatar(m: np.ndarray) -> np.ndarray:
    a, b, c, d = _vecinos(m)
    return m | a | b | c | d


def erosionar(m: np.ndarray) -> np.ndarray:
    a, b, c, d = _vecinos(m)
    return m & a & b & c & d


def apertura(m: np.ndarray, pasos: int) -> np.ndarray:
    """Erosión seguida de dilatación: elimina motas de hasta ~2·pasos px."""
    for _ in range(pasos):
        m = erosionar(m)
    for _ in range(pasos):
        m = dilatar(m)
    return m


def suavizar(a: np.ndarray, radio: int) -> np.ndarray:
    """Media en ventana (2·radio+1)² por canal, con imagen integral."""
    k = 2 * radio + 1
    p = np.pad(a, ((radio + 1, radio), (radio + 1, radio), (0, 0)), mode="edge").cumsum(0).cumsum(1)
    return (p[k:, k:] - p[:-k, k:] - p[k:, :-k] + p[:-k, :-k]) / (k * k)


# --- pasos ---------------------------------------------------------------------

def tiene_transparencia(img: np.ndarray) -> bool:
    return img.shape[2] == 4 and bool((img[..., 3] < 250).mean() > 0.01)


def color_borde(img: np.ndarray) -> np.ndarray:
    """Color RGB mediano del marco exterior de la imagen (el fondo, en una salida de IA)."""
    rgb = img[..., :3].astype(float)
    return np.median(np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]]), axis=0)


MAX_CORRECCION = 40.0  # por canal, en 0..255
MAX_DISTANCIA_FONDO = 45.0  # delta E: más lejos, el fondo no es el pedido y no se corrige


def corregir_tinte(img: np.ndarray, fondo_esperado: str) -> np.ndarray:
    """Quita un tinte global usando el fondo como referencia de color conocida.

    Si la IA pintó el fondo pedido algo desplazado (más cálido, más oscuro…),
    ese mismo desplazamiento afecta a la figura. Se mide en el fondo y se resta
    a toda la imagen. Solo se aplica si el fondo medido se parece al esperado;
    si no, la imagen no se generó sobre ese fondo y se deja como está.
    """
    if tiene_transparencia(img):
        return img
    esperado = np.array(hex_a_rgb(fondo_esperado), dtype=float)
    medido = color_borde(img)
    distancia = np.sqrt(((rgb_a_lab(esperado) - rgb_a_lab(medido)) ** 2).sum())
    if distancia > MAX_DISTANCIA_FONDO:
        return img
    desplazamiento = np.clip(esperado - medido, -MAX_CORRECCION, MAX_CORRECCION)
    salida = img.copy()
    salida[..., :3] = np.clip(img[..., :3].astype(float) + desplazamiento, 0, 255).astype(np.uint8)
    return salida


LADO_MASCARA = 512  # la máscara se calcula a esta escala: el sprite final es ~20 veces menor
MAX_FRACCION_FIGURA = 0.45  # más que esto = el fondo no se separó (textura): se reintenta
MARGEN_IGNORADO = 0.04  # franja exterior descartada: las IAs a veces dibujan un marco ahí


def componentes_principales(m: np.ndarray, fraccion: float = 0.25) -> np.ndarray:
    """Conserva los componentes conexos (4-vecinos) con área >= fraccion del mayor."""
    from collections import deque

    etiquetas = np.zeros(m.shape, dtype=np.int32)
    areas = [0]
    h, w = m.shape
    for y0, x0 in zip(*np.nonzero(m), strict=True):
        if etiquetas[y0, x0]:
            continue
        n = len(areas)
        etiquetas[y0, x0] = n
        cola, area = deque([(y0, x0)]), 0
        while cola:
            y, x = cola.popleft()
            area += 1
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and m[ny, nx] and not etiquetas[ny, nx]:
                    etiquetas[ny, nx] = n
                    cola.append((ny, nx))
        areas.append(area)
    if len(areas) == 1:
        return m
    areas = np.array(areas)
    grandes = np.flatnonzero(areas >= fraccion * areas[1:].max())
    return np.isin(etiquetas, grandes[grandes > 0])


def colores_del_borde(lab: np.ndarray, k: int = 6, iteraciones: int = 8) -> np.ndarray:
    """Centros (k-medias) de los colores del marco exterior: fondo, degradado, suelo…"""
    borde = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    centros = borde[np.linspace(0, len(borde) - 1, k).astype(int)].copy()
    for _ in range(iteraciones):
        grupo = ((borde[:, None, :] - centros[None]) ** 2).sum(-1).argmin(1)
        for i in range(k):
            if (grupo == i).any():
                centros[i] = borde[grupo == i].mean(0)
    return centros


def crecer_fondo(lab: np.ndarray, tolerancia: float) -> np.ndarray:
    """Fondo = lo alcanzable desde el borde de la imagen pasando entre vecinos parecidos.

    Cualquier píxel del borde es semilla (el personaje nunca toca el borde: la IA
    lo dibuja con margen). Desde ahí, un píxel se une al fondo si un vecino suyo
    ya es fondo y sus colores difieren menos de `tolerancia` (delta E). Esto sigue
    degradados, viñeteados, sombras suaves y suelos, y se detiene en el contorno.

    Límite de seguridad: solo puede ser fondo lo que se parezca (delta E <
    `5·tolerancia`) a alguno de los colores del borde. Evita que el fondo se cuele
    por un hueco del contorno en una figura de color parecido.
    """
    def parecidos(a, b):
        return ((a - b) ** 2).sum(-1) < tolerancia ** 2

    centros = colores_del_borde(lab)
    cerca_del_borde = (((lab[..., None, :] - centros) ** 2).sum(-1).min(-1)) < (5 * tolerancia) ** 2
    pv = parecidos(lab[1:], lab[:-1]) & cerca_del_borde[1:] & cerca_del_borde[:-1]
    ph = parecidos(lab[:, 1:], lab[:, :-1]) & cerca_del_borde[:, 1:] & cerca_del_borde[:, :-1]
    region = np.zeros(lab.shape[:2], dtype=bool)
    region[0] = region[-1] = region[:, 0] = region[:, -1] = True
    while True:
        nueva = region.copy()
        nueva[1:] |= region[:-1] & pv
        nueva[:-1] |= region[1:] & pv
        nueva[:, 1:] |= region[:, :-1] & ph
        nueva[:, :-1] |= region[:, 1:] & ph
        if np.array_equal(nueva, region):
            return region
        region = nueva


def rellenar_huecos(figura: np.ndarray, radio: int) -> np.ndarray:
    """Rellena el interior al que el fondo solo llegaba por canales estrechos.

    Si el contorno de la IA tiene huecos, el fondo se cuela por ellos y deja la
    figura hueca. Se engorda la figura `radio` px (tapa canales de hasta 2·radio),
    se ve qué fondo sigue conectado al borde y todo lo demás pasa a ser figura.
    """
    gorda = figura
    for _ in range(radio):
        gorda = dilatar(gorda)
    fuera = np.zeros_like(figura)
    fuera[0], fuera[-1], fuera[:, 0], fuera[:, -1] = ~gorda[0], ~gorda[-1], ~gorda[:, 0], ~gorda[:, -1]
    while True:
        nueva = dilatar(fuera) & ~gorda
        if np.array_equal(nueva, fuera):
            break
        fuera = nueva
    # Lo que no es "fuera" ni la franja añadida al engordar es hueco interior.
    for _ in range(radio):
        fuera = dilatar(fuera)
    return figura | ~fuera


def silueta_de_boceto(boceto: Image.Image, tamano: tuple[int, int]) -> np.ndarray:
    """Silueta aproximada del personaje a partir del boceto limpio (trazo blanco).

    Se engordan los trazos para unir los huecos entre ellos y se rellena el
    interior: el resultado es la zona que ocupa el dibujo. La IA sigue el boceto
    (ControlNet), así que su personaje cae dentro de esta zona o muy cerca. Se
    calcula a baja resolución (basta para una zona aproximada) y se amplía.
    """
    escala = min(1.0, 160 / max(tamano))
    pequeno = (max(1, round(tamano[0] * escala)), max(1, round(tamano[1] * escala)))
    trazo = boceto.convert("L").resize(pequeno, Image.BOX)
    k = max(3, int(max(pequeno) * 0.04) | 1)  # unir trazos separados hasta ~4 % del lado
    gordo = np.array(trazo.filter(ImageFilter.MaxFilter(k))) > 0
    fuera = np.zeros_like(gordo)
    fuera[0], fuera[-1], fuera[:, 0], fuera[:, -1] = ~gordo[0], ~gordo[-1], ~gordo[:, 0], ~gordo[:, -1]
    while True:
        nueva = dilatar(fuera) & ~gordo
        if np.array_equal(nueva, fuera):
            break
        fuera = nueva
    return np.array(Image.fromarray((~fuera).astype(np.uint8) * 255).resize(tamano, Image.NEAREST)) > 0


FRACCION_SOSPECHOSA = 0.35  # máscara por color así de grande = probablemente falló (textura)


def aplicar_silueta(figura: np.ndarray, silueta: np.ndarray, img: np.ndarray | None = None,
                    tolerancia: float = 5.0) -> np.ndarray:
    """Combina la máscara por color con la silueta del boceto.

    - El interior de la silueta (algo encogida) es figura aunque el fondo haya
      "entrado" por un contorno suave (figuras claras sobre fondo claro), salvo
      los píxeles que tienen prácticamente el color del fondo: así no se rellenan
      huecos reales del dibujo (entre un brazo y el cuerpo).
    - Solo si la máscara por color parece fallida (ocupa demasiado: fondo con
      textura), además se descarta todo lo que esté lejos de la silueta. No se
      hace siempre porque la IA puede dibujar cosas que sobresalen del boceto
      (una lanza larga) y se cortarían.
    """
    h, w = silueta.shape
    # Engordar/encoger a baja resolución: con núcleos de ~50 px a tamaño completo
    # los filtros de Pillow tardan segundos.
    escala = min(1.0, 160 / max(h, w))
    pequena = Image.fromarray(silueta.astype(np.uint8) * 255).resize(
        (max(1, round(w * escala)), max(1, round(h * escala))), Image.NEAREST)
    lado = max(pequena.size)

    def a_completa(im: Image.Image) -> np.ndarray:
        return np.array(im.resize((w, h), Image.NEAREST)) > 0

    cerca = a_completa(pequena.filter(ImageFilter.MaxFilter(max(3, int(lado * 0.05) | 1))))
    nucleo = a_completa(pequena.filter(ImageFilter.MinFilter(max(3, int(lado * 0.03) | 1))))
    if img is not None:
        h, w = img.shape[:2]
        f = min(1.0, LADO_MASCARA / max(h, w))
        rgb = img[..., :3]
        if f < 1.0:
            rgb = np.array(Image.fromarray(rgb).resize((max(1, round(w * f)), max(1, round(h * f))),
                                                       Image.BOX))
        lab = suavizar(rgb_a_lab(rgb), 1)
        centros = colores_del_borde(lab)
        lejos = (((lab[..., None, :] - centros) ** 2).sum(-1).min(-1)) > (2 * tolerancia) ** 2
        if f < 1.0:
            lejos = np.array(Image.fromarray(lejos.astype(np.uint8) * 255).resize((w, h), Image.NEAREST)) > 0
        nucleo &= lejos
    combinada = figura | nucleo
    return combinada & cerca if figura.mean() >= FRACCION_SOSPECHOSA else combinada


def mascara_figura(img: np.ndarray, tolerancia: float = 5.0) -> np.ndarray:
    """True donde está la figura.

    Si la imagen trae transparencia se usa el alfa. Si no, el fondo crece desde
    los bordes (`crecer_fondo`). Rellenar desde fuera respeta zonas interiores
    del color del fondo (unos ojos blancos sobre fondo blanco).

    Se calcula a `LADO_MASCARA` px con el color suavizado contra el ruido y sin
    la franja exterior (`MARGEN_IGNORADO`), donde a veces hay marcos. Si la
    figura resultante ocupa casi toda la imagen, el fondo tenía textura: se
    reintenta suavizando más. Al final se quitan motas y restos sueltos, se
    rellenan los huecos por los que se coló el fondo y se devuelve al tamaño
    original.
    """
    if tiene_transparencia(img):
        return img[..., 3] >= 128

    h, w = img.shape[:2]
    f = min(1.0, LADO_MASCARA / max(h, w))
    rgb = img[..., :3]
    if f < 1.0:
        rgb = np.array(Image.fromarray(rgb).resize((max(1, round(w * f)), max(1, round(h * f))), Image.BOX))
    lab_base = rgb_a_lab(rgb)
    # Se ignora una franja exterior (marcos que dibuja la IA); el personaje
    # siempre está más adentro porque el boceto se encuadra con margen.
    mh, mw = (int(d * MARGEN_IGNORADO) for d in lab_base.shape[:2])
    interior = lab_base[mh:lab_base.shape[0] - mh, mw:lab_base.shape[1] - mw]

    for radio, factor_tol in ((1, 1.0), (4, 1.2)):
        figura_interior = ~crecer_fondo(suavizar(interior, radio), tolerancia * factor_tol)
        if figura_interior.mean() < MAX_FRACCION_FIGURA:
            break
    figura = np.zeros(lab_base.shape[:2], dtype=bool)
    figura[mh:mh + interior.shape[0], mw:mw + interior.shape[1]] = figura_interior

    limpia = apertura(figura, 1)
    figura = componentes_principales(limpia if limpia.any() else figura)
    figura = rellenar_huecos(figura, radio=max(2, round(4 * f * max(h, w) / LADO_MASCARA)))
    if f < 1.0:
        figura = np.array(Image.fromarray(figura.astype(np.uint8) * 255).resize((w, h), Image.NEAREST)) > 0
    return figura


def tamano_destino(w: int, h: int, max_w: int, max_h: int) -> tuple[int, int]:
    """Mayor tamaño que cabe en max_w x max_h conservando la proporción."""
    escala = min(max_w / w, max_h / h)
    return max(1, min(max_w, round(w * escala))), max(1, min(max_h, round(h * escala)))


def reducir_por_moda(indices: np.ndarray, mascara: np.ndarray, n_colores: int,
                     ancho: int, alto: int, umbral_alfa: float = 0.5,
                     nucleo: np.ndarray | None = None):
    """Reduce una imagen de índices de paleta a ancho x alto.

    Cada píxel destino cubre un bloque de la imagen original; toma el índice
    más frecuente entre los píxeles de figura del bloque y es opaco si la
    figura cubre al menos `umbral_alfa` del bloque. A diferencia de promediar,
    no inventa colores intermedios (los "sucios" típicos del reescalado).

    `nucleo` (opcional) es la figura sin su franja exterior: si se da, votan
    solo sus píxeles, para que la mezcla figura/fondo del borde no aporte
    colores falsos. Las celdas sin núcleo votan con la máscara completa.
    """
    h, w = indices.shape
    fila = (np.arange(h) * alto // h)[:, None]
    col = (np.arange(w) * ancho // w)[None, :]
    celda = (fila * ancho + col).ravel()
    n_celdas = ancho * alto
    clave = celda * n_colores + indices.ravel()

    def votar(m: np.ndarray) -> np.ndarray:
        return np.bincount(clave, weights=m.ravel().astype(float),
                           minlength=n_celdas * n_colores).reshape(n_celdas, n_colores)

    votos = votar(mascara)
    if nucleo is not None:
        votos_nucleo = votar(nucleo)
        con_nucleo = votos_nucleo.sum(1) > 0
        votos[con_nucleo] = votos_nucleo[con_nucleo]

    cobertura = np.bincount(celda, weights=mascara.ravel().astype(float), minlength=n_celdas)
    total = np.bincount(celda, minlength=n_celdas)
    salida = votos.argmax(1).reshape(alto, ancho)
    opaco = (cobertura / np.maximum(total, 1) >= umbral_alfa).reshape(alto, ancho)
    return salida, opaco


def limitar_colores(indices: np.ndarray, opaco: np.ndarray, paleta_lab: np.ndarray,
                    maximo: int, proteger: int | None = None) -> np.ndarray:
    """Fusiona colores hasta quedar en `maximo`.

    En cada paso se fusiona el color con menor coste = píxeles que ocupa ×
    distancia a su color más parecido. Así primero se unen colores casi iguales
    y se conservan los pequeños pero distintos (una cara naranja en un cuerpo
    azul), que son los que dan identidad al personaje. `proteger` es un índice
    que nunca se fusiona (el color del contorno).
    """
    indices = indices.copy()
    while True:
        usados, cuenta = np.unique(indices[opaco], return_counts=True)
        if len(usados) <= max(1, maximo):
            return indices
        lab = paleta_lab[usados]
        d = np.sqrt(((lab[:, None, :] - lab[None, :, :]) ** 2).sum(-1))
        np.fill_diagonal(d, np.inf)
        coste = cuenta * d.min(1)
        if proteger is not None:
            coste[usados == proteger] = np.inf
        i = coste.argmin()
        indices[opaco & (indices == usados[i])] = usados[d[i].argmin()]


def limpiar(indices: np.ndarray, opaco: np.ndarray):
    """Quita píxeles opacos aislados y rellena agujeros de 1 px."""
    opaco = opaco.copy()
    vecinos = _vecinos(opaco)
    n = sum(v.astype(int) for v in vecinos)
    opaco &= n > 0

    vecinos = _vecinos(opaco)
    agujero = ~opaco & np.logical_and.reduce(vecinos)
    if agujero.any():
        p = np.pad(indices, 1)
        candidatos = np.stack([p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]], -1)
        for y, x in zip(*np.where(agujero), strict=True):
            valores, cuenta = np.unique(candidatos[y, x], return_counts=True)
            indices[y, x] = valores[cuenta.argmax()]
        opaco |= agujero
    return indices, opaco


def limpiar_mascara(m: np.ndarray) -> np.ndarray:
    """Quita piezas sueltas pequeñas (una luna, restos) de una máscara, a baja resolución."""
    h, w = m.shape
    f = min(1.0, LADO_MASCARA / max(h, w))
    if f < 1.0:
        peque = np.array(Image.fromarray(m.astype(np.uint8) * 255).resize(
            (max(1, round(w * f)), max(1, round(h * f))), Image.NEAREST)) > 0
        principales = componentes_principales(peque)
        principales = np.array(Image.fromarray(principales.astype(np.uint8) * 255).resize(
            (w, h), Image.NEAREST)) > 0
        return m & principales
    return componentes_principales(m)


def pixelizar(imagen: Image.Image, op: Opciones, silueta: np.ndarray | None = None,
              mascara: np.ndarray | None = None) -> Resultado:
    """Convierte `imagen` en un sprite según `op`.

    `mascara` (opcional, del tamaño de la imagen) es la figura ya separada, p. ej.
    por el segmentador (`motor.segmentar`); si se da, se usa en lugar de la
    separación por color. Si no, `silueta` (zona aproximada del personaje, p. ej.
    `silueta_de_boceto(boceto_limpio)`) ayuda a la separación por color.
    """
    img = np.array(imagen.convert("RGBA"))
    if op.fondo_esperado and mascara is None:
        img = corregir_tinte(img, op.fondo_esperado)
    if mascara is not None and not tiene_transparencia(img):
        figura = limpiar_mascara(mascara)
    else:
        figura = mascara_figura(img, op.tolerancia_fondo)
        if silueta is not None and not tiene_transparencia(img):
            figura = aplicar_silueta(figura, silueta, img, op.tolerancia_fondo)
    if not figura.any():
        raise ValueError("No se encontró ninguna figura: ¿la imagen es de un solo color?")

    ys, xs = np.where(figura)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    recorte, figura = img[y0:y1, x0:x1], figura[y0:y1, x0:x1]
    if op.escala:
        # Escala fija (fotogramas de una animación): mismo tamaño de píxel que el sprite
        # base, aunque cambie la pose. Solo se reduce si no cabe en el lienzo.
        w, h = max(1, round((x1 - x0) * op.escala)), max(1, round((y1 - y0) * op.escala))
        if w > op.ancho or h > op.alto:
            w, h = tamano_destino(x1 - x0, y1 - y0, op.ancho, op.alto)
    else:
        w, h = tamano_destino(x1 - x0, y1 - y0, op.ancho, min(op.alto, op.alto_figura or op.alto))
    if not op.escala and not op.alto_exacto and w < 0.6 * op.ancho:
        # Figura esbelta: con el alto del preset quedaría de pocos píxeles de ancho;
        # se le deja usar todo el alto del lienzo (salvo con `alto_exacto`: una runa o una roca
        # deben medir lo mismo estén o no en una piedra alta).
        w, h = tamano_destino(x1 - x0, y1 - y0, op.ancho, op.alto)

    paleta = list(op.paleta)
    idx_contorno = None
    if op.contorno:
        if op.color_contorno.lower() not in (c.lower() for c in paleta):
            paleta.append(op.color_contorno)
        idx_contorno = [c.lower() for c in paleta].index(op.color_contorno.lower())
    paleta_rgb = np.array([hex_a_rgb(c) for c in paleta], dtype=np.uint8)
    paleta_lab = rgb_a_lab(paleta_rgb)

    # Franja de mezcla con el fondo: ~1/4 del tamaño de un píxel final.
    nucleo = figura
    for _ in range(max(1, (x1 - x0) // w // 4)):
        nucleo = erosionar(nucleo)

    indices = color_mas_cercano(rgb_a_lab(recorte[..., :3]), paleta_lab)
    indices, opaco = reducir_por_moda(indices, figura, len(paleta), w, h, op.umbral_alfa,
                                      nucleo=nucleo if nucleo.any() else None)
    if op.limpiar:
        indices, opaco = limpiar(indices, opaco)

    if op.contorno:
        # Contorno interior: el anillo exterior de la silueta pasa a ser contorno.
        # Si la IA ya dibujó contorno, coincide con él y no se duplica; si no, se crea.
        anillo = opaco
        for _ in range(op.grosor_contorno):
            anillo = erosionar(anillo)
        indices[opaco & ~anillo] = idx_contorno
    indices = limitar_colores(indices, opaco, paleta_lab, op.max_colores, proteger=idx_contorno)

    lienzo = np.zeros((op.alto, op.ancho, 4), dtype=np.uint8)
    ox, oy = (op.ancho - w) // 2, op.alto - h
    zona = lienzo[oy:oy + h, ox:ox + w]
    zona[opaco, :3] = paleta_rgb[indices[opaco]]
    zona[opaco, 3] = 255

    solido = lienzo[..., 3] > 0
    colores = sorted({rgb_a_hex(c) for c in lienzo[solido][:, :3]})
    return Resultado(Image.fromarray(lienzo, "RGBA"), colores, (w, h))


def coincidencia(a: Image.Image, b: Image.Image) -> float:
    """Parecido entre dos sprites: fracción de píxeles iguales (color y opacidad).

    Se comparan las figuras recortadas a su caja, así que un desplazamiento
    dentro del lienzo no penaliza. Si las cajas miden distinto se compara en
    el lienzo común alineando abajo-centro.
    """
    def figura(im: Image.Image) -> np.ndarray:
        x = np.array(im.convert("RGBA"))
        ys, xs = np.where(x[..., 3] > 0)
        return x[ys.min():ys.max() + 1, xs.min():xs.max() + 1] if len(ys) else x[:0, :0]

    fa, fb = figura(a), figura(b)
    h, w = max(fa.shape[0], fb.shape[0]), max(fa.shape[1], fb.shape[1])
    if h == 0 or w == 0:
        return 0.0

    def en_lienzo(f: np.ndarray) -> np.ndarray:
        lienzo = np.zeros((h, w, 4), np.uint8)
        ox = (w - f.shape[1]) // 2
        lienzo[h - f.shape[0]:, ox:ox + f.shape[1]] = f
        return lienzo

    x, y = en_lienzo(fa), en_lienzo(fb)
    vx, vy = x[..., 3] > 0, y[..., 3] > 0
    igual = (vx == vy) & (~vx | (x[..., :3] == y[..., :3]).all(-1))
    return float(igual[vx | vy].mean())


def ampliar(imagen: Image.Image, factor: int) -> Image.Image:
    """Ampliación sin suavizado, para ver el sprite."""
    return imagen.resize((imagen.width * factor, imagen.height * factor), Image.NEAREST)


def main() -> None:
    ap = argparse.ArgumentParser(prog="motor.pixelizar", description=__doc__.split("\n")[0])
    ap.add_argument("entrada", type=Path)
    ap.add_argument("--preset", type=Path, required=True, help="carpeta del preset (con estilo.json)")
    ap.add_argument("-o", "--salida", type=Path, help="PNG de salida (por defecto <entrada>_sprite.png)")
    ap.add_argument("--colores", type=int, dest="max_colores", help="máximo de colores, contorno incluido")
    ap.add_argument("--alto-figura", type=int,
                    help="alto del personaje en píxeles (por defecto, el del preset)")
    ap.add_argument("--sin-contorno", action="store_const", const=False, dest="contorno")
    ap.add_argument("--tolerancia-fondo", type=float,
                    help="delta E entre vecinos para que el fondo avance (5)")
    ap.add_argument("--ver", type=int, default=0, metavar="N",
                    help="guarda además una copia ampliada xN para revisarla")
    args = ap.parse_args()

    op = Opciones.desde_preset(args.preset, max_colores=args.max_colores, alto_figura=args.alto_figura,
                               contorno=args.contorno, tolerancia_fondo=args.tolerancia_fondo)
    res = pixelizar(Image.open(args.entrada), op)
    salida = args.salida or args.entrada.with_name(args.entrada.stem + "_sprite.png")
    res.imagen.save(salida)
    print(f"{salida}: {op.ancho}x{op.alto}, figura {res.tamano_figura[0]}x{res.tamano_figura[1]}, "
          f"{len(res.colores)} colores")
    if args.ver > 1:
        vista = salida.with_name(f"{salida.stem}_x{args.ver}.png")
        ampliar(res.imagen, args.ver).save(vista)
        print(vista)


if __name__ == "__main__":
    main()
