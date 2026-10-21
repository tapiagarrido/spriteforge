"""Mapas: suelos, transiciones, bloques de plataforma y fondos en capas.

Solo el terreno (nada de árboles, rocas ni personajes). Lo que se genera depende
de la `perspectiva` del proyecto:

    cenital  (todas direcciones)  suelos sin costuras + transiciones entre dos suelos
    lateral  (plataformas)         bloques de plataforma + fondo en capas (cielo,
                                   montañas lejanas, colinas cercanas)

La IA solo pinta **texturas sin costuras**: durante la generación las convoluciones
de la UNet y del VAE "dan la vuelta" por los bordes (relleno circular), así que lo
que sale por un lado entra por el otro. Todo lo demás es determinista: reducir a
baldosas con la paleta del proyecto, variantes que comparten borde (intercambiables
en cualquier orden), transiciones por esquinas (las 16 combinaciones de un "Wang
set" de esquinas de Tiled), bloques de plataforma con superficie y contorno, y capas
de fondo con transparencia.

Salida en `salidas/mapas/<fecha>_<nombre>/`: `tileset.png` (+ `_x4`), `tileset.tsx`
(Tiled, con los bordes automáticos), capas `fondo_*.png`, `mapa_prueba.png` e
`info.json`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import types
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np
from PIL import Image

from motor.color import color_mas_cercano, hex_a_rgb, lab_a_rgb, rgb_a_lab
from motor.modelos import RAIZ
from motor.pixelizar import ampliar, limitar_colores

log = logging.getLogger(__name__)

LADO_TEXTURA = 768  # generación de suelos (divisible por 16, 32 y 48)
FONDO_ANCHO, FONDO_ALTO = 1344, 576  # capas de fondo (21×64, 9×64)
COLORES_MATERIAL = 6  # colores por material de suelo: pocos, como en un tileset clásico
COLORES_CAPA = 8
COLUMNAS_TILESET = 8
# Una textura de 768 px se reduce a una superficie de ~96 px: es la escala a la que el
# LoRA de pixel art pinta sus "píxeles" (~8 px), así que el detalle sobrevive. Las
# baldosas son recortes de esa superficie (3×3 baldosas de 32, 6×6 de 16, 2×2 de 48).
SUPERFICIE_PX = 96

# Legibilidad: el suelo debe decir en qué ambiente estás, pero sin competir con los personajes
# (más vivos y claros). Se suaviza su contraste y su color y se mide cuánto se "camuflan".
NIVEL_POR_DEFECTO = 0.7  # 0 = tal cual lo pinta la IA, 1 = suelo lo más apagado posible
NIVELES_AUTO = (0.3, 0.5, 0.7, 0.85, 1.0)  # se prueban todos; se elige el más bajo que sirva
CAMUFLAJE_OBJETIVO = 0.10  # fracción máxima de píxeles de un personaje casi del color del suelo
CAMUFLAJE_DELTA_E = 14.0  # a menos de esta distancia (CIELAB) de algún color del suelo = camuflado
L_SUELO = 50.0  # luminosidad media máxima del suelo con nivel 0 (baja 10 puntos con nivel 1)


def banda_suelo(nivel: float) -> tuple[float, float]:
    """Franja de luminosidad (L*) que puede usar el suelo: ni los oscuros (contornos y sombras de
    los personajes) ni los claros (sus luces y colores vivos) con nivel alto."""
    return 24 + 10 * nivel, 74 - 24 * nivel

NEGATIVO_TEXTURA = ("perspective, horizon, sky, objects, items, icons, inventory, sprites, gems, symbols, "
                    "letters, ui, trees, buildings, characters, people, animals, shadows, vignette, "
                    "border, frame, text, watermark, blurry, realistic, 3d render")
NEGATIVO_FONDO = ("characters, people, animals, buildings, text, watermark, frame, border, blurry, "
                  "realistic, 3d render, ground tiles, ui")


# --- IA: texturas sin costuras ------------------------------------------------------

@contextlib.contextmanager
def mosaico(*modulos, x: bool = True, y: bool = True):
    """Relleno circular en las convoluciones de `modulos` mientras dura el bloque.

    Con `x` (y/o `y`) la imagen generada es periódica en ese eje: encaja consigo
    misma sin costuras. Se parchea `_conv_forward` de cada `nn.Conv2d` (también las
    de las LoRA, que comparten relleno con su capa base) y se restaura al salir.
    """
    import torch.nn.functional as F  # noqa: N812
    from torch import nn

    def conv_mosaico(self, entrada, peso, sesgo):
        ph, pw = self.padding
        if pw:
            entrada = F.pad(entrada, (pw, pw, 0, 0), mode="circular" if x else "constant")
        if ph:
            entrada = F.pad(entrada, (0, 0, ph, ph), mode="circular" if y else "constant")
        return F.conv2d(entrada, peso, sesgo, self.stride, 0, self.dilation, self.groups)

    parcheadas = []
    for modulo in modulos:
        for capa in modulo.modules():
            if (isinstance(capa, nn.Conv2d) and isinstance(capa.padding, tuple)
                    and any(capa.padding) and capa.padding_mode == "zeros"):
                capa._conv_forward = types.MethodType(conv_mosaico, capa)
                parcheadas.append(capa)
    try:
        yield
    finally:
        for capa in parcheadas:
            del capa._conv_forward


def _tuberia_texto(gestor):
    """SDXL de texto a imagen con los mismos componentes ya cargados (sin ControlNet)."""
    from diffusers import StableDiffusionXLPipeline

    tuberia = gestor.obtener()
    if getattr(gestor, "_texto_de", None) is not tuberia:
        componentes = {k: v for k, v in tuberia.components.items() if k != "controlnet"}
        gestor._texto = StableDiffusionXLPipeline(**componentes)
        gestor._texto.set_progress_bar_config(disable=True)
        gestor._texto_de = tuberia
    return tuberia, gestor._texto


def decodificar_periodico(vae, latentes, x: bool, y: bool, margen: int = 8) -> list[Image.Image]:
    """Decodifica latentes periódicos sin perder la periodicidad ni disparar la VRAM.

    Se añade un margen circular al latente (cada borde "ve" el lado contrario), se
    decodifica con el VAE normal (por mosaicos: memoria acotada) y se recorta el
    centro. Parchear el VAE en su lugar obligaría a decodificar 768² de golpe.
    """
    import torch
    import torch.nn.functional as F  # noqa: N812

    mx, my = (margen if x else 0), (margen if y else 0)
    lat = F.pad(latentes / vae.config.scaling_factor, (mx, mx, my, my), mode="circular")
    with torch.no_grad():
        img = vae.decode(lat.to(vae.dtype)).sample
    f = img.shape[-1] // lat.shape[-1]
    img = img[:, :, my * f:img.shape[2] - my * f, mx * f:img.shape[3] - mx * f]
    arr = ((img.float() / 2 + 0.5).clamp(0, 1) * 255).round().byte().permute(0, 2, 3, 1).cpu().numpy()
    return [Image.fromarray(a) for a in arr]


def texturas_ia(gestor, prompt: str, negativo: str, referencia: Image.Image, n: int, semilla: int,
                ancho: int = LADO_TEXTURA, alto: int = LADO_TEXTURA, x: bool = True, y: bool = True,
                fuerza_estilo: float = 0.3, fuerza_pixel_art: float = 0.8,
                control: Image.Image | None = None) -> list[Image.Image]:
    """`n` texturas periódicas (en x y/o y) con el estilo de la referencia del preset.

    Con `control` (trazo blanco sobre negro, p. ej. el perfil de unas montañas) la
    forma la marca ControlNet, como el boceto de un personaje.
    """
    import torch

    from motor.generar import MODOS

    modo = MODOS["calidad"]
    with gestor._lock:
        tuberia, texto = _tuberia_texto(gestor)
        tuberia.set_adapters(["pixel_art"], adapter_weights=[fuerza_pixel_art])
        tuberia.set_ip_adapter_scale({"up": {"block_0": [0.0, fuerza_estilo, 0.0]}})  # solo estilo
        disp = "cuda" if torch.cuda.is_available() else "cpu"
        t0 = time.perf_counter()
        imagenes = []
        for i in range(n):  # de una en una: 768² y 1344×576 sin picos de VRAM
            comun = dict(prompt=prompt, negative_prompt=negativo, ip_adapter_image=referencia,
                         num_inference_steps=modo.pasos, guidance_scale=modo.guia,
                         width=ancho, height=alto, output_type="latent",
                         generator=torch.Generator(disp).manual_seed(semilla + i))
            if control is None:
                with mosaico(tuberia.unet, x=x, y=y):  # la UNet genera un latente periódico
                    latentes = texto(**comun).images
            else:  # ControlNet también en mosaico: si no, la forma tendría costura
                with mosaico(tuberia.unet, tuberia.controlnet, x=x, y=y):
                    latentes = tuberia(image=control.convert("RGB"), controlnet_conditioning_scale=0.8,
                                       control_guidance_end=0.7, **comun).images
            imagenes += decodificar_periodico(tuberia.vae, latentes, x, y)
        gestor._ultimo_uso = time.monotonic()
        log.info("%d texturas %dx%d en %.1f s", n, ancho, alto, time.perf_counter() - t0)
        return imagenes


# --- agua: textura procedural --------------------------------------------------------

PALABRAS_AGUA = ("agua", "water", "lago", "laguna", "charco", "río", "rio", "estanque", "mar ")


def es_agua(descripcion: str) -> bool:
    d = f"{descripcion.lower()} "
    return any(p in d for p in PALABRAS_AGUA) and not any(
        x in d for x in ("hielo", "ice", "congelad", "frozen", "lava"))


def textura_agua(descripcion: str, lado: int = LADO_TEXTURA, semilla: int = 0) -> Image.Image:
    """Agua azul con ondas, periódica en x e y. El modelo de pixel art no sabe pintar agua (dibuja
    losas marrones o mosaicos rojos y azules), así que se genera a mano: un azul que varía con
    ruido suave y líneas claras onduladas. «oscura» o «profunda» la oscurece; «clara», la aclara."""
    d = descripcion.lower()
    base = np.array([34, 86, 150], float)
    if any(p in d for p in ("oscur", "dark", "profund", "deep")):
        base = np.array([22, 52, 104], float)
    elif any(p in d for p in ("clara", "light", "cristalina", "clear")):
        base = np.array([60, 128, 190], float)
    ruido = np.kron(ruido_periodico(96, semilla, celdas=6), np.ones((lado // 96, lado // 96)))
    ruido2 = np.kron(ruido_periodico(96, semilla + 1, celdas=3), np.ones((lado // 96, lado // 96)))
    y = np.arange(lado)[:, None]
    x = np.arange(lado)[None, :]
    onda = np.sin(2 * np.pi * (y / (lado / 16) + 0.35 * ruido2 + 0.15 * np.sin(2 * np.pi * x / (lado / 4))))
    brillo = 1 + 0.18 * ruido
    rgb = base[None, None, :] * brillo[..., None]
    crestas = (onda > 0.93)[..., None]
    rgb = np.where(crestas, np.minimum(255, rgb * 1.55 + 25), rgb)
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))


# --- reducir a baldosas con la paleta del proyecto ----------------------------------

def redimensionar_periodico(imagen: Image.Image, ancho: int, alto: int, x: bool = True,
                            y: bool = True) -> Image.Image:
    """Redimensiona conservando la periodicidad: se añade un margen circular antes
    (sin él, el filtro trata los bordes como si acabaran ahí y aparece la costura)."""
    a = np.array(imagen.convert("RGB"))
    mx = a.shape[1] // 8 if x else 0
    my = a.shape[0] // 8 if y else 0
    a = np.pad(a, ((my, my), (mx, mx), (0, 0)), mode="wrap")
    fx, fy = ancho / imagen.width, alto / imagen.height
    grande = Image.fromarray(a).resize((round(a.shape[1] * fx), round(a.shape[0] * fy)), Image.BOX)
    ox, oy = round(mx * fx), round(my * fy)
    return grande.crop((ox, oy, ox + ancho, oy + alto))


def paleta_lab(paleta_hex: list[str]) -> tuple[np.ndarray, np.ndarray]:
    rgb = np.array([hex_a_rgb(h) for h in paleta_hex], dtype=np.uint8)
    return rgb, rgb_a_lab(rgb.astype(float))


def suavizar(rgb: np.ndarray, nivel: float) -> np.ndarray:
    """Apaga unos colores para usarlos de suelo (RGB 0..255, cualquier forma ...x3 -> float).

    La luminosidad se remapea a la franja `banda_suelo(nivel)` (más estrecha cuanto mayor es el
    nivel: ni los oscuros de los contornos ni los claros de las luces de los personajes) y el
    color (a*, b*) se reduce hasta un 50 %. El **matiz se conserva**: el verde sigue siendo verde y
    el morado, morado. Es la misma función para la textura de la IA y para la paleta, así que la
    textura se corresponde con colores de su gama."""
    rgb = np.asarray(rgb, dtype=float)
    if nivel <= 0:
        return rgb
    lab = rgb_a_lab(rgb)
    bajo, alto = banda_suelo(nivel)
    lab[..., 0] = bajo + np.clip(lab[..., 0], 0, 100) / 100 * (alto - bajo)
    lab[..., 1:] *= 1 - 0.5 * nivel
    return lab_a_rgb(lab)


def paleta_suelo(paleta_rgb: np.ndarray, nivel: float) -> tuple[np.ndarray, np.ndarray]:
    """La paleta del estilo con cada color apagado (`suavizar`): mismas gamas, sin duplicados.
    Con nivel 0 es la paleta tal cual."""
    if nivel <= 0:
        return paleta_rgb, rgb_a_lab(paleta_rgb.astype(float))
    apagada = np.unique(np.round(suavizar(paleta_rgb, nivel)).astype(np.uint8), axis=0)
    return apagada, rgb_a_lab(apagada.astype(float))


def camuflaje(suelos: list[np.ndarray], personajes: list[Image.Image],
              delta_e: float = CAMUFLAJE_DELTA_E) -> dict:
    """Cuánto se pierden los `personajes` sobre ese suelo.

    Para cada sprite, fracción de sus píxeles (contorno incluido) que está a menos de `delta_e`
    del color más parecido del suelo: ahí se confundiría con él. `medio` y `peor` (el sprite más
    camuflado) se comparan con `CAMUFLAJE_OBJETIVO`."""
    colores = np.unique(np.concatenate([t[..., :3].reshape(-1, 3) for t in suelos]), axis=0)
    lab_suelo = rgb_a_lab(colores.astype(float))
    fracciones = []
    for sp in personajes:
        a = np.array(sp.convert("RGBA"))
        px = a[a[..., 3] > 0][:, :3]
        if not len(px):
            continue
        lab = rgb_a_lab(np.unique(px, axis=0).astype(float))
        d = np.sqrt(((lab[:, None, :] - lab_suelo[None, :, :]) ** 2).sum(-1)).min(1)
        fracciones.append(float((d < delta_e).mean()))
    if not fracciones:
        return {"medio": 0.0, "peor": 0.0, "personajes": 0}
    return {"medio": round(float(np.mean(fracciones)), 3), "peor": round(max(fracciones), 3),
            "personajes": len(fracciones)}


def cuantizar(rgb: np.ndarray, paleta_rgb: np.ndarray, pal_lab: np.ndarray, maximo: int,
              opaco: np.ndarray | None = None) -> np.ndarray:
    """Colores de la paleta del proyecto, limitados a `maximo` (fusionando los menos
    importantes), para una textura o capa ya del tamaño final."""
    indices = color_mas_cercano(rgb_a_lab(rgb.astype(float)), pal_lab)
    opaco = np.ones(indices.shape, bool) if opaco is None else opaco
    indices = limitar_colores(indices, opaco, pal_lab, maximo)
    return paleta_rgb[indices]


def a_baldosa(textura: Image.Image, lado: int, paleta_rgb: np.ndarray, pal_lab: np.ndarray,
              maximo: int = COLORES_MATERIAL) -> np.ndarray:
    """Textura periódica -> baldosa periódica de `lado` px con la paleta (H×W×3 uint8)."""
    pequena = np.array(redimensionar_periodico(textura, lado, lado))
    return cuantizar(pequena, paleta_rgb, pal_lab, maximo)


def superficie(textura: Image.Image, lado: int, superficie_px: int = SUPERFICIE_PX) -> np.ndarray:
    """Textura periódica -> superficie periódica de ~SUPERFICIE_PX (múltiplo de `lado`),
    en RGB float (aún sin cuantizar)."""
    s = lado * max(2, round(superficie_px / lado))
    return np.array(redimensionar_periodico(textura, s, s), dtype=float)


def baldosa_de(sup: np.ndarray, lado: int, x0: int, y0: int) -> np.ndarray:
    """Recorte de `lado` px de la superficie convertido en periódico: las últimas filas y
    columnas se funden con las que siguen en la superficie, así que el final de la
    baldosa continúa en su principio sin costura. RGB float."""
    s = sup.shape[0]
    m = max(2, lado // 4)
    filas = (np.arange(lado + m) + y0) % s
    cols = (np.arange(lado + m) + x0) % s
    c = sup[filas][:, cols]
    w = ((np.arange(m) + 0.5) / m)[:, None, None]
    v = c[:lado].copy()
    v[:m] = w * c[:m] + (1 - w) * c[lado:lado + m]
    w = w.transpose(1, 0, 2)
    h = v[:, :lado].copy()
    h[:, :m] = w * v[:, :m] + (1 - w) * v[:, lado:lado + m]
    return h


def paleta_material(sup: np.ndarray, paleta_rgb: np.ndarray, pal_lab: np.ndarray,
                    maximo: int = COLORES_MATERIAL) -> tuple[np.ndarray, np.ndarray]:
    """Los `maximo` colores de la paleta que mejor representan la superficie. Todas las baldosas
    del material usan los mismos."""
    indices = color_mas_cercano(rgb_a_lab(sup), pal_lab)
    indices = limitar_colores(indices, np.ones(indices.shape, bool), pal_lab, maximo)
    usados = np.unique(indices)
    return paleta_rgb[usados], pal_lab[usados]


def cuantizar_con(rgb: np.ndarray, sub_rgb: np.ndarray, sub_lab: np.ndarray) -> np.ndarray:
    return sub_rgb[color_mas_cercano(rgb_a_lab(rgb.astype(float)), sub_lab)]


def baldosas_material(textura: Image.Image, lado: int, n: int, paleta_rgb: np.ndarray,
                      pal_lab: np.ndarray, semilla: int = 0, nivel: float = 0.0,
                      superficie_px: int = SUPERFICIE_PX,
                      colores: int = COLORES_MATERIAL) -> list[np.ndarray]:
    """`n` baldosas intercambiables de un material (la base y variantes con su mismo
    borde), recortadas de la misma superficie y con los mismos colores. `nivel` (0..1) es la
    legibilidad: apaga la textura y los colores de la paleta (ver `suavizar`). `superficie_px` (a cuánto
    se reduce la textura: más grande = más detalle por baldosa) y `colores` (por material) suben el
    detalle para texturas con piezas definidas, como losas o ladrillos."""
    sup = suavizar(superficie(textura, lado, superficie_px), nivel)
    if nivel > 0:  # colores del estilo, apagados: mismas gamas pero sin chocar con los personajes
        paleta_rgb, pal_lab = paleta_suelo(paleta_rgb, nivel)
    sub_rgb, sub_lab = paleta_material(sup, paleta_rgb, pal_lab, colores)
    rng = np.random.default_rng(semilla)
    s = sup.shape[0]
    origenes = [(0, 0)] + [tuple(int(v) for v in rng.integers(0, s, 2)) for _ in range(n - 1)]
    tiles = [cuantizar_con(baldosa_de(sup, lado, x, y), sub_rgb, sub_lab) for x, y in origenes]
    return variantes_intercambiables(tiles[0], tiles[1:], semilla=semilla)


def anillo(lado: int, grosor: int, semilla: int = 0) -> np.ndarray:
    """Máscara del borde de una baldosa (True = borde), con el límite interior irregular
    para que el cambio de textura no forme un cuadrado visible."""
    rng = np.random.default_rng(semilla)
    yy, xx = np.mgrid[0:lado, 0:lado]
    dist = np.minimum.reduce([xx, yy, lado - 1 - xx, lado - 1 - yy]).astype(float)
    return dist + rng.uniform(-1.0, 1.0, (lado, lado)) < grosor


def variantes_intercambiables(base: np.ndarray, otras: list[np.ndarray], grosor: int | None = None,
                              semilla: int = 0) -> list[np.ndarray]:
    """La base y variantes con **el mismo borde que la base**: todas encajan entre sí
    en cualquier orden (y la base consigo misma, por ser periódica)."""
    lado = base.shape[0]
    grosor = grosor or max(2, lado // 10)
    salida = [base]
    for k, otra in enumerate(otras):
        borde = anillo(lado, grosor, semilla + k)
        salida.append(np.where(borde[..., None], base, otra))
    return salida


# --- transiciones por esquinas (Wang set de esquinas de Tiled) ----------------------

def _u(lado: int) -> np.ndarray:
    """Coordenada 0..1 de cada fila/columna, con la primera en 0 y la última en 1: la
    última fila de una baldosa y la primera de su vecina representan el mismo borde."""
    return np.arange(lado) / max(1, lado - 1)


def ruido_periodico(lado: int, semilla: int = 0, celdas: int = 4) -> np.ndarray:
    """Ruido suave en [-1, 1] que vale lo mismo en los cuatro bordes opuestos (periódico
    en 0..1) y es el mismo en todas las baldosas: los bordes de vecinas coinciden."""
    rng = np.random.default_rng(semilla)
    rejilla = rng.uniform(-1, 1, (celdas, celdas))
    t = _u(lado) * celdas
    base = np.floor(t).astype(int)
    f = t - base
    f = f * f * (3 - 2 * f)  # interpolación suave
    i0 = base % celdas
    i1 = (base + 1) % celdas
    fila = rejilla[i0][:, i0] * (1 - f)[None, :] + rejilla[i0][:, i1] * f[None, :]
    fila2 = rejilla[i1][:, i0] * (1 - f)[None, :] + rejilla[i1][:, i1] * f[None, :]
    return fila * (1 - f)[:, None] + fila2 * f[:, None]


def mascara_esquinas(esquinas: tuple[int, int, int, int], lado: int, ruido: np.ndarray,
                     amplitud: float = 0.25) -> np.ndarray:
    """True donde va el material B. `esquinas` = (sup-izq, sup-der, inf-der, inf-izq),
    1 = B. Interpolación bilineal de las esquinas + ruido periódico: en cada borde solo
    influyen sus dos esquinas, así que dos baldosas vecinas siempre casan."""
    tl, tr, br, bl = esquinas
    u = _u(lado)
    arriba = tl * (1 - u) + tr * u
    abajo = bl * (1 - u) + br * u
    valor = arriba[None, :] * (1 - u)[:, None] + abajo[None, :] * u[:, None]
    # El ruido se atenúa en las esquinas puras para que no aparezcan islas sueltas.
    mezcla = 4 * valor * (1 - valor)
    return valor + amplitud * mezcla * ruido > 0.5


ESQUINAS = [(tl, tr, br, bl) for tl in (0, 1) for tr in (0, 1) for br in (0, 1) for bl in (0, 1)]


def contorno(mascara: np.ndarray) -> np.ndarray:
    """Píxeles de `mascara` con algún vecino (4-conexo) fuera de ella, dentro de la baldosa."""
    m = np.pad(mascara, 1, mode="edge")
    vecino_fuera = ~m[:-2, 1:-1] | ~m[2:, 1:-1] | ~m[1:-1, :-2] | ~m[1:-1, 2:]
    return mascara & vecino_fuera


def oscuro_de(baldosa: np.ndarray, suave: bool = False) -> np.ndarray:
    """El color más oscuro de una baldosa (para contornos). Con `suave`, el segundo más oscuro
    si hay tres o más: el borde entre dos suelos no debe parecerse al contorno casi negro de los
    personajes."""
    colores = np.unique(baldosa.reshape(-1, 3), axis=0)
    orden = np.argsort(rgb_a_lab(colores.astype(float))[:, 0])
    return colores[orden[1 if suave and len(colores) >= 3 else 0]]


def transiciones(a: np.ndarray, b: np.ndarray, semilla: int = 0, borde_oscuro: bool = True
                 ) -> dict[tuple[int, int, int, int], np.ndarray]:
    """Las 16 baldosas de transición entre A y B, por combinación de esquinas."""
    lado = a.shape[0]
    ruido = ruido_periodico(lado, semilla)
    oscuro = oscuro_de(b, suave=True)
    salida = {}
    for esq in ESQUINAS:
        m = mascara_esquinas(esq, lado, ruido)
        tile = np.where(m[..., None], b, a)
        if borde_oscuro and 0 < m.mean() < 1:
            tile[contorno(m)] = oscuro
        salida[esq] = tile
    return salida


# --- vista lateral: bloques de plataforma -------------------------------------------

def plataformas(superficie: np.ndarray, relleno: np.ndarray, semilla: int = 0
                ) -> dict[tuple[int, int, int, int], np.ndarray]:
    """Bloques de plataforma por esquinas (1 = suelo, 0 = aire): relleno, con una capa
    de superficie donde hay aire encima y contorno oscuro hacia el aire. RGBA: el aire
    es transparente. Las 15 combinaciones con algo de suelo."""
    lado = relleno.shape[0]
    ruido = ruido_periodico(lado, semilla, celdas=2)
    capa = max(2, lado // 5)  # grosor de la superficie (p. ej. el pasto)
    oscuro = oscuro_de(relleno)
    salida = {}
    for esq in ESQUINAS:
        if not any(esq):
            continue
        suelo = mascara_esquinas(esq, lado, ruido, amplitud=0.08)
        # Filas de suelo desde el aire de encima, dentro de la baldosa. Lo que está por
        # encima de la baldosa cuenta como suelo: si allí hubiera aire, la superficie
        # la pinta la baldosa de arriba (comparten esquinas).
        lejos = 10 ** 6
        hasta_aire = np.zeros((lado, lado), int)
        hasta_aire[0] = np.where(suelo[0], lejos, 0)
        for y in range(1, lado):
            hasta_aire[y] = np.where(suelo[y], np.where(suelo[y - 1], hasta_aire[y - 1] + 1, 1), 0)
        sup = suelo & (hasta_aire <= capa)
        rgb = np.where(sup[..., None], superficie, relleno)
        rgb[contorno(suelo)] = oscuro
        alfa = np.where(suelo, 255, 0).astype(np.uint8)
        salida[esq] = np.dstack([rgb, alfa])
    return salida


# --- fondos en capas (vista lateral) -------------------------------------------------

# (clave, prompt, perfil, usa la ambientación). El cielo no la usa: manda la descripción
# del cielo (la ambientación, p. ej. "brillo verde de las runas", lo teñía). Las capas
# con perfil reciben su silueta como boceto (ControlNet) y se recortan por ella: la IA
# pinta paisajes completos aunque se le pida "cielo liso", así que separar el cielo a
# posteriori no es fiable (ni por color ni con el segmentador).
CAPAS_FONDO = [
    ("cielo", "pixel art sky background, soft clouds, smooth gradient, wide panorama, no mountains, "
     "no ground", None, False),
    ("lejos", "pixel art distant mountain range, far away, atmospheric, wide panorama, side view",
     "montanas", True),
    ("cerca", "pixel art rolling green hills, close, wide panorama, side view", "colinas", True),
]
# Altura de la silueta (fracción desde arriba): (más alta, más baja).
PERFILES = {"montanas": (0.22, 0.55), "colinas": (0.5, 0.72)}


def perfil(ancho: int, alto: int, tipo: str, semilla: int = 0) -> np.ndarray:
    """Fila de la silueta en cada columna, periódica en horizontal (la capa se repite).

    Montañas: suma de ondas triangulares (picos); colinas: suma de senos (suaves). Las
    frecuencias son enteras, así que el final enlaza con el principio.
    """
    rng = np.random.default_rng(semilla)
    t = np.arange(ancho) / ancho
    v = np.zeros(ancho)
    for k, peso in ((2, 1.0), (3, 0.6), (5, 0.35), (9, 0.15)):
        fase = rng.uniform(0, 1)
        if tipo == "montanas":
            onda = 1 - 2 * np.abs(((k * t + fase) % 1) - 0.5) * 2  # triangular en [-1, 1]
        else:
            onda = np.sin(2 * np.pi * (k * t + fase))
        v += peso * onda * rng.uniform(0.6, 1.0)
    v = (v - v.min()) / (np.ptp(v) + 1e-9)  # 1 = cima
    arriba, abajo = PERFILES[tipo]
    return np.round(alto * (abajo - (abajo - arriba) * v)).astype(int)


def boceto_perfil(filas: np.ndarray, alto: int, grosor: int = 6) -> Image.Image:
    """El perfil como trazo blanco sobre negro (lo que espera ControlNet scribble)."""
    lienzo = np.zeros((alto, len(filas)), np.uint8)
    for x, y in enumerate(filas):
        y0, y1 = sorted((y, filas[(x + 1) % len(filas)]))  # sin huecos en las pendientes
        lienzo[max(0, y0 - grosor // 2):min(alto, y1 + grosor // 2 + 1), x] = 255
    return Image.fromarray(lienzo)


def capa_fondo(imagen: Image.Image, alto: int, paleta_rgb: np.ndarray, pal_lab: np.ndarray,
               filas: np.ndarray | None = None, nivel: float = 0.0) -> np.ndarray:
    """Capa de fondo de `alto` px (ancho proporcional), periódica en horizontal, con la
    paleta del proyecto. Con `filas` (el perfil, en píxeles de `imagen`) lo de encima es
    transparente. RGBA."""
    ancho = round(alto * imagen.width / imagen.height)
    alfa = np.ones((alto, ancho), bool)
    if filas is not None:
        x = (np.arange(ancho) * imagen.width / ancho).astype(int)
        corte = np.round(filas[x] * alto / imagen.height).astype(int)
        alfa = np.arange(alto)[:, None] >= corte[None, :]
    rgb = suavizar(np.array(redimensionar_periodico(imagen, ancho, alto, y=False)), nivel)
    if nivel > 0:
        paleta_rgb, pal_lab = paleta_suelo(paleta_rgb, nivel)
    rgb = cuantizar(rgb, paleta_rgb, pal_lab, COLORES_CAPA, opaco=alfa)
    return np.dstack([rgb, np.where(alfa, 255, 0).astype(np.uint8)])


# --- exportación: tileset, Tiled y mapa de prueba ------------------------------------

@dataclass
class Baldosa:
    imagen: np.ndarray  # H×W×3 o H×W×4
    esquinas: tuple[int, int, int, int] | None = None  # en el Wang set (None = suelta)
    material: str = ""


@dataclass
class Tileset:
    nombre: str
    lado: int
    baldosas: list[Baldosa] = field(default_factory=list)
    colores_wang: list[tuple[str, str]] = field(default_factory=list)  # (nombre, #rrggbb)
    tipo: str = "cenital"

    def imagen(self, columnas: int = COLUMNAS_TILESET) -> Image.Image:
        filas = (len(self.baldosas) + columnas - 1) // columnas
        hoja = Image.new("RGBA", (columnas * self.lado, max(1, filas) * self.lado), (0, 0, 0, 0))
        for i, b in enumerate(self.baldosas):
            im = b.imagen if b.imagen.shape[2] == 4 else np.dstack(
                [b.imagen, np.full(b.imagen.shape[:2], 255, np.uint8)])
            hoja.paste(Image.fromarray(im, "RGBA"), ((i % columnas) * self.lado, (i // columnas) * self.lado))
        return hoja


def wangid(esquinas: tuple[int, int, int, int], colores: tuple[int, int]) -> str:
    """Orden de Tiled: arriba, arriba-der, der, abajo-der, abajo, abajo-izq, izq, arriba-izq.
    En un Wang set de esquinas los lados van a 0. `colores` = (color de 0, color de 1)."""
    tl, tr, br, bl = (colores[v] for v in esquinas)
    return f"0,{tr},0,{br},0,{bl},0,{tl}"


def tsx(ts: Tileset, archivo_imagen: str, columnas: int = COLUMNAS_TILESET) -> str:
    """Tileset de Tiled (.tsx) con un Wang set de esquinas para pintar los bordes solos."""
    alto_img = ((len(ts.baldosas) + columnas - 1) // columnas) * ts.lado
    lineas = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<tileset version="1.10" tiledversion="1.10.2" name={quoteattr(ts.nombre)} '
        f'tilewidth="{ts.lado}" tileheight="{ts.lado}" tilecount="{len(ts.baldosas)}" columns="{columnas}">',
        f' <image source={quoteattr(archivo_imagen)} width="{columnas * ts.lado}" height="{alto_img}"/>',
    ]
    con_wang = [(i, b) for i, b in enumerate(ts.baldosas) if b.esquinas is not None]
    if con_wang and ts.colores_wang:
        # Lateral: 0 = aire (sin color), 1 = suelo. Cenital: 1 = material A, 2 = material B.
        colores = (0, 1) if ts.tipo == "lateral" else (1, 2)
        lineas.append(f' <wangsets>\n  <wangset name={quoteattr(ts.nombre)} type="corner" tile="-1">')
        for nombre, color in ts.colores_wang:
            lineas.append(f'   <wangcolor name={quoteattr(nombre)} color="{color}" tile="-1" '
                          'probability="1"/>')
        for i, b in con_wang:
            lineas.append(f'   <wangtile tileid="{i}" wangid="{wangid(b.esquinas, colores)}"/>')
        lineas.append("  </wangset>\n </wangsets>")
    lineas.append("</tileset>")
    return "\n".join(lineas) + "\n"


def _por_esquinas(ts: Tileset) -> dict[tuple[int, int, int, int], list[np.ndarray]]:
    tabla: dict = {}
    for b in ts.baldosas:
        if b.esquinas is not None:
            tabla.setdefault(b.esquinas, []).append(b.imagen)
    return tabla


def mapa_prueba_cenital(ts: Tileset, ancho: int = 16, alto: int = 10, semilla: int = 0) -> Image.Image:
    """Un mapa de ejemplo pintado con el Wang set: zonas de B sobre A, con bordes."""
    rng = np.random.default_rng(semilla)
    tabla = _por_esquinas(ts)
    campo = ruido_periodico(max(ancho, alto) + 1, semilla, celdas=3)[:alto + 1, :ancho + 1]
    esquinas = (campo > 0.15).astype(int)
    lienzo = Image.new("RGBA", (ancho * ts.lado, alto * ts.lado))
    for y in range(alto):
        for x in range(ancho):
            clave = (esquinas[y, x], esquinas[y, x + 1], esquinas[y + 1, x + 1], esquinas[y + 1, x])
            opciones = tabla.get(clave)
            if not opciones:
                continue
            im = opciones[rng.integers(len(opciones))]
            lienzo.paste(Image.fromarray(im).convert("RGBA"), (x * ts.lado, y * ts.lado))
    return lienzo


def alturas_terreno(ancho: int, alto: int, semilla: int = 0) -> np.ndarray:
    """Altura (en baldosas) del suelo en cada columna de esquinas del mapa lateral de prueba."""
    return np.clip(np.round(3 + 1.5 * ruido_periodico(ancho + 1, semilla, celdas=4)[0]), 2, alto - 2)


def escena_con_personajes(mapa: Image.Image, personajes: list[Image.Image], perspectiva: str, lado: int,
                          semilla: int = 0) -> Image.Image:
    """El mapa de prueba con los personajes encima, para ver si destacan o se pierden.

    Cenital: repartidos en dos filas (así pisan los dos suelos y los bordes). Lateral: de pie sobre
    la superficie del terreno."""
    escena = mapa.convert("RGBA").copy()
    if not personajes:
        return escena
    ancho_t, alto_t = mapa.width // lado, mapa.height // lado
    alturas = alturas_terreno(ancho_t, alto_t, semilla) if perspectiva == "lateral" else None
    n = len(personajes)
    for k, sp in enumerate(personajes):
        sp = sp.convert("RGBA")
        cx = int((k + 0.5) * mapa.width / n)
        if alturas is not None:  # sobre el suelo de esa columna
            base = mapa.height - int(alturas[min(len(alturas) - 1, cx // lado)]) * lado + 4
        else:
            base = int(mapa.height * (0.55 if k % 2 == 0 else 0.92))
        escena.alpha_composite(sp, (max(0, min(mapa.width - sp.width, cx - sp.width // 2)),
                                    max(0, min(mapa.height - sp.height, base - sp.height))))
    return escena


def muestras_de_personajes(carpeta_salidas: Path, proyecto_id: str | None,
                           maximo: int = 6) -> list[Image.Image]:
    """Un sprite de cada personaje del proyecto (el retoque guardado si existe; el más reciente
    primero) para comprobar la legibilidad del suelo."""
    muestras = []
    infos = sorted(Path(carpeta_salidas).glob("*/info.json"), reverse=True)
    for info_json in infos:
        try:
            info = json.loads(info_json.read_text())
        except json.JSONDecodeError:
            continue
        if not proyecto_id or info.get("proyecto") != proyecto_id or not info.get("variantes"):
            continue
        carpeta = info_json.parent
        finales = sorted(carpeta.glob("final_??.png"))
        archivo = finales[0] if finales else carpeta / info["variantes"][0]["sprite"]
        if archivo.exists():
            muestras.append(Image.open(archivo).convert("RGBA"))
        if len(muestras) >= maximo:
            break
    return muestras


def mapa_prueba_lateral(ts: Tileset, capas: list[np.ndarray], ancho: int = 20, alto: int = 9,
                        semilla: int = 0) -> Image.Image:
    """Escena de ejemplo: capas de fondo detrás y un terreno con desniveles delante."""
    rng = np.random.default_rng(semilla)
    tabla = _por_esquinas(ts)
    W, H = ancho * ts.lado, alto * ts.lado  # noqa: N806
    lienzo = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    for capa in capas:  # de atrás hacia delante, repetidas en horizontal
        im = Image.fromarray(capa, "RGBA")
        if im.height != H:
            im = im.resize((max(1, round(im.width * H / im.height)), H), Image.NEAREST)
        for x0 in range(0, W, im.width):
            lienzo.alpha_composite(im, (x0, 0))
    alturas = alturas_terreno(ancho, alto, semilla)
    suelo = np.array([[int(cy >= alto - alturas[cx]) for cx in range(ancho + 1)] for cy in range(alto + 1)])
    for y in range(alto):
        for x in range(ancho):
            clave = (suelo[y, x], suelo[y, x + 1], suelo[y + 1, x + 1], suelo[y + 1, x])
            opciones = tabla.get(clave)
            if not opciones:
                continue
            im = opciones[rng.integers(len(opciones))]
            lienzo.alpha_composite(Image.fromarray(im, "RGBA"), (x * ts.lado, y * ts.lado))
    return lienzo


# --- orquestación ----------------------------------------------------------------------

CARPETA_MAPAS_REL = "mapas"


def _slug(texto: str) -> str:
    from motor.proyectos import a_id

    return a_id(texto)[:40] or "mapa"


def forjar_mapa(proyecto: dict, carpeta_preset: Path, carpeta_salidas: Path, *, gestor=None,
                generador=None, materiales: dict | None = None, piezas: list[str] | None = None,
                variantes: int = 4, semilla: int = 0, traducir: bool = True,
                legibilidad: float | None = None, personajes: list[Image.Image] | None = None,
                piso: str | None = None, superficie_px: int = SUPERFICIE_PX,
                colores: int = COLORES_MATERIAL) -> dict:
    """Genera el mapa del proyecto según su perspectiva. Devuelve el resumen (info.json).

    `legibilidad` (0..1) suaviza el contraste y el color del suelo para que los personajes
    destaquen. Con `None` y `personajes` (sprites del juego), en vista cenital se prueban
    `NIVELES_AUTO` y se queda el más suave que cumple `CAMUFLAJE_OBJETIVO`; sin personajes se usa
    `NIVEL_POR_DEFECTO`.

    `materiales`: cenital {"a": "pasto", "b": "tierra"}; lateral {"superficie": "pasto",
    "relleno": "tierra", "cielo": "atardecer"}. Textos en español o inglés.
    `piezas`: subconjunto de suelos, transiciones (cenital) / plataformas, fondo (lateral).
    `generador(prompt, negativo, n, semilla, ancho, alto, x, y, control=None)` sustituye a la
    IA (tests). `superficie_px` y `colores`: detalle de las baldosas (ver `baldosas_material`).
    """
    from motor import generar as gen
    from motor.traducir import a_ingles
    from motor.traducir import traducir as traducir_texto

    t0 = time.perf_counter()
    perspectiva = proyecto.get("perspectiva", "cenital")
    lado = int(proyecto.get("baldosa", 32))
    materiales = {k: (v or "").strip() for k, v in (materiales or {}).items()}
    if perspectiva == "cenital":
        piezas = piezas or ["suelos", "transiciones"]
        if not materiales.get("a"):
            raise ValueError("Indica el suelo principal (p. ej. «pasto»)")
        if "transiciones" in piezas and not materiales.get("b"):
            raise ValueError("Las transiciones necesitan un segundo suelo (p. ej. «tierra»)")
    else:
        piezas = piezas or ["plataformas", "fondo"]
        if "plataformas" in piezas and not (materiales.get("superficie") and materiales.get("relleno")):
            raise ValueError("Los bloques de plataforma necesitan superficie y relleno "
                             "(p. ej. pasto y tierra)")
    validas = {"cenital": {"suelos", "transiciones"}, "lateral": {"plataformas", "fondo"}}[perspectiva]
    if not set(piezas) <= validas:
        raise ValueError(f"Piezas no válidas para la vista {perspectiva}: {sorted(set(piezas) - validas)}")
    variantes = max(1, min(int(variantes), 6))

    estilo = json.loads((Path(carpeta_preset) / "estilo.json").read_text())
    paleta_rgb, pal_lab = paleta_lab(estilo["paleta"])
    ambientacion = proyecto.get("ambientacion", "")
    if traducir:
        ambientacion = a_ingles(ambientacion)[0]
    vista = "top-down view, flat ground seen from directly above" if perspectiva == "cenital" else "side view"

    if generador is None:
        gestor = gestor or gen.GestorModelos(minutos_inactividad=0)
        referencia = gen.rejilla_referencia(Path(carpeta_preset), estilo)

        def generador(prompt, negativo, n, semilla, ancho, alto, x=True, y=True, control=None):  # noqa: F811
            # Sin referencia de estilo (escala 0): los ejemplos del preset son sprites sobre
            # blanco y metían objetos sueltos en el suelo. La coherencia la da la paleta.
            return texturas_ia(gestor, prompt, negativo, referencia, n, semilla, ancho, alto, x, y,
                               fuerza_estilo=0.0,
                               fuerza_pixel_art=estilo["generacion"].get("fuerza_pixel_art", 0.8),
                               control=control)

    def en_ingles(texto):
        # Siempre por el traductor: textos cortos como "pasto oscuro" no tienen tildes ni
        # palabras típicas y la detección de español no los reconoce.
        return traducir_texto(texto).rstrip(".") if traducir and texto else texto

    def prompt_suelo(material):
        # Sin la ambientación: sus "ruinas", "runas"… convertían el pasto en losas con musgo.
        # El color del mundo lo impone la paleta del proyecto al cuantizar.
        return gen.unir_con_limite(
            ["pixel art", f"seamless {en_ingles(material)} ground texture", vista,
             "natural terrain, 16-bit rpg tileset, simple, small evenly spread details, "
             "flat even lighting"], [])

    textos, imagenes_ia = {}, {}
    rondas = 0

    if legibilidad is not None and not 0 <= legibilidad <= 1:
        raise ValueError("La legibilidad debe estar entre 0 y 1")
    personajes = personajes or []
    medidas: dict | None = None

    def textura(clave):
        """Una sola textura de la IA por material: las variantes son recortes de su superficie."""
        nonlocal rondas
        if es_agua(materiales[clave]):
            textos[clave] = f"(textura procedural de agua: {materiales[clave]})"
            ims = [textura_agua(materiales[clave], semilla=semilla + 100 * rondas)]
            rondas += 1
            imagenes_ia[clave] = ims
            return ims[0]
        prompt = prompt_suelo(materiales[clave])
        textos[clave] = prompt
        ims = generador(prompt, NEGATIVO_TEXTURA, 1, semilla + 100 * rondas, LADO_TEXTURA, LADO_TEXTURA)
        rondas += 1
        imagenes_ia[clave] = ims
        return ims[0]

    def material(clave, n, nivel):
        return baldosas_material(textura(clave), lado, n, paleta_rgb, pal_lab, semilla, nivel,
                                 superficie_px, colores)

    nombre = proyecto.get("nombre", "mapa")
    ts = Tileset(nombre=f"{nombre} ({perspectiva})", lado=lado, tipo=perspectiva)
    capas: list[np.ndarray] = []
    nivel = NIVEL_POR_DEFECTO if legibilidad is None else legibilidad
    if perspectiva == "cenital":
        tex_a = textura("a")
        tex_b = textura("b") if materiales.get("b") else None
        niveles = [legibilidad] if legibilidad is not None else (list(NIVELES_AUTO) if personajes
                                                                  else [NIVEL_POR_DEFECTO])
        candidatos = []
        for candidato in niveles:
            a_c = baldosas_material(tex_a, lado, variantes, paleta_rgb, pal_lab, semilla, candidato,
                                    superficie_px, colores)
            b_c = (baldosas_material(tex_b, lado, variantes, paleta_rgb, pal_lab, semilla, candidato,
                                     superficie_px, colores)
                   if tex_b is not None else None)
            m = camuflaje([*a_c, *(b_c or [])], personajes) if personajes else None
            candidatos.append((candidato, a_c, b_c, m))
        if personajes and len(candidatos) > 1:
            # El nivel MÁS BAJO (el suelo con más carácter) que cumpla el objetivo o que se quede a
            # menos de 0.02 de lo mejor posible: apretar más aplana el suelo sin que ganen los personajes.
            mejor_medio = min(c[3]["medio"] for c in candidatos)
            tope = max(CAMUFLAJE_OBJETIVO, mejor_medio + 0.02)
            elegido = next(c for c in candidatos if c[3]["medio"] <= tope)
        else:
            elegido = candidatos[0]
        nivel, a, b, medidas = elegido
        if "transiciones" in piezas and b is not None:
            ts.colores_wang = [(materiales["a"], "#3c8c3c"), (materiales["b"], "#8c6a3c")]
            for esq, tile in transiciones(a[0], b[0], semilla).items():
                ts.baldosas.append(Baldosa(tile, esq))
            # Variantes de los suelos puros: Tiled elige entre ellas al pintar.
            ts.baldosas += [Baldosa(t, (0, 0, 0, 0), materiales["a"]) for t in a[1:]]
            ts.baldosas += [Baldosa(t, (1, 1, 1, 1), materiales["b"]) for t in b[1:]]
        else:
            ts.baldosas += [Baldosa(t, None, materiales["a"]) for t in a]
            if b is not None:
                ts.baldosas += [Baldosa(t, None, materiales["b"]) for t in b]
    else:
        if "plataformas" in piezas:
            sup = material("superficie", 1, nivel)[0]
            rel = material("relleno", variantes, nivel)
            ts.colores_wang = [(materiales["relleno"], "#8c6a3c")]
            bloques = plataformas(sup, rel[0], semilla)
            for esq, tile in bloques.items():
                ts.baldosas.append(Baldosa(tile, esq))
            for extra in rel[1:]:  # variantes del bloque interior
                otro = plataformas(sup, extra, semilla)[(1, 1, 1, 1)]
                ts.baldosas.append(Baldosa(otro, (1, 1, 1, 1), materiales["relleno"]))
        if "fondo" in piezas:
            alto_capa = lado * 9
            cielo = en_ingles(materiales.get("cielo") or "")
            for i, (clave, base, tipo_perfil, con_ambiente) in enumerate(CAPAS_FONDO):
                partes = ([cielo] if cielo else []) + [base]  # lo pedido, primero (pesa más)
                prompt = gen.unir_con_limite(partes, gen._partes(ambientacion) if con_ambiente else [])
                textos[f"fondo_{clave}"] = prompt
                filas = control = None
                if tipo_perfil:
                    filas = perfil(FONDO_ANCHO, FONDO_ALTO, tipo_perfil, semilla + i)
                    control = boceto_perfil(filas, FONDO_ALTO)
                im = generador(prompt, NEGATIVO_FONDO, 1, semilla + 1000 + i, FONDO_ANCHO, FONDO_ALTO,
                               True, False, control=control)[0]
                imagenes_ia[f"fondo_{clave}"] = [im]
                capas.append(capa_fondo(im, alto_capa, paleta_rgb, pal_lab, filas, nivel))

    fecha = datetime.now()
    base_nombre = piso or materiales.get("a") or materiales.get("superficie") or "fondo"
    from motor.forja import carpeta_libre

    carpeta = carpeta_libre(Path(carpeta_salidas) / CARPETA_MAPAS_REL,
                            f"{fecha:%Y-%m-%d_%H%M%S}_{_slug(nombre)}-{_slug(base_nombre)}")
    archivos = {}
    if ts.baldosas:
        hoja = ts.imagen()
        hoja.save(carpeta / "tileset.png")
        ampliar(hoja, 4).save(carpeta / "tileset_x4.png")
        (carpeta / "tileset.tsx").write_text(tsx(ts, "tileset.png"))
        archivos.update(tileset="tileset.png", tileset_x4="tileset_x4.png", tsx="tileset.tsx")
    for (clave, *_), capa in zip(CAPAS_FONDO, capas, strict=False):
        Image.fromarray(capa, "RGBA").save(carpeta / f"fondo_{clave}.png")
        archivos[f"fondo_{clave}"] = f"fondo_{clave}.png"
    if perspectiva == "cenital" and ts.colores_wang:
        prueba = mapa_prueba_cenital(ts, semilla=semilla)
    elif perspectiva == "lateral" and (ts.baldosas or capas):
        prueba = mapa_prueba_lateral(ts, capas, semilla=semilla)
    else:
        prueba = None
    if prueba is not None:
        prueba.save(carpeta / "mapa_prueba.png")
        ampliar(prueba, 2).save(carpeta / "mapa_prueba_x2.png")
        archivos.update(mapa_prueba="mapa_prueba.png", mapa_prueba_x2="mapa_prueba_x2.png")
        if personajes:  # los personajes del proyecto sobre el mapa: ¿destacan o se pierden?
            escena = escena_con_personajes(prueba, personajes, perspectiva, lado, semilla)
            escena.save(carpeta / "escena_prueba.png")
            ampliar(escena, 2).save(carpeta / "escena_prueba_x2.png")
            archivos.update(escena_prueba="escena_prueba.png", escena_prueba_x2="escena_prueba_x2.png")
    ia = carpeta / "ia"
    ia.mkdir()
    for clave, ims in imagenes_ia.items():
        for i, im in enumerate(ims, 1):
            im.save(ia / f"{clave}_{i}.png")
    info = {
        "fecha": fecha.isoformat(timespec="seconds"), "proyecto": proyecto.get("id"),
        "piso": piso, "perspectiva": perspectiva, "baldosa": lado, "materiales": materiales, "piezas": piezas,
        "variantes": variantes, "semilla": semilla, "baldosas": len(ts.baldosas),
        "columnas": COLUMNAS_TILESET, "prompts": textos, "archivos": archivos,
        "legibilidad": {"nivel": nivel, "automatica": legibilidad is None and bool(personajes),
                        "luminosidad_suelo": round(float(np.mean([
                            rgb_a_lab(b.imagen[..., :3].astype(float))[..., 0].mean()
                            for b in ts.baldosas if b.imagen.shape[2] == 3] or [0])), 1),
                        "camuflaje": medidas},
        "segundos": round(time.perf_counter() - t0, 1),
    }
    (carpeta / "info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n")
    info["carpeta"] = carpeta.name
    return info


def mapas_de_pisos(proyecto_id: str, pisos: list[str] | None = None, variantes: int = 4, semilla: int = 5,
                   legibilidad: float | None = None, carpeta_salidas: Path | None = None, gestor=None,
                   generador=None, carpeta_proyectos: Path | None = None) -> list[dict]:
    """Un mapa por cada piso del proyecto (o los indicados), con sus dos suelos y los personajes del
    proyecto como muestra de legibilidad. Devuelve los resúmenes (`info.json`) de cada uno."""
    from motor import proyectos
    from motor.forja import CARPETA_SALIDAS

    carpeta_salidas = Path(carpeta_salidas or CARPETA_SALIDAS)
    proyecto = proyectos.cargar(proyecto_id, carpeta_proyectos or proyectos.CARPETA_PROYECTOS)
    elegidos = [p for p in proyecto.get("pisos", []) if not pisos or p["id"] in pisos]
    if not elegidos:
        raise ValueError("El proyecto no tiene pisos" if not pisos else f"Pisos desconocidos: {pisos}")
    desconocidos = set(pisos or []) - {p["id"] for p in elegidos}
    if desconocidos:
        raise ValueError(f"Pisos desconocidos: {', '.join(sorted(desconocidos))}")
    personajes = muestras_de_personajes(carpeta_salidas, proyecto_id)
    from motor.generar import GestorModelos

    gestor = gestor or (GestorModelos(minutos_inactividad=0) if generador is None else None)
    salida = []
    for piso in elegidos:
        log.info("Piso: %s", piso["nombre"])
        salida.append(forjar_mapa(
            proyecto, RAIZ / "presets" / proyecto["preset"], carpeta_salidas, gestor=gestor,
            generador=generador, materiales={"a": piso["a"], "b": piso.get("b", "")},
            variantes=variantes, semilla=semilla, legibilidad=legibilidad, personajes=personajes,
            piso=piso["id"]))
    return salida


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="forja-mapas", description="Mapas de todos los pisos de un proyecto.")
    ap.add_argument("-p", "--proyecto", required=True)
    ap.add_argument("--pisos", help="ids separados por comas (por defecto, todos)")
    ap.add_argument("-n", "--variantes", type=int, default=4)
    ap.add_argument("--semilla", type=int, default=5)
    ap.add_argument("--legibilidad", type=float, help="0..1 (por defecto, automática según los personajes)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for info in mapas_de_pisos(args.proyecto, args.pisos.split(",") if args.pisos else None, args.variantes,
                               args.semilla, args.legibilidad):
        leg = info["legibilidad"]
        print(f"{info['carpeta']}: nivel {leg['nivel']}, camuflaje {leg['camuflaje']}")


if __name__ == "__main__":
    main()
