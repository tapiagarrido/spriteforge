"""Banco de pruebas del pixelizador.

Toma una carpeta de sprites reales, simula para cada uno varias "salidas de IA"
con distinta dificultad (desenfoque, ruido, corrimiento de color, fondo) y mide
cuánto se parece el resultado pixelizado al sprite original.

Cada sprite se pixeliza con su propio lienzo y alto de figura, pero con la paleta,
el contorno y los colores del preset, así se evalúa el algoritmo y no el tamaño.

Uso:
    python scripts/banco_pruebas.py <carpeta_sprites> --preset presets/heroes-mazmorra \
        --patron '*_idle_anim_f0.png' --hoja docs/img/banco_pruebas.png
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from motor.pixelizar import Opciones, ampliar, coincidencia, pixelizar
from motor.presets import fuente

LADO = 512


@dataclass(frozen=True)
class Escenario:
    nombre: str
    desenfoque: float
    ruido: float
    corrimiento: float
    fondo: str | None  # None = fondo de generación del preset


ESCENARIOS = [
    Escenario("limpio", 0, 0, 0, None),
    Escenario("ia típica", 3, 6, 10, None),
    Escenario("ia mala", 6, 12, 20, None),
    Escenario("fondo claro", 3, 6, 10, "#ece8e0"),
]


def simular(sprite: Image.Image, esc: Escenario, fondo_preset: str, semilla: int) -> Image.Image:
    rng = np.random.default_rng(semilla)
    factor = int(LADO * 0.75 / max(sprite.size))
    grande = ampliar(sprite.convert("RGBA"), factor)
    lienzo = Image.new("RGBA", (LADO, LADO), esc.fondo or fondo_preset)
    desfase = rng.integers(-20, 21, 2)
    lienzo.alpha_composite(grande, ((LADO - grande.width) // 2 + int(desfase[0]),
                                    (LADO - grande.height) // 2 + int(desfase[1])))
    img = lienzo.convert("RGB")
    if esc.desenfoque:
        img = img.filter(ImageFilter.GaussianBlur(esc.desenfoque))
    a = np.array(img, dtype=float)
    a += rng.normal(0, esc.ruido or 1e-9, a.shape) + rng.uniform(-esc.corrimiento, esc.corrimiento + 1e-9, 3)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def opciones_para(sprite: Image.Image, base: Opciones) -> Opciones:
    alfa = np.array(sprite.convert("RGBA"))[..., 3] > 0
    ys = np.where(alfa.any(1))[0]
    return Opciones(**{**base.__dict__, "ancho": sprite.width, "alto": sprite.height,
                       "alto_figura": int(ys.max() - ys.min() + 1)})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta", type=Path)
    ap.add_argument("--preset", type=Path, required=True)
    ap.add_argument("--patron", default="*.png")
    ap.add_argument("--hoja", type=Path, help="PNG con la comparación visual")
    args = ap.parse_args()

    estilo = json.loads((args.preset / "estilo.json").read_text())
    base = Opciones.desde_preset(args.preset)
    archivos = sorted(args.carpeta.glob(args.patron))
    en_preset = set()
    for e in estilo["ejemplos"]:
        en_preset.add(Image.open(args.preset / e).tobytes())

    resultados: dict[str, list[float]] = {e.nombre: [] for e in ESCENARIOS}
    filas, tiempos = [], []
    for i, ruta in enumerate(archivos):
        sprite = Image.open(ruta).convert("RGBA")
        op = opciones_para(sprite, base)
        celdas = []
        for j, esc in enumerate(ESCENARIOS):
            entrada = simular(sprite, esc, estilo["fondo_generacion"], semilla=i * 10 + j)
            t = time.perf_counter()
            salida = pixelizar(entrada, op).imagen
            tiempos.append(time.perf_counter() - t)
            c = coincidencia(sprite, salida)
            resultados[esc.nombre].append(c)
            celdas.append((entrada, salida, c))
        nombre = ruta.stem.replace("_idle_anim_f0", "")
        marca = " (en preset)" if sprite.tobytes() in en_preset else ""
        filas.append((nombre + marca, sprite, celdas))
        print(f"{nombre + marca:28s} " + "  ".join(f"{c:5.0%}" for _, _, c in celdas))

    print("\n" + f"{'media':28s} " + "  ".join(f"{np.mean(v):5.0%}" for v in resultados.values()))
    print(f"{'peor':28s} " + "  ".join(f"{np.min(v):5.0%}" for v in resultados.values()))
    print(f"escenarios: {', '.join(e.nombre for e in ESCENARIOS)}")
    print(f"{len(tiempos)} pixelizados, {np.mean(tiempos) * 1000:.0f} ms de media")

    if args.hoja:
        dibujar_hoja(filas, resultados, args.hoja)
        print(args.hoja)


def dibujar_hoja(filas, resultados, salida: Path) -> None:
    esc_mostrar = [1, 2]  # "ia típica" e "ia mala": entrada + salida
    escala, alto_fila, col_nombre = 3, 120, 170
    ancho_celda = 110
    n_cols = 1 + 2 * len(esc_mostrar)
    ancho = col_nombre + n_cols * ancho_celda + 20
    alto = 60 + len(filas) * alto_fila + 40
    hoja = Image.new("RGB", (ancho, alto), (40, 36, 46))
    d = ImageDraw.Draw(hoja)
    f, fp = fuente(13), fuente(11)
    titulos = ["original"]
    for j in esc_mostrar:
        titulos += [f"entrada: {ESCENARIOS[j].nombre}", "pixelizado"]
    for k, t in enumerate(titulos):
        d.text((col_nombre + k * ancho_celda, 20), t, fill=(230, 230, 230), font=fp)
    for r, (nombre, original, celdas) in enumerate(filas):
        y = 50 + r * alto_fila
        d.text((10, y + 40), nombre, fill=(230, 230, 230), font=f)
        imgs = [(ampliar(original, escala), None)]
        for j in esc_mostrar:
            entrada, sal, c = celdas[j]
            imgs += [(entrada.resize((100, 100)), None), (ampliar(sal, escala), c)]
        for k, (im, c) in enumerate(imgs):
            im = im.convert("RGBA")
            x = col_nombre + k * ancho_celda
            hoja.paste(im, (x, y + (110 - im.height) // 2), im)
            if c is not None:
                d.text((x + 60, y + 95), f"{c:.0%}", fill=(230, 230, 230), font=fp)
    medias = "   ".join(f"{k}: {np.mean(v):.0%}" for k, v in resultados.items())
    d.text((10, alto - 30), f"Media — {medias}", fill=(230, 230, 230), font=f)
    salida.parent.mkdir(parents=True, exist_ok=True)
    hoja.save(salida)


if __name__ == "__main__":
    main()
