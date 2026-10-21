# ruff: noqa: E501
"""Mapas grandes de mazmorra con superficies trabajadas por Qwen-Image.

Un tile suelto de 32 px no puede llevar mucho detalle; una **superficie periódica de 8×8 baldosas**
(256 px) sí. Por eso el mapa se pinta con superficies grandes que se repiten sin costura, y no con
baldosas sueltas:

    trazado de losas / ladrillos (por código, periódico)
      -> Qwen lo convierte en piedra trabajada (desgaste, grietas, bisel, moho; mismo trazado)
      -> a la resolución del juego y a la paleta del proyecto            (superficies/)
    plano del mapa (salas y pasillos por código)
      -> suelo, muros con borde y sombra, moho y charcos de agua encima
      -> PNG + mapa de Tiled (.tmx) con capa de colisiones

Las superficies se guardan y se reutilizan: sacar otro mapa del mismo ambiente con otra semilla no gasta
GPU. Solo usa Pillow, numpy y la biblioteca estándar (más `motor.qwen` para las superficies).
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from motor import escenas, mapas, qwen
from motor.modelos import RAIZ

log = logging.getLogger(__name__)

LADO = 32
PERIODO = 256  # px de juego de una superficie (8×8 baldosas)
PERIODO_AGUA = 128  # el agua, con ondas la mitad de grandes: a 256 px las ondas eran rayas anchas en un charco
FUENTE = 1024  # px que dibuja Qwen
COLORES = 20
UMBRAL_MUSGO = 0.74  # más alto = menos moho (con 0,74 cubre ~20 % del suelo)

NOMBRES = {"catacumbas": "Catacumbas", "cavernas-fungicas": "Cavernas fúngicas", "cripta-helada": "Cripta helada",
           "forja-de-lava": "Forja de lava", "fortaleza": "Fortaleza", "jardin-de-hongos": "Jardín de hongos",
           "oasis-subterraneo": "Oasis subterráneo", "rocas-resbaladizas": "Rocas resbaladizas"}
CARPETA_SUPERFICIES = RAIZ / "assets" / "mapas" / "grande"
SUPERFICIES = ("suelo", "muro", "agua", "musgo")


def superficies_listas(ambiente: str, carpeta: Path | None = None) -> bool:
    """¿Están ya las cuatro superficies del ambiente? (si no, generar un mapa necesita el servidor Qwen y ~4 min)."""
    base = Path(carpeta) if carpeta else CARPETA_SUPERFICIES / ambiente / "superficies"
    return all((base / f"{n}.png").exists() for n in SUPERFICIES)


# Cada ambiente define cómo se pide cada superficie. `trazado` elige el plano por código que Qwen pinta:
# «losas» (celdas irregulares: `celdas`, `jitter`, `junta`) o «ladrillos» (hiladas: `filas`, `columnas`,
# `desplazar`). `liquido.rampa` son los colores exactos del líquido (de oscuro a destello) que se añaden a
# la paleta del proyecto solo en el mapa; `cortes` reparte la luminosidad de Qwen entre esos tonos.
AMBIENTES = {
    "catacumbas": {
        "tono": "cool dark blue-grey stone, desaturated, grim and grotesque",
        "suelo": {"trazado": "losas", "celdas": 4, "pieza": "stone slabs",
                  "texto": "irregular cracked flagstones of cool dark blue-grey stone", "extra": "a little moss in a few joints"},
        "muro": {"trazado": "ladrillos", "filas": 8, "columnas": 4, "pieza": "bricks",
                 "texto": "dark blue-grey stone bricks of an old crypt wall", "extra": "a little moss"},
        "cobertura": "dense dark green moss and mold growth with small lighter tufts and wet spots",
        "liquido": {"texto": "murky dark blue stagnant water: dark navy-blue water with soft irregular ripples following the "
                             "pattern, subtle pale reflections on the ripple crests, a few floating algae specks and tiny bubbles",
                    "rampa": [(24, 38, 72), (34, 60, 108), (52, 94, 150), (110, 160, 205)], "cortes": (0.16, 0.88, 0.985)},
        "charcos": (3, 1.0), "umbral": 0.74,
    },
    "cavernas-fungicas": {
        "ajuste": {"muro": {"luz": 1.55, "croma": 1.1}},
        "tono": "dark wet cave stone with a cold blue-green tint, damp and eerie",
        "suelo": {"trazado": "losas", "celdas": 5, "jitter": 0.42, "junta": 10, "pieza": "uneven rock plates",
                  "texto": "uneven dark wet cave rock floor with patches of damp earth", "extra": "tiny glowing fungus specks in a few cracks"},
        "muro": {"trazado": "losas", "celdas": 4, "jitter": 0.4, "junta": 12, "pieza": "rounded boulders",
                 "texto": "natural dark cave rock wall of rounded boulders", "extra": "damp streaks and a little fungus"},
        "cobertura": "dense patches of glowing blue-green bioluminescent fungus with bright spore speckles",
        "liquido": {"texto": "dark teal cave water with faint glowing spores: dark blue-green water, soft irregular ripples "
                             "following the pattern, a few glowing cyan specks and pale reflections",
                    "rampa": [(14, 40, 46), (22, 72, 78), (40, 120, 118), (120, 230, 200)], "cortes": (0.2, 0.85, 0.97)},
        "charcos": (3, 1.0), "umbral": 0.72,
    },
    "cripta-helada": {
        "ajuste": {"muro": {"luz": 0.78}},
        "tono": "pale frozen blue-grey stone covered in frost, cold and silent",
        "suelo": {"trazado": "losas", "celdas": 4, "pieza": "stone slabs",
                  "texto": "frozen stone slabs covered with frost and thin ice cracks, pale blue-grey", "extra": "white frost in the joints"},
        "muro": {"trazado": "ladrillos", "filas": 8, "columnas": 4, "pieza": "bricks",
                 "texto": "frost-covered pale blue-grey stone bricks", "extra": "white frost along the mortar"},
        "cobertura": "white frost and ice crystal patches with snow dust, pale blue and white",
        "liquido": {"texto": "frozen ice puddle: smooth pale blue ice with fine white cracks following the pattern and soft "
                             "glints, almost flat",
                    "rampa": [(120, 150, 185), (160, 190, 222), (205, 225, 242), (244, 250, 255)], "cortes": (0.2, 0.62, 0.92)},
        "charcos": (3, 1.0), "umbral": 0.74,
    },
    "forja-de-lava": {
        "ajuste": {"suelo": {"mediana": 0.22, "croma": 1.4, "luz": 1.35}, "muro": {"luz": 1.4, "croma": 1.3}},
        "tono": "scorched black volcanic stone with warm orange glow, hot and oppressive",
        "suelo": {"trazado": "losas", "celdas": 5, "jitter": 0.4, "junta": 9, "pieza": "basalt plates",
                  "texto": "black cracked basalt floor plates with thin glowing orange lava cracks between them", "extra": "ash and soot"},
        "muro": {"trazado": "ladrillos", "filas": 8, "columnas": 4, "pieza": "bricks",
                 "texto": "scorched blackened dark bricks with soot", "extra": "a faint ember glow in the mortar"},
        "cobertura": "scattered black ash and cinders with small glowing orange embers",
        "liquido": {"texto": "molten lava: glowing orange-red lava with darker cooling crust patches following the pattern "
                             "and bright yellow hot veins",
                    "rampa": [(70, 14, 10), (150, 40, 14), (230, 100, 24), (255, 214, 110)], "cortes": (0.15, 0.55, 0.88)},
        "charcos": (3, 1.0), "umbral": 0.74,
    },
    "fortaleza": {
        "ajuste": {"suelo": {"mediana": 0.26, "croma": 0.35}, "muro": {"croma": 0.35, "luz": 0.8}, "musgo": {"croma": 0.8, "luz": 0.7}},
        "tono": "weathered neutral grey castle stone, dusty and austere",
        "suelo": {"trazado": "losas", "celdas": 3, "jitter": 0.1, "junta": 9, "pieza": "large nearly rectangular stone slabs",
                  "texto": "large grey stone floor slabs neatly laid, worn, with chipped corners and clear dark joints", "extra": "dust in the joints"},
        "muro": {"trazado": "ladrillos", "filas": 4, "columnas": 2, "pieza": "massive wall blocks",
                 "texto": "massive grey castle wall stone blocks, weathered", "extra": "dark stains"},
        "cobertura": "dark dried blood stains, mud and grime splatters in dark red-brown",
        "liquido": {"texto": "dirty stagnant puddle: murky olive-brown water with a few pale reflections and soft ripples "
                             "following the pattern",
                    "rampa": [(34, 32, 24), (52, 50, 34), (74, 70, 44), (120, 114, 78)], "cortes": (0.2, 0.85, 0.98)},
        "charcos": (2, 0.9), "umbral": 0.76,
    },
    "jardin-de-hongos": {
        "ajuste": {"musgo": {"croma": 0.7, "luz": 0.8}},
        "tono": "dark damp earthy ground, overgrown and unsettling",
        "suelo": {"trazado": "losas", "celdas": 5, "jitter": 0.42, "junta": 10, "pieza": "ground plates",
                  "texto": "dark damp soil ground with embedded small stones and thin roots between the plates", "extra": "tiny mushrooms"},
        "muro": {"trazado": "losas", "celdas": 4, "jitter": 0.4, "junta": 12, "pieza": "rounded rocks",
                 "texto": "mossy earthen cave wall with roots and stones", "extra": "hanging roots and fungus"},
        "cobertura": "dense small mushrooms and fungus cluster caps in red and brown with tiny pale grass",
        "liquido": {"texto": "murky green-brown swamp water with soft ripples following the pattern, floating algae and a few "
                             "dull reflections",
                    "rampa": [(22, 36, 22), (36, 60, 32), (58, 92, 44), (120, 150, 70)], "cortes": (0.18, 0.86, 0.975)},
        "charcos": (3, 1.0), "umbral": 0.7,
    },
    "oasis-subterraneo": {
        "ajuste": {"suelo": {"croma": 0.7, "luz": 0.9}, "muro": {"croma": 0.7, "luz": 0.85}, "musgo": {"croma": 0.75, "luz": 0.75}},
        "tono": "warm tan sandstone, sunlit and humid",
        "suelo": {"trazado": "losas", "celdas": 4, "pieza": "sandstone slabs",
                  "texto": "warm tan sandstone slabs, sandy and worn", "extra": "sand in the joints"},
        "muro": {"trazado": "ladrillos", "filas": 8, "columnas": 4, "pieza": "bricks",
                 "texto": "warm sandstone bricks, weathered", "extra": "a little green moss"},
        "cobertura": "short lush green grass and small reed tufts",
        "liquido": {"texto": "clear turquoise oasis water: bright turquoise water with soft ripples following the pattern and "
                             "white sparkles",
                    "rampa": [(24, 96, 120), (36, 134, 156), (70, 180, 190), (200, 240, 235)], "cortes": (0.2, 0.62, 0.93)},
        "charcos": (2, 1.7), "umbral": 0.72,
    },
    "rocas-resbaladizas": {
        "tono": "smooth wet dark grey-blue rock, glossy and slippery",
        "suelo": {"trazado": "losas", "celdas": 5, "jitter": 0.4, "junta": 9, "pieza": "smooth rock plates",
                  "texto": "smooth wet slippery dark grey-blue rock plates, glossy with wet highlights", "extra": "water streaks"},
        "muro": {"trazado": "losas", "celdas": 4, "jitter": 0.4, "junta": 12, "pieza": "wet rock masses",
                 "texto": "wet dark grey-blue rock wall, glossy", "extra": "dripping water streaks"},
        "cobertura": "dark wet slimy moss and algae film patches",
        "liquido": {"texto": "shallow clear water film over rock: grey-blue water with soft ripples following the pattern and "
                             "bright glints",
                    "rampa": [(40, 56, 72), (60, 84, 104), (92, 120, 140), (180, 205, 220)], "cortes": (0.2, 0.8, 0.96)},
        "charcos": (4, 1.2), "umbral": 0.7,
    },
}


# --- superficies ---------------------------------------------------------------------

def trazado_losas(celdas: int = 4, semilla: int = 0, n: int = FUENTE, jitter: float = 0.32,
                  junta: float = 7) -> Image.Image:
    """Losas irregulares (Voronoi periódico) con juntas oscuras y bisel: el plano que Qwen pinta."""
    r = np.random.default_rng(semilla)
    puntos = [((i + 0.5 + r.uniform(-jitter, jitter)) / celdas, (j + 0.5 + r.uniform(-jitter, jitter)) / celdas)
              for j in range(celdas) for i in range(celdas)]
    yy, xx = np.mgrid[0:n, 0:n] / n
    d1 = np.full((n, n), 9.0)
    d2 = np.full((n, n), 9.0)
    idx = np.zeros((n, n), int)
    for k, (px, py) in enumerate(puntos):
        dx = np.abs(xx - px)
        dy = np.abs(yy - py)
        dist = np.hypot(np.minimum(dx, 1 - dx), np.minimum(dy, 1 - dy))
        mas_cerca = dist < d1
        d2 = np.where(mas_cerca, d1, np.minimum(d2, dist))
        idx = np.where(mas_cerca, k, idx)
        d1 = np.minimum(d1, dist)
    borde = (d2 - d1) * n / 2  # px hasta la junta
    tono = np.random.default_rng(semilla + 1).uniform(105, 150, len(puntos))[idx]
    img = np.where(borde[..., None] < junta, np.array([18, 18, 22]), np.stack([tono] * 3, -1))
    gy, gx = np.gradient(borde)
    luz = np.clip((-gx - gy) * 1.2, -1, 1) * (borde < 22)
    return Image.fromarray(np.clip(img + luz[..., None] * 45, 0, 255).astype(np.uint8))


def trazado_ladrillos(semilla: int = 0, n: int = FUENTE, filas: int = 8, columnas: int = 4,
                      desplazar: bool = True) -> Image.Image:
    """Ladrillos a soga y tizón (hiladas desplazadas), periódico, con mortero oscuro."""
    if n % filas or n % columnas:  # si no, las hiladas no encajan y la textura deja de ser periódica
        raise ValueError(f"{filas} hiladas y {columnas} columnas no dividen {n} px: usa 2, 4, 8 o 16")
    alto, ancho = n // filas, n // columnas
    yy, xx = np.mgrid[0:n, 0:n]
    fila = yy // alto
    xs = (xx + (np.where(fila % 2, ancho // 2, 0) if desplazar else 0)) % n
    columna = xs // ancho
    dentro = ((xs % ancho >= 4) & (xs % ancho < ancho - 4) & (yy % alto >= 4) & (yy % alto < alto - 4))
    tono = np.random.default_rng(semilla).uniform(95, 140, (filas, columnas))[fila, columna]
    img = np.where(dentro[..., None], tono[..., None], np.array([16.0, 16, 20]))
    return Image.fromarray(img.astype(np.uint8))


def trazado_ondas(semilla: int = 0, n: int = FUENTE) -> Image.Image:
    """Patrón de ondas irregulares en grises (crestas claras, senos oscuros), periódico: el plano que Qwen
    pinta como agua. Qwen dibuja el agua a partir de solo texto como un plano liso, sin ondas (medido);
    con este patrón conserva las ondas y añade reflejos, algas y burbujas."""
    k = n // 64
    ruido = np.kron(mapas.ruido_periodico(64, semilla, celdas=4), np.ones((k, k)))
    ruido2 = np.kron(mapas.ruido_periodico(64, semilla + 1, celdas=2), np.ones((k, k)))
    y, x = np.mgrid[0:n, 0:n]
    fase = y / (n / 7) + 1.4 * ruido2 + 0.4 * np.sin(2 * np.pi * x / (n / 2) + 3 * ruido)
    onda = (np.sin(2 * np.pi * fase) * 0.5 + 0.5) * (0.6 + 0.8 * ruido)
    return Image.fromarray(np.clip(40 + onda * 150, 0, 255).astype(np.uint8)).convert("RGB")


def agua_procedural(base: tuple[int, int, int], semilla: int = 0, lado: int = PERIODO) -> np.ndarray:
    """Agua estancada periódica con ondas, brillos y manchas más claras (RGB uint8, `lado` px).

    Qwen-Image dibuja el agua de arriba como un plano liso sin ondas (medido), así que se hace a mano:
    un color base que varía con ruido suave, líneas de onda onduladas claras y unos pocos reflejos."""
    k = lado // 64
    ruido = np.kron(mapas.ruido_periodico(64, semilla, celdas=4), np.ones((k, k)))
    ruido2 = np.kron(mapas.ruido_periodico(64, semilla + 1, celdas=2), np.ones((k, k)))
    y, x = np.mgrid[0:lado, 0:lado]
    # ondas irregulares: la fase se deforma mucho con ruido, y las crestas son cortas y suaves
    fase = y / (lado / 9) + 1.3 * ruido2 + 0.35 * np.sin(2 * np.pi * x / (lado / 2) + 3 * ruido)
    onda = np.sin(2 * np.pi * fase) * (0.5 + ruido)
    rgb = np.array(base, float)[None, None, :] * (0.78 + 0.55 * ruido[..., None])
    rgb = np.where((onda > 0.8)[..., None], rgb * 1.3 + 6, rgb)  # crestas finas y poco claras
    rgb = np.where((onda < -0.45)[..., None], rgb * 0.62, rgb)  # senos oscuros: el agua es turbia y oscura
    brillos = mapas.ruido_periodico(64, semilla + 7, celdas=8)
    rgb = np.where((np.kron(brillos, np.ones((k, k))) > 0.93)[..., None], np.minimum(255, rgb * 1.6 + 30), rgb)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def a_superficie(imagen: Image.Image, paleta_rgb, pal_lab, colores: int = COLORES, periodo: int = PERIODO,
                 banda: float = 0.06, ajuste: dict | None = None) -> np.ndarray:
    """Imagen de Qwen -> superficie periódica de `periodo` px con la paleta del proyecto (RGB uint8).
    `ajuste` {mediana, croma, luz}: realce de color y luminosidad antes de la paleta (por ambiente)."""
    p = qwen.periodizar(imagen, banda=banda).resize((periodo, periodo), Image.BOX)
    return np.asarray(escenas.a_paleta(p, paleta_rgb, pal_lab, colores, **(ajuste or {})), dtype=np.uint8)


RAMPA_AGUA = AMBIENTES["catacumbas"]["liquido"]["rampa"]  # (compatibilidad: el agua azul de las catacumbas)
CORTES_AGUA = AMBIENTES["catacumbas"]["liquido"]["cortes"]


def superficie_liquido(imagen: Image.Image, rampa=None, cortes=None, periodo: int = PERIODO_AGUA) -> np.ndarray:
    """Líquido de Qwen -> superficie con la `rampa` del ambiente (RGB uint8, `periodo` px).

    La luminosidad de Qwen (las ondas y los reflejos) se reparte en los tonos de la rampa con `cortes`
    (fracción de píxeles bajo cada tono): casi todo tono oscuro y liso, algunas ondas más claras y
    destellos escasos. Si se deja que una paleta cuantice sola, el líquido oscuro cae en grises sueltos y
    se ve a rayas."""
    rampa = rampa or RAMPA_AGUA
    cortes = cortes or CORTES_AGUA
    p = qwen.periodizar(imagen, banda=0.06).resize((periodo, periodo), Image.BOX)
    luz = np.asarray(p.convert("L"), dtype=float)
    nivel = np.searchsorted(np.quantile(luz, cortes), luz)
    return np.array(rampa, np.uint8)[nivel]


superficie_agua = superficie_liquido  # nombre anterior


def trazado(cfg: dict, semilla: int) -> Image.Image:
    """Plano por código de una superficie de piedra según su configuración (ver `AMBIENTES`)."""
    if cfg["trazado"] == "losas":
        return trazado_losas(cfg.get("celdas", 4), semilla, jitter=cfg.get("jitter", 0.32), junta=cfg.get("junta", 7))
    return trazado_ladrillos(semilla, filas=cfg.get("filas", 8), columnas=cfg.get("columnas", 4),
                             desplazar=cfg.get("desplazar", True))


def pedido_piedra(cfg: dict, tono: str, que: str) -> str:
    """Texto para que Qwen convierta el plano de `cfg` en suelo (`que`="floor") o muro (`que`="wall")."""
    objeto = "dungeon floor" if que == "floor" else "dungeon wall"
    return (f"Turn this flat diagram of {cfg['pieza']} into a richly detailed top-down pixel art {objeto} texture "
            f"({cfg['texto']}; {tono}). Keep EXACTLY the same layout and joint positions. Make every piece look "
            "different and hand-painted: weathered, cracked, chipped edges, grime, tiny stains, bevelled edges lit "
            f"from the top-left, deep dark joints, {cfg['extra']}. Chunky pixel art, clear dark outlines, no "
            "objects, no characters.")


def remapear_superficies(ambiente: str, paleta_rgb, pal_lab, carpeta: Path) -> dict[str, np.ndarray]:
    """Rehace las superficies de `carpeta` a partir de los originales de Qwen guardados en `carpeta/ia/`, con el
    `ajuste` actual del ambiente (color y luminosidad). No usa la GPU: sirve para afinar el aspecto."""
    cfg = AMBIENTES[ambiente]
    carpeta = Path(carpeta)
    salida = {}
    for nombre in ("suelo", "muro", "agua", "musgo"):
        original = carpeta / "ia" / f"{nombre}.png"
        if not original.exists():
            continue
        ia = Image.open(original).convert("RGB")
        if nombre == "agua":
            sup = superficie_liquido(ia, cfg["liquido"]["rampa"], cfg["liquido"]["cortes"])
        else:
            sup = a_superficie(ia, paleta_rgb, pal_lab, 10 if nombre == "musgo" else COLORES,
                               ajuste=cfg.get("ajuste", {}).get(nombre))
        Image.fromarray(sup).save(carpeta / f"{nombre}.png")
        salida[nombre] = sup
    return salida


def generar_superficies(ambiente: str, url: str, paleta_rgb, pal_lab, carpeta: Path, semilla: int = 0
                        ) -> dict[str, np.ndarray]:
    """Las cuatro superficies del ambiente (suelo, muro, agua o líquido, musgo o cobertura); las que ya estén
    en `carpeta` se reutilizan. Guarda también las imágenes originales de Qwen en `carpeta/ia/`."""
    cfg = AMBIENTES[ambiente]
    carpeta = Path(carpeta)
    (carpeta / "ia").mkdir(parents=True, exist_ok=True)
    tono, liq = cfg["tono"], cfg["liquido"]
    recetas = {
        "suelo": lambda: qwen.editar([trazado(cfg["suelo"], semilla + 2)], pedido_piedra(cfg["suelo"], tono, "floor"),
                                     url, FUENTE, FUENTE, semilla, ref_args=qwen.REF_ARGS),
        "muro": lambda: qwen.editar([trazado(cfg["muro"], semilla + 3)], pedido_piedra(cfg["muro"], tono, "wall"),
                                    url, FUENTE, FUENTE, semilla, ref_args=qwen.REF_ARGS),
        "agua": lambda: qwen.editar(
            [trazado_ondas(semilla + 6)],
            f"Turn this grey ripple pattern into a top-down pixel art texture of {liq['texto']}. Keep the same "
            "ripple layout. Chunky pixel art, no objects, filling the whole image.",
            url, FUENTE, FUENTE, semilla, ref_args=qwen.REF_ARGS),
        "musgo": lambda: qwen.texto_a_imagen(
            f"Top-down pixel art texture of {cfg['cobertura']}, filling the whole image edge to edge, flat "
            "orthographic view, no objects, no border, chunky pixels.", url, FUENTE, FUENTE, semilla + 5),
    }
    salida = {}
    for nombre, crear in recetas.items():
        archivo = carpeta / f"{nombre}.png"
        if archivo.exists():
            salida[nombre] = np.asarray(Image.open(archivo).convert("RGB"), dtype=np.uint8)
            continue
        t = time.perf_counter()
        ia = crear()
        ia.save(carpeta / "ia" / f"{nombre}.png")
        if nombre == "agua":
            salida[nombre] = superficie_liquido(ia, liq["rampa"], liq["cortes"])
        else:
            salida[nombre] = a_superficie(ia, paleta_rgb, pal_lab, 10 if nombre == "musgo" else COLORES,
                                          ajuste=cfg.get("ajuste", {}).get(nombre))
        Image.fromarray(salida[nombre]).save(archivo)
        log.info("Superficie «%s» de %s en %.0f s", nombre, ambiente, time.perf_counter() - t)
    return salida


# --- plano del mapa ------------------------------------------------------------------

def plano_mazmorra(ancho: int, alto: int, semilla: int = 0, salas: int = 10) -> tuple[np.ndarray, list]:
    """Salas rectangulares unidas por pasillos de 3 baldosas. Devuelve (suelo[alto, ancho] bool, salas).

    Entre dos salas siempre quedan ≥ 5 baldosas de roca (dos de muro por lado), y el borde del mapa
    es roca. Todas las salas quedan conectadas (cada una se une a la anterior más cercana)."""
    r = np.random.default_rng(semilla)
    lista: list[tuple[int, int, int, int]] = []
    for _ in range(salas * 80):
        if len(lista) >= salas:
            break
        w, h = int(r.integers(8, 15)), int(r.integers(6, 11))
        x, y = int(r.integers(4, ancho - w - 3)), int(r.integers(4, alto - h - 3))
        if all(x > a + aw + 4 or a > x + w + 4 or y > b + bh + 4 or b > y + h + 4 for a, b, aw, bh in lista):
            lista.append((x, y, w, h))
    suelo = np.zeros((alto, ancho), bool)
    for x, y, w, h in lista:
        suelo[y:y + h, x:x + w] = True

    def centro(s):
        return s[0] + s[2] // 2, s[1] + s[3] // 2

    def pasillo(a, b):
        (x1, y1), (x2, y2) = centro(a), centro(b)
        if r.random() < 0.5:  # primero en horizontal o primero en vertical
            suelo[y1 - 1:y1 + 2, min(x1, x2) - 1:max(x1, x2) + 2] = True
            suelo[min(y1, y2) - 1:max(y1, y2) + 2, x2 - 1:x2 + 2] = True
        else:
            suelo[min(y1, y2) - 1:max(y1, y2) + 2, x1 - 1:x1 + 2] = True
            suelo[y2 - 1:y2 + 2, min(x1, x2) - 1:max(x1, x2) + 2] = True

    orden = sorted(lista, key=lambda s: centro(s)[0])
    for i in range(1, len(orden)):
        previas = orden[:i]
        cercana = min(previas, key=lambda s: abs(centro(s)[0] - centro(orden[i])[0])
                      + abs(centro(s)[1] - centro(orden[i])[1]))
        pasillo(cercana, orden[i])
    for _ in range(max(1, len(orden) // 4)):  # algún bucle más, para no ir siempre en árbol
        a, b = r.choice(len(orden), 2, replace=False)
        pasillo(orden[a], orden[b])
    suelo[:3] = suelo[-3:] = False
    suelo[:, :3] = suelo[:, -3:] = False
    return suelo, lista


# --- pintar --------------------------------------------------------------------------

def _ampliar(m: np.ndarray, lado: int = LADO) -> np.ndarray:
    return np.kron(m, np.ones((lado, lado), dtype=m.dtype))


def _dilatar(m: np.ndarray, n: int) -> np.ndarray:
    """Dilatación cuadrada de n celdas (Chebyshev) de una máscara bool."""
    salida = m.copy()
    for dy in range(-n, n + 1):
        for dx in range(-n, n + 1):
            salida |= np.roll(np.roll(m, dy, 0), dx, 1)
    return salida


def _ruido(alto: int, ancho: int, celda: int, semilla: int) -> np.ndarray:
    """Ruido suave 0..1 con rasgos de ~`celda` px."""
    r = np.random.default_rng(semilla)
    base = r.random((max(2, alto // celda + 2), max(2, ancho // celda + 2))).astype(np.float32)
    g = Image.fromarray((base * 255).astype(np.uint8)).resize((ancho, alto), Image.BICUBIC)
    return np.asarray(g, dtype=np.float32) / 255


def _desenfocar(m: np.ndarray, radio: float) -> np.ndarray:
    im = Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(radio)), dtype=np.float32) / 255


def _a_paleta_rapido(rgb: np.ndarray, paleta_rgb: np.ndarray, pal_lab: np.ndarray) -> np.ndarray:
    """Cada píxel a su color más cercano de la paleta (se resuelven solo los colores distintos)."""
    plano = rgb.reshape(-1, 3).astype(np.uint8)
    unicos, inverso = np.unique(plano, axis=0, return_inverse=True)
    cercano = mapas.color_mas_cercano(mapas.rgb_a_lab(unicos.astype(float)), pal_lab)
    return paleta_rgb[cercano][inverso.reshape(-1)].reshape(rgb.shape)


def pintar(suelo: np.ndarray, salas: list, sup: dict[str, np.ndarray], paleta_rgb: np.ndarray,
           pal_lab: np.ndarray, semilla: int = 0, charcos: tuple[int, float] = (3, 1.0),
           umbral: float = UMBRAL_MUSGO, con_agua: bool = False) -> Image.Image | tuple[Image.Image, np.ndarray]:
    """Pinta el mapa: suelo, muros alrededor, sombra y borde en el encuentro, moho y charcos de agua."""
    r = np.random.default_rng(semilla)
    alto_t, ancho_t = suelo.shape
    h, w = alto_t * LADO, ancho_t * LADO
    yy, xx = np.mgrid[0:h, 0:w]
    por = lambda s: s[yy % s.shape[0], xx % s.shape[1]].astype(np.float32)  # noqa: E731  (cada superficie con su periodo)

    piso = _ampliar(suelo)
    pared = _ampliar(_dilatar(suelo, 2) & ~suelo)
    img = np.zeros((h, w, 3), np.float32)
    img[:] = (6, 6, 9)  # roca sin pintar, más allá de los muros
    img = np.where(pared[..., None], por(sup["muro"]), img)
    img = np.where(piso[..., None], por(sup["suelo"]), img)

    # moho: manchas grandes de ruido (más hacia las paredes) con borde roto
    cerca = _desenfocar(_ampliar(_dilatar(suelo, 1) & ~_dilatar(suelo, 0) | _dilatar(~suelo, 1) & suelo) * 1.0, 18)
    densidad = 0.55 * _ruido(h, w, 150, semilla + 1) + 0.30 * _ruido(h, w, 40, semilla + 2) + 0.28 * cerca
    musgo = piso & (densidad + 0.12 * (_ruido(h, w, 5, semilla + 3) - 0.5) > umbral)
    img = np.where(musgo[..., None], por(sup["musgo"]), img)

    # charcos de agua en las salas más grandes: borde de piedra oscuro y orilla sombreada
    agua = np.zeros((h, w), bool)
    grandes = [s for s in salas if s[2] * s[3] >= 56]
    r.shuffle(grandes)
    ruido_orilla = _ruido(h, w, 70, semilla + 4) - 0.5  # contorno suave: charco redondeado, no moco
    cuantos, escala = charcos
    for x, y, sw, sh in grandes[:cuantos]:
        for _ in range(int(r.integers(1, 3))):
            cx = (x + 2.5 + r.random() * (sw - 5)) * LADO
            cy = (y + 2.5 + r.random() * (sh - 5)) * LADO
            rx, ry = r.uniform(1.6, 3.2) * LADO * escala, r.uniform(1.2, 2.4) * LADO * escala
            agua |= ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 + 0.5 * ruido_orilla < 1
    agua &= piso
    anillo = _desenfocar(agua * 1.0, 2.6)
    labio = piso & ~agua & (anillo > 0.1)  # borde de piedra mojada, oscuro
    img = np.where(agua[..., None], por(sup["agua"]), img)
    img = np.where(labio[..., None], img * 0.5, img)
    sombra_orilla = agua & (anillo < 0.82)  # el agua pierde luz junto al borde...
    img = np.where(sombra_orilla[..., None], img * 0.78, img)
    linea_agua = agua & (anillo < 0.6)  # ...salvo una línea fina de reflejo en la orilla
    brillo = _desenfocar(np.roll(np.roll(agua * 1.0, 2, 0), 2, 1), 2.6) < 0.6  # solo en la orilla de arriba/izquierda
    img = np.where((linea_agua & brillo)[..., None], np.minimum(255, img * 1.5 + 20), img)

    # sombra de los muros sobre el suelo (cae hacia abajo y a la derecha) y borde oscuro del encuentro
    sombra = _desenfocar(np.roll(np.roll(pared * 1.0, 7, 0), 4, 1), 5)
    img = np.where(piso[..., None], img * (1 - 0.58 * sombra[..., None]), img)
    pared_im = Image.fromarray(pared.astype(np.uint8) * 255)
    piso_im = Image.fromarray(piso.astype(np.uint8) * 255)
    borde_suelo = (np.asarray(pared_im.filter(ImageFilter.MaxFilter(5))) > 0) & piso
    borde_muro = (np.asarray(piso_im.filter(ImageFilter.MaxFilter(5))) > 0) & pared
    img = np.where((borde_suelo | borde_muro)[..., None], img * 0.4, img)
    # la roca sin pintar queda negra pero con un degradado en el pie del muro
    salida = Image.fromarray(_a_paleta_rapido(np.clip(img, 0, 255).astype(np.uint8), paleta_rgb, pal_lab))
    return (salida, agua) if con_agua else salida


# --- exportar ------------------------------------------------------------------------

def tmx(imagen: str, colision: str, ancho: int, alto: int, solido: np.ndarray, lado: int = LADO,
        objetos: str = "") -> str:
    """Mapa de Tiled: la imagen del mapa partida en baldosas (capa «mapa») y una capa de colisiones
    (1 = muro o roca) que usa un tileset de una sola baldosa."""
    n = ancho * alto
    mapa = ",\n".join(",".join(str(y * ancho + x + 1) for x in range(ancho)) for y in range(alto))
    col = ",\n".join(",".join(str(n + 1 if solido[y, x] else 0) for x in range(ancho)) for y in range(alto))
    medidas = f'tilewidth="{lado}" tileheight="{lado}"'
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<map version="1.10" orientation="orthogonal" renderorder="right-down" width="{ancho}" '
        f'height="{alto}" {medidas} infinite="0" nextlayerid="3" nextobjectid="1">',
        f' <tileset firstgid="1" name="mapa" {medidas} tilecount="{n}" columns="{ancho}">',
        f'  <image source="{imagen}" width="{ancho * lado}" height="{alto * lado}"/>',
        " </tileset>",
        f' <tileset firstgid="{n + 1}" name="colision" {medidas} tilecount="1" columns="1">',
        f'  <image source="{colision}" width="{lado}" height="{lado}"/>',
        " </tileset>",
        f' <layer id="1" name="mapa" width="{ancho}" height="{alto}">',
        '  <data encoding="csv">', mapa, "  </data>", " </layer>",
        f' <layer id="2" name="colision" width="{ancho}" height="{alto}" opacity="0.35">',
        '  <data encoding="csv">', col, "  </data>", " </layer>" + objetos, "</map>", "",
    ])


def forjar_mapa_grande(ambiente: str, proyecto: dict, carpeta_preset: Path, carpeta_salidas: Path, *,
                       url: str = qwen.URL_POR_DEFECTO, ancho: int = 64, alto: int = 48, semilla: int = 0,
                       salas: int = 10, superficies: Path | None = None, nombre: str | None = None,
                       decorar: bool | Path = True, rehacer_superficies: bool = False) -> dict:
    """Genera un mapa grande del ambiente en `carpeta_salidas/<nombre>/` y devuelve su resumen.

    `rehacer_superficies`: borra y vuelve a pedir a Qwen las superficies del ambiente (~4 min).
    `superficies`: carpeta donde se guardan/reutilizan las superficies del ambiente (por defecto,
    `assets/mapas/grande/<ambiente>/superficies`). `decorar`: coloca los decorados redibujados (True = la
    biblioteca por defecto si existe; o la carpeta de otra biblioteca; False = sin decorados)."""
    from motor.forja import carpeta_libre

    if ambiente not in AMBIENTES:
        raise ValueError(f"Ambiente sin definir: {ambiente!r} ({', '.join(AMBIENTES)})")
    if not (24 <= ancho <= 160 and 24 <= alto <= 160):
        raise ValueError("El mapa debe medir entre 24 y 160 baldosas por lado")
    t0 = time.perf_counter()
    estilo = json.loads((Path(carpeta_preset) / "estilo.json").read_text())
    paleta_rgb, pal_lab = mapas.paleta_lab(estilo["paleta"])
    superficies = Path(superficies) if superficies else CARPETA_SUPERFICIES / ambiente / "superficies"
    if rehacer_superficies:
        for n in SUPERFICIES:
            (superficies / f"{n}.png").unlink(missing_ok=True)
    sup = generar_superficies(ambiente, url, paleta_rgb, pal_lab, superficies, semilla=0)
    suelo, lista = plano_mazmorra(ancho, alto, semilla, salas)
    cfg = AMBIENTES[ambiente]
    paleta_mapa, lab_mapa = mapas.paleta_lab([f"#{r:02x}{g:02x}{b:02x}" for r, g, b in
                                              [*paleta_rgb.tolist(), *cfg["liquido"]["rampa"]]])
    imagen, agua = pintar(suelo, lista, sup, paleta_mapa, lab_mapa, semilla, tuple(cfg["charcos"]), cfg["umbral"],
                          con_agua=True)

    carpeta = carpeta_libre(Path(carpeta_salidas), nombre or f"{datetime.now():%Y-%m-%d_%H%M%S}_{ambiente}-grande")
    imagen.save(carpeta / "mapa.png")
    Image.new("RGBA", (LADO, LADO), (220, 40, 40, 255)).save(carpeta / "colision.png")
    solido = ~suelo
    objetos_xml, colocados = "", []
    biblioteca = (decorar if isinstance(decorar, Path) else None) or None
    try:  # los decorados son opcionales: sin `motor/decorar_mapa.py` solo se genera el suelo
        from motor import decorar_mapa
    except ImportError:
        decorar = False
    if decorar is not False:
        carpeta_bib = biblioteca or decorar_mapa.BIBLIOTECA
        if (carpeta_bib / "decorados.json").exists():
            bib = decorar_mapa.Biblioteca(carpeta_bib, ambiente)
            decorado, colocados = decorar_mapa.colocar(suelo, lista, ambiente, imagen, semilla, agua, bib)
            decorado.save(carpeta / "mapa_decorado.png")
            decorar_mapa.guardar_lista(carpeta, colocados)
            objetos_xml = decorar_mapa.objetos_tmx(colocados)
    (carpeta / "mapa.tmx").write_text(tmx("mapa.png", "colision.png", ancho, alto, solido, objetos=objetos_xml))
    (carpeta / "plano.json").write_text(json.dumps({
        "baldosa": LADO, "ancho": ancho, "alto": alto, "semilla": semilla,
        "salas": [{"x": x, "y": y, "ancho": w, "alto": h} for x, y, w, h in lista],
        "suelo": ["".join("." if v else "#" for v in fila) for fila in suelo]}, indent=1))
    archivos = {"mapa_prueba": "mapa_decorado.png" if colocados else "mapa.png", "mapa": "mapa.png", "tmx": "mapa.tmx",
                "plano": "plano.json", "colision": "colision.png"}
    if colocados:
        archivos.update(mapa_decorado="mapa_decorado.png", decorados="decorados_colocados.json")
    info = {"fecha": datetime.now().isoformat(timespec="seconds"), "ambiente": ambiente, "tipo": "grande",
            "perspectiva": "cenital", "baldosa": LADO, "piso": None, "materiales": {"ambiente": NOMBRES.get(ambiente, ambiente)},
            "archivos": archivos,
            "proyecto": proyecto.get("id"), "baldosas": [ancho, alto], "pixeles": list(imagen.size),
            "semilla": semilla, "salas": len(lista), "decorados": len(colocados), "superficies": str(superficies),
            "segundos": round(time.perf_counter() - t0, 1), "carpeta": carpeta.name}
    (carpeta / "info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n")
    return info


def main() -> None:
    from motor import proyectos

    ap = argparse.ArgumentParser(description="Mapa grande de mazmorra con superficies de Qwen-Image.")
    ap.add_argument("ambiente", choices=sorted(AMBIENTES))
    ap.add_argument("-p", "--proyecto", default="mazmorra-oscura")
    ap.add_argument("--ancho", type=int, default=64, help="baldosas de ancho")
    ap.add_argument("--alto", type=int, default=48, help="baldosas de alto")
    ap.add_argument("--semilla", type=int, default=0)
    ap.add_argument("--salas", type=int, default=10)
    ap.add_argument("-o", "--salida", type=Path, default=RAIZ / "salidas" / "mapas_grandes")
    ap.add_argument("--url", default=None, help="servidor Qwen (por defecto, el de Ajustes)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from motor import config

    pr = proyectos.cargar(args.proyecto)
    info = forjar_mapa_grande(args.ambiente, pr, RAIZ / "presets" / pr["preset"], args.salida,
                              url=args.url or config.qwen_url(), ancho=args.ancho, alto=args.alto,
                              semilla=args.semilla, salas=args.salas)
    print(json.dumps(info, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
