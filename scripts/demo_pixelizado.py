"""Demostración del pixelizador sin GPU.

Simula una "salida de IA" a partir de los ejemplos del preset: los amplía a
512 px, los pone sobre el fondo de generación del preset, los desenfoca, les
mete ruido y les corre los colores. Después los pixeliza y compara con el original.

Uso:
    python scripts/demo_pixelizado.py presets/heroes-mazmorra docs/img/demo_pixelizado.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from motor.pixelizar import Opciones, ampliar, coincidencia, pixelizar
from motor.presets import fuente

LADO_IA = 512


def simular_salida_ia(sprite: Image.Image, fondo: str = "#ece8e0", semilla: int = 0) -> Image.Image:
    """Sprite -> imagen grande, borrosa y con ruido, como la que devuelve un difusor."""
    rng = np.random.default_rng(semilla)
    factor = int(LADO_IA * 0.8 / max(sprite.size))
    grande = ampliar(sprite, factor)
    lienzo = Image.new("RGBA", (LADO_IA, LADO_IA), fondo)
    lienzo.alpha_composite(grande, ((LADO_IA - grande.width) // 2 + 7, (LADO_IA - grande.height) // 2 - 5))
    img = lienzo.convert("RGB").filter(ImageFilter.GaussianBlur(3))
    a = np.array(img, dtype=float)
    a += rng.normal(0, 6, a.shape) + rng.uniform(-10, 10, 3)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("preset", type=Path)
    ap.add_argument("salida", type=Path)
    args = ap.parse_args()

    estilo = json.loads((args.preset / "estilo.json").read_text())
    op = Opciones.desde_preset(args.preset)
    filas = []
    for i, nombre in enumerate(estilo["ejemplos"]):
        original = Image.open(args.preset / nombre).convert("RGBA")
        ia = simular_salida_ia(original, estilo["fondo_generacion"], semilla=i)
        # Cada ejemplo con su alto real, para comparar con el original.
        caja = estilo["detalle_ejemplos"][i]["caja"]
        op.alto_figura = caja[3] - caja[1]
        res = pixelizar(ia, op)
        filas.append((nombre, original, ia, res.imagen, coincidencia(original, res.imagen)))
        print(f"{nombre}: {coincidencia(original, res.imagen):.0%} de píxeles iguales al original, "
              f"{len(res.colores)} colores")

    celda, alto_fila, f = 200, 200, fuente(14)
    hoja = Image.new("RGB", (celda * 3 + 40, alto_fila * len(filas) + 50), (40, 36, 46))
    d = ImageDraw.Draw(hoja)
    for k, titulo in enumerate(["original", "salida de IA (simulada)", "pixelizado"]):
        d.text((20 + k * celda, 14), titulo, fill=(230, 230, 230), font=f)
    for j, (_nombre, original, ia, px, coinc) in enumerate(filas):
        y = 44 + j * alto_fila
        for k, im in enumerate([ampliar(original, 6), ia.resize((180, 180)), ampliar(px, 6)]):
            im = im.convert("RGBA")
            hoja.paste(im, (20 + k * celda, y), im)
        d.text((20 + 2 * celda + 110, y + 150), f"{coinc:.0%}", fill=(230, 230, 230), font=f)
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    hoja.save(args.salida)
    print(args.salida)


if __name__ == "__main__":
    main()
