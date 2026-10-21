import base64
import io
import json
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pytest
from PIL import Image

from motor.modelos import CATALOGO
from motor.servidor import CARPETA_PRESETS, Motor, crear_servidor
from tests.test_mapas import generador_falso as texturas_falsas


def claves_falsas(base, accion, variantes, semilla, boceto_pose, texto, identidad=None):
    """Sustituto de la IA de poses: el sprite base volteado, con el tamaño correcto."""
    sprite = base.sprite.transpose(Image.FLIP_LEFT_RIGHT)
    return [(base.ia, sprite) for _ in range(variantes)]


def vistas_falsas(base, vista, semilla, boceto, identidad, texto=None):
    """Sustituto de la IA de vistas: la variante (de espaldas, más oscura para distinguirla)."""
    sprite = base.sprite.copy()
    if vista == "arriba":
        a = np.array(sprite)
        a[..., :3] //= 2
        sprite = Image.fromarray(a)
    return base.ia, sprite


def rediseno_falso(base, cambio, semilla):
    """Sustituto de Gemini: la misma variante con otro color (y recuerda el cambio pedido)."""
    rediseno_falso.pedidos.append(cambio)
    a = np.array(base.sprite)
    a[..., 2] = np.minimum(255, a[..., 2].astype(int) + 90)
    return base.ia, Image.fromarray(a)


rediseno_falso.pedidos = []


@pytest.fixture
def api(tmp_path, generador_falso):
    (tmp_path / "proyectos").mkdir()
    (tmp_path / "proyectos" / "oscuro.json").write_text(json.dumps(
        {"nombre": "Oscuro", "preset": "heroes-mazmorra", "ambientacion": "dark dungeon"}))
    shutil.copytree(CARPETA_PRESETS, tmp_path / "presets")  # los tests pueden crear y borrar presets
    motor = Motor(carpeta_presets=tmp_path / "presets", carpeta_salidas=tmp_path / "salidas",
                  generador=generador_falso, minutos_inactividad=0,
                  carpeta_proyectos=tmp_path / "proyectos", segmentar=False, generador_claves=claves_falsas,
                  generador_texturas=texturas_falsas,
                  generador_vistas=lambda *a, **k: vistas_falsas(*a, **k),  # al llamar: parcheable
                  generador_escenas=lambda prompt, an, al, s: Image.new("RGB", (an, al), (40, 36, 40)),
                  generador_rediseno=rediseno_falso)
    servidor = crear_servidor(motor, puerto=0)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{servidor.server_address[1]}"

    def llamar(ruta, datos=None, cabeceras=None, origen=None):
        propias = cabeceras if cabeceras is not None else {"X-Forja": "1"}
        cab = {"Content-Type": "application/json", **propias}
        if origen:
            cab["Origin"] = origen
        req = urllib.request.Request(base + ruta, data=None if datos is None else json.dumps(datos).encode(),
                                     headers=cab, method="GET" if datos is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                cuerpo = r.read()
                tipo = r.headers.get("Content-Type", "")
                return r.status, (json.loads(cuerpo) if "json" in tipo else cuerpo), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), e.headers

    yield llamar
    servidor.shutdown()


def data_url(img: Image.Image) -> str:
    b = io.BytesIO()
    img.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def esperar(api, trabajo, limite=20):
    for _ in range(limite * 10):
        _, t, _ = api(f"/api/trabajos/{trabajo}")
        if t["estado"] in ("listo", "error"):
            return t
        time.sleep(0.1)
    raise TimeoutError


def test_estado_y_presets(api):
    codigo, estado, _ = api("/api/estado")
    assert codigo == 200 and "gpu" in estado and len(estado["modelos"]) == len(CATALOGO)
    codigo, presets, _ = api("/api/presets")
    assert codigo == 200 and {"heroes-mazmorra", "criaturas-64"} <= {p["id"] for p in presets}
    codigo, png, _ = api(presets[0]["vista_previa"])
    assert codigo == 200 and png[:4] == b"\x89PNG"


def test_flujo_generar_repixelizar_guardar(api, boceto_digital):
    codigo, r, _ = api("/api/generar", {"boceto": data_url(boceto_digital), "preset": "heroes-mazmorra",
                                         "prompt": "knight", "variantes": 2, "semilla": 3})
    assert codigo == 202
    t = esperar(api, r["trabajo"])
    assert t["estado"] == "listo", t["error"]
    res = t["resultado"]
    assert len(res["variantes"]) == 2

    codigo, png, _ = api(res["variantes"][0]["sprite"])
    assert codigo == 200 and Image.open(io.BytesIO(png)).size == (16, 28)

    codigo, rep, _ = api("/api/pixelizar", {"carpeta": res["carpeta"], "indice": 1, "colores": 3})
    assert codigo == 200 and len(rep["variantes"][0]["colores"]) <= 3

    retocado = Image.new("RGBA", (16, 28), (255, 0, 0, 255))
    codigo, g, _ = api("/api/guardar", {"carpeta": res["carpeta"], "indice": 1, "sprite": data_url(retocado)})
    assert codigo == 200 and g["ruta"].endswith("final_01.png")


