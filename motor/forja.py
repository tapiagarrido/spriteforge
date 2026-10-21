"""Cadena completa: boceto -> sprites pixel art.

    boceto (foto o dibujo) --limpiar--> trazo --IA--> imágenes --pixelizar--> sprites

Cada ejecución se guarda en `salidas/<fecha>_<nombre>/`:

    boceto.png            entrada original
    boceto_limpio.png     lo que ve ControlNet
    referencia.png        rejilla de estilo que ve IP-Adapter
    ia_01.png ...         salidas de la IA
    sprite_01.png ...     sprites finales (tamaño real)
    sprite_01_x8.png ...  sprites ampliados para verlos
    info.json             prompt, semillas, parámetros y tiempos

Uso:
    forja-generar boceto.png --proyecto mazmorra-oscura \
        --prompt "caballero con armadura roja y una espada grande" --variantes 4
"""

from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image

from motor import generar as gen
from motor import segmentar as segmentador
from motor.boceto import limpiar_boceto
from motor.modelos import RAIZ
from motor.pixelizar import Opciones, ampliar, pixelizar, silueta_de_boceto
from motor.proyectos import cargar as cargar_proyecto
from motor.proyectos import tipo as tipo_proyecto

CARPETA_SALIDAS = RAIZ / "salidas"
CARPETA_PRESETS = RAIZ / "presets"
MOTORES_IA = ("local", "gemini", "qwen")
AMPLIACION_VISTA = 8


@dataclass
class Variante:
    indice: int
    semilla: int
    ia: str
    sprite: str
    sprite_vista: str
    colores: list[str]


@dataclass
class Forjado:
    carpeta: Path
    variantes: list[Variante] = field(default_factory=list)
    info: dict = field(default_factory=dict)


def nombre_carpeta(prompt: str, ahora: datetime | None = None) -> str:
    """'2026-10-16_1530_knight-with-red-armor' (ASCII, corto, sin espacios)."""
    texto = unicodedata.normalize("NFKD", prompt).encode("ascii", "ignore").decode().lower()
    slug = "-".join(re.findall(r"[a-z0-9]+", texto)[:5]) or "personaje"
    return f"{(ahora or datetime.now()):%Y-%m-%d_%H%M%S}_{slug}"


def pixelizar_imagen(imagen: Image.Image, preset: Path, *, segmentar: bool = True, **cambios) -> Image.Image:
    """Pixeliza una imagen de la IA ya guardada con las opciones del preset y los `cambios`
    (alto_figura, max_colores, alto_exacto…), sin volver a generar nada."""
    op = Opciones.desde_preset(preset, **{k: v for k, v in cambios.items() if v is not None})
    figura = segmentador.mascara(imagen) if segmentar and segmentador.disponible() else None
    return pixelizar(imagen, op, None, figura).imagen


def carpeta_libre(base: Path, nombre: str) -> Path:
    """Crea `base/nombre` (o `nombre_2`, `nombre_3`…): nunca reutiliza una carpeta existente.

    Dos generaciones con la misma descripción en el mismo segundo tendrían el mismo
    nombre y la segunda sobrescribiría a la primera.
    """
    base.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        carpeta = base / (nombre if n == 1 else f"{nombre}_{n}")
        try:
            carpeta.mkdir()
            return carpeta
        except FileExistsError:
            continue
    raise RuntimeError(f"No se pudo crear una carpeta libre para {nombre!r}")


