"""Rediseñar una criatura conservando su base: otro color o elementos nuevos.

Cada rediseño se guarda como **una variante nueva** del mismo resultado (`ia_XX.png`,
`sprite_XX.png`…, con `origen` en `info.json`), así que hereda todo lo demás: se puede
retocar, girar (direcciones), animar y listar en la galería como cualquier variante.

Dos caminos, medidos con una criatura de prueba:

    color      recolorear los píxeles (tono, saturación, brillo; todos los colores o solo
               los que elijas). Sin IA: instantáneo, exacto y el resto del sprite no cambia.
    elementos  Gemini o Qwen editan la imagen de la variante ("la misma criatura, con un casco y
               un cuerno"). La IA local no sirve: con la imagen de partida y su contorno
               conserva la criatura pero NO aplica el cambio, ni con la fuerza máxima
               (el IP-Adapter y el contorno pesan más que el texto).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from motor.color import color_mas_cercano, hex_a_rgb, rgb_a_hex, rgb_a_lab


@dataclass
class Ajuste:
    tono: float = 0.0  # grados (-180..180)
    saturacion: float = 1.0  # multiplicador (0 = grises)
    brillo: float = 1.0  # multiplicador
    colores: list[str] | None = None  # colores del sprite a cambiar (None = todos)
    tenir: float | None = None  # tono destino (grados): todos toman ese tono y conservan claros/oscuros
    sat_min: float = 0.0  # al teñir, saturación mínima (0..1) para que un gris-marrón no quede gris

    def validar(self) -> Ajuste:
        if not -180 <= self.tono <= 180:
            raise ValueError("El tono debe estar entre -180 y 180 grados")
        if not 0 <= self.saturacion <= 2.5:
            raise ValueError("La saturación debe estar entre 0 y 2.5")
        if not 0.3 <= self.brillo <= 2:
            raise ValueError("El brillo debe estar entre 0.3 y 2")
        return self

    @property
    def es_identidad(self) -> bool:
        return self.tono == 0 and self.saturacion == 1 and self.brillo == 1 and self.tenir is None


def _transformar(rgb: np.ndarray, a: Ajuste) -> np.ndarray:
    """Tono/saturación/brillo sobre un array HxWx3 uint8 (vía HSV de Pillow)."""
    hsv = np.array(Image.fromarray(rgb, "RGB").convert("HSV"), dtype=float)
    if a.tenir is not None:
        hsv[..., 0] = a.tenir % 360 * 255 / 360
        vivo = hsv[..., 2] > 0.2 * 255  # el contorno casi negro no se tiñe
        hsv[..., 1] = np.clip(np.where(vivo, np.maximum(hsv[..., 1] * a.saturacion, a.sat_min * 255),
                                       hsv[..., 1] * a.saturacion), 0, 255)
    else:
        hsv[..., 0] = (hsv[..., 0] + a.tono * 255 / 360) % 256
        hsv[..., 1] = np.clip(hsv[..., 1] * a.saturacion, 0, 255)
    hsv[..., 2] = np.clip(hsv[..., 2] * a.brillo, 0, 255)
    return np.array(Image.fromarray(hsv.round().astype(np.uint8), "HSV").convert("RGB"))


def recolorear(imagen: Image.Image, ajuste: Ajuste, paleta_ref: list[str],
               mascara: np.ndarray | None = None) -> Image.Image:
    """Recolorea `imagen` (un sprite RGBA o la imagen de la IA RGB).

    Cada píxel se asigna al color más parecido de `paleta_ref` (los colores del sprite) y
    solo cambian los asignados a `ajuste.colores` (o todos). En un sprite la asignación es
    exacta; en la imagen de la IA, aproximada. `mascara` (True = figura) deja el fondo
    intacto; en un sprite, la figura es lo opaco.
    """
    ajuste.validar()
    rgba = np.array(imagen.convert("RGBA"))
    rgb = rgba[..., :3]
    figura = rgba[..., 3] > 0 if mascara is None and imagen.mode == "RGBA" else (
        mascara if mascara is not None else np.ones(rgb.shape[:2], bool))
    if ajuste.es_identidad or not figura.any():
        return imagen.copy()
    ref = np.array([hex_a_rgb(h) for h in paleta_ref], dtype=np.uint8)
    cambiar = np.ones(rgb.shape[:2], bool)
    if ajuste.colores is not None:
        elegidos = {tuple(hex_a_rgb(h)) for h in ajuste.colores}
        indices = color_mas_cercano(rgb_a_lab(rgb.astype(float)), rgb_a_lab(ref.astype(float)))
        marcados = np.array([tuple(c) in elegidos for c in ref])
        cambiar = marcados[indices]
    donde = figura & cambiar
    nuevo = rgb.copy()
    nuevo[donde] = _transformar(rgb, ajuste)[donde]
    salida = np.dstack([nuevo, rgba[..., 3]]) if imagen.mode == "RGBA" else nuevo
    return Image.fromarray(salida, "RGBA" if imagen.mode == "RGBA" else "RGB")


def ajuste_hacia(sprite: Image.Image, objetivo: str, colores: list[str] | None = None,
                 intensidad: float = 1.0, brillo: float = 1.0, modo: str = "tenir") -> Ajuste:
    """Ajuste que lleva los `colores` (o todos) del sprite hacia el color `objetivo`.

    `modo="tenir"`: todos toman el tono del objetivo (monocromo en sus claros y oscuros;
    suele leerse mejor como «ahora en azul»). `modo="girar"`: se gira el tono y se conserva
    la variedad de tonos del sprite (marrones y morados siguen distintos).

    El tono sale de la diferencia entre el tono dominante de lo elegido (media circular
    ponderada por cuántos píxeles y cuánto color tienen: los grises no cuentan) y el del
    objetivo. La saturación se refuerza sola si el objetivo es más vivo que el pelaje
    (un pelaje gris-marrón se vería apenas azul con solo girar el tono); `intensidad` la
    multiplica."""
    import colorsys

    rgba = np.array(sprite.convert("RGBA"))
    px = rgba[rgba[..., 3] > 0][:, :3]
    if colores is not None:
        elegidos = np.array([hex_a_rgb(h) for h in colores], dtype=np.uint8)
        px = px[(px[:, None, :] == elegidos[None]).all(-1).any(1)] if len(elegidos) else px[:0]
    if not len(px):
        raise ValueError("Elige al menos un color del sprite")
    hsv = np.array([colorsys.rgb_to_hsv(*(c / 255)) for c in px])
    peso = hsv[:, 1] * hsv[:, 2]  # un píxel gris u oscuro apenas tiene tono
    if peso.sum() < 1e-6:
        actual, sat = 0.0, 0.0  # todo gris: se partirá del tono del objetivo
    else:
        ang = hsv[:, 0] * 2 * np.pi
        actual = (np.arctan2((np.sin(ang) * peso).sum(), (np.cos(ang) * peso).sum()) / (2 * np.pi)) % 1
        sat = float((hsv[:, 1] * peso).sum() / peso.sum())
    h, s, _ = colorsys.rgb_to_hsv(*(np.array(hex_a_rgb(objetivo)) / 255))
    giro = ((h - actual) * 360 + 180) % 360 - 180
    auto = float(np.clip(s / max(sat, 0.05), 1.0, 2.5)) if sat < s else 1.0
    satur = float(np.clip(auto * intensidad, 0, 2.5))
    if modo == "tenir":
        return Ajuste(saturacion=satur, brillo=brillo, colores=colores, tenir=round(h * 360, 1),
                      sat_min=round(0.5 * s * min(intensidad, 1.0), 3)).validar()
    return Ajuste(tono=float(round(giro, 1)), saturacion=satur, brillo=brillo, colores=colores).validar()


def ajustar_a_paleta(sprite: Image.Image, paleta: list[str]) -> Image.Image:
    """Cada píxel opaco, al color más cercano de la paleta del estilo (coherencia del juego)."""
    rgba = np.array(sprite.convert("RGBA"))
    opaco = rgba[..., 3] > 0
    if not opaco.any():
        return sprite.copy()
    ref = np.array([hex_a_rgb(h) for h in paleta], dtype=np.uint8)
    cercano = color_mas_cercano(rgb_a_lab(rgba[opaco][:, :3].astype(float)), rgb_a_lab(ref.astype(float)))
    rgba[opaco, :3] = ref[cercano]
    return Image.fromarray(rgba, "RGBA")


def colores_de(sprite: Image.Image) -> list[str]:
    a = np.array(sprite.convert("RGBA"))
    opaco = a[..., 3] > 0
    return sorted({rgb_a_hex(c) for c in a[opaco][:, :3]})


def instruccion(cambio_en_ingles: str) -> str:
    return ("The image is a pixel art game character. Redraw exactly the same character - same pose, same "
            "body, same proportions, same pixel art style, same plain white background - but with these "
            f"changes: {cambio_en_ingles}. Keep everything else identical.")


def pixelizar_rediseno(base, imagen: Image.Image) -> Image.Image:
    """Pixeliza una imagen rediseñada a la escala y el lienzo de la variante, con la paleta
    completa del estilo (no solo los colores de la base: el rediseño puede traer otros)."""
    from motor import segmentar as segmentador
    from motor.pixelizar import Opciones, mascara_figura, pixelizar

    ancho, alto = base.sprite.size
    op = Opciones.desde_preset(base.preset, max_colores=len(base.colores) + 3, escala=base.escala,
                               ancho=ancho, alto=alto, fondo_esperado=None)
    mascara = (segmentador.mascara(imagen) if segmentador.disponible()
               else mascara_figura(np.array(imagen.convert("RGBA"))))
    return pixelizar(imagen, op, mascara=mascara).imagen


def rediseno_gemini(base, cambio_en_ingles: str, api_key: str, modelo: str,
                    semilla: int = 0) -> tuple[Image.Image, Image.Image]:
    """(imagen de la IA, sprite): la variante con los cambios pedidos, editada por Gemini."""
    from motor import gemini

    ancho, alto = base.ia.size
    ia = gemini.editar([base.ia], instruccion(cambio_en_ingles), api_key, modelo, ancho, alto, semilla)
    return ia, pixelizar_rediseno(base, ia)


def rediseno_qwen(base, cambio_en_ingles: str, url: str,
                  semilla: int = 0) -> tuple[Image.Image, Image.Image]:
    """(imagen de la IA, sprite): la variante con los cambios pedidos, editada por Qwen-Image."""
    from motor import qwen

    ancho, alto = base.ia.size
    ia = qwen.editar([base.ia], instruccion(cambio_en_ingles), url, ancho, alto, semilla)
    return ia, pixelizar_rediseno(base, ia)


def anadir_variante(carpeta: Path, sprite: Image.Image, ia: Image.Image, origen: dict,
                    nombre: str | None = None, semilla: int = 0) -> dict:
    """Guarda `sprite` + `ia` como una variante nueva de `carpeta` y devuelve su entrada."""
    from motor.forja import AMPLIACION_VISTA
    from motor.pixelizar import ampliar

    carpeta = Path(carpeta)
    archivo = carpeta / "info.json"
    info = json.loads(archivo.read_text())
    n = max(v["indice"] for v in info["variantes"]) + 1
    ia_nombre, sp_nombre, vista_nombre = (f"ia_{n:02d}.png", f"sprite_{n:02d}.png",
                                          f"sprite_{n:02d}_x{AMPLIACION_VISTA}.png")
    ia.convert("RGB").save(carpeta / ia_nombre)
    sprite.convert("RGBA").save(carpeta / sp_nombre)
    ampliar(sprite.convert("RGBA"), AMPLIACION_VISTA).save(carpeta / vista_nombre)
    entrada = {"indice": n, "semilla": int(semilla), "ia": ia_nombre, "sprite": sp_nombre,
               "sprite_vista": vista_nombre, "colores": colores_de(sprite), "origen": origen}
    if nombre:
        entrada["nombre"] = nombre.strip()[:40]
    info["variantes"].append(entrada)
    archivo.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n")
    return entrada
