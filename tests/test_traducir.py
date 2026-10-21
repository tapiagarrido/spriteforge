import pytest

from motor.modelos import CATALOGO
from motor.traducir import a_ingles, parece_espanol


@pytest.mark.parametrize("texto, esperado", [
    ("monstruo de aspecto grotesco hecho de hilos", True),
    ("caballero con armadura roja", True),
    ("mago anciano con túnica morada", True),
    ("knight with red armor and a sword", False),
    ("green orc warrior", False),
    ("", False),
])
def test_parece_espanol(texto, esperado):
    assert parece_espanol(texto) is esperado


def test_ingles_no_se_toca():
    assert a_ingles("knight with a sword") == ("knight with a sword", False)


@pytest.mark.skipif(not CATALOGO["traductor"].descargado(), reason="traductor no descargado")
def test_traduce_al_ingles():
    texto, traducido = a_ingles("caballero con armadura roja y una espada")
    assert traducido
    assert "knight" in texto.lower() and "red" in texto.lower()


def test_glosario_corrige_terminos_de_juego():
    from motor.traducir import _corregir

    assert _corregir("espectro con runas", "spectrum with runes") == "specter with runes"
    assert _corregir("un ejército de no muertos", "an army of not dead") == "an army of undead"
    assert _corregir("arcoíris", "spectrum of colors") == "spectrum of colors"  # sin el término, no se toca


@pytest.mark.skipif(not CATALOGO["traductor"].descargado(), reason="traductor no descargado")
def test_traduce_textos_largos_por_frases():
    texto, traducido = a_ingles("Un reino maldito. Las ruinas están cubiertas de niebla; la luz es verde.")
    assert traducido and texto.count(",") >= 2
    assert "fog" in texto.lower() or "mist" in texto.lower()


def test_listas_cortas_de_evitar_se_reconocen_como_espanol():
    """Sin tildes ni artículos, "aspecto tierno, adorable, humano, ropa" no se traducía y
    la IA recibía el negativo en español (lo ignoraba)."""
    from motor.traducir import parece_espanol

    for texto in ("aspecto tierno, adorable, humano, ropa", "personaje, criatura, manos, suelo",
                  "grotesco, repugnante, sangre, babas, colmillos, amenazante"):
        assert parece_espanol(texto), texto
    for texto in ("cute, small, human", "dark dungeon, grim", "character, creature, hands, ground"):
        assert not parece_espanol(texto), texto