def traducir_textos(prompt: str, proyecto: dict | None, plantilla: dict | None, traducir: bool,
                    tiempos: dict | None = None) -> tuple[str, bool, dict, dict | None]:
    """Pasa al inglés la descripción, la ambientación/negativo del proyecto y la plantilla."""
    proyecto_ia = dict(proyecto or {})
    tipo_ia = dict(plantilla) if plantilla else None
    if not traducir:
        return prompt, False, proyecto_ia, tipo_ia
    from motor.traducir import a_ingles

    t = time.perf_counter()
    prompt_ia, traducido = a_ingles(prompt)
    for campo in ("ambientacion", "negativo"):
        if proyecto_ia.get(campo):
            proyecto_ia[campo] = a_ingles(proyecto_ia[campo])[0]
    for campo in ("prompt", "negativo"):
        if tipo_ia and tipo_ia.get(campo):
            tipo_ia[campo] = a_ingles(tipo_ia[campo])[0]
    if tiempos is not None:
        tiempos["traducir"] = time.perf_counter() - t
    return prompt_ia, traducido, proyecto_ia, tipo_ia


def forjar(boceto: Image.Image, preset: Path | None, prompt: str, *, proyecto: dict | None = None,
           tipo: str | None = None,
           variantes: int = 4, semilla: int | None = None, modo: str = gen.MODO_POR_DEFECTO,
           parecido_boceto: float | None = None, fuerza_estilo: float | None = None,
           alto_figura: int | None = None, max_colores: int | None = None,
           gestor: gen.GestorModelos | None = None, generador=None, traducir: bool = True,
           segmentar: bool = True, motor_ia: str = "local", modelo_gemini: str | None = None,
           prompt_base: str | None = None, ilustracion: bool | None = None, alto_exacto: bool = False,
           simplificar: float = 0.3, grosor: float = 0.006,
           carpeta_salidas: Path = CARPETA_SALIDAS) -> Forjado:
    """Ejecuta la cadena completa y guarda todo en una carpeta nueva.

    `proyecto` (ver `motor.proyectos`) aporta la dirección de arte común: su
    ambientación y negativo se añaden al prompt, y si no se indica `preset` se usa
    el del proyecto. `tipo` es el id de una plantilla de personaje del proyecto
    (monstruo, npc, arbol…). `generador(peticion) -> list[Image]` permite sustituir la IA
    (tests, otros modelos); por defecto se usa `generar.generar` con `gestor`. Con
    `traducir`, los textos en español se pasan al inglés antes de dárselos a la IA.
    Con `segmentar` (y el modelo descargado) la figura se separa del fondo con el
    segmentador; si no, por color con ayuda de la silueta del boceto.
    `motor_ia` = "local" (GPU propia), "gemini" (API externa, ver `motor.gemini`) o "qwen"
    (servidor Qwen-Image propio, ver `motor.qwen`).
    """
    if motor_ia not in MOTORES_IA:
        raise ValueError(f"Motor de IA desconocido: {motor_ia!r}")
    if preset is None:
        if not proyecto:
            raise ValueError("Hace falta un preset o un proyecto")
        preset = CARPETA_PRESETS / proyecto["preset"]
    preset = Path(preset)
    estilo = json.loads((preset / "estilo.json").read_text())
    semilla = int(time.time()) % 1_000_000 if semilla is None else semilla
    ancho, alto = gen.tamano_generacion(estilo["sprite"]["ancho"], estilo["sprite"]["alto"],
                                        estilo["generacion"].get("resolucion_generacion", 768))
    tiempos = {}

    t = time.perf_counter()
    limpio = limpiar_boceto(boceto, ancho, alto, simplificar=simplificar, grosor=grosor,
                            ilustracion=ilustracion)
    tiempos["limpiar"] = time.perf_counter() - t

    plantilla = tipo_proyecto(proyecto, tipo)
    if parecido_boceto is None and plantilla and plantilla.get("parecido_boceto") is not None:
        parecido_boceto = plantilla["parecido_boceto"]  # p. ej. efectos: boceto como guía suelta
    prompt = (prompt or "").strip()
    if not prompt and not (plantilla and plantilla.get("prompt")):
        raise ValueError("Falta la descripción del personaje (o elige un tipo de personaje)")
    prompt_ia, traducido, proyecto_ia, tipo_ia = traducir_textos(prompt, proyecto, plantilla, traducir,
                                                                 tiempos)

    peticion = gen.peticion_desde_preset(preset, limpio, prompt_ia, proyecto=proyecto_ia, tipo=tipo_ia,
                                         parecido_boceto=parecido_boceto, variantes=variantes,
                                         semilla=semilla, modo=modo, fuerza_estilo=fuerza_estilo,
                                         prompt_base=prompt_base)
    if generador is None and motor_ia == "gemini":
        from motor import config, gemini

        clave = config.gemini_api_key()
        modelo_gemini = (modelo_gemini or config.leer().get("gemini_modelo")
                         or gemini.modelo_por_defecto(clave))
        generador = lambda p: gemini.generar(p, clave, modelo_gemini)  # noqa: E731
    elif generador is None and motor_ia == "qwen":
        from motor import config, qwen

        url = config.qwen_url()
        generador = lambda p: qwen.generar(p, url, boceto)  # noqa: E731
    elif generador is None:
        gestor = gestor or gen.GestorModelos(minutos_inactividad=0)
        generador = lambda p: gen.generar(gestor, p)  # noqa: E731

    t = time.perf_counter()
    imagenes = generador(peticion)
    tiempos["ia"] = time.perf_counter() - t

    carpeta = carpeta_libre(Path(carpeta_salidas), nombre_carpeta(prompt or plantilla["nombre"]))
    boceto.save(carpeta / "boceto.png")
    limpio.save(carpeta / "boceto_limpio.png")
    peticion.referencia.save(carpeta / "referencia.png")

    op = Opciones.desde_preset(preset, alto_figura=alto_figura, max_colores=max_colores,
                               alto_exacto=alto_exacto or None)
    resultado = Forjado(carpeta)
    t = time.perf_counter()
    silueta = silueta_de_boceto(limpio, (ancho, alto))
    usar_segmentador = segmentar and segmentador.disponible()
    for i, imagen in enumerate(imagenes, start=1):
        figura = segmentador.mascara(imagen) if usar_segmentador else None
        px = pixelizar(imagen, op, silueta if imagen.size == (ancho, alto) else None, figura)
        nombres = (f"ia_{i:02d}.png", f"sprite_{i:02d}.png", f"sprite_{i:02d}_x{AMPLIACION_VISTA}.png")
        imagen.save(carpeta / nombres[0])
        px.imagen.save(carpeta / nombres[1])
        ampliar(px.imagen, AMPLIACION_VISTA).save(carpeta / nombres[2])
        resultado.variantes.append(Variante(i, semilla + i - 1, *nombres, px.colores))
    tiempos["pixelizar"] = time.perf_counter() - t

    resultado.info = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "preset": preset.name,
        "proyecto": (proyecto or {}).get("id"),
        "tipo": {"id": plantilla["id"], "nombre": plantilla["nombre"]} if plantilla else None,
        "ambientacion": proyecto_ia.get("ambientacion") or None,
        "prompt_usuario": prompt,
        "prompt_traducido": prompt_ia if traducido else None,
        "prompt": peticion.prompt,
        "prompt_negativo": peticion.prompt_negativo,
        "motor_ia": f"gemini:{modelo_gemini}" if motor_ia == "gemini" else motor_ia,
        "modo": modo if motor_ia == "local" else None,
        "semilla": semilla,
        "tamano_generacion": [ancho, alto],
        "boceto": {"simplificar": simplificar, "grosor": grosor},
        "fuerza_boceto": peticion.fuerza_boceto,
        "fin_boceto": peticion.fin_boceto,
        "fuerza_estilo": peticion.fuerza_estilo,
        "fuerza_pixel_art": peticion.fuerza_pixel_art,
        "pixelizado": {"alto_figura": op.alto_figura, "max_colores": op.max_colores,
                       "separacion": "segmentador" if usar_segmentador else "color"},
        "tiempos_s": {k: round(v, 2) for k, v in tiempos.items()},
        "variantes": [asdict(v) for v in resultado.variantes],
    }
    (carpeta / "info.json").write_text(json.dumps(resultado.info, indent=2, ensure_ascii=False) + "\n")
    return resultado


