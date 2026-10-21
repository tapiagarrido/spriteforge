"""Análisis de presets de estilo.

Un preset es una carpeta con 2-5 sprites de ejemplo (PNG con transparencia).
`analizar` mide sus rasgos de estilo y escribe `estilo.json`, que luego usan
la generación (fuerzas, prompt) y el pixelizado (paleta, tamaño, contorno).

Uso:
    python -m motor.presets analizar presets/heroes-mazmorra \
        --paleta-de assets/.../frames --nombre "Héroes de mazmorra"
    python -m motor.presets vista presets/heroes-mazmorra
"""

from __future__ import annotations

import argparse
import colorsys
import json
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from motor.color import luminancia, rgb_a_lab
from motor.color import rgb_a_hex as a_hex

EXTENSIONES = {".png", ".gif", ".webp"}

# Valores iniciales de `generacion` (editables por preset). Ajustados con pruebas
# reales: "chibi / minimal detail" para que el personaje sobreviva a 16-20 px y un
# negativo agresivo contra fondos grises, degradados, suelos y marcos, que son lo
# que más dificulta separar la figura.
PROMPT_BASE = ("pixel art, tiny 16-bit dungeon rpg sprite, chibi, super deformed, huge head, "
               "very short body, very simple, minimal detail, flat colors, thick dark outline, "
               "full body, side view")
PROMPT_NEGATIVO = ("gray background, dark background, colored background, gradient background, "
                   "vignette, frame, border, card, panel, texture, pattern, shadow, ground, floor, "
                   "scenery, background objects, plants, tree, rock, building, arch, detailed, "
                   "realistic, soft shading, blurry, text, watermark, multiple characters")
FONDO_GENERACION = "#ffffff"
# Presets creados desde la app: prompt neutro (el estilo lo aportan los ejemplos y la
# paleta; lo propio de cada personaje, la plantilla del tipo y la descripción).
PROMPT_BASE_APP = ("pixel art, game sprite, full body, clean pixel shading, dark outline, "
                   "limited palette")
MAX_LADO_EJEMPLO = 512
ARCHIVO_ESTILO = "estilo.json"


def cargar_rgba(ruta: Path) -> np.ndarray:
    return np.array(Image.open(ruta).convert("RGBA"))


def ejemplos_de(carpeta: Path) -> list[Path]:
    return sorted(p for p in carpeta.iterdir()
                  if p.suffix.lower() in EXTENSIONES and p.stem.startswith("ejemplo"))


def escala_pixel(img: np.ndarray) -> int:
    """Tamaño del 'píxel lógico' si el sprite viene ampliado (p. ej. dibujado a x4)."""
    h, w = img.shape[:2]
    for s in range(8, 1, -1):
        if h % s or w % s:
            continue
        reducida = img[::s, ::s]
        if np.array_equal(np.repeat(np.repeat(reducida, s, 0), s, 1), img):
            return s
    return 1


def mascara_borde(opaco: np.ndarray) -> np.ndarray:
    """Píxeles opacos con al menos un vecino (4-conexo) transparente."""
    p = np.pad(opaco, 1)
    interior = p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return opaco & ~interior


def niveles_sombreado(colores: list[tuple[int, int, int]]) -> int:
    """Tonos por familia de color (agrupando por matiz), mediana entre familias."""
    familias: Counter = Counter()
    for r, g, b in colores:
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if v < 0.2:
            continue  # contorno / negros
        clave = "neutro" if s < 0.15 else int(h * 12) % 12
        familias[clave] += 1
    conteos = [n for n in familias.values() if n >= 2]
    return int(np.median(conteos)) if conteos else 1


