"""Fotogramas clave con IA para las animaciones (p. ej. la pose de ataque).

La animación procedural (`motor.animar`) deforma el sprite, pero no puede abrir
más la boca ni levantar las garras. Para las poses clave se redibuja con IA:

    1. SDXL dibuja la pose con el prompt original + la acción ("jaws wide open,
       claws extended…"), IP-Adapter *completo* con la imagen de la variante elegida
       como referencia de identidad y ControlNet sobre un boceto: el de la pose si
       el usuario la dibuja o, si no, **el boceto original del personaje** con poca
       fuerza y solo al principio (conserva el cuerpo y deja mover la postura).
    2. Se generan varias candidatas para elegir (la pose inventada varía).
    3. Se recorta (segmentador) y se pixeliza **solo con los colores del sprite
       elegido** y **a su misma escala**, para que encaje con el resto de fotogramas.

Requiere la GPU y los modelos locales (como la generación).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from motor import generar as gen
from motor import segmentar as segmentador
from motor.color import rgb_a_hex, transferir_color
from motor.pixelizar import Opciones, mascara_figura, pixelizar

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Accion:
    prompt: str
    descripcion: str


ACCIONES = {
    "ataque": Accion("attacking, lunging forward aggressively, jaws wide open, claws extended forward",
                     "Embestida con mandíbulas y garras"),
    "zarpazo": Accion("claw swipe attack, one clawed arm raised high above the head, aggressive",
                      "Zarpazo con el brazo en alto"),
    "rugido": Accion("roaring, head thrown back, mouth wide open, intimidating display",
                     "Rugido intimidante"),
}


@dataclass
class Base:
    """Lo necesario de la variante elegida para redibujar poses."""

    carpeta: Path
    indice: int
    info: dict
    ia: Image.Image
    mascara: np.ndarray
    sprite: Image.Image
    preset: Path
    escala: float
    colores: list[str]
    boceto_original: Path | None = None  # guía del cuerpo (None en las vistas de dirección)


def cargar_base(carpeta: Path, indice: int, carpeta_presets: Path, segmentar: bool = True,
                vista: str | None = None) -> Base:
    """La variante (o, con `vista`, su vista de dirección: abajo/arriba/lado) como base."""
    usar_segmentador = segmentar
    carpeta = Path(carpeta)
    info = json.loads((carpeta / "info.json").read_text())
    v = next(x for x in info["variantes"] if x["indice"] == indice)
    boceto_original = carpeta / "boceto_limpio.png"
    if vista:
        from motor import direcciones

        d = direcciones.carpeta_de(carpeta, indice)
        if not (d / f"{vista}.png").exists():
            raise ValueError(f"Aún no hay vista «{vista}» de esta variante")
        ia = Image.open(d / f"ia_{vista}.png").convert("RGB")
        sprite = Image.open(d / f"{vista}.png").convert("RGBA")
        boceto_original = None  # el boceto es de otra vista: no sirve de guía
    else:
        ia = Image.open(carpeta / v["ia"]).convert("RGB")
        final = carpeta / f"final_{indice:02d}.png"  # si se retocó en el editor, manda el retoque
        sprite = Image.open(final if final.exists() else carpeta / v["sprite"]).convert("RGBA")
    if usar_segmentador and segmentador.disponible():
        mascara = segmentador.mascara(ia)
    else:
        mascara = mascara_figura(np.array(ia.convert("RGBA")))
    alto_ia = np.ptp(np.nonzero(mascara)[0]) + 1
    alfa = np.array(sprite)[..., 3] > 0
    alto_sprite = np.ptp(np.nonzero(alfa)[0]) + 1
    colores = sorted({rgb_a_hex(c) for c in np.array(sprite)[alfa][:, :3]})
    return Base(carpeta, indice, info, ia, mascara, sprite, Path(carpeta_presets) / info["preset"],
                alto_sprite / alto_ia, colores,
                boceto_original if boceto_original and boceto_original.exists() else None)


def _tuberia_img2img(gestor: gen.GestorModelos):
    from diffusers import StableDiffusionXLControlNetImg2ImgPipeline

    tuberia = gestor.obtener()
    if getattr(gestor, "_img2img_de", None) is not tuberia:
        # Mismos componentes, sin copiar: `from_pipe` vuelve a llamar a `.to()` y en la
        # práctica duplicaba VRAM hasta agotarla.
        gestor._img2img = StableDiffusionXLControlNetImg2ImgPipeline(**tuberia.components)
        gestor._img2img.set_progress_bar_config(disable=True)
        gestor._img2img_de = tuberia
    return tuberia, gestor._img2img


def semilla_de_accion(semilla: int, texto_accion: str) -> int:
    """Semilla propia de cada acción (determinista): con la misma semilla y la misma
    guía, el texto de la acción apenas cambiaba la imagen y el zarpazo, la embestida
    y el rugido salían con la misma pose."""
    import zlib

    return semilla + zlib.crc32(texto_accion.encode()) % 100_003


def guia_de_pose(base: Base, boceto_pose: Image.Image | None = None,
                 fuerza_identidad: float | None = None) -> tuple[Image.Image, float, float, float]:
    """(imagen de control, fuerza de ControlNet, fin de ControlNet, identidad).

    - Con `boceto_pose` (dibujada por el usuario) la pose la marca ese boceto.
    - Si no, el boceto original del personaje con fuerza 0.45 durante el primer 35 %
      de los pasos: fija el cuerpo (cuántas patas, si es un gusano o una bestia
      agazapada) y deja que la postura cambie. Medido con criaturas no humanoides:
      sin ninguna guía, la IA las convertía en un humanoide genérico (el texto de un
      monstruo sin descripción es solo la plantilla del tipo) y la animación
      "colapsaba"; guiando con el contorno del propio sprite o partiendo de su imagen,
      la pose apenas cambiaba.
    - Sin boceto original (resultados antiguos): sin guía, como antes.
    """
    from motor.boceto import limpiar_boceto

    ancho, alto = base.ia.size
    if boceto_pose is not None:
        control = limpiar_boceto(boceto_pose, ancho, alto).convert("RGB")
        return control, 0.75, 0.8, 0.55 if fuerza_identidad is None else fuerza_identidad
    original = base.boceto_original
    if original is not None and original.exists():
        control = Image.open(original).convert("RGB")
        if control.size != (ancho, alto):
            control = control.resize((ancho, alto), Image.NEAREST)
        return control, 0.45, 0.35, 0.6 if fuerza_identidad is None else fuerza_identidad
    # Medido: 0.35 da poses variadas pero el personaje deriva; 0.7 lo conserva mejor
    # pero repite más la postura original. 0.5 es el compromiso.
    return Image.new("RGB", (ancho, alto)), 0.0, 0.01, 0.5 if fuerza_identidad is None else fuerza_identidad


def candidatos_clave(gestor: gen.GestorModelos, base: Base, accion: str, variantes: int = 4,
                     semilla: int = 0, boceto_pose: Image.Image | None = None, texto: str | None = None,
                     fuerza_identidad: float | None = None, guia: tuple | None = None,
                     quitar_vistas: bool = False) -> list[tuple[Image.Image, Image.Image]]:
    """Varias poses candidatas para una acción: [(imagen de IA, sprite pixelizado)].

    La IA no parte de la imagen (fuerza 1.0: con la imagen base como punto de partida
    conserva la identidad pero no cambia la pose). El cuerpo lo fija ControlNet sobre
    un boceto y la identidad, la referencia (IP-Adapter), el prompt, la transferencia
    de color y la paleta del sprite (ver `guia_de_pose`).
    """
    import torch

    acc = ACCIONES.get(accion)
    if acc is None and not texto:
        raise ValueError(f"Acción desconocida: {accion!r}")
    texto_accion = (texto or acc.prompt).strip()
    ancho, alto = base.ia.size
    guia = guia or guia_de_pose(base, boceto_pose, fuerza_identidad)
    control, fuerza_control, fin_control, identidad = guia
    partes = [p.strip() for p in base.info["prompt"].split(",") if p.strip()]
    if quitar_vistas:  # al pedir una vista concreta, fuera "three-quarter view" y similares
        partes = [p for p in partes if "view" not in p.lower()]
    prompt = gen.unir_con_limite(partes[:2] + [texto_accion], partes[2:])
    inicial = Image.new("RGB", (ancho, alto), "white")
    with gestor._lock:
        tuberia, img2img = _tuberia_img2img(gestor)
        tuberia.set_adapters(["pixel_art"], adapter_weights=[base.info.get("fuerza_pixel_art", 0.8)])
        tuberia.set_ip_adapter_scale(identidad)  # completo: identidad, no solo estilo
        disp = "cuda" if torch.cuda.is_available() else "cpu"
        imagenes = img2img(
            prompt=prompt, negative_prompt=base.info["prompt_negativo"],
            image=inicial, control_image=control, strength=1.0,
            controlnet_conditioning_scale=fuerza_control, control_guidance_end=fin_control,
            ip_adapter_image=base.ia, num_inference_steps=30, guidance_scale=6.0,
            width=ancho, height=alto, num_images_per_prompt=variantes,
            generator=[torch.Generator(disp).manual_seed(semilla_de_accion(semilla, texto_accion) + i)
                       for i in range(variantes)],
        ).images
    return [(im, pixelizar_como_base(base, im)) for im in imagenes]


def clave_qwen(base: Base, accion: str, url: str, semilla: int = 0, boceto_pose: Image.Image | None = None,
               texto: str | None = None) -> tuple[Image.Image, Image.Image]:
    """(imagen de la IA, sprite): el personaje redibujado en la pose pedida por Qwen-Image.

    A diferencia de la IA local (ControlNet sobre un boceto), Qwen parte de la propia imagen del
    personaje, así que conserva su diseño y colores, y cambia la pose según el texto: sirve
    cualquier pose ("saltando con la espada en alto"), no solo las de `ACCIONES`. Con
    `boceto_pose` (tu dibujo del lienzo) se da como guía de la pose."""
    from PIL import ImageOps

    from motor import qwen

    acc = ACCIONES.get(accion)
    if acc is None and not texto:
        raise ValueError(f"Acción desconocida: {accion!r}")
    pose = (texto or acc.prompt).strip()
    entradas = [base.ia]
    inicio = ""
    if boceto_pose is not None:
        entradas.append(ImageOps.invert(boceto_pose.convert("L")).convert("RGB") if boceto_pose.mode == "L"
                        else boceto_pose.convert("RGB"))
        inicio = "Image 1 is a pixel art character and Image 2 is a rough sketch of the desired pose. "
    instruccion = (f"{inicio}Redraw this same pixel art character - identical design, anatomy, colors, "
                   f"equipment and pixel art style - in this pose: {pose}."
                   + (" Follow the pose of Image 2." if boceto_pose is not None else "")
                   + " Full body, centered, on a plain white background, no shadow on the ground, no text, "
                   "no other objects. Do not add any weapon, shield, armor or accessory the character "
                   "does not already have.")
    ancho, alto = base.ia.size
    ia = qwen.editar(entradas, instruccion, url, ancho, alto, semilla_de_accion(semilla, pose),
                     ref_args=qwen.REF_ARGS)
    return ia, pixelizar_como_base(base, ia)


# Poses de paso de un ciclo de caminar (A y B son zancadas opuestas); texto en inglés para Qwen, por vista.
PASOS = {
    "derecha": (
        "taking a walking step in side view, facing right: the front leg stepped far forward "
        "and the back leg stretched behind, both feet on the ground, body leaning slightly "
        "forward, cloth, cape or fur swaying behind it",
        "taking the opposite walking step in side view, facing right: the leg that was behind "
        "is now stepping far forward and the other leg is stretched behind, both feet on the "
        "ground, body leaning slightly forward, cloth, cape or fur swaying"),
    "abajo": (
        "walking toward the viewer: its left foot stepping forward toward the camera and its "
        "right foot behind, the body dipping a little toward the stepping side, still seen "
        "from the front",
        "walking toward the viewer, the opposite step: its right foot stepping forward toward "
        "the camera and its left foot behind, the body dipping a little toward the stepping "
        "side, still seen from the front"),
    "arriba": (
        "walking away from the viewer: its left foot stepping forward away from the camera "
        "and its right foot behind, the body dipping a little toward the stepping side, still "
        "seen from behind",
        "walking away from the viewer, the opposite step: its right foot stepping forward and "
        "its left foot behind, the body dipping a little toward the stepping side, still seen "
        "from behind"),
}


def pasos_qwen(base: Base, url: str, semilla: int = 0, direccion: str | None = None
               ) -> tuple[tuple[Image.Image, Image.Image], tuple[Image.Image, Image.Image]]:
    """Las dos poses de paso de un ciclo de caminar, dibujadas por Qwen-Image a partir de la vista `base`:
    ((imagen IA, sprite) del paso A, (imagen IA, sprite) del paso B).
    `direccion`: abajo, arriba o derecha (None = lado)."""
    texto_a, texto_b = PASOS["derecha" if direccion in (None, "derecha", "izquierda") else direccion]
    a = clave_qwen(base, "ataque", url, semilla, None, texto_a)
    b = clave_qwen(base, "ataque", url, semilla + 1, None, texto_b)
    return a, b


def pixelizar_como_base(base: Base, imagen: Image.Image) -> Image.Image:
    """Pixeliza con los tonos, solo los colores y la misma escala del sprite base.

    Antes se transfiere la distribución de color de la figura base a la pose
    (la IA tiende a desviar los tonos: un marrón oscuro sale rojizo).
    """
    ancho, alto = base.sprite.size
    op = Opciones.desde_preset(base.preset, paleta=base.colores, max_colores=len(base.colores),
                               escala=base.escala, ancho=ancho, alto=alto, fondo_esperado=None)
    rgb = np.array(imagen.convert("RGB"))
    mascara = segmentador.mascara(imagen) if segmentador.disponible() else mascara_figura(
        np.array(imagen.convert("RGBA")))
    rgb = transferir_color(rgb, mascara, np.array(base.ia), base.mascara)
    return pixelizar(Image.fromarray(rgb), op, mascara=mascara).imagen