def test_validaciones(api, boceto_digital):
    b = data_url(boceto_digital)
    assert api("/api/generar", {"boceto": b, "preset": "heroes-mazmorra", "prompt": ""})[0] == 400
    assert api("/api/generar", {"boceto": b, "preset": "../etc", "prompt": "x"})[0] == 400
    assert api("/api/generar", {"boceto": b, "preset": "heroes-mazmorra", "prompt": "x",
                                "variantes": 50})[0] == 400
    assert api("/api/trabajos/999")[0] == 404


def test_seguridad(api):
    # POST sin la cabecera propia: rechazado (una web cualquiera no puede enviarla sin preflight).
    assert api("/api/liberar", {}, cabeceras={})[0] == 403
    # Preflight desde un origen ajeno: rechazado.
    assert api("/api/salidas/../../etc/passwd")[0] in (400, 404)
    codigo, _, cab = api("/api/estado", origen="https://malicioso.example")
    assert codigo == 200 and cab.get("Access-Control-Allow-Origin") is None
    _, _, cab = api("/api/estado", origen="tauri://localhost")
    assert cab.get("Access-Control-Allow-Origin") == "tauri://localhost"


def test_error_de_la_ia_se_informa(tmp_path, boceto_digital):
    def ia_rota(_):
        raise RuntimeError("sin VRAM")

    motor = Motor(carpeta_salidas=tmp_path, generador=ia_rota, minutos_inactividad=0, segmentar=False)
    t = motor.encolar({"boceto": data_url(boceto_digital), "preset": "heroes-mazmorra", "prompt": "x"})
    for _ in range(100):
        if motor.trabajo(t.id)["estado"] == "error":
            break
        time.sleep(0.05)
    assert "sin VRAM" in motor.trabajo(t.id)["error"]


def test_proyectos_api(api, boceto_digital):
    codigo, lista, _ = api("/api/proyectos")
    assert codigo == 200 and [p["id"] for p in lista] == ["oscuro"]
    codigo, p, _ = api("/api/proyectos", {"nombre": "Nuevo juego", "preset": "heroes-mazmorra",
                                         "ambientacion": "sunny meadow"})
    assert codigo == 200 and p["id"] == "nuevo-juego"
    assert api("/api/proyectos", {"nombre": "x", "preset": "no-existe"})[0] == 400

    codigo, r, _ = api("/api/generar", {"boceto": data_url(boceto_digital), "proyecto": "oscuro",
                                         "prompt": "knight", "variantes": 1})
    assert codigo == 202
    t = esperar(api, r["trabajo"])
    assert t["estado"] == "listo", t["error"]
    assert "dark dungeon" in t["resultado"]["info"]["prompt"]


def test_config_gemini_no_filtra_la_clave(api, monkeypatch):
    from motor import gemini

    clave = "AIzaFAKE-clave-de-prueba-1234567890wxyz"
    codigo, estado, _ = api("/api/config")
    assert codigo == 200 and estado["gemini"]["configurada"] is False

    codigo, r, _ = api("/api/config", {"gemini_api_key": clave, "gemini_modelo": "m-image"})
    assert codigo == 200 and r["gemini"]["terminacion"] == "wxyz"
    for ruta in ("/api/config", "/api/estado"):
        _, cuerpo, _ = api(ruta)
        assert clave not in str(cuerpo)

    monkeypatch.setattr(gemini, "listar_modelos", lambda k: ["m-image"] if k == clave else [])
    codigo, r, _ = api("/api/config/probar-gemini", {})
    assert codigo == 200 and r["modelos"] == ["m-image"]

    assert api("/api/config", {"gemini_api_key": "corta"})[0] == 400
    codigo, r, _ = api("/api/config", {"gemini_api_key": ""})
    assert r["gemini"]["configurada"] is False


def test_generar_con_gemini_sin_clave_falla(api, boceto_digital):
    codigo, r, _ = api("/api/generar", {"boceto": data_url(boceto_digital), "preset": "heroes-mazmorra",
                                         "prompt": "x", "motor_ia": "gemini"})
    assert codigo == 400 and "Ajustes" in r["error"]


def test_generar_con_gemini(api, boceto_digital, monkeypatch, generador_falso):
    from motor import config, gemini

    config.guardar({"gemini_api_key": "AIzaFAKE-clave-de-prueba-1234567890wxyz", "gemini_modelo": "m"})
    monkeypatch.setattr(gemini, "generar", lambda p, clave, modelo: generador_falso(p))
    codigo, r, _ = api("/api/generar", {"boceto": data_url(boceto_digital), "preset": "heroes-mazmorra",
                                         "prompt": "knight", "motor_ia": "gemini", "variantes": 1})
    t = esperar(api, r["trabajo"])
    assert t["estado"] == "listo", t["error"]
    assert t["resultado"]["info"]["motor_ia"] == "gemini:m"