def analizar_sprite(ruta: Path) -> dict:
    img = cargar_rgba(ruta)
    s = escala_pixel(img)
    img = img[::s, ::s]
    opaco = img[..., 3] > 0
    filas, cols = np.where(opaco)
    borde = mascara_borde(opaco)
    lum = luminancia(img.astype(float))

    colores = [tuple(int(v) for v in c) for c in {tuple(c) for c in img[opaco][:, :3]}]
    color_contorno = Counter(tuple(c) for c in img[borde][:, :3]).most_common(1)[0][0]
    # Contorno = borde claramente más oscuro que el relleno.
    relleno = opaco & ~borde
    tiene_contorno = bool(relleno.any() and lum[borde].mean() < 0.5 * lum[relleno].mean())
    # Grosor 2 si la segunda capa hacia dentro también es oscura.
    segunda = mascara_borde(relleno) if relleno.any() else relleno
    centro = relleno & ~segunda
    grosor = 1
    if tiene_contorno and segunda.any() and centro.any():
        grosor = 2 if lum[segunda].mean() < 0.5 * lum[centro].mean() else 1

    return {
        "archivo": ruta.name,
        "lienzo": [int(img.shape[1]), int(img.shape[0])],
        "caja": [int(cols.min()), int(filas.min()), int(cols.max()) + 1, int(filas.max()) + 1],
        "escala_pixel_origen": s,
        "colores": len(colores),
        "paleta": sorted(a_hex(c) for c in colores),
        "contorno": {"tiene": tiene_contorno, "grosor": grosor, "color": a_hex(color_contorno)},
        "niveles_sombreado": niveles_sombreado(colores),
    }


def contar_colores(rutas: list[Path]) -> Counter:
    """Píxeles opacos de cada color RGB en un conjunto de imágenes."""
    cuenta: Counter = Counter()
    for ruta in rutas:
        img = cargar_rgba(ruta)
        cuenta.update(tuple(int(v) for v in c) for c in img[img[..., 3] > 0][:, :3])
    return cuenta


def reducir_paleta(cuenta: Counter, maximo: int, iteraciones: int = 20) -> list[tuple[int, int, int]]:
    """Reduce una paleta a `maximo` colores representativos.

    k-medias en CIELAB ponderado por cuántos píxeles usan cada color, y de cada
    grupo se elige el color **real** más usado (no un promedio inventado): la
    paleta reducida solo contiene colores que el artista usó.
    """
    colores = list(cuenta)
    if len(colores) <= maximo:
        return colores
    rgb = np.array(colores, dtype=float)
    peso = np.array([cuenta[c] for c in colores], dtype=float)
    lab = rgb_a_lab(rgb)
    centros = lab[np.argsort(-peso)[:maximo]].copy()  # empezar por los más usados
    for _ in range(iteraciones):
        grupo = ((lab[:, None, :] - centros[None]) ** 2).sum(-1).argmin(1)
        for k in range(maximo):
            m = grupo == k
            if m.any():
                centros[k] = (lab[m] * peso[m, None]).sum(0) / peso[m].sum()
    grupo = ((lab[:, None, :] - centros[None]) ** 2).sum(-1).argmin(1)
    elegidos = []
    for k in range(maximo):
        idx = np.flatnonzero(grupo == k)
        if len(idx):
            elegidos.append(colores[idx[peso[idx].argmax()]])
    return elegidos


def ordenar_hex(colores) -> list[str]:
    return [a_hex(c) for c in sorted(colores, key=lambda c: (luminancia(np.array(c, float)), c))]


def paleta_de_carpeta(carpeta: Path, patron: str = "*.png", maximo: int | None = None) -> list[str]:
    """Colores opacos de los PNG de una carpeta (reducidos a `maximo` si se indica)."""
    cuenta = contar_colores(sorted(Path(carpeta).glob(patron)))
    colores = reducir_paleta(cuenta, maximo) if maximo else list(cuenta)
    return ordenar_hex(colores)


