"""Traducción español -> inglés de las descripciones de personajes.

El codificador de texto de SDXL (CLIP) está entrenado casi solo en inglés: con
"monstruo grotesco hecho de hilos" dibuja un hombre genérico. Las descripciones
en español se traducen con un modelo pequeño y local (Helsinki-NLP opus-mt-es-en,
~300 MB, en CPU, < 1 s).

Uso:
    python -m motor.traducir "monstruo grotesco hecho de hilos descuidados"
"""

from __future__ import annotations

import functools
import re
import sys
import threading

from motor.modelos import CATALOGO

# Palabras muy frecuentes en español y raras en inglés.
_ESPANOL = {
    "de", "del", "la", "las", "el", "los", "un", "una", "unos", "unas", "y", "con", "sin",
    "que", "por", "para", "su", "sus", "muy", "como", "hecho", "hecha", "lleva", "tiene",
    "pelo", "ojos", "rojo", "roja", "azul", "verde", "negro", "negra", "blanco", "blanca",
    "armadura", "espada", "escudo", "capa", "mago", "guerrero", "monstruo", "caballero",
    # Vocabulario frecuente en plantillas y listas de "evitar" (cortas y sin tildes: sin
    # estas palabras, "aspecto tierno, adorable, humano, ropa" no se traducía).
    "aspecto", "tierno", "tierna", "humano", "humana", "ropa", "personaje", "personajes", "criatura",
    "criaturas", "manos", "suelo", "varios", "varias", "objeto", "objetos", "arma", "armas", "mascota",
    "grotesco", "grotesca", "repugnante", "sangre", "babas", "colmillos", "amenazante", "pequeño",
    "pequeña", "grande", "cuerpo", "completo", "completa", "vista", "lateral", "frontal", "fondo",
    "colores", "alegres", "dibujos", "animados", "brillante", "oscuro", "oscura", "rata", "fuego",
    "hielo", "piedra", "tierra", "pasto", "agua", "lava", "barro", "musgo", "hongos", "monedas",
}
_INGLES = {"the", "with", "and", "of", "a", "an", "in", "on", "wearing", "holding", "his", "her",
           "look", "cute", "human", "clothes", "character", "creature", "hands", "ground", "objects",
           "weapons", "pet", "blood", "fangs", "small", "big", "body", "full", "view", "single", "one",
           "colors", "background", "dark", "bright", "side", "front"}

# Términos de juegos que el traductor general confunde: (palabra en español,
# traducción errónea habitual, traducción correcta para un prompt).
GLOSARIO = [
    ("espectro", "spectrum", "specter"),
    ("espectros", "spectra", "specters"),
    ("gul", "gul", "ghoul"),
    ("liche", "liche", "lich"),
    ("trasgo", "trasgo", "goblin"),
    ("no muerto", "not dead", "undead"),
    ("no muertos", "not dead", "undead"),
    ("orbe", "orb", "orb"),
    ("aldeano", "villager", "villager"),
    ("mazmorra", "dungeon", "dungeon"),
    # Terrenos (mapas): el traductor general elige el sentido no geográfico.
    ("tierra", "land", "dirt"),
    ("tierra", "earth", "dirt"),
    ("pasto", "pasture", "grass"),
    ("césped", "lawn", "grass"),
    ("barro", "clay", "mud"),
    ("losas", "slabs", "flagstones"),
    ("adoquines", "pavers", "cobblestones"),
    # Personajes, objetos y efectos.
    ("capa raída", "shaved layer", "tattered cape"),
    ("estela", "stele", "trail"),
    ("botín", "booty", "loot"),
    ("lanzable", "launcher", "throwable"),
    ("erizado", "erect", "bristly"),
    ("decidida", "decided", "determined"),
    ("potenciador", "enhancer", "power-up"),
    # Decorados (rocas, arbustos, árboles).
    ("retorcido", "wrinkled", "twisted"),
    ("retorcida", "wrinkled", "twisted"),
    ("aislado", "insulated", "isolated"),
    ("aislada", "insulated", "isolated"),
    ("carbonizado", "charified", "charred"),
    ("carbonizada", "charified", "charred"),
    ("sombrero", "hat", "cap"),
    ("suelo", "soil", "ground"),
    ("centrado", "centering", "centered"),
    ("centrada", "centering", "centered"),
    ("tronco", "trunk", "trunk"),
]

_modelo = None
_lock = threading.Lock()


def parece_espanol(texto: str) -> bool:
    """Heurística: acentos/ñ o más palabras típicas del español que del inglés."""
    if re.search(r"[áéíóúñ¿¡]", texto.lower()):
        return True
    palabras = re.findall(r"[a-záéíóúñü]+", texto.lower())
    es = sum(p in _ESPANOL for p in palabras)
    en = sum(p in _INGLES for p in palabras)
    return es > en


def _cargar():
    global _modelo
    with _lock:
        if _modelo is None:
            from transformers import MarianMTModel, MarianTokenizer

            m = CATALOGO["traductor"]
            if not m.descargado():
                raise RuntimeError("Falta el traductor. Ejecuta: forja-modelos descargar traductor")
            _modelo = (MarianTokenizer.from_pretrained(m.carpeta), MarianMTModel.from_pretrained(m.carpeta))
        return _modelo


def _corregir(original: str, traduccion: str) -> str:
    """Aplica el GLOSARIO: si el original usa el término, se corrige su traducción."""
    bajo = original.lower()
    for es, mal, bien in GLOSARIO:
        if re.search(rf"\b{re.escape(es)}\b", bajo) and mal != bien:
            traduccion = re.sub(rf"\b{re.escape(mal)}\b", bien, traduccion, flags=re.IGNORECASE)
    return traduccion


@functools.lru_cache(maxsize=512)
def traducir(texto: str) -> str:
    """Traduce `texto` del español al inglés (en CPU). Las traducciones se guardan
    en memoria: la ambientación y las plantillas se repiten en cada generación."""
    import torch

    tokenizador, modelo = _cargar()
    with torch.no_grad():
        entrada = tokenizador([texto], return_tensors="pt", truncation=True)
        salida = modelo.generate(**entrada, max_new_tokens=120)
    return _corregir(texto, tokenizador.decode(salida[0], skip_special_tokens=True).strip())


def a_ingles(texto: str) -> tuple[str, bool]:
    """(texto en inglés, si se tradujo). Si no parece español, se devuelve tal cual.

    Los textos largos (la ambientación de un mundo) se traducen por frases: el
    modelo limita la longitud de cada traducción. El resultado se une con comas,
    que es como se separan las ideas en un prompt.
    """
    if not texto.strip() or not parece_espanol(texto):
        return texto, False
    frases = [f.strip() for f in re.split(r"[.;:\n]+", texto) if f.strip()]
    return ", ".join(traducir(f).rstrip(".") for f in frases), True


def main() -> None:
    texto = " ".join(sys.argv[1:])
    print(a_ingles(texto)[0])


if __name__ == "__main__":
    main()
