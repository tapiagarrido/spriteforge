"""Proyectos de juego: dirección de arte común a todos sus personajes.

Un proyecto fija lo que debe compartir todo lo que se genera para un mismo juego,
para que los personajes no parezcan de juegos distintos:

    - `preset`: estilo pixel art (tamaño, paleta exacta, contorno). La paleta la
      impone el pixelizador, así que es la garantía más fuerte de coherencia.
    - `ambientacion`: atmósfera que se añade al prompt de cada personaje
      ("dark gothic dungeon, grim, desaturated colors").
    - `negativo`: lo que no encaja en el juego ("bright, cheerful, cute").
    - `parecido_boceto`: cuánto se ajusta la IA a los bocetos (0 libre, 1 calcado).
    - `tipos`: plantillas de tipo de personaje propias del juego (p. ej. "Espectro
      rúnico"). Se suman a la biblioteca global de plantillas (`plantillas/tipos.json`,
      editable en Ajustes: monstruo, NPC, árbol…); si un tipo del proyecto tiene el
      mismo id que uno global, manda el del proyecto.
    - `notas`: texto libre (historia del mundo, ideas); no se envía a la IA.
    - `perspectiva`: forma del juego, que decide qué mapas se generan: "cenital"
      (movimiento en todas direcciones: suelos y transiciones) o "lateral"
      (plataformas: bloques de plataforma y fondo en capas).
    - `baldosa`: lado en píxeles de las baldosas del mapa (16, 32 o 48).
    - `pisos`: ambientes de mapa con nombre (p. ej. "Catacumbas"), cada uno con sus
      dos suelos `a` y `b` (en vista lateral: superficie y relleno) y, opcional, el
      `cielo`. En el taller de mapas se cargan con un clic. Opcional: `decorado` con la
      descripción de `roca`, `arbusto` y `arbol` de ese ambiente y la `forma_arbol`.

Todos los textos pueden escribirse en español: se traducen al inglés al generar.
Se guardan como JSON en `proyectos/<id>.json`.

Uso:
    python -m motor.proyectos listar
    python -m motor.proyectos ver mazmorra-oscura
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

from motor.modelos import RAIZ

CARPETA_PROYECTOS = RAIZ / "proyectos"
ARCHIVO_PLANTILLAS = RAIZ / "plantillas" / "tipos.json"
CAMPOS_TEXTO = {"nombre": 80, "preset": 80, "ambientacion": 1500, "negativo": 600, "notas": 6000}
MAX_TIPOS = 24
PERSPECTIVAS = ("cenital", "lateral")
FORMAS_ARBOL = ("copa", "seco", "hongo", "palmera")  # silueta del árbol del piso (ver motor/decorado.py)
FORMAS_ARBUSTO = ("mata", "espinoso", "hierba", "hongos")  # silueta del arbusto (por defecto, según el árbol)
MAX_PISOS = 24
BALDOSAS = (16, 32, 48)

# Plantillas de un juego clásico. El fragmento de cada tipo va justo antes de la
# descripción del personaje; el negativo se suma al del proyecto.
TIPOS_POR_DEFECTO = [
    {"id": "monstruo", "nombre": "Monstruo",
     "prompt": "una sola criatura monstruosa, cuerpo completo, vista de tres cuartos, amenazante",
     "negativo": ""},
    {"id": "jefe", "nombre": "Jefe",
     "prompt": "jefe final enorme e imponente, una sola criatura, cuerpo completo, vista de tres cuartos",
     "negativo": "pequeño, adorable"},
    {"id": "heroe", "nombre": "Héroe / humano",
     "prompt": "personaje humano aventurero, cuerpo completo, postura de combate, vista de tres cuartos",
     "negativo": "monstruo"},
    {"id": "npc", "nombre": "NPC (aldeano)",
     "prompt": "aldeano pacífico, personaje no jugable, cuerpo completo, de pie, vista frontal",
     "negativo": "arma, postura de combate, monstruo"},
    {"id": "animal", "nombre": "Animal",
     "prompt": "un solo animal, cuerpo completo, vista lateral",
     "negativo": "humano, ropa"},
    {"id": "arbol", "nombre": "Árbol / planta",
     "prompt": "un solo árbol o planta completa, sin personajes",
     "negativo": "personaje, criatura, cara, ojos"},
    {"id": "objeto", "nombre": "Objeto",
     "prompt": "un solo objeto, icono de inventario, centrado, sin personajes",
     "negativo": "personaje, criatura, manos"},
]


def plantillas() -> list[dict]:
    """Biblioteca global de plantillas de tipo (la de Ajustes); si no existe, la de serie."""
    if ARCHIVO_PLANTILLAS.exists():
        return json.loads(ARCHIVO_PLANTILLAS.read_text())
    return [dict(t) for t in TIPOS_POR_DEFECTO]


def guardar_plantillas(tipos) -> list[dict]:
    limpios = validar_tipos(tipos)
    ARCHIVO_PLANTILLAS.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVO_PLANTILLAS.write_text(json.dumps(limpios, indent=2, ensure_ascii=False) + "\n")
    return limpios


def tipos_de(proyecto: dict | None) -> list[dict]:
    """Tipos disponibles al generar: la biblioteca global más los del proyecto."""
    todos = {t["id"]: t for t in plantillas()}
    for t in (proyecto or {}).get("tipos") or []:
        todos[t["id"]] = t
    return list(todos.values())


def _propios(tipos: list[dict]) -> list[dict]:
    """Quita las copias sin cambios de las plantillas de serie (proyectos anteriores a
    la biblioteca global guardaban una copia completa)."""
    de_serie = [dict(t) for t in TIPOS_POR_DEFECTO]
    return [t for t in tipos if t not in de_serie]


def a_id(nombre: str) -> str:
    """'Mazmorra Oscura!' -> 'mazmorra-oscura'."""
    texto = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()
    return "-".join(re.findall(r"[a-z0-9]+", texto))[:60] or "proyecto"


def ruta(proyecto_id: str, carpeta: Path = CARPETA_PROYECTOS) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]*", proyecto_id or ""):
        raise ValueError(f"Identificador de proyecto inválido: {proyecto_id!r}")
    return Path(carpeta) / f"{proyecto_id}.json"


def cargar(proyecto_id: str, carpeta: Path = CARPETA_PROYECTOS) -> dict:
    archivo = ruta(proyecto_id, carpeta)
    if not archivo.exists():
        raise ValueError(f"Proyecto desconocido: {proyecto_id!r}")
    datos = json.loads(archivo.read_text())
    datos["id"] = proyecto_id
    datos["tipos"] = _propios(datos.get("tipos") or [])
    datos.setdefault("perspectiva", "cenital")
    datos.setdefault("baldosa", 32)
    datos.setdefault("pisos", [])
    return datos


def listar(carpeta: Path = CARPETA_PROYECTOS) -> list[dict]:
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        return []
    return [cargar(p.stem, carpeta) for p in sorted(carpeta.glob("*.json"))]


def validar(datos: dict) -> dict:
    """Normaliza y valida los campos de un proyecto (los desconocidos se ignoran)."""
    limpio = {}
    for campo, maximo in CAMPOS_TEXTO.items():
        valor = str(datos.get(campo, "") or "").strip()
        if len(valor) > maximo:
            raise ValueError(f"'{campo}' es demasiado largo (máx. {maximo} caracteres)")
        limpio[campo] = valor
    if not limpio["nombre"]:
        raise ValueError("El proyecto necesita un nombre")
    if not limpio["preset"]:
        raise ValueError("El proyecto necesita un preset de estilo")
    parecido = datos.get("parecido_boceto", 0.5)
    try:
        parecido = float(parecido)
    except (TypeError, ValueError):
        raise ValueError("parecido_boceto debe ser un número entre 0 y 1") from None
    if not 0 <= parecido <= 1:
        raise ValueError("parecido_boceto debe estar entre 0 y 1")
    limpio["parecido_boceto"] = parecido
    limpio["perspectiva"] = datos.get("perspectiva") or "cenital"
    if limpio["perspectiva"] not in PERSPECTIVAS:
        raise ValueError(f"Perspectiva desconocida: {limpio['perspectiva']!r} (cenital o lateral)")
    try:
        limpio["baldosa"] = int(datos.get("baldosa") or 32)
    except (TypeError, ValueError):
        raise ValueError("baldosa debe ser 16, 32 o 48") from None
    if limpio["baldosa"] not in BALDOSAS:
        raise ValueError("baldosa debe ser 16, 32 o 48")
    limpio["tipos"] = _propios(validar_tipos(datos.get("tipos") or []))
    limpio["pisos"] = validar_pisos(datos.get("pisos") or [])
    return limpio


def validar_tipos(tipos) -> list[dict]:
    if not isinstance(tipos, list):
        raise ValueError("'tipos' debe ser una lista")
    if len(tipos) > MAX_TIPOS:
        raise ValueError(f"Demasiados tipos de personaje (máx. {MAX_TIPOS})")
    limpios, vistos = [], set()
    for t in tipos:
        nombre = str(t.get("nombre", "") or "").strip()[:40]
        if not nombre:
            raise ValueError("Cada tipo de personaje necesita un nombre")
        tipo_id = a_id(str(t.get("id") or nombre))[:30]
        if tipo_id in vistos:
            raise ValueError(f"Tipo de personaje repetido: {nombre!r}")
        vistos.add(tipo_id)
        prompt = str(t.get("prompt", "") or "").strip()
        negativo = str(t.get("negativo", "") or "").strip()
        if len(prompt) > 300 or len(negativo) > 300:
            raise ValueError(f"El tipo {nombre!r} tiene un texto demasiado largo (máx. 300)")
        limpio = {"id": tipo_id, "nombre": nombre, "prompt": prompt, "negativo": negativo}
        if t.get("usar_ambientacion") is False:  # solo se guarda cuando se desactiva
            limpio["usar_ambientacion"] = False
        if t.get("parecido_boceto") not in (None, ""):  # propio del tipo (si no, el del proyecto)
            try:
                parecido = float(t["parecido_boceto"])
            except (TypeError, ValueError):
                raise ValueError(f"El parecido al boceto del tipo {nombre!r} debe ser un número") from None
            if not 0 <= parecido <= 1:
                raise ValueError(f"El parecido al boceto del tipo {nombre!r} debe estar entre 0 y 1")
            limpio["parecido_boceto"] = parecido
        limpios.append(limpio)
    return limpios


def validar_pisos(pisos) -> list[dict]:
    """Ambientes de mapa: nombre + dos suelos (a, b) y cielo opcional."""
    if not isinstance(pisos, list):
        raise ValueError("'pisos' debe ser una lista")
    if len(pisos) > MAX_PISOS:
        raise ValueError(f"Demasiados pisos (máx. {MAX_PISOS})")
    limpios, vistos = [], set()
    for p in pisos:
        nombre = str(p.get("nombre", "") or "").strip()[:40]
        if not nombre:
            raise ValueError("Cada piso necesita un nombre")
        piso_id = a_id(str(p.get("id") or nombre))[:30]
        if piso_id in vistos:
            raise ValueError(f"Piso repetido: {nombre!r}")
        vistos.add(piso_id)
        textos = {c: str(p.get(c, "") or "").strip() for c in ("a", "b", "cielo")}
        if any(len(v) > 200 for v in textos.values()):
            raise ValueError(f"El piso {nombre!r} tiene un texto demasiado largo (máx. 200)")
        if not textos["a"]:
            raise ValueError(f"El piso {nombre!r} necesita al menos un suelo")
        piso = {"id": piso_id, "nombre": nombre, **textos}
        deco = p.get("decorado")
        if deco:
            if not isinstance(deco, dict):
                raise ValueError(f"El decorado del piso {nombre!r} debe ser un objeto")
            limpio_deco = {c: str(deco.get(c, "") or "").strip() for c in ("roca", "arbusto", "arbol")}
            if any(len(v) > 200 for v in limpio_deco.values()):
                raise ValueError(f"El decorado del piso {nombre!r} tiene un texto demasiado largo (máx. 200)")
            forma = deco.get("forma_arbol") or "copa"
            if forma not in FORMAS_ARBOL:
                raise ValueError(f"Forma de árbol desconocida: {forma!r} ({', '.join(FORMAS_ARBOL)})")
            piso["decorado"] = {**{k: v for k, v in limpio_deco.items() if v}, "forma_arbol": forma}
            if deco.get("forma_arbusto"):
                if deco["forma_arbusto"] not in FORMAS_ARBUSTO:
                    raise ValueError(f"Forma de arbusto desconocida: {deco['forma_arbusto']!r} "
                                     f"({', '.join(FORMAS_ARBUSTO)})")
                piso["decorado"]["forma_arbusto"] = deco["forma_arbusto"]
        limpios.append(piso)
    return limpios


def tipo(proyecto: dict | None, tipo_id: str | None) -> dict | None:
    """Plantilla `tipo_id`: la del proyecto o, si no la tiene, la de la biblioteca global."""
    if not tipo_id:
        return None
    for t in tipos_de(proyecto):
        if t["id"] == tipo_id:
            return t
    raise ValueError(f"Tipo de personaje desconocido: {tipo_id!r}")


def guardar(datos: dict, carpeta: Path = CARPETA_PROYECTOS) -> dict:
    """Crea o actualiza un proyecto. Devuelve el proyecto guardado (con `id`)."""
    proyecto_id = datos.get("id") or a_id(str(datos.get("nombre", "")))
    archivo = ruta(proyecto_id, carpeta)
    if "pisos" not in datos and archivo.exists():
        # Quien no envía los pisos (p. ej. una versión anterior de la app) no los borra.
        datos = {**datos, "pisos": json.loads(archivo.read_text()).get("pisos", [])}
    limpio = validar(datos)
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps(limpio, indent=2, ensure_ascii=False) + "\n")
    return {**limpio, "id": proyecto_id}


def main() -> None:
    ap = argparse.ArgumentParser(prog="motor.proyectos")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listar")
    v = sub.add_parser("ver")
    v.add_argument("id")
    args = ap.parse_args()
    if args.cmd == "listar":
        for p in listar():
            print(f"{p['id']:24s} {p['nombre']}  [preset: {p['preset']}]")
    else:
        print(json.dumps(cargar(args.id), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
