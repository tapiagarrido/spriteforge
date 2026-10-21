"""Hoja comparativa de una o varias carpetas de `salidas/`.

Por carpeta, una fila: boceto limpio | imágenes de la IA | sprites ampliados.

Uso:
    python scripts/hoja_resultados.py docs/img/resultados.png salidas/<carpeta1> salidas/<carpeta2>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from motor.pixelizar import ampliar
from motor.presets import fuente

ANCHO_IA, ALTO_IA = 117, 208
ALTO_SPRITE_VISTA = 150  # los sprites se amplían (entero) hasta ~este alto
FONDO = (232, 228, 220)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("salida", type=Path)
    ap.add_argument("carpetas", type=Path, nargs="+")
    args = ap.parse_args()

    filas = []
    for c in args.carpetas:
        info = json.loads((c / "info.json").read_text())
        filas.append((c, info))
    n = max(len(i["variantes"]) for _, i in filas)
    celda = max(ANCHO_IA, ALTO_SPRITE_VISTA) + 16
    alto_fila = ALTO_IA + ALTO_SPRITE_VISTA + 50
    hoja = Image.new("RGB", (16 + celda * (n + 1), 10 + alto_fila * len(filas)), FONDO)
    d = ImageDraw.Draw(hoja)
    for r, (c, info) in enumerate(filas):
        y = 10 + r * alto_fila
        d.text((16, y), info["prompt_usuario"], fill=(40, 36, 46), font=fuente(14))
        y += 24
        def encajar(im: Image.Image) -> Image.Image:
            f = min(ANCHO_IA / im.width, ALTO_IA / im.height)
            return im.convert("RGB").resize((round(im.width * f), round(im.height * f)))

        limpio = encajar(Image.open(c / "boceto_limpio.png"))
        hoja.paste(limpio, (16, y))
        for k, v in enumerate(info["variantes"]):
            x = 16 + celda * (k + 1)
            hoja.paste(encajar(Image.open(c / v["ia"])), (x, y))
            sprite = Image.open(c / v["sprite"]).convert("RGBA")
            sprite = ampliar(sprite, max(1, ALTO_SPRITE_VISTA // max(sprite.size)))
            hoja.paste(sprite, (x + (ANCHO_IA - sprite.width) // 2, y + ALTO_IA + 8), sprite)
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    hoja.save(args.salida)
    print(args.salida)


if __name__ == "__main__":
    main()
