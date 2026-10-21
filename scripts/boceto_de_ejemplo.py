"""Genera bocetos de ejemplo "a mano alzada" para probar la cadena sin dibujar.

Produce dos versiones del mismo personaje (guerrero con espada y escudo):
- digital: trazo negro sobre transparente, como el lienzo de la app.
- foto: el mismo dibujo sobre "papel" con sombra, textura y lápiz gris.

Uso:
    python scripts/boceto_de_ejemplo.py ejemplos/bocetos
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def trazo(d: ImageDraw.ImageDraw, puntos, rng, ancho=6, color=(20, 20, 20, 255)):
    """Polilínea con temblor de mano."""
    temblor = [(x + rng.normal(0, 2), y + rng.normal(0, 2)) for x, y in puntos]
    d.line(temblor, fill=color, width=ancho, joint="curve")


def elipse(cx, cy, rx, ry, n=40):
    return [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n))
            for i in range(n + 1)]


def guerrero(lado=600, semilla=3) -> Image.Image:
    rng = np.random.default_rng(semilla)
    img = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    t = lambda pts, **k: trazo(d, pts, rng, **k)  # noqa: E731
    t(elipse(300, 170, 70, 75))                           # cabeza grande
    t([(235, 150), (300, 95), (365, 150)])                # casco
    t([(300, 95), (310, 60), (335, 45)])                  # penacho
    t([(275, 170), (285, 170)])                           # ojo
    t([(320, 170), (330, 170)])                           # ojo
    t([(250, 240), (240, 390), (360, 390), (350, 240), (250, 240)])  # torso
    t([(240, 330), (360, 330)])                           # cinturón
    t([(265, 390), (260, 470)])                           # pierna
    t([(335, 390), (340, 470)])                           # pierna
    t([(240, 470), (275, 470)])                           # pie
    t([(325, 470), (360, 470)])                           # pie
    t([(355, 270), (410, 320)])                           # brazo derecho
    t([(410, 320), (450, 180)], ancho=8)                  # espada
    t([(395, 300), (430, 330)])                           # guarda
    t([(245, 270), (195, 320)])                           # brazo izquierdo
    t(elipse(190, 330, 45, 60))                           # escudo
    return img


def como_foto(dibujo: Image.Image, semilla=4) -> Image.Image:
    rng = np.random.default_rng(semilla)
    lado = dibujo.width
    y, x = np.mgrid[0:lado, 0:lado] / lado
    papel = 235 - 45 * x * y - 15 * y + rng.normal(0, 4, (lado, lado))   # sombra + textura
    papel = np.stack([papel, papel * 0.98, papel * 0.93], -1)
    alfa = np.array(dibujo)[..., 3:4] / 255 * 0.75                       # lápiz, no tinta
    lapiz = np.array([70, 70, 75])
    foto = papel * (1 - alfa) + lapiz * alfa
    img = Image.fromarray(np.clip(foto, 0, 255).astype(np.uint8), "RGB")
    return img.filter(ImageFilter.GaussianBlur(1.2)).rotate(3, fillcolor=(200, 196, 185))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta", type=Path)
    args = ap.parse_args()
    args.carpeta.mkdir(parents=True, exist_ok=True)
    dibujo = guerrero()
    dibujo.save(args.carpeta / "guerrero_digital.png")
    como_foto(dibujo).save(args.carpeta / "guerrero_foto.jpg", quality=88)
    print(args.carpeta)


if __name__ == "__main__":
    main()
