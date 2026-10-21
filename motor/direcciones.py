"""Vistas de dirección de un personaje (exploración en vista cenital, como Pokémon).

De la variante elegida se obtienen tres vistas: `abajo` (de frente), `arriba` (de
espaldas) y `lado` (de perfil, mirando a la derecha). La izquierda es el espejo del
lado. Cada vista se puede obtener:

    ia        la IA la redibuja con la identidad de la variante (IP-Adapter completo,
              colores y escala del sprite). De frente y de espaldas sin guía de boceto:
              el boceto original es de otra vista y forzaría su silueta.
    dibujo    la IA sigue tu dibujo de esa vista (lo más fiable para criaturas raras)
    variante  la propia variante, si ya está en esa vista

Se guardan en `<resultado>/direcciones_XX/` (`<vista>.png`, `ia_<vista>.png`,
`<vista>.json`). Las animaciones de cada dirección (`caminar_abajo`…) se generan con
el sprite de su vista (ver `motor.animar.animaciones_direccion`).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

VISTAS = {
    "abajo": "front view, facing the viewer, full body, standing, top-down rpg overworld character",
    "arriba": "back view, seen from behind, facing away from the viewer, back of the head, full body, "
              "standing, top-down rpg overworld character",
    "lado": "side view, in profile facing right, full body, standing, rpg character",
}
# Dirección de animación que corresponde a cada vista (la izquierda sale del lado en espejo).
DIRECCION_DE_VISTA = {"abajo": "abajo", "arriba": "arriba", "lado": "derecha"}
VISTA_DE_DIRECCION = {"abajo": "abajo", "arriba": "arriba", "derecha": "lado", "izquierda": "lado"}
MODOS = ("ia", "dibujo", "variante")


def carpeta_de(carpeta: Path, indice: int) -> Path:
    return Path(carpeta) / f"direcciones_{int(indice):02d}"


def listar(carpeta: Path, indice: int) -> dict[str, dict]:
    """{vista: meta} de las vistas guardadas."""
    d = carpeta_de(carpeta, indice)
    salida = {}
    for vista in VISTAS:
        if (d / f"{vista}.png").exists():
            meta = d / f"{vista}.json"
            salida[vista] = json.loads(meta.read_text()) if meta.exists() else {"vista": vista}
    return salida


def mira_a_la_derecha(sprite: Image.Image, ia: Image.Image) -> tuple[Image.Image, Image.Image]:
    """La vista de lado se guarda mirando a la derecha (la izquierda es su espejo)."""
    from motor.animar import mirando_hacia

    alfa = np.array(sprite.convert("RGBA"))[..., 3] > 0
    if alfa.any() and mirando_hacia(alfa) == -1:
        return sprite.transpose(Image.FLIP_LEFT_RIGHT), ia.transpose(Image.FLIP_LEFT_RIGHT)
    return sprite, ia


def guardar(carpeta: Path, indice: int, vista: str, ia: Image.Image, sprite: Image.Image,
            extra: dict | None = None) -> dict:
    if vista not in VISTAS:
        raise ValueError(f"Vista desconocida: {vista!r} (abajo, arriba o lado)")
    if vista == "lado":
        sprite, ia = mira_a_la_derecha(sprite, ia)
    d = carpeta_de(carpeta, indice)
    d.mkdir(parents=True, exist_ok=True)
    sprite.save(d / f"{vista}.png")
    ia.convert("RGB").save(d / f"ia_{vista}.png")
    meta = {"vista": vista, "direccion": DIRECCION_DE_VISTA[vista], **(extra or {})}
    (d / f"{vista}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    return meta


def sprite_de(carpeta: Path, indice: int, direccion: str) -> Image.Image:
    """Sprite para animar en `direccion` (abajo, arriba, derecha o izquierda)."""
    vista = VISTA_DE_DIRECCION.get(direccion)
    if vista is None:
        raise ValueError(f"Dirección desconocida: {direccion!r}")
    archivo = carpeta_de(carpeta, indice) / f"{vista}.png"
    if not archivo.exists():
        raise ValueError(f"Primero genera la vista «{vista}» de esta variante")
    sprite = Image.open(archivo).convert("RGBA")
    return sprite.transpose(Image.FLIP_LEFT_RIGHT) if direccion == "izquierda" else sprite


def guia_de_vista(base, vista: str, boceto: Image.Image | None = None,
                  identidad: float | None = None) -> tuple[Image.Image, float, float, float]:
    """(control, fuerza, fin, identidad) para redibujar la variante en una vista.

    - Con tu dibujo de la vista: lo sigue (como la pose dibujada de un ataque).
    - De lado: el boceto original, suave (suele ser de perfil o de tres cuartos).
    - De frente o de espaldas: sin boceto; la forma la aportan la identidad (más alta
      que en las poses: 0.65) y la plantilla del tipo en el prompt.
    """
    from motor.boceto import limpiar_boceto

    ancho, alto = base.ia.size
    if boceto is not None:
        control = limpiar_boceto(boceto, ancho, alto).convert("RGB")
        return control, 0.75, 0.8, 0.55 if identidad is None else identidad
    if vista == "lado" and base.boceto_original is not None and base.boceto_original.exists():
        control = Image.open(base.boceto_original).convert("RGB").resize((ancho, alto), Image.NEAREST)
        return control, 0.35, 0.3, 0.6 if identidad is None else identidad
    return Image.new("RGB", (ancho, alto)), 0.0, 0.01, 0.65 if identidad is None else identidad


VISTAS_GEMINI = {
    "abajo": "seen straight from the front: symmetrical frontal view, head and body facing directly toward "
             "the viewer, both eyes visible, NOT a three-quarter view, standing",
    "arriba": "seen from behind (back view: we see its back and the back of its head, not its face), "
              "standing",
    "lado": "seen from the side, in profile, facing right, standing",
}


# Medido: sin esto, Qwen añade a los monstruos el equipo del ejemplo (espada, escudo).
SIN_EXTRAS = ("Keep exactly the same equipment: do not add any weapon, shield, armor or accessory that the "
              "character does not already have, and do not remove any it has.")
VISTAS_QWEN = {
    "abajo": "seen straight from the front, facing the viewer, standing",
    "arriba": "seen from behind (back view: we see its back and the back of its head, not its face), "
              "standing",
    # Medido: "rotate / strict side profile" deja la imagen igual; pedirlo como un paso de lado
    # hace que Qwen gire el cuerpo (el perfil estricto de un chibi de frente sigue costándole).
    "lado": "moving to the right (mid-stride if it has legs), seen from the side in profile, facing the "
            "right edge of the image",
}


def instruccion_qwen(vista: str, con_boceto: bool) -> str:
    texto = ("Redraw this same pixel art character - identical design, anatomy, colors, markings, "
             f"equipment and pixel art style - {VISTAS_QWEN[vista]}, full body, centered, on a plain "
             "white background. Only the character: no shadow on the ground, no text, no other objects. "
             + SIN_EXTRAS)
    if con_boceto:
        texto = ("Image 1 is a pixel art character and Image 2 is a rough sketch of the desired pose. "
                 + texto + " Follow the pose and facing direction of Image 2.")
    return texto


def instruccion_gemini(vista: str, con_boceto: bool) -> str:
    texto = ("The first image is a pixel art game character. Redraw exactly the same character - same "
             f"design, anatomy, colors, markings and pixel art style - {VISTAS_GEMINI[vista]}, full body, "
             "centered, on a plain white background. Only the character: no shadow on the ground, no text, "
             "no other objects.")
    if con_boceto:
        texto += " The second image is a rough sketch of the desired pose: follow its silhouette."
    return texto


def vista_editada(editar, base, vista: str, semilla: int = 0, boceto: Image.Image | None = None,
                  instruccion=None) -> tuple[Image.Image, Image.Image]:
    """La variante redibujada en `vista` por un editor de imágenes externo (bueno girando un
    personaje a partir de una imagen, donde SDXL suele devolver la misma postura).
    `editar(imagenes, instruccion, ancho, alto, semilla) -> imagen`. Se pixeliza como las
    poses: colores y escala de la variante."""
    from PIL import ImageOps

    from motor import animar_ia

    entradas = [base.ia]
    if boceto is not None:
        entradas.append(ImageOps.invert(boceto.convert("L")) if boceto.mode == "L" else boceto)
    ancho, alto = base.ia.size
    texto = (instruccion or instruccion_gemini)(vista, boceto is not None)
    ia = editar(entradas, texto, ancho, alto, semilla)
    return ia, animar_ia.pixelizar_como_base(base, ia)


def vista_gemini(base, vista: str, api_key: str, modelo: str, semilla: int = 0,
                 boceto: Image.Image | None = None) -> tuple[Image.Image, Image.Image]:
    """`vista_editada` con Gemini."""
    from motor import gemini

    return vista_editada(lambda im, txt, an, al, s: gemini.editar(im, txt, api_key, modelo, an, al, s),
                         base, vista, semilla, boceto)


def _perfil_qwen(base, boceto: Image.Image | None) -> tuple[list[Image.Image], str]:
    """Entradas y texto para el perfil con Qwen, que lo hace mal desde una sola imagen de frente
    (devuelve la misma). Medido: con el frente y la espalda a la vez dibuja un perfil real (según
    la semilla); sin espalda, el frente + una copia comprimida a lo ancho (pista de cuerpo girado)
    da al menos un paso de lado."""
    from PIL import ImageOps

    ancho, alto = base.ia.size
    guia = ""
    if boceto is not None:
        guia = " Follow the pose of the last image (a rough sketch)."
    espalda = carpeta_de(base.carpeta, base.indice) / "ia_arriba.png"
    if espalda.exists():
        entradas = [base.ia, Image.open(espalda).convert("RGB")]
        texto = ("Image 1 is a pixel art character seen from the front and Image 2 is the same character "
                 "seen from behind. Draw the same character in strict side profile, moving to the right: a "
                 "true side view, body turned 90 degrees, so only the features on the near side are visible. "
                 "Identical design, colors and pixel art style. " + SIN_EXTRAS
                 + " Plain white background, full "
                 "body, centered.")
    else:
        estrecha = base.ia.resize((int(ancho * 0.55), alto), Image.LANCZOS)
        comprimida = Image.new("RGB", (ancho, alto), "white")
        comprimida.paste(estrecha, ((ancho - estrecha.width) // 2, 0))
        entradas = [base.ia, comprimida]
        texto = ("Image 1 is a pixel art character. Image 2 is the same character squeezed horizontally to "
                 "hint at a body turned sideways. Redraw the character as a true side-view sprite facing "
                 "right, mid-motion, with natural side proportions, same design, colors and pixel art "
                 "style. " + SIN_EXTRAS + " Plain white background, full body, centered.")
    if boceto is not None:
        entradas.append(ImageOps.invert(boceto.convert("L")).convert("RGB") if boceto.mode == "L" else boceto)
        texto += guia
    return entradas, texto


def vista_qwen(base, vista: str, url: str, semilla: int = 0,
               boceto: Image.Image | None = None) -> tuple[Image.Image, Image.Image]:
    """`vista_editada` con Qwen-Image (el perfil usa además la espalda si existe, ver `_perfil_qwen`)."""
    from motor import animar_ia, qwen

    if vista == "lado":
        entradas, texto = _perfil_qwen(base, boceto)
        ancho, alto = base.ia.size
        ia = qwen.editar(entradas, texto, url, ancho, alto, semilla, ref_args=qwen.REF_ARGS)
        return ia, animar_ia.pixelizar_como_base(base, ia)

    def editar(im, txt, an, al, s):
        return qwen.editar(im, txt, url, an, al, s, ref_args=qwen.REF_ARGS)

    return vista_editada(editar, base, vista, semilla, boceto, instruccion_qwen)


def vista_ia(gestor, base, vista: str, semilla: int = 0, boceto: Image.Image | None = None,
             identidad: float | None = None, texto: str | None = None) -> tuple[Image.Image, Image.Image]:
    """La variante redibujada en `vista` por la IA local: (imagen de la IA, sprite).

    `texto` describe la criatura ("oruga de piedra con cráneo y runas"): sin él, un
    monstruo sin descripción solo tiene la plantilla genérica del tipo y la IA inventa
    otra criatura (medido: un gusano acababa como un humanoide agachado)."""
    from motor import animar_ia

    guia = guia_de_vista(base, vista, boceto, identidad)
    (ia, sprite), = animar_ia.candidatos_clave(gestor, base, "ataque", variantes=1, semilla=semilla,
                                              texto=f"{texto}, {VISTAS[vista]}" if texto else VISTAS[vista],
                                              guia=guia, quitar_vistas=True)
    return ia, sprite