def analizar(carpeta: Path, nombre: str | None, paleta_de: Path | None,
             patron: str = "*.png", max_paleta: int | None = None) -> dict:
    archivos = ejemplos_de(carpeta)
    if not archivos:
        raise SystemExit(f"No hay archivos ejemplo*.png en {carpeta}")
    sprites = [analizar_sprite(a) for a in archivos]

    previo_ruta = carpeta / ARCHIVO_ESTILO
    previo = json.loads(previo_ruta.read_text()) if previo_ruta.exists() else {}

    lienzos = Counter(tuple(s["lienzo"]) for s in sprites)
    ancho, alto = lienzos.most_common(1)[0][0]
    paleta_ejemplos = sorted({c for s in sprites for c in s["paleta"]})
    n_colores = [s["colores"] for s in sprites]
    contornos = [s["contorno"] for s in sprites]

    estilo = {
        "nombre": nombre or previo.get("nombre") or carpeta.name,
        "fuente": previo.get("fuente", {}),
        "ejemplos": [s["archivo"] for s in sprites],
        "sprite": {"ancho": ancho, "alto": alto,
                   "alto_figura": max(s["caja"][3] - s["caja"][1] for s in sprites),
                   "fondo": "transparente", "anclaje": "abajo-centro"},
        "contorno": {
            "tiene": sum(c["tiene"] for c in contornos) > len(contornos) / 2,
            "grosor": Counter(c["grosor"] for c in contornos).most_common(1)[0][0],
            "color": Counter(c["color"] for c in contornos).most_common(1)[0][0],
        },
        "colores_por_sprite": {"min": min(n_colores), "max": max(n_colores),
                               "objetivo": int(round(np.mean(n_colores)))},
        "niveles_sombreado": int(np.median([s["niveles_sombreado"] for s in sprites])),
        "paleta": (paleta_de_carpeta(paleta_de, patron, max_paleta) if paleta_de
                   else ordenar_hex(reducir_paleta(contar_colores(archivos), max_paleta)) if max_paleta
                   else paleta_ejemplos),
        "paleta_ejemplos": paleta_ejemplos,
        # Fondo blanco: se pide en el prompt y se usa en la referencia de estilo.
        # (Nombrar un color en el prompt, p. ej. "blue background", tiñe al personaje.)
        "fondo_generacion": previo.get("fondo_generacion", FONDO_GENERACION),
        # Valores editables a mano: se conservan entre análisis.
        "generacion": previo.get("generacion", {
            "prompt_base": PROMPT_BASE,
            "prompt_negativo": PROMPT_NEGATIVO,
            "fuerza_estilo": 0.3,  # más alto copia contenido de los ejemplos (cascos, penachos…)
            "parecido_boceto": 0.5,  # 0 libre … 1 calcado (ver generar.controlnet_desde_parecido)
            "fuerza_pixel_art": 0.8,
            "resolucion_generacion": 768,
        }),
        "detalle_ejemplos": sprites,
    }
    previo_ruta.write_text(json.dumps(estilo, indent=2, ensure_ascii=False) + "\n")
    return estilo


def crear(carpeta_presets: Path, nombre: str, ejemplos: list[Image.Image],
          estilo: str = "", max_paleta: int | None = None) -> str:
    """Crea un preset a partir de 2-5 sprites de ejemplo (PNG con transparencia).

    Devuelve el id (nombre de carpeta). `estilo` es la descripción del estilo que
    verá la IA (por defecto, PROMPT_BASE_APP).
    """
    from motor.proyectos import a_id

    nombre = (nombre or "").strip()[:60]
    if not nombre:
        raise ValueError("El estilo necesita un nombre")
    if not 2 <= len(ejemplos) <= 5:
        raise ValueError("Hacen falta entre 2 y 5 sprites de ejemplo")
    for i, im in enumerate(ejemplos, 1):
        if max(im.size) > MAX_LADO_EJEMPLO:
            raise ValueError(f"El ejemplo {i} es demasiado grande (máx. {MAX_LADO_EJEMPLO} px)")
        alfa = np.array(im.convert("RGBA"))[..., 3]
        if alfa.min() > 0:
            raise ValueError(f"El ejemplo {i} no tiene fondo transparente: usa PNG con transparencia")
        if not alfa.any():
            raise ValueError(f"El ejemplo {i} está vacío")
    base = a_id(nombre)
    carpeta, n = Path(carpeta_presets) / base, 2
    while carpeta.exists():
        carpeta, n = Path(carpeta_presets) / f"{base}-{n}", n + 1
    carpeta.mkdir(parents=True)
    for i, im in enumerate(ejemplos, 1):
        im.convert("RGBA").save(carpeta / f"ejemplo{i}.png")
    previo = {"nombre": nombre, "fuente": {"origen": "creado en la app"},
              "generacion": {"prompt_base": (estilo or "").strip()[:400] or PROMPT_BASE_APP,
                             "prompt_negativo": PROMPT_NEGATIVO, "fuerza_estilo": 0.3,
                             "parecido_boceto": 0.5, "fuerza_pixel_art": 0.8,
                             "resolucion_generacion": 768}}
    (carpeta / ARCHIVO_ESTILO).write_text(json.dumps(previo, ensure_ascii=False))
    try:
        analizar(carpeta, nombre, None, max_paleta=max_paleta)
        vista_previa(carpeta)
    except Exception:
        import shutil

        shutil.rmtree(carpeta, ignore_errors=True)
        raise
    return carpeta.name