def test_config_qwen_url_y_prueba(api, monkeypatch):
    from motor import qwen

    codigo, estado, _ = api("/api/config")
    assert codigo == 200 and estado["qwen"] == {"url": qwen.URL_POR_DEFECTO, "desde_entorno": False}
    codigo, r, _ = api("/api/config", {"qwen_url": "http://10.0.0.5:1234/"})
    assert codigo == 200 and r["qwen"]["url"] == "http://10.0.0.5:1234"
    assert api("/api/config", {"qwen_url": "ftp://x"})[0] == 400

    vistos = []
    monkeypatch.setattr(qwen, "estado", lambda url: vistos.append(url) or {"sd": True, "llm": False})
    codigo, r, _ = api("/api/config/probar-qwen", {})
    assert codigo == 200 and r["ok"] and not r["traductor"] and vistos == ["http://10.0.0.5:1234"]

    def caido(url):
        raise qwen.ErrorQwen("No se pudo conectar con Qwen")

    monkeypatch.setattr(qwen, "estado", caido)
    codigo, r, _ = api("/api/config/probar-qwen", {})
    assert codigo == 400 and "Qwen" in r["error"]
    codigo, r, _ = api("/api/config", {"qwen_url": ""})
    assert r["qwen"]["url"] == qwen.URL_POR_DEFECTO


def test_generar_con_qwen(api, boceto_digital, monkeypatch, generador_falso):
    from motor import qwen

    urls = []
    monkeypatch.setattr(qwen, "generar", lambda p, url, original=None: urls.append(url) or generador_falso(p))
    codigo, r, _ = api("/api/generar", {"boceto": data_url(boceto_digital), "preset": "heroes-mazmorra",
                                         "prompt": "knight", "motor_ia": "qwen", "variantes": 1})
    assert codigo == 202  # no exige ninguna clave
    t = esperar(api, r["trabajo"])
    assert t["estado"] == "listo", t["error"]
    assert t["resultado"]["info"]["motor_ia"] == "qwen" and urls == [qwen.URL_POR_DEFECTO]


def test_motor_desconocido_se_rechaza(api, boceto_digital):
    codigo, r, _ = api("/api/generar", {"boceto": data_url(boceto_digital), "preset": "heroes-mazmorra",
                                         "prompt": "x", "motor_ia": "otro"})
    assert codigo == 400 and "desconocido" in r["error"]


def generar_personaje(api, boceto, **extra):
    _, r, _ = api("/api/generar", {"boceto": data_url(boceto), "preset": "heroes-mazmorra",
                                    "prompt": "knight", "variantes": 1, **extra})
    t = esperar(api, r["trabajo"])
    assert t["estado"] == "listo", t["error"]
    return t["resultado"]["carpeta"]


def animar(api, carpeta, **datos):
    codigo, r, _ = api("/api/animar", {"carpeta": carpeta, "indice": 1, **datos})
    assert codigo == 202, r
    t = esperar(api, r["trabajo"])
    assert t["estado"] == "listo", t["error"]
    return t["resultado"]


def test_flujo_animacion_una_a_una(api, boceto_digital):
    carpeta = generar_personaje(api, boceto_digital)

    r = animar(api, carpeta, animacion="caminar")
    assert [a["nombre"] for a in r["animaciones"]] == ["caminar"] and r["recien"] == "caminar"
    caminar = r["animaciones"][0]
    assert len(caminar["frames_url"]) == caminar["fotogramas"] == 8  # la secuencia completa
    codigo, png, _ = api(caminar["frames_url"][3])
    assert codigo == 200 and png[:4] == b"\x89PNG"

    # Ataque con golpe dibujado por la IA (simulada): se añade a la hoja, no la sustituye.
    r = animar(api, carpeta, animacion="ataque", golpe="ia", identidad=0.6)
    assert [a["nombre"] for a in r["animaciones"]] == ["caminar", "ataque"]
    ataque = r["animaciones"][1]
    assert ataque["golpe"] == "ia" and ataque["clave_url"]
    codigo, meta, _ = api(r["json_url"])
    assert codigo == 200 and [t["name"] for t in meta["meta"]["frameTags"]] == ["caminar", "ataque"]

    # Regenerar el ataque lo reemplaza; reabrir lista lo guardado; borrar lo quita.
    r = animar(api, carpeta, animacion="ataque")
    assert [a["nombre"] for a in r["animaciones"]] == ["caminar", "ataque"]
    assert r["animaciones"][1]["golpe"] == "deformacion"
    _, guardadas, _ = api(f"/api/animaciones/{carpeta}/1")
    assert len(guardadas["animaciones"]) == 2
    _, r, _ = api("/api/animaciones/borrar", {"carpeta": carpeta, "indice": 1, "animacion": "caminar"})
    assert [a["nombre"] for a in r["animaciones"]] == ["ataque"]