def main() -> None:
    import logging

    ap = argparse.ArgumentParser(prog="forja-generar", description="Boceto -> sprites pixel art.")
    ap.add_argument("boceto", type=Path, help="foto o dibujo del boceto")
    ap.add_argument("--preset", type=Path, help="carpeta del preset (por defecto, la del proyecto)")
    ap.add_argument("--proyecto", help="id del proyecto de juego (ver forja-proyectos listar)")
    ap.add_argument("--tipo", help="tipo de personaje del proyecto: monstruo, npc, arbol, heroe…")
    ap.add_argument("--prompt", default="",
                    help="descripción del personaje (opcional si se elige --tipo)")
    ap.add_argument("--variantes", type=int, default=4)
    ap.add_argument("--semilla", type=int)
    ap.add_argument("--modo", choices=list(gen.MODOS), default=gen.MODO_POR_DEFECTO,
                    help="calidad (por defecto, ~11 s/imagen) o rapido (borrador, ~2 s/imagen)")
    ap.add_argument("--parecido-boceto", type=float, help="0 libre … 1 calcado (por defecto 0.5)")
    ap.add_argument("--simplificar", type=float, default=0.3,
                    help="0-1: funde trazos repetidos del boceto en líneas únicas (0.3)")
    ap.add_argument("--grosor", type=float, default=0.006, help="grosor de línea (fracción del lado, 0.006)")
    ap.add_argument("--fuerza-estilo", type=float, help="0-1, cuánto imitar los ejemplos")
    ap.add_argument("--alto-figura", type=int)
    ap.add_argument("--colores", type=int, dest="max_colores")
    ap.add_argument("--ahorro-vram", action="store_true", help="más lento, usa menos VRAM")
    ap.add_argument("--motor", choices=MOTORES_IA, default="local", dest="motor_ia",
                    help="local (tu GPU), gemini (API externa de pago; clave en Ajustes o GEMINI_API_KEY) o "
                         "qwen (servidor Qwen-Image; dirección en Ajustes o QWEN_URL)")
    ap.add_argument("--modelo-gemini", help="modelo de imagen de Gemini (por defecto, el configurado)")
    ap.add_argument("--sin-segmentador", action="store_false", dest="segmentar",
                    help="separar figura y fondo por color en lugar de con el segmentador")
    ap.add_argument("--sin-traducir", action="store_false", dest="traducir",
                    help="no traducir la descripción al inglés aunque parezca español")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not args.preset and not args.proyecto:
        ap.error("indica --preset o --proyecto")
    proyecto = cargar_proyecto(args.proyecto) if args.proyecto else None

    info = gen.info_gpu() if args.motor_ia == "local" else {"gpu": True}
    if not info.get("gpu"):
        print(f"Aviso: {info.get('motivo')}. En CPU tardará varios minutos por imagen.")
    r = forjar(Image.open(args.boceto), args.preset, args.prompt, proyecto=proyecto, tipo=args.tipo,
               variantes=args.variantes,
               semilla=args.semilla, modo=args.modo, parecido_boceto=args.parecido_boceto,
               fuerza_estilo=args.fuerza_estilo, alto_figura=args.alto_figura, max_colores=args.max_colores,
               gestor=gen.GestorModelos(minutos_inactividad=0, ahorro_vram=args.ahorro_vram),
               traducir=args.traducir, segmentar=args.segmentar, motor_ia=args.motor_ia,
               modelo_gemini=args.modelo_gemini, simplificar=args.simplificar, grosor=args.grosor)
    if r.info["prompt_traducido"]:
        print(f"Descripción traducida: {r.info['prompt_traducido']}")
    print(f"\n{r.carpeta}")
    for v in r.variantes:
        print(f"  {v.sprite} (semilla {v.semilla}, {len(v.colores)} colores)")
    print("tiempos:", r.info["tiempos_s"])


if __name__ == "__main__":
    main()
