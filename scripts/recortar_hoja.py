"""Corta una hoja de sprites en archivos individuales (uno por celda no vacía).

Uso:
    python scripts/recortar_hoja.py hoja.png carpeta_salida --celda 64 64 [--prefijo sprite]

Las celdas totalmente transparentes se omiten. Los archivos se nombran
<prefijo>_<fila>_<columna>.png para poder localizarlos en la hoja original.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("hoja", type=Path)
    ap.add_argument("salida", type=Path)
    ap.add_argument("--celda", type=int, nargs=2, metavar=("ANCHO", "ALTO"), required=True)
    ap.add_argument("--prefijo", default="sprite")
    args = ap.parse_args()

    hoja = Image.open(args.hoja).convert("RGBA")
    ancho, alto = args.celda
    args.salida.mkdir(parents=True, exist_ok=True)
    n = 0
    for fila in range(hoja.height // alto):
        for col in range(hoja.width // ancho):
            celda = hoja.crop((col * ancho, fila * alto, (col + 1) * ancho, (fila + 1) * alto))
            alfa = np.array(celda)[..., 3]
            if not (alfa > 0).any():
                continue
            ys, xs = np.nonzero(alfa)
            celda.save(args.salida / f"{args.prefijo}_{fila}_{col}.png")
            ancho_fig, alto_fig = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
            print(f"{args.prefijo}_{fila}_{col}.png  figura {ancho_fig}x{alto_fig}")
            n += 1
    print(f"{n} sprites")


if __name__ == "__main__":
    main()
