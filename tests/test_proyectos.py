import json

import pytest

from motor import proyectos
from motor.modelos import RAIZ


def test_proyectos_incluidos_son_validos():
    lista = proyectos.listar(RAIZ / "proyectos")
    assert {p["id"] for p in lista} >= {"mazmorra-oscura", "aventura-clasica"}
    for p in lista:
        proyectos.validar(p)
        assert (RAIZ / "presets" / p["preset"] / "estilo.json").exists()


def test_guardar_y_cargar(tmp_path):
    p = proyectos.guardar({"nombre": "Cripta Helada!", "preset": "heroes-mazmorra",
                           "ambientacion": "frozen crypt", "parecido_boceto": "0.7"}, tmp_path)
    assert p["id"] == "cripta-helada" and p["parecido_boceto"] == 0.7
    assert proyectos.cargar("cripta-helada", tmp_path)["ambientacion"] == "frozen crypt"
    # actualizar conserva el id aunque cambie el nombre
    proyectos.guardar({**p, "nombre": "Cripta"}, tmp_path)
    assert json.loads((tmp_path / "cripta-helada.json").read_text())["nombre"] == "Cripta"


@pytest.mark.parametrize("datos", [
    {"nombre": "", "preset": "x"},
    {"nombre": "a", "preset": ""},
    {"nombre": "a", "preset": "x", "parecido_boceto": 2},
    {"nombre": "a", "preset": "x", "ambientacion": "y" * 2000},
    {"nombre": "a", "preset": "x", "tipos": [{"nombre": ""}]},
    {"nombre": "a", "preset": "x", "tipos": [{"nombre": "Orco"}, {"nombre": "orco"}]},
])
def test_validar_rechaza(datos):
    with pytest.raises(ValueError):
        proyectos.validar(datos)


def test_ids_peligrosos(tmp_path):
    for malo in ["../etc", "a/b", "", "Mayus"]:
        with pytest.raises(ValueError):
            proyectos.ruta(malo, tmp_path)


def test_tipos_globales_y_del_proyecto(tmp_path):
    p = proyectos.guardar({"nombre": "Mazmorra oscura", "preset": "criaturas-64"}, tmp_path)
    assert p["tipos"] == []  # sin tipos propios: usa la biblioteca global
    assert {t["id"] for t in proyectos.tipos_de(p)} >= {"monstruo", "npc", "arbol", "heroe", "objeto"}
    espectro = {"nombre": "Espectro", "prompt": "fantasma translúcido"}
    p = proyectos.guardar({**p, "tipos": [espectro]}, tmp_path)
    assert p["tipos"] == [{"id": "espectro", **espectro, "negativo": ""}]
    assert proyectos.tipo(p, "espectro")["prompt"] == "fantasma translúcido"
    assert proyectos.tipo(p, "arbol")["nombre"] == "Árbol / planta"  # global
    assert proyectos.tipo(None, "arbol")["nombre"] == "Árbol / planta"
    with pytest.raises(ValueError):
        proyectos.tipo(p, "no-existe")


def test_biblioteca_global_editable(tmp_path):
    proyectos.guardar_plantillas([{"nombre": "Golem", "prompt": "golem de piedra"},
                                  {"nombre": "Monstruo", "prompt": "bestia enorme"}])
    assert [t["id"] for t in proyectos.plantillas()] == ["golem", "monstruo"]
    p = {"tipos": [{"id": "monstruo", "nombre": "Monstruo", "prompt": "monstruo del pantano",
                    "negativo": ""}]}
    assert proyectos.tipo(p, "monstruo")["prompt"] == "monstruo del pantano"  # manda el del proyecto
    assert proyectos.tipo(None, "monstruo")["prompt"] == "bestia enorme"
    with pytest.raises(ValueError):
        proyectos.tipo(None, "npc")  # ya no está en la biblioteca


def test_proyecto_antiguo_sin_copias_de_serie(tmp_path):
    """Los proyectos anteriores guardaban una copia de las plantillas de serie: se quita
    (las da la biblioteca global) y se conservan las propias."""
    propio = {"id": "espectro", "nombre": "Espectro", "prompt": "fantasma", "negativo": ""}
    (tmp_path / "viejo.json").write_text(json.dumps(
        {"nombre": "Viejo", "preset": "x", "tipos": [*proyectos.TIPOS_POR_DEFECTO, propio]}))
    assert proyectos.cargar("viejo", tmp_path)["tipos"] == [propio]
    (tmp_path / "mas-viejo.json").write_text(json.dumps({"nombre": "Más viejo", "preset": "x"}))
    assert proyectos.cargar("mas-viejo", tmp_path)["tipos"] == []


def test_pisos_del_proyecto(tmp_path):
    pisos = [{"nombre": "Catacumbas", "a": "losas con huesos", "b": "tierra"},
             {"nombre": "Forja de lava", "a": "roca volcánica", "b": "lava", "cielo": ""}]
    p = proyectos.guardar({"nombre": "Abismo", "preset": "x", "pisos": pisos}, tmp_path)
    assert [x["id"] for x in p["pisos"]] == ["catacumbas", "forja-de-lava"]
    assert proyectos.cargar(p["id"], tmp_path)["pisos"][1]["b"] == "lava"
    # Guardar sin enviar los pisos no los borra.
    sin = {k: v for k, v in p.items() if k != "pisos"}
    assert len(proyectos.guardar({**sin, "notas": "otra"}, tmp_path)["pisos"]) == 2
    for malo in ([{"nombre": "", "a": "x"}], [{"nombre": "Sin suelo"}],
                 [{"nombre": "A", "a": "x"}, {"nombre": "a", "a": "y"}]):
        with pytest.raises(ValueError):
            proyectos.guardar({"nombre": "Abismo", "preset": "x", "pisos": malo}, tmp_path)


def test_tipo_puede_desactivar_la_ambientacion(tmp_path):
    tipos = [{"nombre": "Fuego", "prompt": "bola de fuego", "usar_ambientacion": False},
             {"nombre": "Rata", "prompt": "rata", "usar_ambientacion": True}]
    p = proyectos.guardar({"nombre": "X", "preset": "x", "tipos": tipos}, tmp_path)
    assert p["tipos"][0]["usar_ambientacion"] is False and "usar_ambientacion" not in p["tipos"][1]


def test_tipo_con_parecido_propio(tmp_path):
    tipos = [{"nombre": "Fuego", "prompt": "bola de fuego", "parecido_boceto": 0.15},
             {"nombre": "Rata", "prompt": "rata", "parecido_boceto": None}]
    p = proyectos.guardar({"nombre": "X", "preset": "x", "tipos": tipos}, tmp_path)
    assert p["tipos"][0]["parecido_boceto"] == 0.15 and "parecido_boceto" not in p["tipos"][1]
    with pytest.raises(ValueError):
        proyectos.guardar({"nombre": "X", "preset": "x",
                           "tipos": [{"nombre": "Mal", "prompt": "x", "parecido_boceto": 2}]}, tmp_path)