def test_animacion_valida_entradas(api, boceto_digital):
    carpeta = generar_personaje(api, boceto_digital)

    def pedir(**datos):
        return api("/api/animar", {"carpeta": carpeta, "indice": 1, **datos})[0]

    assert pedir(animacion="bailar") == 400
    assert pedir(animacion="reposo", golpe="ia") == 400  # solo los ataques admiten pose
    assert pedir(animacion="ataque", golpe="dibujo") == 400  # falta el dibujo
    assert pedir(animacion="especial", golpe="ia") == 400  # falta la descripción
    assert pedir(animacion="ataque", golpe="magia") == 400
    assert api("/api/animar", {"carpeta": "../etc", "indice": 1, "animacion": "reposo"})[0] == 400
    assert api("/api/animar", {"carpeta": carpeta, "indice": 9, "animacion": "reposo"})[0] == 400
    assert api(f"/api/salidas/{carpeta}/../../etc/passwd")[0] in (400, 403, 404)


def test_personajes_del_proyecto_y_reabrir(api, boceto_digital):
    carpeta = generar_personaje(api, boceto_digital, proyecto="oscuro", tipo="npc", preset="")
    generar_personaje(api, boceto_digital)  # sin proyecto: no aparece
    animar(api, carpeta, animacion="reposo")
    codigo, lista, _ = api("/api/proyectos/oscuro/personajes")
    assert codigo == 200 and [p["carpeta"] for p in lista] == [carpeta]
    assert lista[0]["tipo"] == "NPC (aldeano)" and lista[0]["animaciones"] == ["reposo"]
    codigo, res, _ = api(f"/api/resultados/{carpeta}")
    assert codigo == 200 and res["carpeta"] == carpeta and len(res["variantes"]) == 1
    assert api("/api/proyectos/no-existe/personajes")[0] == 400


def test_descripcion_opcional_con_tipo(api, boceto_digital):
    """Con un tipo de personaje su plantilla basta; sin tipo, la descripción es obligatoria."""
    b = data_url(boceto_digital)
    codigo, r, _ = api("/api/generar", {"boceto": b, "preset": "heroes-mazmorra", "prompt": " "})
    assert codigo == 400 and "tipo" in r["error"]
    carpeta = generar_personaje(api, boceto_digital, proyecto="oscuro", tipo="npc", prompt="")
    assert carpeta.endswith("_npc-aldeano")
    _, r, _ = api(f"/api/resultados/{carpeta}")
    assert r["info"]["tipo"]["id"] == "npc"


def test_galeria_abre_la_variante_animada(api, boceto_digital):
    """Si se animó la variante 2, la galería muestra y abre esa (no la 1)."""
    carpeta = generar_personaje(api, boceto_digital, proyecto="oscuro", variantes=2)
    _, lista, _ = api("/api/proyectos/oscuro/personajes")
    assert lista[0]["indice"] == 1 and lista[0]["sprite_url"].endswith("sprite_01.png")
    animar(api, carpeta, animacion="caminar", indice=2)
    _, lista, _ = api("/api/proyectos/oscuro/personajes")
    assert lista[0]["indice"] == 2 and lista[0]["animaciones"] == ["caminar"]
    assert lista[0]["sprite_url"].endswith("sprite_02.png")


def test_biblioteca_de_plantillas(api):
    codigo, tipos, _ = api("/api/tipos")
    assert codigo == 200 and "monstruo" in [t["id"] for t in tipos]
    golem = {"nombre": "Gólem", "prompt": "gólem de piedra con runas"}
    codigo, r, _ = api("/api/tipos", {"tipos": [*tipos, golem]})
    assert codigo == 200 and r[-1]["id"] == "golem"
    assert api("/api/tipos")[1][-1]["prompt"] == "gólem de piedra con runas"
    # Disponible al generar en cualquier proyecto, aunque el proyecto no lo tenga.
    _, r, _ = api("/api/prompt", {"proyecto": "oscuro", "tipo": "golem", "prompt": ""})
    assert "golem" in r["positivo"].lower() or "gólem" in r["positivo"].lower()
    assert api("/api/tipos", {"tipos": [{"nombre": ""}]})[0] == 400


