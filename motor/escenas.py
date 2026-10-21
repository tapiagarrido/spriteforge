"""Salas de mapa completas con Qwen-Image: catacumbas, cavernas, criptas…

El taller de mapas normal construye el terreno con baldosas que se repiten (suelos sin costuras
y transiciones). Eso da un suelo uniforme: sin paredes, charcos ni moho colocados donde toca.
Qwen-Image, en cambio, dibuja muy bien una **sala entera** vista desde arriba (muros de ladrillo,
losas de piedra superpuestas, moho entre ellas, charcos de agua estancada, huesos…), así que aquí se
le pide la sala completa y se adapta al juego:

    descripción (es) -> Qwen (vista cenital) -> a la resolución del juego (baldosa × baldosas)
    -> paleta del estilo del proyecto -> PNG + mapa de Tiled (.tmx) editable

El resultado es **una sala única** (no un tileset que se repite): sirve de fondo/suelo de una sala
y Tiled permite añadirle capas de colisiones y objetos sobre la rejilla. Misma paleta que los
personajes del proyecto, así que no se mezclan estilos.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from motor import mapas

log = logging.getLogger(__name__)

# forma -> (baldosas de ancho, de alto, tamaño que se le pide a Qwen)
FORMAS = {
    "cuadrada": (16, 16, (1024, 1024)),
    "ancha": (24, 16, (1216, 832)),
    "grande": (24, 24, (1024, 1024)),
}
COLORES = 32
MEDIANA_LUZ = 0.20  # luminosidad (L*/100) mínima de la mediana de la sala: oscura pero legible
REALCE_CROMA = 1.3
DETALLE = {"definido": 0.0, "apagado": 0.5}  # `mapas.suavizar`: «apagado» deja destacar a los personajes

PLANTILLA = ("Top-down 2D pixel art game map seen from directly above (orthographic, no perspective), "
             "16-bit dungeon crawler style. {descripcion}. Clearly defined details, dark outlines between "
             "stones, muted colors, evenly lit so every detail is visible, not too dark. "
             "No characters, no text, no interface.")


def prompt_sala(descripcion_en: str) -> str:
    return PLANTILLA.format(descripcion=descripcion_en.strip().rstrip("."))


def ajustar(imagen: Image.Image, ancho: int, alto: int) -> Image.Image:
    """Escala la imagen para cubrir `ancho`×`alto` (promedio de píxeles) y recorta el sobrante."""
    f = max(ancho / imagen.width, alto / imagen.height)
    grande = imagen.resize((max(ancho, round(imagen.width * f)), max(alto, round(imagen.height * f))),
                           Image.BOX if f < 1 else Image.LANCZOS)
    x, y = (grande.width - ancho) // 2, (grande.height - alto) // 2
    return grande.crop((x, y, x + ancho, y + alto))


def realzar(rgb: np.ndarray, mediana: float = MEDIANA_LUZ, croma: float = REALCE_CROMA) -> np.ndarray:
    """Aclara una sala oscura y avivar sus colores antes de pasarla a la paleta.

    Medido: Qwen dibuja las catacumbas con una luminosidad media de ~9/100 (L*); al buscar el color
    más cercano de una paleta llena de oscuros, el verde del agua y del moho caía en el casi negro y
    la sala se quedaba en 10 colores. Se aplica un gamma a la luminosidad hasta que su mediana sea
    `mediana` (0..1; nunca se oscurece) y se refuerza el color (a*, b*) por `croma`."""
    lab = mapas.rgb_a_lab(np.asarray(rgb, dtype=float))
    luz = np.clip(lab[..., 0], 0, 100) / 100
    actual = float(np.median(luz))
    if 0 < actual < mediana:
        luz = luz ** (np.log(mediana) / np.log(max(actual, 1e-3)))
    lab[..., 0] = luz * 100
    lab[..., 1:] *= croma
    return mapas.lab_a_rgb(lab)


def a_paleta(imagen: Image.Image, paleta_rgb: np.ndarray, pal_lab: np.ndarray, colores: int = COLORES,
             nivel: float = 0.0, mediana: float = MEDIANA_LUZ, croma: float = REALCE_CROMA,
             luz: float = 1.0) -> Image.Image:
    """La imagen reducida a la paleta del proyecto (con `colores` como máximo). `mediana` y `croma` regulan
    el realce previo (ver `realzar`); `luz` multiplica la luminosidad después (separa un muro del suelo)."""
    rgb = realzar(np.array(imagen.convert("RGB"), dtype=float), mediana, croma)
    if luz != 1.0:
        lab = mapas.rgb_a_lab(rgb)
        lab[..., 0] = np.clip(lab[..., 0] * luz, 0, 100)
        rgb = mapas.lab_a_rgb(lab)
    if nivel > 0:
        rgb = mapas.suavizar(rgb, nivel)
        paleta_rgb, pal_lab = mapas.paleta_suelo(paleta_rgb, nivel)
    return Image.fromarray(mapas.cuantizar(rgb, paleta_rgb, pal_lab, colores).astype(np.uint8))


def tmx(imagen: str, ancho: int, alto: int, lado: int) -> str:
    """Mapa de Tiled de `ancho`×`alto` baldosas cuyo tileset es la propia imagen de la sala."""
    filas = [",".join(str(y * ancho + x + 1) for x in range(ancho)) for y in range(alto)]
    medidas = f'tilewidth="{lado}" tileheight="{lado}"'
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<map version="1.10" orientation="orthogonal" renderorder="right-down" width="{ancho}" '
        f'height="{alto}" {medidas} infinite="0" nextlayerid="2" nextobjectid="1">',
        f' <tileset firstgid="1" name="sala" {medidas} tilecount="{ancho * alto}" columns="{ancho}">',
        f'  <image source="{imagen}" width="{ancho * lado}" height="{alto * lado}"/>',
        " </tileset>",
        f' <layer id="1" name="sala" width="{ancho}" height="{alto}">',
        '  <data encoding="csv">',
        ",\n".join(filas),
        "  </data>",
        " </layer>",
        "</map>",
        "",
    ])


def forjar_sala(proyecto: dict, carpeta_preset: Path, carpeta_salidas: Path, descripcion: str, generador, *,
                forma: str = "cuadrada", variantes: int = 2, semilla: int = 0, traducir: bool = True,
                detalle: str = "definido", destino: Path | None = None) -> dict:
    """Genera `variantes` salas y las guarda en `salidas/mapas/<fecha>_<proyecto>-sala…/`
    (o en `destino`, una carpeta nueva con el nombre que se quiera).

    `generador(prompt, ancho, alto, semilla) -> Image` es la IA (Qwen; sustituible en tests).
    Devuelve el resumen (`info.json`) en el mismo formato que `mapas.forjar_mapa`.
    """
    from motor.forja import carpeta_libre

    if forma not in FORMAS:
        raise ValueError(f"Forma desconocida: {forma!r} ({', '.join(FORMAS)})")
    if detalle not in DETALLE:
        raise ValueError(f"Detalle desconocido: {detalle!r} ({', '.join(DETALLE)})")
    descripcion = (descripcion or "").strip()
    if not descripcion:
        raise ValueError("Describe la sala (p. ej. «catacumbas con paredes, losas, moho y charcos»)")
    variantes = max(1, min(int(variantes), 4))
    t0 = time.perf_counter()
    lado = int(proyecto.get("baldosa", 32))
    bx, by, (gw, gh) = FORMAS[forma]
    estilo = json.loads((Path(carpeta_preset) / "estilo.json").read_text())
    paleta_rgb, pal_lab = mapas.paleta_lab(estilo["paleta"])
    if traducir:
        from motor.traducir import traducir as traducir_texto

        descripcion_en = traducir_texto(descripcion).rstrip(".")
    else:
        descripcion_en = descripcion
    prompt = prompt_sala(descripcion_en)

    fecha = datetime.now()
    if destino is not None:
        carpeta = carpeta_libre(Path(destino).parent, Path(destino).name)
    else:
        carpeta = carpeta_libre(Path(carpeta_salidas) / mapas.CARPETA_MAPAS_REL,
                                f"{fecha:%Y-%m-%d_%H%M%S}_{mapas._slug(proyecto.get('nombre', 'p'))}-sala")
    (carpeta / "ia").mkdir()
    archivos, salas = {}, []
    for i in range(1, variantes + 1):
        ia = generador(prompt, gw, gh, semilla + i - 1)
        ia.save(carpeta / "ia" / f"sala_{i}.png")
        sala = a_paleta(ajustar(ia, bx * lado, by * lado), paleta_rgb, pal_lab, COLORES, DETALLE[detalle])
        sala.save(carpeta / f"sala_{i}.png")
        mapas.ampliar(sala, 2).save(carpeta / f"sala_{i}_x2.png")
        (carpeta / f"sala_{i}.tmx").write_text(tmx(f"sala_{i}.png", bx, by, lado))
        archivos.update({f"sala_{i}": f"sala_{i}.png", f"sala_{i}_x2": f"sala_{i}_x2.png",
                         f"tmx_{i}": f"sala_{i}.tmx"})
        salas.append(i)
    archivos.update(mapa_prueba="sala_1.png", mapa_prueba_x2="sala_1_x2.png", tmx="sala_1.tmx")
    info = {
        "fecha": fecha.isoformat(timespec="seconds"), "proyecto": proyecto.get("id"), "tipo": "sala",
        "piso": None, "perspectiva": "cenital", "baldosa": lado, "materiales": {"sala": descripcion},
        "forma": forma, "detalle": detalle, "baldosas_sala": [bx, by], "variantes": variantes,
        "salas": salas, "semilla": semilla, "baldosas": bx * by, "colores": COLORES, "prompt": prompt,
        "archivos": archivos, "segundos": round(time.perf_counter() - t0, 1),
    }
    (carpeta / "info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n")
    info["carpeta"] = carpeta.name
    return info