def actualizar(carpeta: Path, nombre: str | None = None, prompt_base: str | None = None,
               prompt_negativo: str | None = None) -> dict:
    """Cambia el nombre o los textos para la IA de un preset (lo medido no se toca)."""
    archivo = Path(carpeta) / ARCHIVO_ESTILO
    estilo = json.loads(archivo.read_text())
    if nombre is not None:
        if not nombre.strip():
            raise ValueError("El estilo necesita un nombre")
        estilo["nombre"] = nombre.strip()[:60]
    gen = estilo.setdefault("generacion", {})
    for campo, valor in (("prompt_base", prompt_base), ("prompt_negativo", prompt_negativo)):
        if valor is not None:
            if len(valor) > 600:
                raise ValueError(f"'{campo}' es demasiado largo (máx. 600 caracteres)")
            gen[campo] = valor.strip()
    if not gen.get("prompt_base"):
        raise ValueError("La descripción del estilo no puede quedar vacía")
    archivo.write_text(json.dumps(estilo, indent=2, ensure_ascii=False) + "\n")
    vista_previa(Path(carpeta))
    return estilo


def fuente(tam: int = 13):
    """Fuente con acentos; la de Pillow por defecto no los tiene."""
    for ruta in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf"):
        if Path(ruta).exists():
            return ImageFont.truetype(ruta, tam)
    return ImageFont.load_default(size=tam)


def vista_previa(carpeta: Path, escala: int = 8) -> Path:
    """PNG con los ejemplos ampliados y la paleta, para revisar el preset."""
    estilo = json.loads((carpeta / ARCHIVO_ESTILO).read_text())
    imgs = [Image.open(carpeta / a).convert("RGBA") for a in estilo["ejemplos"]]
    celda = max(i.width for i in imgs) * escala + 24
    alto_spr = max(i.height for i in imgs) * escala
    paleta = estilo["paleta"]
    por_fila, lado = 15, 28
    filas_pal = (len(paleta) + por_fila - 1) // por_fila
    ancho = max(celda * len(imgs), por_fila * lado, 560) + 32
    lienzo = Image.new("RGB", (ancho, alto_spr + 70 + filas_pal * lado), (40, 36, 46))
    d = ImageDraw.Draw(lienzo)
    for k, im in enumerate(imgs):
        grande = im.resize((im.width * escala, im.height * escala), Image.NEAREST)
        lienzo.paste(grande, (16 + k * celda, 16), grande)
    y = alto_spr + 30
    c = estilo["contorno"]
    d.text((16, y - 8), f"{estilo['nombre']}  ·  {estilo['sprite']['ancho']}x{estilo['sprite']['alto']}  ·  "
           f"contorno {'sí' if c['tiene'] else 'no'} {c['grosor']}px  ·  "
           f"{estilo['colores_por_sprite']['objetivo']} colores/sprite  ·  "
           f"paleta {len(paleta)}", fill=(230, 230, 230), font=fuente())
    for i, hx in enumerate(paleta):
        x0, y0 = 16 + (i % por_fila) * lado, y + 12 + (i // por_fila) * lado
        d.rectangle([x0, y0, x0 + lado - 3, y0 + lado - 3], fill=hx)
    salida = carpeta / "vista_previa.png"
    lienzo.save(salida)
    return salida


def main() -> None:
    ap = argparse.ArgumentParser(prog="motor.presets")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analizar", help="mide los ejemplos y escribe estilo.json")
    a.add_argument("carpeta", type=Path)
    a.add_argument("--nombre")
    a.add_argument("--paleta-de", type=Path, help="carpeta de PNG de donde sacar la paleta completa")
    a.add_argument("--patron", default="*.png", help="filtro de archivos para --paleta-de")
    a.add_argument("--max-paleta", type=int, help="reducir la paleta a N colores representativos")
    v = sub.add_parser("vista", help="genera vista_previa.png del preset")
    v.add_argument("carpeta", type=Path)
    args = ap.parse_args()

    if args.cmd == "analizar":
        e = analizar(args.carpeta, args.nombre, args.paleta_de, args.patron, args.max_paleta)
        print(f"{e['nombre']}: {e['sprite']['ancho']}x{e['sprite']['alto']}, "
              f"contorno={e['contorno']}, colores/sprite={e['colores_por_sprite']}, "
              f"sombreado={e['niveles_sombreado']}, paleta={len(e['paleta'])}")
    else:
        print(vista_previa(args.carpeta))


if __name__ == "__main__":
    main()