def test_crear_editar_y_borrar_preset(api, preset_heroes):
    ejemplos = [data_url(Image.open(preset_heroes / f)) for f in
                ("ejemplo1_caballero.png", "ejemplo2_elfa.png", "ejemplo3_mago.png")]
    codigo, p, _ = api("/api/presets", {"nombre": "Mis héroes", "ejemplos": ejemplos,
                                        "estilo": "pixel art, 16-bit hero sprite"})
    assert codigo == 200 and p["id"] == "mis-heroes" and p["creado_en_app"]
    assert p["generacion"]["prompt_base"] == "pixel art, 16-bit hero sprite"
    assert p["sprite"]["ancho"] == 16 and api(p["vista_previa"])[0] == 200
    cambios = {"nombre": "Héroes míos", "prompt_base": "pixel art, knight"}
    codigo, p, _ = api("/api/presets/mis-heroes", cambios)
    assert codigo == 200 and p["nombre"] == cambios["nombre"]
    assert p["generacion"]["prompt_base"] == cambios["prompt_base"]
    # En uso por un proyecto: no se borra.
    api("/api/proyectos", {"id": "usa", "nombre": "Usa", "preset": "mis-heroes"})
    codigo, r, _ = api("/api/presets/mis-heroes/borrar", {})
    assert codigo == 400 and "Usa" in r["error"]
    api("/api/proyectos", {"id": "usa", "nombre": "Usa", "preset": "heroes-mazmorra"})
    assert api("/api/presets/mis-heroes/borrar", {})[0] == 200
    assert "mis-heroes" not in [x["id"] for x in api("/api/presets")[1]]
    assert api("/api/presets/heroes-mazmorra/borrar", {})[0] == 400  # incluido con la app


def test_preset_con_ejemplos_no_validos(api):
    opaco = data_url(Image.new("RGBA", (16, 16), (200, 0, 0, 255)))
    codigo, r, _ = api("/api/presets", {"nombre": "Malo", "ejemplos": [opaco, opaco]})
    assert codigo == 400 and "transparente" in r["error"]
    assert api("/api/presets", {"nombre": "Pocos", "ejemplos": [opaco]})[0] == 400
    assert api("/api/presets", {"nombre": "", "ejemplos": []})[0] == 400


def vista(api, carpeta, vista, modo="ia"):
    codigo, r, _ = api("/api/direcciones", {"carpeta": carpeta, "indice": 1, "vista": vista, "modo": modo})
    assert codigo == 202, r
    t = esperar(api, r["trabajo"])
    assert t["estado"] == "listo", t["error"]
    return t["resultado"]


def test_direcciones_y_animaciones_por_direccion(api, boceto_digital):
    carpeta = generar_personaje(api, boceto_digital)
    # Animar una dirección sin su vista: error claro.
    codigo, r, _ = api("/api/animar", {"carpeta": carpeta, "indice": 1, "animacion": "caminar",
                                       "direccion": "abajo"})
    assert codigo == 400 and "vista" in r["error"]
    vista(api, carpeta, "abajo")
    vista(api, carpeta, "arriba")
    r = vista(api, carpeta, "lado", modo="variante")
    assert set(r["vistas"]) == {"abajo", "arriba", "lado"} and r["vistas"]["lado"]["modo"] == "variante"
    assert api(r["vistas"]["arriba"]["url"])[0] == 200
    assert api(f"/api/direcciones/{carpeta}/1")[1]["vistas"].keys() == r["vistas"].keys()

    animar(api, carpeta, animacion="caminar", direccion="abajo")
    r = animar(api, carpeta, animacion="ataque", direccion="derecha")
    nombres = [a["nombre"] for a in r["animaciones"]]
    assert nombres == ["caminar_abajo", "ataque_derecha", "ataque_izquierda"]  # orden por dirección
    der = next(a for a in r["animaciones"] if a["nombre"] == "ataque_derecha")
    izq = next(a for a in r["animaciones"] if a["nombre"] == "ataque_izquierda")
    f_der, f_izq = api(der["frames_url"][3])[1], api(izq["frames_url"][3])[1]
    a = np.array(Image.open(io.BytesIO(f_der)))
    b = np.array(Image.open(io.BytesIO(f_izq)))
    assert np.array_equal(a[:, ::-1], b)  # la izquierda es el espejo exacto
    # Borrar una de las dos borra su espejo.
    datos = {"carpeta": carpeta, "indice": 1, "animacion": "ataque_izquierda"}
    _, r, _ = api("/api/animaciones/borrar", datos)
    assert [a["nombre"] for a in r["animaciones"]] == ["caminar_abajo"]
    assert api("/api/direcciones", {"carpeta": carpeta, "indice": 1, "vista": "diagonal"})[0] == 400


