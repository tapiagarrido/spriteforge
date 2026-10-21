"""Animación procedural de un sprite: reposo, caminar, ataque, daño y muerte.

Cada fotograma se construye deformando el sprite elegido con un **mapeo inverso
y vecino más cercano**: para cada píxel del fotograma se busca de qué píxel del
sprite original viene. Nunca se interpola, así que:

    - no aparecen colores nuevos (la paleta del preset se conserva exacta),
    - los bordes siguen siendo píxeles nítidos,
    - el personaje es siempre el mismo (no se redibuja).

Las deformaciones se miden respecto a los pies (la figura está apoyada abajo):
estirar/encoger el cuerpo, inclinarlo (cizalla), desplazarlo, mover las piernas
en tijera, y efectos clásicos de pixel art: destello blanco al recibir daño,
estela tramada en el ataque y disolución con trama (Bayer) al morir.

Salida (`exportar`): hoja de sprites PNG + JSON (formato de Aseprite, que leen
Phaser, Godot, Unity…) y un GIF ampliado por animación para revisarla.

Uso:
    python -m motor.animar salidas/<carpeta>/sprite_01.png -o salidas/<carpeta>/animacion_01
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from motor.pixelizar import ampliar


@dataclass
class Pose:
    """Parámetros de un fotograma, relativos al tamaño de la figura (alto h, ancho w)."""

    dx: float = 0.0  # desplazamiento horizontal (fracción de w), + = hacia donde mira
    dy: float = 0.0  # desplazamiento vertical (fracción de h), + = hacia arriba
    sx: float = 1.0  # escala horizontal
    sy: float = 1.0  # escala vertical (desde los pies)
    inclinacion: float = 0.0  # la cabeza se adelanta esta fracción de h (+ = hacia donde mira)
    piernas: float = 0.0  # tijera de piernas (fracción de w), signo = fase
    brazos: float = 0.0  # balanceo de la parte media, opuesto a las piernas
    cabeza: float = 0.0  # cabeceo: la parte alta sube/baja (fracción de h)
    pasos: float = 0.0  # vista de frente/espalda: levanta el pie de un lado (fracción de h);
    #                     el signo elige el lado (paso alterno en lugar de la tijera lateral)
    garra: float = 0.0  # giro (radianes) de la parte delantera alta alrededor del "hombro":
    #                     + levanta el brazo/garra (o la cabeza de un gusano), - lo baja en arco
    destello: bool = False  # todos los píxeles pasan al color más claro del sprite
    estela: float = 0.0  # copia tramada desplazada hacia atrás (fracción de w)
    disolucion: float = 0.0  # 0..1: fracción de píxeles eliminados con trama Bayer
    temblor: int = 0  # desplazamiento aleatorio ±px (impacto)
    figura: str = "base"  # qué dibujo deformar: "base" o el nombre de un fotograma clave


@dataclass
class Animacion:
    nombre: str
    poses: list[Pose]
    duracion_ms: int
    bucle: bool = True
    descripcion: str = ""
    duraciones: list[int] = field(default_factory=list)  # opcional, por fotograma


def _onda(n: int, fase: float = 0.0) -> list[float]:
    return [math.sin(2 * math.pi * i / n + fase) for i in range(n)]


def animaciones_estandar() -> dict[str, Animacion]:
    """Biblioteca de animaciones. Valores ajustados para sprites de 16 a 96 px."""
    reposo = [Pose(sy=1 + 0.035 * s, sx=1 - 0.015 * s, cabeza=0.012 * s) for s in _onda(6)]

    caminar = []
    for i, s in enumerate(_onda(8)):
        caminar.append(Pose(dy=0.03 * abs(math.cos(2 * math.pi * i / 8)), inclinacion=0.05,
                            piernas=0.10 * s, brazos=-0.04 * s, cabeza=0.01 * s))

    # Zarpazo: la garra sube detrás de la cabeza y baja en arco hacia delante; el cuerpo
    # apenas se desplaza (a diferencia de la embestida).
    zarpazo = [
        Pose(),
        Pose(garra=0.35, inclinacion=-0.04, sy=1.03),
        Pose(garra=0.75, inclinacion=-0.08, sy=1.05),  # garra en alto
        Pose(garra=-0.35, dx=0.06, inclinacion=0.10, estela=0.10),  # el golpe
        Pose(garra=-0.6, dx=0.08, inclinacion=0.13, estela=0.05, temblor=1),
        Pose(garra=-0.35, dx=0.05, inclinacion=0.07),
        Pose(garra=-0.1, dx=0.02, inclinacion=0.02),
        Pose(),
    ]

    # Rugido: se yergue, echa la cabeza atrás y tiembla en el sitio.
    rugido = [
        Pose(),
        Pose(sy=0.95, sx=1.03, inclinacion=0.04),  # coge aire
        Pose(sy=1.06, sx=0.98, inclinacion=-0.10, cabeza=0.03, garra=0.2),
        Pose(sy=1.08, sx=0.97, inclinacion=-0.14, cabeza=0.04, garra=0.3, temblor=1),
        Pose(sy=1.08, sx=0.97, inclinacion=-0.14, cabeza=0.04, garra=0.3, temblor=1),
        Pose(sy=1.07, sx=0.97, inclinacion=-0.12, cabeza=0.035, garra=0.25, temblor=1),
        Pose(sy=1.03, inclinacion=-0.05, cabeza=0.01),
        Pose(),
    ]

    # Especial (sin pose de IA): carga encogido con destellos y libera hacia delante.
    especial = [
        Pose(),
        Pose(sy=0.94, sx=1.04),
        Pose(sy=0.91, sx=1.06, destello=True),
        Pose(sy=0.90, sx=1.07, temblor=1),
        Pose(sy=0.90, sx=1.07, destello=True, temblor=1),
        Pose(dx=0.10, sy=1.06, sx=0.97, inclinacion=0.10, estela=0.18, garra=-0.3),  # libera
        Pose(dx=0.06, sy=1.02, inclinacion=0.05, estela=0.06),
        Pose(),
    ]

    ataque = [
        Pose(),
        Pose(dx=-0.04, inclinacion=-0.08, sy=0.95, sx=1.03),  # anticipación
        Pose(dx=-0.07, inclinacion=-0.13, sy=0.92, sx=1.05),
        Pose(dx=0.16, inclinacion=0.20, sx=1.12, sy=0.98, estela=0.14),  # embestida
        Pose(dx=0.22, inclinacion=0.24, sx=1.16, sy=0.97, estela=0.08, temblor=1),  # impacto
        Pose(dx=0.14, inclinacion=0.13, sx=1.06),  # recuperación
        Pose(dx=0.05, inclinacion=0.04),
        Pose(),
    ]

    dano = [
        Pose(dx=-0.06, inclinacion=-0.08, destello=True),
        Pose(dx=-0.08, inclinacion=-0.10, temblor=1),
        Pose(dx=-0.05, inclinacion=-0.06, destello=True),
        Pose(dx=-0.02, inclinacion=-0.02),
    ]

    muerte = []
    for i in range(8):
        t = i / 7
        muerte.append(Pose(dx=-0.08 * t, inclinacion=-0.18 * t, sy=1 - 0.5 * t ** 1.5, sx=1 + 0.15 * t,
                           destello=(i == 0), disolucion=max(0.0, (t - 0.45) / 0.55)))

    return {
        "reposo": Animacion("reposo", reposo, 150, descripcion="Respiración en el sitio"),
        "caminar": Animacion("caminar", caminar, 100, descripcion="Paso en el sitio (rebote y piernas)"),
        "ataque": Animacion("ataque", ataque, 80, bucle=False,
                            descripcion="Anticipación, embestida con estela y recuperación",
                            duraciones=[90, 90, 140, 60, 160, 90, 90, 100]),
        "zarpazo": Animacion("zarpazo", zarpazo, 80, bucle=False,
                             descripcion="Garra en alto y golpe en arco hacia delante",
                             duraciones=[90, 100, 160, 60, 170, 90, 90, 100]),
        "rugido": Animacion("rugido", rugido, 90, bucle=False,
                            descripcion="Se yergue, cabeza atrás y temblor en el sitio",
                            duraciones=[90, 120, 120, 90, 90, 140, 110, 100]),
        "especial": Animacion("especial", especial, 90, bucle=False,
                              descripcion="Carga con destellos y descarga hacia delante",
                              duraciones=[90, 110, 90, 90, 90, 70, 140, 110]),
        "dano": Animacion("dano", dano, 80, bucle=False, descripcion="Destello y retroceso"),
        "muerte": Animacion("muerte", muerte, 110, bucle=False, descripcion="Colapso y disolución"),
    }


def animacion_con_clave(nombre: str, descripcion: str = "", direccion: str | None = None) -> Animacion:
    """Ataque cuyo golpe es un fotograma clave dibujado por la IA (`figura=nombre`).

    Anticipación y recuperación salen del sprite base (deformado); el golpe, del
    dibujo clave, que entra con estela y tiembla en el impacto. El salto de un
    dibujo a otro es deliberadamente seco, como en la animación clásica de juegos.
    """
    if direccion in ("abajo", "arriba"):  # de frente/espaldas el golpe va hacia el espectador o el fondo
        hacia = -1 if direccion == "abajo" else 1
        poses = [Pose(), Pose(sy=0.95, sx=1.03), Pose(sy=0.92, sx=1.05),
                 Pose(dy=0.06 * hacia, figura=nombre), Pose(dy=0.08 * hacia, temblor=1, figura=nombre),
                 Pose(dy=0.05 * hacia, figura=nombre), Pose(dy=0.02 * hacia), Pose()]
    else:
        poses = [
            Pose(),
            Pose(dx=-0.04, inclinacion=-0.08, sy=0.95, sx=1.03),
            Pose(dx=-0.07, inclinacion=-0.13, sy=0.92, sx=1.05),
            Pose(dx=0.10, estela=0.16, figura=nombre),
            Pose(dx=0.14, estela=0.06, temblor=1, figura=nombre),
            Pose(dx=0.12, figura=nombre),
            Pose(dx=0.04, inclinacion=0.03),
            Pose(),
        ]
    return Animacion(nombre, poses, 90, bucle=False,
                     descripcion=descripcion or f"Ataque con pose clave de IA ({nombre})",
                     duraciones=[90, 90, 140, 70, 180, 120, 90, 100])


def animacion_caminata() -> Animacion:
    """Ciclo de caminar con dos pasos de la IA (`paso_a` y `paso_b`, un pie adelantado cada uno) y la
    pose base entre ellos, con el cuerpo un poco más alto al pasar. Con la deformación (tijera de piernas)
    un personaje de capa larga apenas se mueve: sus piernas no se ven."""
    poses = [Pose(figura="paso_a"), Pose(dy=0.025), Pose(figura="paso_b"), Pose(dy=0.025)]
    return Animacion("caminar", poses, 120, bucle=True, descripcion="Caminar con pasos dibujados por la IA",
                     duraciones=[140, 100, 140, 100])


# --- geometría del sprite --------------------------------------------------------

@dataclass
class Figura:
    sprite: np.ndarray  # RGBA del lienzo animado (con márgenes)
    pies: int  # fila de los pies
    centro: float  # columna central de la figura
    ancho: int
    alto: int
    mira: int  # +1 derecha, -1 izquierda


def mirando_hacia(alfa: np.ndarray) -> int:
    """+1 si la figura mira a la derecha, -1 a la izquierda.

    Heurística: la cabeza (franja alta de la figura) suele estar adelantada
    respecto al cuerpo hacia donde mira el personaje.
    """
    ys, xs = np.nonzero(alfa)
    if not len(ys):
        return 1
    alto = ys.max() - ys.min() + 1
    alta = ys < ys.min() + 0.3 * alto
    return 1 if xs[alta].mean() >= xs.mean() else -1


def preparar(sprite: Image.Image, margen: float = 0.35, mira: int | None = None) -> Figura:
    """Coloca el sprite en un lienzo con márgenes para que quepan embestidas y estiramientos."""
    rgba = np.array(sprite.convert("RGBA"))
    alfa = rgba[..., 3] > 0
    if not alfa.any():
        raise ValueError("El sprite está vacío")
    h, w = rgba.shape[:2]
    mx, mt = int(round(w * margen)), int(round(h * margen * 0.5))
    lienzo = np.zeros((h + mt, w + 2 * mx, 4), dtype=np.uint8)
    lienzo[mt:, mx:mx + w] = rgba
    a = lienzo[..., 3] > 0
    ys, xs = np.nonzero(a)
    return Figura(lienzo, int(ys.max()), float((xs.min() + xs.max()) / 2), int(xs.max() - xs.min() + 1),
                  int(ys.max() - ys.min() + 1), mira if mira in (1, -1) else mirando_hacia(alfa))


_BAYER4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) / 16


def fotograma(fig: Figura, pose: Pose, semilla: int = 0) -> np.ndarray:
    """Aplica una pose al sprite. Devuelve RGBA del mismo tamaño que el lienzo."""
    src = fig.sprite
    H, W = src.shape[:2]
    m, w, h = fig.mira, fig.ancho, fig.alto
    yy, xx = np.mgrid[0:H, 0:W].astype(float)

    # Coordenadas del destino relativas a los pies, con el desplazamiento global deshecho.
    rng = np.random.default_rng(semilla)
    temblor = rng.integers(-pose.temblor, pose.temblor + 1, 2) if pose.temblor else (0, 0)
    u = xx - fig.centro - m * pose.dx * w - temblor[0]
    v = fig.pies - yy + pose.dy * h + temblor[1]  # altura sobre los pies

    # Escala desde los pies (inversa).
    v = v / pose.sy
    u = u / pose.sx
    a = np.clip(v / h, 0, 1)  # 0 en los pies, 1 en lo alto

    # Garra: la parte delantera alta gira alrededor del "hombro" (delante del centro, a
    # 55 % de la altura). El peso crece hacia delante y hacia arriba, así que el
    # cuerpo y las patas traseras no se mueven y el giro se reparte sin cortes.
    if pose.garra:
        pu, pv = m * 0.05 * w, 0.55 * h
        frente = np.clip(m * (u - pu * 0.5) / max(1.0, 0.14 * w), 0, 1)
        alto_ = np.clip((a - 0.12) / 0.3, 0, 1)  # desde abajo: las garras de una bestia agazapada
        phi = -m * pose.garra * frente * alto_  # giro inverso (se busca el píxel de origen)
        du, dv = u - pu, v - pv
        c, s = np.cos(phi), np.sin(phi)
        u, v = pu + c * du - s * dv, pv + s * du + c * dv
        a = np.clip(v / h, 0, 1)

    # Inclinación: la parte alta se desplaza proporcionalmente a su altura.
    u = u - m * pose.inclinacion * h * a

    # Cabeceo: solo el tercio superior, con transición suave.
    if pose.cabeza:
        peso = np.clip((a - 0.6) / 0.25, 0, 1)
        v = v - pose.cabeza * h * peso

    # Piernas en tijera (tercio inferior): cada mitad se mueve en sentido opuesto.
    lado = np.tanh(u / max(1.0, w * 0.08))  # -1 izquierda … +1 derecha, suave en el centro
    if pose.piernas:
        peso = np.clip(1 - a / 0.38, 0, 1)
        u = u - pose.piernas * w * peso * lado
    if pose.pasos:
        # Frente/espalda: el pie de un lado sube (el otro queda apoyado).
        peso = np.clip(1 - a / 0.3, 0, 1) * np.clip(np.sign(pose.pasos) * lado, 0, 1)
        v = v - abs(pose.pasos) * h * peso
    if pose.brazos:
        peso = np.clip(1 - abs(a - 0.6) / 0.18, 0, 1)
        u = u - pose.brazos * w * peso * lado

    x_src = np.rint(u + fig.centro).astype(int)
    y_src = np.rint(fig.pies - v).astype(int)
    dentro = (x_src >= 0) & (x_src < W) & (y_src >= 0) & (y_src < H)
    salida = np.zeros_like(src)
    salida[dentro] = src[y_src[dentro], x_src[dentro]]

    opaco = salida[..., 3] > 0
    if pose.estela:
        # Copia desplazada hacia atrás, solo en las casillas pares de un tablero:
        # sugiere movimiento sin introducir semitransparencias ni colores nuevos.
        paso = max(1, int(round(pose.estela * w)))
        atras = np.roll(salida, -m * paso, axis=1)
        tablero = ((yy + xx) % 2 == 0)
        poner = (atras[..., 3] > 0) & ~opaco & tablero
        salida[poner] = atras[poner]
    if pose.destello:
        colores = src[src[..., 3] > 0][:, :3]
        claro = colores[(colores.astype(int) @ np.array([299, 587, 114])).argmax()]
        salida[salida[..., 3] > 0, :3] = claro
    if pose.disolucion > 0:
        umbral = _BAYER4[(yy % 4).astype(int), (xx % 4).astype(int)]
        salida[umbral < pose.disolucion] = 0
    return salida


def animar(sprite: Image.Image, nombres: list[str] | None = None, mira: int | None = None,
           biblioteca: dict[str, Animacion] | None = None,
           claves: dict[str, Image.Image] | None = None) -> tuple[Figura, dict[str, list[Image.Image]]]:
    """Fotogramas de cada animación. `claves` = {nombre: sprite de la pose clave}.

    Cada fotograma clave añade una animación de ataque con su nombre. Los sprites
    clave deben tener el mismo lienzo que el base (como los devuelve `animar_ia`).
    """
    biblioteca = dict(biblioteca or animaciones_estandar())
    claves = claves or {}
    for nombre in claves:
        biblioteca.setdefault(nombre, animacion_con_clave(nombre))
    nombres = list(animaciones_estandar() if nombres is None else nombres)
    nombres += [n for n in claves if n not in nombres]
    desconocidas = set(nombres) - set(biblioteca)
    if desconocidas:
        raise ValueError(f"Animaciones desconocidas: {', '.join(sorted(desconocidas))}")
    fig = preparar(sprite, mira=mira)
    figuras = {"base": fig}
    for nombre, clave in claves.items():
        if clave.size != sprite.size:
            raise ValueError(f"El fotograma clave '{nombre}' no tiene el tamaño del sprite base")
        # La IA no siempre respeta la orientación: el golpe debe mirar como el personaje.
        if mirando_hacia(np.array(clave.convert("RGBA"))[..., 3] > 0) != fig.mira:
            clave = clave.transpose(Image.FLIP_LEFT_RIGHT)
        figuras[nombre] = preparar(clave, mira=fig.mira)
        # Mismos pies y centro que el base: el golpe no "salta" de sitio.
        figuras[nombre].pies, figuras[nombre].centro = fig.pies, fig.centro
    resultado = {}
    for nombre in nombres:
        anim = biblioteca[nombre]
        resultado[nombre] = [Image.fromarray(fotograma(figuras.get(p.figura, fig), p, semilla=i), "RGBA")
                             for i, p in enumerate(anim.poses)]
    return fig, resultado


# --- exportación -----------------------------------------------------------------

ORDEN = ["reposo", "caminar", "ataque", "zarpazo", "rugido", "dano", "muerte"]
# Direcciones de exploración (vista cenital, como el mapa de Pokémon). "derecha" se genera
# desde la vista de lado e "izquierda" es su espejo. Sin dirección = la variante tal cual.
DIRECCIONES = ["abajo", "arriba", "derecha", "izquierda"]


def separar_direccion(nombre: str) -> tuple[str, str | None]:
    """'caminar_abajo' -> ('caminar', 'abajo'); 'caminar' -> ('caminar', None)."""
    base, _, sufijo = nombre.rpartition("_")
    return (base, sufijo) if base and sufijo in DIRECCIONES else (nombre, None)


def animaciones_direccion(direccion: str | None) -> dict[str, Animacion]:
    """Biblioteca para una dirección. De lado (o sin dirección) es la estándar; de frente
    (abajo) o de espaldas (arriba) cambian los movimientos que dependen del eje:
    caminar alterna los pies, el ataque va hacia el espectador o se aleja, y el zarpazo
    cruza de un lado a otro."""
    biblioteca = animaciones_estandar()
    if direccion not in ("abajo", "arriba"):
        return biblioteca
    hacia = -1 if direccion == "abajo" else 1  # dy + = hacia arriba en pantalla
    crece = 1 if direccion == "abajo" else -1  # hacia el espectador se ve más grande

    caminar = []
    for i, s in enumerate(_onda(8)):
        caminar.append(Pose(dy=0.025 * abs(math.cos(2 * math.pi * i / 8)), pasos=0.06 * s,
                            sx=1 + 0.02 * abs(s), cabeza=0.008 * s))
    ataque = [
        Pose(),
        Pose(sy=0.95, sx=1.03, dy=-0.01 * hacia),  # anticipación: se encoge y retrocede
        Pose(sy=0.92, sx=1.05, dy=-0.02 * hacia),
        Pose(sx=1 + 0.10 * crece, sy=1 + 0.10 * crece, dy=0.07 * hacia),  # embestida
        Pose(sx=1 + 0.13 * crece, sy=1 + 0.12 * crece, dy=0.09 * hacia, temblor=1),
        Pose(sx=1 + 0.06 * crece, sy=1 + 0.05 * crece, dy=0.05 * hacia),
        Pose(dy=0.02 * hacia),
        Pose(),
    ]
    zarpazo = [  # golpe que cruza de un lado a otro, con estela
        Pose(),
        Pose(inclinacion=-0.08, sy=1.03),
        Pose(inclinacion=-0.14, sy=1.05),
        Pose(inclinacion=0.12, estela=0.10, dy=0.03 * hacia),
        Pose(inclinacion=0.17, estela=0.05, dy=0.04 * hacia, temblor=1),
        Pose(inclinacion=0.08, dy=0.02 * hacia),
        Pose(inclinacion=0.02),
        Pose(),
    ]
    biblioteca["caminar"] = Animacion("caminar", caminar, 100, descripcion="Paso alterno en el sitio")
    hacia_donde = "hacia el espectador" if direccion == "abajo" else "hacia el fondo"
    biblioteca["ataque"] = Animacion("ataque", ataque, 80, bucle=False,
                                     descripcion=f"Anticipación y embestida {hacia_donde}",
                                     duraciones=[90, 90, 140, 60, 160, 90, 90, 100])
    biblioteca["zarpazo"] = Animacion("zarpazo", zarpazo, 80, bucle=False,
                                      descripcion="Golpe que cruza de un lado a otro",
                                      duraciones=[90, 100, 160, 60, 170, 90, 90, 100])
    return biblioteca
ATAQUES = {"ataque", "zarpazo", "rugido", "especial"}  # admiten golpe dibujado por la IA o por el usuario


def construir(sprite: Image.Image, nombre: str, mira: int | None = None,
              clave: Image.Image | None = None, direccion: str | None = None
              ) -> tuple[Figura, Animacion, list[Image.Image]]:
    """Fotogramas de UNA animación. Con `clave`, el golpe del ataque es ese dibujo.
    `direccion` (abajo/arriba/derecha) elige la biblioteca de esa vista."""
    biblioteca = animaciones_direccion(direccion)
    if clave is not None:
        if nombre not in ATAQUES:
            raise ValueError(f"La animación {nombre!r} no admite una pose clave")
        anim = animacion_con_clave(nombre, direccion=direccion)
        fig, anims = animar(sprite, [], mira, {nombre: anim}, {nombre: clave})
    else:
        anim = biblioteca.get(nombre) or (biblioteca["ataque"] if nombre in ATAQUES else None)
        if anim is None:
            raise ValueError(f"Animación desconocida: {nombre!r}")
        anim = Animacion(nombre, anim.poses, anim.duracion_ms, anim.bucle, anim.descripcion, anim.duraciones)
        fig, anims = animar(sprite, [nombre], mira, {nombre: anim})
    return fig, anim, anims[nombre]


def construir_caminata(sprite: Image.Image, paso_a: Image.Image, paso_b: Image.Image, mira: int | None = None
                       ) -> tuple[Figura, Animacion, list[Image.Image]]:
    """Fotogramas del ciclo de caminar a partir de dos poses de paso del mismo lienzo que `sprite`."""
    anim = animacion_caminata()
    fig, anims = animar(sprite, ["caminar"], mira, {"caminar": anim}, {"paso_a": paso_a, "paso_b": paso_b})
    return fig, anim, anims["caminar"]


def guardar_animacion(carpeta: Path, anim: Animacion, frames: list[Image.Image], mira: int,
                      extra: dict | None = None, ampliacion_gif: int = 4) -> dict:
    """Guarda una animación como secuencia (`<carpeta>/<nombre>/frame_XX.png`) + GIF y
    reconstruye la hoja del personaje con todas las animaciones guardadas."""
    carpeta = Path(carpeta)
    destino = carpeta / anim.nombre
    if destino.exists():
        for viejo in destino.glob("frame_*.png"):
            viejo.unlink()
    destino.mkdir(parents=True, exist_ok=True)
    duraciones = anim.duraciones or [anim.duracion_ms] * len(frames)
    for i, f in enumerate(frames):
        f.save(destino / f"frame_{i:02d}.png")
    meta = {"nombre": anim.nombre, "fotogramas": len(frames), "duraciones_ms": duraciones,
            "bucle": anim.bucle, "descripcion": anim.descripcion,
            "mira": "derecha" if mira == 1 else "izquierda", **(extra or {})}
    (destino / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    guardar_gif([ampliar(f, ampliacion_gif) for f in frames], duraciones, carpeta / f"{anim.nombre}.gif",
                anim.bucle)
    return reconstruir_hoja(carpeta)


def borrar_animacion(carpeta: Path, nombre: str) -> dict:
    carpeta = Path(carpeta)
    destino = carpeta / nombre
    if not (destino / "meta.json").exists():
        raise ValueError(f"No existe la animación {nombre!r}")
    for archivo in destino.iterdir():
        archivo.unlink()
    destino.rmdir()
    (carpeta / f"{nombre}.gif").unlink(missing_ok=True)
    return reconstruir_hoja(carpeta)


def animaciones_guardadas(carpeta: Path) -> list[dict]:
    """Animaciones guardadas de un personaje, en orden de juego (reposo, caminar, ataques…)."""
    carpeta = Path(carpeta)
    metas = [json.loads(m.read_text()) for m in carpeta.glob("*/meta.json")] if carpeta.is_dir() else []

    def orden(m):  # la variante tal cual primero; luego por dirección y en orden de juego
        nombre, direccion = separar_direccion(m["nombre"])
        d = DIRECCIONES.index(direccion) + 1 if direccion else 0
        return (d, ORDEN.index(nombre) if nombre in ORDEN else len(ORDEN), nombre)

    return sorted(metas, key=orden)


def reconstruir_hoja(carpeta: Path) -> dict:
    """Hoja PNG + JSON (Aseprite) + resumen con todas las animaciones guardadas."""
    carpeta = Path(carpeta)
    metas = animaciones_guardadas(carpeta)
    for viejo in ("hoja.png", "hoja_x4.png", "hoja.json"):
        (carpeta / viejo).unlink(missing_ok=True)
    resumen = {"animaciones": {}, "hoja": None, "json": None, "lienzo": None}
    if not metas:
        (carpeta / "animacion.json").write_text(json.dumps(resumen, indent=2) + "\n")
        return resumen
    filas = [[Image.open(carpeta / m["nombre"] / f"frame_{i:02d}.png").convert("RGBA")
              for i in range(m["fotogramas"])] for m in metas]
    ancho, alto = filas[0][0].size
    hoja = Image.new("RGBA", (ancho * max(len(f) for f in filas), alto * len(filas)), (0, 0, 0, 0))
    frames_json, etiquetas, indice = [], [], 0
    for fila, (m, frames) in enumerate(zip(metas, filas, strict=True)):
        for col, (f, dur) in enumerate(zip(frames, m["duraciones_ms"], strict=True)):
            hoja.paste(f, (col * ancho, fila * alto))
            frames_json.append({"filename": f"{m['nombre']}_{col}", "duration": dur,
                                "frame": {"x": col * ancho, "y": fila * alto, "w": ancho, "h": alto},
                                "sourceSize": {"w": ancho, "h": alto}})
        etiquetas.append({"name": m["nombre"], "from": indice, "to": indice + len(frames) - 1,
                          "direction": "forward", "repeat": "0" if m["bucle"] else "1"})
        indice += len(frames)
        resumen["animaciones"][m["nombre"]] = {**m, "gif": f"{m['nombre']}.gif",
                                               "frames": [f"{m['nombre']}/frame_{i:02d}.png"
                                                          for i in range(len(frames))]}
    hoja.save(carpeta / "hoja.png")
    ampliar(hoja, 4).save(carpeta / "hoja_x4.png")
    meta = {"frames": frames_json,
            "meta": {"app": "Forja de Sprites", "image": "hoja.png", "format": "RGBA8888",
                     "size": {"w": hoja.width, "h": hoja.height}, "scale": "1", "frameTags": etiquetas}}
    (carpeta / "hoja.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    resumen.update(hoja="hoja.png", json="hoja.json", lienzo=[ancho, alto], mira=metas[0].get("mira"))
    (carpeta / "animacion.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False) + "\n")
    return resumen


def exportar(sprite: Image.Image, carpeta: Path, nombres: list[str] | None = None, mira: int | None = None,
             ampliacion_gif: int = 4, claves: dict[str, Image.Image] | None = None) -> dict:
    """Genera y guarda varias animaciones de una vez (procedurales y con pose clave)."""
    claves = claves or {}
    nombres = list(animaciones_estandar() if nombres is None else nombres)
    nombres += [n for n in claves if n not in nombres]
    resumen = {}
    for nombre in nombres:
        fig, anim, frames = construir(sprite, nombre, mira, claves.get(nombre))
        mira = fig.mira
        resumen = guardar_animacion(carpeta, anim, frames, fig.mira, ampliacion_gif=ampliacion_gif)
    return resumen


def guardar_gif(frames: list[Image.Image], duraciones: list[int], ruta: Path, bucle: bool = True) -> None:
    """GIF con transparencia de 1 bit (los sprites no tienen semitransparencias)."""
    convertidos = []
    for f in frames:
        rgba = f.convert("RGBA")
        p = rgba.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT,
                                         dither=Image.Dither.NONE)
        transparente = np.array(rgba)[..., 3] == 0
        paleta = (p.getpalette() or [])[:255 * 3]
        paleta += [0] * (255 * 3 - len(paleta)) + [0, 0, 0]  # índice 255: transparente
        datos = np.array(p)
        datos[transparente] = 255
        p = Image.fromarray(datos, "P")  # imagen nueva: hay que volver a ponerle la paleta
        p.putpalette(paleta)
        p.info["transparency"] = 255
        convertidos.append(p)
    convertidos[0].save(ruta, save_all=True, append_images=convertidos[1:], duration=duraciones,
                        loop=0 if bucle else 1, disposal=2, transparency=255, optimize=False)


def main() -> None:
    ap = argparse.ArgumentParser(prog="motor.animar", description="Animaciones procedurales de un sprite.")
    ap.add_argument("sprite", type=Path)
    ap.add_argument("-o", "--salida", type=Path, help="carpeta de salida (por defecto <sprite>_animacion)")
    ap.add_argument("--animaciones", nargs="*",
                    help=f"por defecto todas: {', '.join(animaciones_estandar())}")
    ap.add_argument("--mira", choices=["izquierda", "derecha"],
                    help="hacia dónde mira (por defecto, se detecta)")
    args = ap.parse_args()
    salida = args.salida or args.sprite.with_name(args.sprite.stem + "_animacion")
    mira = {"izquierda": -1, "derecha": 1}.get(args.mira)
    r = exportar(Image.open(args.sprite), salida, args.animaciones, mira)
    print(f"{salida}  (mira a la {r['mira']}, lienzo {r['lienzo'][0]}x{r['lienzo'][1]})")
    for nombre, a in r["animaciones"].items():
        print(f"  {nombre:8s} {a['fotogramas']} fotogramas  {a['gif']}")


if __name__ == "__main__":
    main()