def test_recolorear_y_redisenar_crean_variantes_nuevas(api, boceto_digital):
    carpeta = generar_personaje(api, boceto_digital)
    # Vista previa: no crea nada.
    codigo, r, _ = api("/api/recolorear", {"carpeta": carpeta, "indice": 1, "objetivo": "#3a6fd8"})
    assert codigo == 200 and r["imagen"].startswith("data:image/png;base64,") and r["colores"]
    assert api(f"/api/resultados/{carpeta}")[1]["variantes"][0]["origen"] is None
    assert len(api(f"/api/resultados/{carpeta}")[1]["variantes"]) == 1
    # Guardar: variante nueva con origen y nombre; hereda todo (aquí, una animación).
    codigo, r, _ = api("/api/recolorear", {"carpeta": carpeta, "indice": 1, "objetivo": "#3a6fd8",
                                           "guardar": True, "nombre": "Azul"})
    assert codigo == 200 and r["nueva"] == 2
    nueva = r["variantes"][1]
    assert nueva["nombre"] == "Azul"
    assert (nueva["origen"]["tipo"], nueva["origen"]["variante"]) == ("color", 1)
    assert api(nueva["sprite"])[0] == 200 and api(nueva["ia"])[0] == 200
    animar(api, carpeta, animacion="reposo", indice=2)
    # Rediseño con elementos (el generador falso hace de Gemini); se traduce el texto.
    codigo, r, _ = api("/api/redisenar", {"carpeta": carpeta, "indice": 2,
                                          "cambio": "con un casco de hierro", "nombre": "Casco"})
    assert codigo == 202
    t = esperar(api, r["trabajo"])
    assert t["estado"] == "listo", t["error"]
    assert t["resultado"]["nueva"] == 3 and t["resultado"]["variantes"][2]["origen"]["variante"] == 2
    pedido = rediseno_falso.pedidos[-1].lower()
    assert "helmet" in pedido or "casco" in pedido  # "casco" si el traductor no está descargado (CI)
    # Errores claros.
    for datos, texto in (({"cambio": ""}, "Describe"), ({"cambio": "x", "motor_ia": "local"}, "Gemini"),
                         ({"cambio": "x" * 400}, "larga")):
        codigo, r, _ = api("/api/redisenar", {"carpeta": carpeta, "indice": 1, **datos})
        assert codigo == 400 and texto in r["error"]
    assert api("/api/recolorear", {"carpeta": carpeta, "indice": 1, "objetivo": "azul"})[0] == 400
    assert api("/api/recolorear", {"carpeta": carpeta, "indice": 1, "tono": 999})[0] == 400


def test_mapas_segun_la_perspectiva(api):
    api("/api/proyectos", {"id": "plataformas", "nombre": "Plataformas", "preset": "heroes-mazmorra",
                           "perspectiva": "lateral", "baldosa": 16})
    codigo, r, _ = api("/api/mapas", {"proyecto": "plataformas", "traducir": False, "variantes": 2,
                                      "materiales": {"superficie": "grass", "relleno": "dirt"}})
    assert codigo == 202
    t = esperar(api, r["trabajo"])
    assert t["estado"] == "listo", t["error"]
    m = t["resultado"]
    assert m["perspectiva"] == "lateral" and m["baldosa"] == 16
    for clave in ("tileset", "tsx", "fondo_cielo", "fondo_lejos", "fondo_cerca", "mapa_prueba"):
        assert api(m["urls"][clave])[0] == 200, clave
    # Cenital (el proyecto "oscuro" no indica perspectiva: cenital por defecto).
    codigo, r, _ = api("/api/mapas", {"proyecto": "oscuro", "traducir": False, "variantes": 2,
                                      "materiales": {"a": "grass", "b": "dirt"}})
    t = esperar(api, r["trabajo"])
    assert t["estado"] == "listo", t["error"]
    assert t["resultado"]["perspectiva"] == "cenital" and t["resultado"]["baldosa"] == 32
    lista = api("/api/proyectos/plataformas/mapas")[1]
    assert [x["carpeta"] for x in lista] == [m["carpeta"]]
    # Errores: proyecto desconocido o materiales que faltan (falla el trabajo, con mensaje).
    assert api("/api/mapas", {"proyecto": "no-existe"})[0] == 400
    _, r, _ = api("/api/mapas", {"proyecto": "oscuro", "materiales": {"a": "grass"}, "traducir": False})
    t = esperar(api, r["trabajo"])
    assert t["estado"] == "error" and "segundo suelo" in t["error"]


def test_previsualizar_prompt(api):
    codigo, r, _ = api("/api/prompt", {"proyecto": "oscuro", "tipo": "arbol", "prompt": "old oak"})
    assert codigo == 200
    assert r["positivo"].startswith("pixel art, plain white background, ")
    assert "old oak" in r["positivo"] and "dark dungeon" in r["positivo"]
    assert r["tokens"] <= r["limite"] == 77
    # Ambientación aún sin guardar (editor del proyecto) y demasiado larga: se avisa de lo que no cabe.
    larga = ", ".join(f"detail number {i}" for i in range(40))
    _, r, _ = api("/api/prompt", {"proyecto": "oscuro", "prompt": "orc", "ambientacion": larga})
    assert r["ambientacion_fuera"] and r["tokens"] <= 77
    assert api("/api/prompt", {"proyecto": "oscuro", "tipo": "nada", "prompt": "x"})[0] == 400


def test_previsualizar_boceto(api, boceto_foto):
    codigo, r, _ = api("/api/boceto", {"boceto": data_url(boceto_foto), "preset": "heroes-mazmorra",
                                        "simplificar": 0.3, "grosor": 0.006})
    assert codigo == 200 and r["imagen"].startswith("data:image/png;base64,")
    img = Image.open(io.BytesIO(base64.b64decode(r["imagen"].split(",", 1)[1])))
    assert img.size == (r["ancho"], r["alto"]) and img.mode == "L"
    assert api("/api/boceto", {"boceto": data_url(boceto_foto), "preset": "heroes-mazmorra",
                               "simplificar": 3})[0] == 400


def test_generar_todo_crea_vistas_y_todas_las_animaciones(api, boceto_digital):
    carpeta = generar_personaje(api, boceto_digital)
    codigo, r, _ = api("/api/generar-todo", {"carpeta": carpeta, "indice": 1,
                                             "animaciones": ["reposo", "caminar"]})
    assert codigo == 202
    t = esperar(api, r["trabajo"], limite=60)
    assert t["estado"] == "listo", t["error"]
    res = t["resultado"]
    assert res["errores"] == [] and sorted(res["vistas"]) == ["abajo", "arriba", "lado"]
    assert (res["animaciones_hechas"], res["animaciones_total"]) == (6, 6)
    assert t["progreso"] is None
    assert api(f"/api/direcciones/{carpeta}/1")[1]["vistas"].keys() == {"abajo", "arriba", "lado"}
    nombres = {a["nombre"] for a in api(f"/api/animaciones/{carpeta}/1")[1]["animaciones"]}
    # la dirección «derecha» deja también su espejo («izquierda»)
    direcciones = ("abajo", "arriba", "derecha", "izquierda")
    esperadas = {f"{n}_{d}" for n in ("reposo", "caminar") for d in direcciones}
    assert esperadas <= nombres


def test_generar_todo_no_rehace_vistas_y_sigue_si_un_paso_falla(api, boceto_digital, monkeypatch):
    carpeta = generar_personaje(api, boceto_digital)
    _, r, _ = api("/api/direcciones", {"carpeta": carpeta, "indice": 1, "vista": "arriba", "modo": "ia"})
    esperar(api, r["trabajo"])
    llamadas = []
    original = vistas_falsas

    def vistas_contadas(base, vista, *a, **k):
        llamadas.append(vista)
        if vista == "lado":
            raise RuntimeError("Qwen no respondió")
        return original(base, vista, *a, **k)

    monkeypatch.setattr(sys.modules[__name__], "vistas_falsas", vistas_contadas)
    _, r, _ = api("/api/generar-todo", {"carpeta": carpeta, "indice": 1, "animaciones": ["reposo"]})
    t = esperar(api, r["trabajo"], limite=60)
    assert t["estado"] == "listo", t["error"]
    res = t["resultado"]
    assert llamadas == ["abajo", "lado"]  # «arriba» ya existía
    assert sorted(res["vistas"]) == ["abajo", "arriba"]
    assert any("«lado»" in e and "Qwen no respondió" in e for e in res["errores"])
    assert any("falta la vista" in e for e in res["errores"])
    assert res["animaciones_hechas"] == 2 and res["animaciones_total"] == 3  # abajo y arriba sí; lado no


def test_generar_todo_valida(api, boceto_digital):
    carpeta = generar_personaje(api, boceto_digital)
    base = {"carpeta": carpeta, "indice": 1}
    assert api("/api/generar-todo", {**base, "motor_ia": "otro"})[0] == 400
    assert api("/api/generar-todo", {**base, "motor_ia": "gemini"})[0] == 400  # sin clave
    assert api("/api/generar-todo", {**base, "direcciones": ["diagonal"]})[0] == 400
    assert api("/api/generar-todo", {**base, "animaciones": ["volar"]})[0] == 400
    assert api("/api/generar-todo", {**base, "indice": 9})[0] == 400


def test_generar_sala_con_qwen(api):
    codigo, r, _ = api("/api/salas", {"proyecto": "oscuro", "descripcion": "catacumbas con moho",
                                       "forma": "cuadrada", "variantes": 2, "traducir": False})
    assert codigo == 202
    t = esperar(api, r["trabajo"], limite=60)
    assert t["estado"] == "listo", t["error"]
    res = t["resultado"]
    assert res["salas"] == [1, 2] and res["urls"]["mapa_prueba_x2"].endswith("sala_1_x2.png")
    assert api(res["urls"]["sala_2"])[0] == 200 and api(res["urls"]["tmx_1"])[0] == 200
    _, lista, _ = api("/api/proyectos/oscuro/mapas")
    assert lista[0]["materiales"] == {"sala": "catacumbas con moho"}


def test_generar_sala_valida(api):
    base = {"proyecto": "oscuro", "descripcion": "x"}
    assert api("/api/salas", {**base, "descripcion": " "})[0] == 400
    assert api("/api/salas", {**base, "descripcion": "x" * 401})[0] == 400
    assert api("/api/salas", {**base, "forma": "enorme"})[0] == 400
    assert api("/api/salas", {**base, "detalle": "raro"})[0] == 400
    assert api("/api/salas", {**base, "proyecto": "no-existe"})[0] in (400, 404)


def test_ambientes_del_mapa_grande(api):
    codigo, lista, _ = api("/api/mapas-grandes/ambientes")
    assert codigo == 200 and len(lista) == 8
    assert {a["id"] for a in lista} >= {"catacumbas", "forja-de-lava", "oasis-subterraneo"}
    assert all(a["nombre"] and isinstance(a["listo"], bool) for a in lista)


def test_generar_mapa_grande(api, tmp_path, monkeypatch):
    from motor import mapa_grande
    from tests.test_mapa_grande import superficies_falsas

    # Superficies ya "pintadas" en una carpeta temporal: no se llama a Qwen ni se usa `assets/`.
    monkeypatch.setattr(mapa_grande, "CARPETA_SUPERFICIES", tmp_path / "superficies")
    sup = tmp_path / "superficies" / "catacumbas" / "superficies"
    sup.mkdir(parents=True)
    for n, a in superficies_falsas().items():
        Image.fromarray(a).save(sup / f"{n}.png")
    codigo, r, _ = api("/api/mapas-grandes", {"proyecto": "oscuro", "ambiente": "catacumbas", "ancho": 40,
                                               "alto": 32, "salas": 5, "semilla": 3, "decorar": False})
    assert codigo == 202
    t = esperar(api, r["trabajo"], limite=60)
    assert t["estado"] == "listo", t["error"]
    res = t["resultado"]
    assert res["tipo"] == "grande" and res["baldosas"] == [40, 32] and res["pixeles"] == [1280, 1024]
    assert res["urls"]["mapa_prueba"].endswith("mapa.png") and res["urls"]["tmx"].endswith("mapa.tmx")
    assert api(res["urls"]["mapa_prueba"])[0] == 200 and api(res["urls"]["tmx"])[0] == 200
    _, lista, _ = api("/api/proyectos/oscuro/mapas")
    assert lista[0]["tipo"] == "grande" and lista[0]["materiales"] == {"ambiente": "Catacumbas"}


def test_mapa_grande_valida(api):
    base = {"proyecto": "oscuro", "ambiente": "catacumbas"}
    malos = ({"ambiente": "volcan"}, {"ancho": 10}, {"alto": 500}, {"salas": 1}, {"salas": 99},
             {"ancho": "x"}, {"proyecto": "no-existe"})
    for extra in malos:
        assert api("/api/mapas-grandes", {**base, **extra})[0] in (400, 404), extra


def test_caminar_con_pasos_dibujados_por_la_ia(api, boceto_digital, monkeypatch):
    """Con `paso_ia` (Qwen) el ciclo alterna dos poses de paso y la pose base; la izquierda es el espejo."""
    from motor import animar_ia

    pedidos = []

    def pasos_falsos(base, url, semilla, direccion):
        pedidos.append(direccion)
        a, b = base.sprite.copy(), base.sprite.copy()
        a.paste((255, 0, 0, 255), (0, 0, 4, 4))  # marcas distintas para reconocer cada paso
        b.paste((0, 0, 255, 255), (0, 0, 4, 4))
        return (base.ia, a), (base.ia, b)

    monkeypatch.setattr(animar_ia, "pasos_qwen", pasos_falsos)
    carpeta = generar_personaje(api, boceto_digital)
    vista(api, carpeta, "lado", modo="variante")
    assert api("/api/animar", {"carpeta": carpeta, "indice": 1, "animacion": "caminar",
                               "direccion": "derecha", "paso_ia": True})[0] == 400  # sin Qwen no vale
    ataque = {"carpeta": carpeta, "indice": 1, "animacion": "ataque", "direccion": "derecha",
              "paso_ia": True, "motor_ia": "qwen"}
    assert api("/api/animar", ataque)[0] == 400  # solo para caminar
    r = animar(api, carpeta, animacion="caminar", direccion="derecha", paso_ia=True, motor_ia="qwen")
    assert pedidos == ["derecha"]
    der = next(a for a in r["animaciones"] if a["nombre"] == "caminar_derecha")
    izq = next(a for a in r["animaciones"] if a["nombre"] == "caminar_izquierda")
    assert der["fotogramas"] == 4 and der["bucle"] is True
    frames = [np.array(Image.open(io.BytesIO(api(u)[1]))) for u in der["frames_url"]]
    assert not np.array_equal(frames[0], frames[2])  # los dos pasos son distintos
    assert np.array_equal(frames[1], frames[3])  # y entre ellos vuelve la pose base
    espejo = np.array(Image.open(io.BytesIO(api(izq["frames_url"][0])[1])))
    assert np.array_equal(frames[0][:, ::-1], espejo)
