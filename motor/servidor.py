"""API local del motor (HTTP + JSON) para la app de escritorio.

Escucha solo en 127.0.0.1. Las generaciones van a una cola y las ejecuta un
único hilo (una a la vez, para no competir por la VRAM). Los modelos se cargan
con la primera generación y se liberan tras unos minutos sin uso.

Además sirve la interfaz (`app/ui/`) en `/`, así que la app también funciona
abriendo http://127.0.0.1:8765 en un navegador.

Endpoints (ver docs/api.md):
    GET  /api/estado                    GPU, modelos, cola
    GET  /api/presets                   presets disponibles
    POST /api/presets                   crea un preset desde 2-5 sprites de ejemplo
    POST /api/presets/<id>              cambia nombre / descripción del estilo de un preset
    POST /api/presets/<id>/borrar       borra un preset creado en la app (si no lo usa un proyecto)
    GET  /api/proyectos                 proyectos de juego (dirección de arte)
    POST /api/proyectos                 crea o actualiza un proyecto
    GET  /api/config                    ajustes (sin claves: solo si hay y su final)
    POST /api/config                    guarda la clave/modelo de Gemini y la dirección de Qwen ("" borra)
    POST /api/config/probar-gemini      comprueba la clave y lista modelos de imagen
    POST /api/config/probar-qwen        comprueba que el servidor Qwen-Image responde
    GET  /api/presets/<id>/<archivo>    vista previa y ejemplos de un preset
    POST /api/generar                   encola boceto + prompt -> {"trabajo": id}
    GET  /api/trabajos/<id>             estado y resultado de un trabajo
    POST /api/pixelizar                 re-pixeliza una variante con otras opciones
    POST /api/guardar                   guarda un sprite retocado en la app
    POST /api/animar                    encola UNA animación de una variante -> {"trabajo": id}
    GET  /api/animaciones/<carpeta>/<n> animaciones guardadas de la variante n
    POST /api/animaciones/borrar        borra una animación de la hoja
    POST /api/recolorear                vista previa (o variante nueva) con otros colores, sin IA
    GET  /api/mapas-grandes/ambientes   ambientes del mapa grande y si sus texturas están listas
    POST /api/mapas-grandes             encola un mapa grande de mazmorra (salas, pasillos, muros y decorados)
    POST /api/salas                     encola una sala de mapa completa dibujada por Qwen-Image
    POST /api/generar-todo              encola todo para una variante: sus vistas y todas las animaciones
    POST /api/redisenar                 encola un rediseño: la variante con elementos nuevos (Gemini)
    POST /api/direcciones               encola una vista de dirección (abajo, arriba, lado)
    GET  /api/direcciones/<carpeta>/<n> vistas de dirección guardadas de la variante n
    GET  /api/tipos                     biblioteca global de plantillas de tipo de personaje
    POST /api/tipos                     guarda la biblioteca global ({"tipos": [...]})
    GET  /api/proyectos/<id>/personajes personajes generados en un proyecto
    POST /api/mapas                     encola un mapa (suelos, transiciones, plataformas, fondo)
    GET  /api/proyectos/<id>/mapas      mapas generados en un proyecto
    GET  /api/resultados/<carpeta>      reabre un resultado (variantes, sprites)
    POST /api/prompt                    qué recibirá la IA (traducido) y qué no cabe
    POST /api/boceto                    el boceto tal como lo verá la IA (limpio)
    POST /api/liberar                   libera los modelos de la VRAM
    GET  /api/salidas/<carpeta>/<arch>  archivos generados

Uso:
    forja-servidor --puerto 8765
"""

from __future__ import annotations

import argparse
import base64
import io
import itertools
import json
import logging
import mimetypes
import queue
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image

from motor import __version__, animar_ia, config
from motor import animar as animar_mod
from motor import direcciones as direcciones_mod
from motor import escenas as escenas_mod
from motor import gemini as gemini_mod
from motor import generar as gen
from motor import mapa_grande as mapa_grande_mod
from motor import mapas as mapas_mod
from motor import presets as presets_mod
from motor import proyectos as proyectos_mod
from motor import rediseno as rediseno_mod
from motor import segmentar as segmentador
from motor.boceto import limpiar_boceto
from motor.forja import AMPLIACION_VISTA, CARPETA_SALIDAS, MOTORES_IA, forjar, traducir_textos
from motor.modelos import RAIZ
from motor.modelos import estado as estado_modelos
from motor.pixelizar import Opciones, ampliar, pixelizar, silueta_de_boceto
from motor.proyectos import CARPETA_PROYECTOS
from motor.proyectos import cargar as cargar_proyecto
from motor.proyectos import guardar as guardar_proyecto
from motor.proyectos import listar as listar_proyectos
from motor.proyectos import tipo as tipo_proyecto

log = logging.getLogger(__name__)

CARPETA_PRESETS = RAIZ / "presets"
CARPETA_UI = RAIZ / "app" / "ui"
GOLPES = ("deformacion", "ia", "dibujo")  # cómo se obtiene el golpe de un ataque
CABECERA_PROPIA = "X-Forja"  # obliga a un "preflight" CORS: otras webs no pueden llamar a la API
MAX_CUERPO = 30 * 2**20
NOMBRE_SEGURO = re.compile(r"^[\w.\-]+$")


@dataclass
class Trabajo:
    id: str
    parametros: dict
    estado: str = "en_cola"  # en_cola | generando | listo | error
    creado: float = field(default_factory=time.time)
    error: str | None = None
    resultado: dict | None = None
    progreso: str | None = None  # trabajos largos de varios pasos («Vista arriba (2 de 14)»)


def decodificar_imagen(data_url: str) -> Image.Image:
    """'data:image/png;base64,...' (o solo el base64) -> Image."""
    datos = data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url
    return Image.open(io.BytesIO(base64.b64decode(datos))).copy()


class Motor:
    """Estado compartido: cola de trabajos, gestor de modelos y operaciones."""

    def __init__(self, carpeta_presets: Path = CARPETA_PRESETS, carpeta_salidas: Path = CARPETA_SALIDAS,
                 generador=None, minutos_inactividad: float = 5.0, ahorro_vram: bool = False,
                 carpeta_proyectos: Path = CARPETA_PROYECTOS, segmentar: bool = True,
                 generador_claves=None, generador_texturas=None, generador_vistas=None,
                 generador_rediseno=None, generador_escenas=None):
        self.segmentar = segmentar
        # (prompt, negativo, n, semilla, ancho, alto, x, y) -> [textura]; None = la IA real.
        self.generador_texturas = generador_texturas
        # (base, cambio_en_ingles, semilla) -> (ia, sprite); None = Gemini/Qwen. Sustituible en tests.
        self.generador_rediseno = generador_rediseno
        # (prompt, ancho, alto, semilla) -> imagen de una sala; None = Qwen-Image. Sustituible en tests.
        self.generador_escenas = generador_escenas
        # (base, vista, semilla, boceto, identidad, texto) -> (ia, sprite); sustituible en tests.
        self.generador_vistas = generador_vistas or (
            lambda base, vista, semilla, boceto, identidad, texto:
                direcciones_mod.vista_ia(self.gestor, base, vista, semilla, boceto, identidad, texto))
        # (base, accion, variantes, semilla, boceto_pose, texto, identidad) -> [(ia, sprite)];
        # sustituible en tests.
        self.generador_claves = generador_claves or (
            lambda base, accion, variantes, semilla, boceto_pose, texto, identidad=None:
                animar_ia.candidatos_clave(self.gestor, base, accion, variantes=variantes, semilla=semilla,
                                           boceto_pose=boceto_pose, texto=texto, fuerza_identidad=identidad))
        self.carpeta_presets = Path(carpeta_presets)
        self.carpeta_proyectos = Path(carpeta_proyectos)
        self.carpeta_salidas = Path(carpeta_salidas)
        self.gestor = gen.GestorModelos(minutos_inactividad, ahorro_vram)
        self.generador = generador or (lambda p: gen.generar(self.gestor, p))
        self.trabajos: dict[str, Trabajo] = {}
        self.cola: queue.Queue[Trabajo] = queue.Queue()
        self._ids = itertools.count(1)
        self._gpu = None
        threading.Thread(target=self._bucle, daemon=True, name="forja-trabajos").start()
        # Importar PyTorch tarda unos segundos: se hace ya, para que la primera
        # consulta de la app a /api/estado responda al momento.
        threading.Thread(target=self.estado, daemon=True, name="forja-estado-gpu").start()

    # --- consultas -------------------------------------------------------------

    def estado(self) -> dict:
        if self._gpu is None:
            self._gpu = gen.info_gpu()
        pendientes = [t.id for t in self.trabajos.values() if t.estado in ("en_cola", "generando")]
        return {
            "version": __version__,
            "gpu": self._gpu,
            "modelos": [{"alias": m.alias, "uso": m.uso, "gb": m.gb, "descargado": ok}
                        for m, ok in estado_modelos()],
            "modelos_cargados": self.gestor.cargada,
            "trabajos_pendientes": pendientes,
            "modos": list(gen.MODOS),
            "config": config.publico(),
        }

    def presets(self) -> list[dict]:
        salida = []
        for carpeta in sorted(self.carpeta_presets.iterdir()):
            archivo = carpeta / "estilo.json"
            if not archivo.exists():
                continue
            e = json.loads(archivo.read_text())
            salida.append({
                "id": carpeta.name, "nombre": e["nombre"], "sprite": e["sprite"],
                "contorno": e["contorno"], "colores_por_sprite": e["colores_por_sprite"],
                "paleta": e["paleta"], "fondo_generacion": e.get("fondo_generacion"),
                "generacion": e.get("generacion", {}), "ejemplos": e["ejemplos"],
                "vista_previa": f"/api/presets/{carpeta.name}/vista_previa.png",
                "creado_en_app": (e.get("fuente") or {}).get("origen") == "creado en la app",
            })
        return salida

    def crear_preset(self, p: dict) -> dict:
        ejemplos = [decodificar_imagen(d) for d in (p.get("ejemplos") or [])[:6]]
        maximo = p.get("max_paleta")
        maximo = int(maximo) if maximo not in (None, "") else None
        if maximo is not None and not 4 <= maximo <= 256:
            raise ValueError("max_paleta debe estar entre 4 y 256")
        preset_id = presets_mod.crear(self.carpeta_presets, p.get("nombre", ""), ejemplos,
                                      p.get("estilo", ""), maximo)
        return next(x for x in self.presets() if x["id"] == preset_id)

    def actualizar_preset(self, preset_id: str, p: dict) -> dict:
        presets_mod.actualizar(self.carpeta_preset(preset_id), p.get("nombre"),
                               p.get("prompt_base"), p.get("prompt_negativo"))
        return next(x for x in self.presets() if x["id"] == preset_id)

    def borrar_preset(self, preset_id: str) -> dict:
        import shutil

        carpeta = self.carpeta_preset(preset_id)
        if not next(x for x in self.presets() if x["id"] == preset_id)["creado_en_app"]:
            raise ValueError("Los estilos incluidos con la app no se pueden borrar")
        usan = [pr["nombre"] for pr in self.proyectos() if pr.get("preset") == preset_id]
        if usan:
            raise ValueError(f"Lo usa el proyecto «{usan[0]}»: cámbiale el estilo antes de borrarlo")
        shutil.rmtree(carpeta)
        return {"borrado": preset_id}

    def guardar_config(self, datos: dict) -> dict:
        cambios = {}
        if "gemini_api_key" in datos:
            cambios["gemini_api_key"] = datos["gemini_api_key"]
        if "gemini_modelo" in datos:
            cambios["gemini_modelo"] = datos["gemini_modelo"]
        if "qwen_url" in datos:
            cambios["qwen_url"] = datos["qwen_url"]
        config.guardar(cambios)
        return config.publico()

    def probar_gemini(self, datos: dict) -> dict:
        """Comprueba la clave (la indicada o la guardada) y lista sus modelos de imagen."""
        from motor import gemini

        clave = (datos.get("gemini_api_key") or "").strip() or config.gemini_api_key()
        if not clave:
            raise ValueError("No hay ninguna clave de Gemini para probar")
        try:
            modelos = gemini.listar_modelos(clave)
        except gemini.ErrorGemini as e:
            raise ValueError(str(e)) from None
        return {"ok": True, "modelos": modelos}

    def probar_qwen(self, datos: dict) -> dict:
        """Comprueba que el servidor Qwen (el indicado o el guardado) responde."""
        from motor import qwen

        url = (datos.get("qwen_url") or "").strip() or config.qwen_url()
        try:
            estado = qwen.estado(url)
        except qwen.ErrorQwen as e:
            raise ValueError(str(e)) from None
        return {"ok": bool(estado.get("sd")), "url": url, "sd": bool(estado.get("sd")),
                "traductor": bool(estado.get("llm"))}

    def proyectos(self) -> list[dict]:
        return listar_proyectos(self.carpeta_proyectos)

    def guardar_proyecto(self, datos: dict) -> dict:
        self.carpeta_preset(datos.get("preset", ""))
        return guardar_proyecto(datos, self.carpeta_proyectos)

    def carpeta_preset(self, preset_id: str) -> Path:
        if not NOMBRE_SEGURO.match(preset_id or "") or not (self.carpeta_presets / preset_id).is_dir():
            raise ValueError(f"Preset desconocido: {preset_id!r}")
        return self.carpeta_presets / preset_id

    def carpeta_salida(self, nombre: str) -> Path:
        if not NOMBRE_SEGURO.match(nombre or "") or not (self.carpeta_salidas / nombre).is_dir():
            raise ValueError(f"Resultado desconocido: {nombre!r}")
        return self.carpeta_salidas / nombre

    # --- trabajos --------------------------------------------------------------

    def encolar(self, p: dict) -> Trabajo:
        if not p.get("boceto"):
            raise ValueError("Falta el boceto")
        proyecto = None
        if p.get("proyecto"):
            proyecto = cargar_proyecto(p["proyecto"], self.carpeta_proyectos)
            self.carpeta_preset(p.get("preset") or proyecto["preset"])
        else:
            self.carpeta_preset(p.get("preset", ""))
        plantilla = tipo_proyecto(proyecto, p.get("tipo") or None)  # valida el tipo de personaje
        # Con un tipo elegido su plantilla ya describe al personaje: la descripción es opcional.
        if not str(p.get("prompt", "")).strip() and not (plantilla and plantilla.get("prompt")):
            raise ValueError("Falta la descripción del personaje (o elige un tipo de personaje)")
        if p.get("modo", gen.MODO_POR_DEFECTO) not in gen.MODOS:
            raise ValueError(f"Modo desconocido: {p.get('modo')!r}")
        if p.get("motor_ia", "local") not in MOTORES_IA:
            raise ValueError(f"Motor de IA desconocido: {p.get('motor_ia')!r}")
        if p.get("motor_ia") == "gemini" and not config.gemini_api_key():
            raise ValueError("Para usar Gemini, guarda primero tu clave en Ajustes")
        variantes = int(p.get("variantes", 4))
        if not 1 <= variantes <= 8:
            raise ValueError("Variantes debe estar entre 1 y 8")
        t = Trabajo(str(next(self._ids)), p)
        self.trabajos[t.id] = t
        self.cola.put(t)
        return t

    def _bucle(self) -> None:
        while True:
            t = self.cola.get()
            t.estado = "generando"
            try:
                if t.parametros.get("_tipo") == "animacion":
                    t.resultado = self._ejecutar_animacion(t.parametros)
                elif t.parametros.get("_tipo") == "mapa":
                    t.resultado = self._ejecutar_mapa(t.parametros)
                elif t.parametros.get("_tipo") == "vista":
                    t.resultado = self._ejecutar_vista(t.parametros)
                elif t.parametros.get("_tipo") == "rediseno":
                    t.resultado = self._ejecutar_rediseno(t.parametros)
                elif t.parametros.get("_tipo") == "mapa_grande":
                    t.resultado = self._ejecutar_mapa_grande(t)
                elif t.parametros.get("_tipo") == "sala":
                    t.resultado = self._ejecutar_sala(t.parametros)
                elif t.parametros.get("_tipo") == "todo":
                    t.resultado = self._ejecutar_todo(t)
                else:
                    t.resultado = self._ejecutar(t.parametros)
                t.estado = "listo"
            except Exception as e:  # noqa: BLE001 - el error se devuelve a la app
                log.error("Trabajo %s falló:\n%s", t.id, traceback.format_exc())
                t.estado, t.error = "error", f"{type(e).__name__}: {e}"

    # --- animación ---------------------------------------------------------------

    def _variante(self, carpeta: Path, indice: int) -> tuple[dict, Image.Image]:
        """(info.json, sprite de la variante: el retoque guardado si existe)."""
        info = json.loads((carpeta / "info.json").read_text())
        v = next((x for x in info["variantes"] if x["indice"] == indice), None)
        if v is None:
            raise ValueError(f"No existe la variante {indice}")
        final = carpeta / f"final_{indice:02d}.png"
        return info, Image.open(final if final.exists() else carpeta / v["sprite"]).convert("RGBA")

    def encolar_animacion(self, p: dict) -> Trabajo:
        """Encola UNA animación (con golpe de IA o del usuario puede tardar: va a la cola)."""
        carpeta = self.carpeta_salida(p.get("carpeta", ""))
        self._variante(carpeta, int(p["indice"]))
        nombre = p.get("animacion", "")
        if nombre not in animar_mod.animaciones_estandar() and nombre not in animar_mod.ATAQUES:
            raise ValueError(f"Animación desconocida: {nombre!r}")
        golpe = p.get("golpe") or "deformacion"
        if golpe not in GOLPES:
            raise ValueError(f"Tipo de golpe desconocido: {golpe!r}")
        if p.get("paso_ia") and (nombre != "caminar" or p.get("motor_ia") != "qwen"):
            raise ValueError("Los pasos dibujados por la IA son solo para «caminar» y con el motor Qwen")
        if golpe != "deformacion" and nombre not in animar_mod.ATAQUES:
            raise ValueError("Solo los ataques admiten una pose dibujada")
        if golpe == "dibujo" and not p.get("boceto_pose"):
            raise ValueError("Dibuja la pose en el lienzo para usarla como golpe")
        if nombre == "especial" and golpe != "deformacion" and not str(p.get("texto", "")).strip():
            raise ValueError("Describe el ataque especial")
        if p.get("motor_ia", "local") not in MOTORES_IA:
            raise ValueError(f"Motor de IA desconocido: {p.get('motor_ia')!r}")
        if p.get("motor_ia") == "gemini" and golpe != "deformacion":
            raise ValueError("Las poses de los ataques se dibujan con la IA local o con Qwen")
        identidad = p.get("identidad")
        if identidad not in (None, "") and not 0 <= float(identidad) <= 1:
            raise ValueError("La fuerza de identidad debe estar entre 0 y 1")
        direccion = p.get("direccion") or None
        if direccion is not None:
            if direccion not in ("abajo", "arriba", "derecha"):
                raise ValueError("Dirección: abajo, arriba o derecha (la izquierda sale sola, en espejo)")
            direcciones_mod.sprite_de(carpeta, int(p["indice"]), direccion)  # la vista debe existir
        t = Trabajo(str(next(self._ids)), {**p, "_tipo": "animacion"})
        self.trabajos[t.id] = t
        self.cola.put(t)
        return t

    # --- todo para un sprite ---------------------------------------------------------

    DIRECCIONES_TODO = ("abajo", "arriba", "derecha")
    # Orden de las vistas: el lado se apoya en la espalda ya hecha (ver `direcciones._perfil_qwen`).
    ORDEN_VISTAS = ("arriba", "abajo", "lado")

    def encolar_todo(self, p: dict) -> Trabajo:
        """Encola TODO para una variante: sus vistas (frente, espalda, lado) y todas las
        animaciones de cada dirección. Un solo trabajo que avanza paso a paso."""
        carpeta = self.carpeta_salida(p.get("carpeta", ""))
        self._variante(carpeta, int(p["indice"]))
        motor_ia = p.get("motor_ia", "local")
        if motor_ia not in MOTORES_IA:
            raise ValueError(f"Motor de IA desconocido: {motor_ia!r}")
        if motor_ia == "gemini" and not config.gemini_api_key():
            raise ValueError("Configura la clave de Gemini en Ajustes")
        direcciones = list(p.get("direcciones") or self.DIRECCIONES_TODO)
        malas = [d for d in direcciones if d not in (*self.DIRECCIONES_TODO, "variante")]
        if malas:
            raise ValueError(f"Direcciones desconocidas: {', '.join(map(str, malas))}")
        conocidas = animar_mod.animaciones_estandar()
        nombres = list(p.get("animaciones") or conocidas)
        if any(n not in conocidas for n in nombres):
            raise ValueError("Animación desconocida")
        if not direcciones:
            raise ValueError("Elige al menos una dirección")
        t = Trabajo(str(next(self._ids)), {**p, "motor_ia": motor_ia, "direcciones": direcciones,
                                           "animaciones": nombres, "_tipo": "todo"})
        self.trabajos[t.id] = t
        self.cola.put(t)
        return t

    def _ejecutar_todo(self, t: Trabajo) -> dict:
        p = t.parametros
        carpeta, indice = self.carpeta_salida(p["carpeta"]), int(p["indice"])
        motor_ia = p["motor_ia"]
        poses_ia = bool(p.get("poses_ia")) and motor_ia in ("local", "qwen")
        direcciones = p["direcciones"]
        vistas_necesarias = [v for v in self.ORDEN_VISTAS
                             if any(direcciones_mod.VISTA_DE_DIRECCION[d] == v for d in direcciones
                                    if d != "variante")]
        total = len(vistas_necesarias) + len(direcciones) * len(p["animaciones"])
        hecho, errores = 0, []
        semilla = int(p["semilla"]) if p.get("semilla") not in (None, "") else int(time.time()) % 1_000_000

        def paso(texto: str) -> None:
            t.progreso = f"{texto} ({hecho + 1} de {total})"

        def intentar(texto: str, funcion) -> bool:
            nonlocal hecho
            paso(texto)
            try:
                funcion()
                return True
            except Exception as e:  # noqa: BLE001 - un paso fallido no detiene el resto
                log.error("Trabajo %s, paso «%s» falló:\n%s", t.id, texto, traceback.format_exc())
                errores.append(f"{texto}: {e}")
                return False
            finally:
                hecho += 1

        vistas_ok = set(direcciones_mod.listar(carpeta, indice)) if not p.get("rehacer") else set()
        for vista in vistas_necesarias:
            if vista in vistas_ok:
                hecho += 1
                continue
            if intentar(f"Dibujando la vista «{vista}»", lambda v=vista: self._ejecutar_vista(
                    {"carpeta": p["carpeta"], "indice": indice, "vista": v, "modo": "ia",
                     "motor_ia": motor_ia, "texto": p.get("texto"), "semilla": semilla})):
                vistas_ok.add(vista)
        hechas = 0
        for direccion in direcciones:
            vista = None if direccion == "variante" else direcciones_mod.VISTA_DE_DIRECCION[direccion]
            for nombre in p["animaciones"]:
                texto = f"Animación «{nombre}» ({direccion})"
                if vista is not None and vista not in vistas_ok:
                    paso(texto)
                    hecho += 1
                    errores.append(f"{texto}: falta la vista «{vista}»")
                    continue
                con_ia = poses_ia and nombre in animar_mod.ATAQUES and nombre != "especial"
                golpe = "ia" if con_ia else "deformacion"
                paso_ia = bool(p.get("caminar_ia")) and nombre == "caminar" and motor_ia == "qwen"
                if intentar(texto, lambda n=nombre, d=direccion, g=golpe, pi=paso_ia: self._ejecutar_animacion(  # noqa: E501
                        {"carpeta": p["carpeta"], "indice": indice, "animacion": n, "golpe": g,
                         "direccion": None if d == "variante" else d, "motor_ia": motor_ia,
                         "semilla": semilla, "paso_ia": pi})):
                    hechas += 1
        t.progreso = None
        return {"carpeta": carpeta.name, "indice": indice, "vistas": sorted(vistas_ok),
                "animaciones_hechas": hechas, "animaciones_total": len(direcciones) * len(p["animaciones"]),
                "errores": errores}

    # --- mapas ---------------------------------------------------------------------

    def encolar_mapa(self, p: dict) -> Trabajo:
        """Encola un mapa del proyecto (la perspectiva y la baldosa las fija el proyecto)."""
        proyecto = cargar_proyecto(p.get("proyecto") or "", self.carpeta_proyectos)
        self.carpeta_preset(proyecto["preset"])
        if not isinstance(p.get("materiales", {}), dict):
            raise ValueError("'materiales' debe ser un objeto")
        if p.get("legibilidad") not in (None, "", "auto") and not 0 <= float(p["legibilidad"]) <= 1:
            raise ValueError("La legibilidad debe estar entre 0 y 1 (o «auto»)")
        for v in (p.get("materiales") or {}).values():
            if len(str(v or "")) > 200:
                raise ValueError("Descripción de material demasiado larga (máx. 200)")
        t = Trabajo(str(next(self._ids)), {**p, "_tipo": "mapa"})
        self.trabajos[t.id] = t
        self.cola.put(t)
        return t

    def _ejecutar_mapa(self, p: dict) -> dict:
        proyecto = cargar_proyecto(p["proyecto"], self.carpeta_proyectos)
        info = mapas_mod.forjar_mapa(
            proyecto, self.carpeta_preset(proyecto["preset"]), self.carpeta_salidas, gestor=self.gestor,
            generador=self.generador_texturas, materiales=p.get("materiales") or {},
            piezas=p.get("piezas") or None, variantes=int(p.get("variantes") or 4),
            semilla=int(p["semilla"]) if p.get("semilla") not in (None, "") else int(time.time()) % 100000,
            traducir=p.get("traducir", True),
            legibilidad=None if p.get("legibilidad") in (None, "", "auto") else float(p["legibilidad"]),
            personajes=mapas_mod.muestras_de_personajes(self.carpeta_salidas, proyecto.get("id")),
            piso=p.get("piso") or None)
        return self._mapa_publico(info)

    def ambientes_mapa_grande(self) -> list[dict]:
        """Ambientes del mapa grande y si sus texturas están hechas (si no, la primera vez tarda ~4 min)."""
        mg = mapa_grande_mod
        return [{"id": a, "nombre": mg.NOMBRES.get(a, a), "listo": mg.superficies_listas(a)}
                for a in mg.AMBIENTES]

    def encolar_mapa_grande(self, p: dict) -> Trabajo:
        """Encola un mapa grande de mazmorra de un ambiente (ver `motor.mapa_grande`)."""
        proyecto = cargar_proyecto(p.get("proyecto") or "", self.carpeta_proyectos)
        self.carpeta_preset(proyecto["preset"])
        if p.get("ambiente") not in mapa_grande_mod.AMBIENTES:
            raise ValueError(f"Ambiente desconocido: {p.get('ambiente')!r}")
        try:
            ancho, alto, salas = int(p.get("ancho", 64)), int(p.get("alto", 48)), int(p.get("salas", 10))
        except (TypeError, ValueError):
            raise ValueError("Ancho, alto y salas deben ser números") from None
        if not (24 <= ancho <= 160 and 24 <= alto <= 160):
            raise ValueError("El mapa debe medir entre 24 y 160 baldosas por lado")
        if not 3 <= salas <= 24:
            raise ValueError("Las salas deben estar entre 3 y 24")
        if p.get("semilla") not in (None, ""):
            int(p["semilla"])
        t = Trabajo(str(next(self._ids)),
                    {**p, "ancho": ancho, "alto": alto, "salas": salas, "_tipo": "mapa_grande"})
        self.trabajos[t.id] = t
        self.cola.put(t)
        return t

    def _ejecutar_mapa_grande(self, t: Trabajo) -> dict:
        p = t.parametros
        proyecto = cargar_proyecto(p["proyecto"], self.carpeta_proyectos)
        if p.get("rehacer_superficies") or not mapa_grande_mod.superficies_listas(p["ambiente"]):
            t.progreso = "Qwen está dibujando las texturas del ambiente (unos 4 min la primera vez)"
        info = mapa_grande_mod.forjar_mapa_grande(
            p["ambiente"], proyecto, self.carpeta_preset(proyecto["preset"]),
            self.carpeta_salidas / mapas_mod.CARPETA_MAPAS_REL, url=config.qwen_url(),
            ancho=p["ancho"], alto=p["alto"],
            semilla=int(p["semilla"]) if p.get("semilla") not in (None, "") else int(time.time()) % 100000,
            salas=p["salas"], decorar=p.get("decorar", True) is not False,
            rehacer_superficies=bool(p.get("rehacer_superficies")))
        t.progreso = None
        return self._mapa_publico(info)

    def encolar_sala(self, p: dict) -> Trabajo:
        """Encola una sala de mapa completa dibujada por Qwen-Image (ver `motor.escenas`)."""
        proyecto = cargar_proyecto(p.get("proyecto") or "", self.carpeta_proyectos)
        self.carpeta_preset(proyecto["preset"])
        descripcion = str(p.get("descripcion", "")).strip()
        if not descripcion:
            raise ValueError("Describe la sala (p. ej. «catacumbas con paredes, losas, moho y charcos»)")
        if len(descripcion) > 400:
            raise ValueError("La descripción de la sala es demasiado larga (máx. 400)")
        if p.get("forma", "cuadrada") not in escenas_mod.FORMAS:
            raise ValueError(f"Forma desconocida: {p.get('forma')!r}")
        if p.get("detalle", "definido") not in escenas_mod.DETALLE:
            raise ValueError(f"Detalle desconocido: {p.get('detalle')!r}")
        t = Trabajo(str(next(self._ids)), {**p, "_tipo": "sala"})
        self.trabajos[t.id] = t
        self.cola.put(t)
        return t

    def _ejecutar_sala(self, p: dict) -> dict:
        from motor import qwen

        proyecto = cargar_proyecto(p["proyecto"], self.carpeta_proyectos)
        url = config.qwen_url()
        generador = self.generador_escenas or (
            lambda prompt, an, al, s: qwen.texto_a_imagen(prompt, url, an, al, s))
        info = escenas_mod.forjar_sala(
            proyecto, self.carpeta_preset(proyecto["preset"]), self.carpeta_salidas, p["descripcion"],
            generador, forma=p.get("forma", "cuadrada"), variantes=int(p.get("variantes") or 2),
            semilla=int(p["semilla"]) if p.get("semilla") not in (None, "") else int(time.time()) % 100000,
            traducir=p.get("traducir", True), detalle=p.get("detalle", "definido"))
        return self._mapa_publico(info)

    @staticmethod
    def _mapa_publico(info: dict) -> dict:
        base = f"/api/salidas/{mapas_mod.CARPETA_MAPAS_REL}/{info['carpeta']}"
        return {**info, "urls": {k: f"{base}/{v}" for k, v in info["archivos"].items()}}

    def mapas(self, proyecto_id: str) -> list[dict]:
        """Mapas generados para un proyecto (lo más reciente primero)."""
        cargar_proyecto(proyecto_id, self.carpeta_proyectos)
        lista = []
        for info_json in (self.carpeta_salidas / mapas_mod.CARPETA_MAPAS_REL).glob("*/info.json"):
            try:
                info = json.loads(info_json.read_text())
            except json.JSONDecodeError:
                continue
            if info.get("proyecto") == proyecto_id:
                lista.append(self._mapa_publico({**info, "carpeta": info_json.parent.name}))
        return sorted(lista, key=lambda x: x.get("fecha") or "", reverse=True)

    # --- rediseño: otros colores o elementos nuevos, conservando la base ------------

    def _ajuste_color(self, p: dict, sprite: Image.Image) -> rediseno_mod.Ajuste:
        colores = p.get("colores")
        if colores is not None and (not isinstance(colores, list) or not all(
                isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in colores)):
            raise ValueError("'colores' debe ser una lista de colores #rrggbb")
        intensidad = float(p.get("saturacion") if p.get("saturacion") not in (None, "") else 1.0)
        brillo = float(p.get("brillo") if p.get("brillo") not in (None, "") else 1.0)
        if p.get("objetivo"):
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", p["objetivo"]):
                raise ValueError("'objetivo' debe ser un color #rrggbb")
            modo = p.get("modo") or "tenir"
            if modo not in ("tenir", "girar"):
                raise ValueError("Modo: tenir o girar")
            return rediseno_mod.ajuste_hacia(sprite, p["objetivo"], colores, intensidad, brillo, modo)
        tono = float(p.get("tono") if p.get("tono") not in (None, "") else 0)
        return rediseno_mod.Ajuste(tono, intensidad, brillo, colores).validar()

    def recolorear(self, p: dict) -> dict:
        """Vista previa del sprite con otros colores (sin IA) o, con `guardar`, una variante
        nueva con ese color (el sprite y la imagen de la IA)."""
        carpeta = self.carpeta_salida(p.get("carpeta", ""))
        indice = int(p["indice"])
        info, sprite = self._variante(carpeta, indice)
        paleta_ref = rediseno_mod.colores_de(sprite)
        ajuste = self._ajuste_color(p, sprite)
        estilo = json.loads((self.carpeta_preset(info["preset"]) / "estilo.json").read_text())
        ajustar = p.get("ajustar", True)

        def cambiar(imagen, mascara=None):
            r = rediseno_mod.recolorear(imagen, ajuste, paleta_ref, mascara)
            if ajustar and imagen.mode == "RGBA":  # solo el sprite: la imagen de la IA no usa la paleta
                r = rediseno_mod.ajustar_a_paleta(r, estilo["paleta"])
            return r

        nuevo = cambiar(sprite)
        if not p.get("guardar"):
            b = io.BytesIO()
            nuevo.save(b, "PNG")
            return {"imagen": "data:image/png;base64," + base64.b64encode(b.getvalue()).decode(),
                    "tono": ajuste.tenir if ajuste.tenir is not None else ajuste.tono,
                    "colores": rediseno_mod.colores_de(nuevo)}
        base = animar_ia.cargar_base(carpeta, indice, self.carpeta_presets, segmentar=self.segmentar)
        ia = cambiar(base.ia, base.mascara)
        origen = {"variante": indice, "tipo": "color",
                  "cambio": {"objetivo": p.get("objetivo"), "modo": p.get("modo"), "tono": ajuste.tono,
                             "saturacion": ajuste.saturacion, "brillo": ajuste.brillo}}
        entrada = rediseno_mod.anadir_variante(carpeta, nuevo, ia, origen, p.get("nombre"))
        resultado = self.resultado(carpeta.name)
        return {**resultado, "nueva": entrada["indice"]}

    def encolar_rediseno(self, p: dict) -> Trabajo:
        carpeta = self.carpeta_salida(p.get("carpeta", ""))
        self._variante(carpeta, int(p["indice"]))
        cambio = str(p.get("cambio", "")).strip()
        if not cambio:
            raise ValueError("Describe qué cambiar (p. ej. «con un casco de hierro y un cuerno»)")
        if len(cambio) > 300:
            raise ValueError("La descripción del cambio es demasiado larga (máx. 300)")
        if p.get("motor_ia", "gemini") not in ("gemini", "qwen"):
            raise ValueError("Añadir elementos necesita Gemini o Qwen: la IA local conserva la criatura pero "
                             "no aplica el cambio. Para cambiar solo el color usa «Recolorear» (sin IA).")
        if (p.get("motor_ia", "gemini") == "gemini" and self.generador_rediseno is None
                and not config.gemini_api_key()):
            raise ValueError("Configura la clave de Gemini en Ajustes")
        t = Trabajo(str(next(self._ids)), {**p, "cambio": cambio, "_tipo": "rediseno"})
        self.trabajos[t.id] = t
        self.cola.put(t)
        return t

    def _ejecutar_rediseno(self, p: dict) -> dict:
        from motor.traducir import a_ingles

        carpeta = self.carpeta_salida(p["carpeta"])
        indice = int(p["indice"])
        base = animar_ia.cargar_base(carpeta, indice, self.carpeta_presets, segmentar=self.segmentar)
        cambio_en = a_ingles(p["cambio"])[0]
        semilla = int(p["semilla"]) if p.get("semilla") not in (None, "") else int(time.time()) % 1_000_000
        if self.generador_rediseno is not None:
            ia, sprite = self.generador_rediseno(base, cambio_en, semilla)
        elif p.get("motor_ia") == "qwen":
            ia, sprite = rediseno_mod.rediseno_qwen(base, cambio_en, config.qwen_url(), semilla)
        else:
            clave = config.gemini_api_key()
            modelo = config.leer().get("gemini_modelo") or gemini_mod.modelo_por_defecto(clave)
            ia, sprite = rediseno_mod.rediseno_gemini(base, cambio_en, clave, modelo, semilla)
        origen = {"variante": indice, "tipo": "ia", "cambio": p["cambio"],
                  "motor_ia": "qwen" if p.get("motor_ia") == "qwen" else "gemini"}
        entrada = rediseno_mod.anadir_variante(carpeta, sprite, ia, origen, p.get("nombre"), semilla)
        return {**self.resultado(carpeta.name), "nueva": entrada["indice"]}

    # --- vistas de dirección -------------------------------------------------------

    def encolar_vista(self, p: dict) -> Trabajo:
        """Encola una vista de dirección de una variante (la IA puede tardar)."""
        carpeta = self.carpeta_salida(p.get("carpeta", ""))
        self._variante(carpeta, int(p["indice"]))
        if p.get("vista") not in direcciones_mod.VISTAS:
            raise ValueError("Vista: abajo, arriba o lado")
        modo = p.get("modo") or "ia"
        if modo not in direcciones_mod.MODOS:
            raise ValueError(f"Modo desconocido: {modo!r} (ia, dibujo o variante)")
        if modo == "dibujo" and not p.get("boceto_pose"):
            raise ValueError("Dibuja la vista en el lienzo")
        if p.get("motor_ia", "local") not in MOTORES_IA:
            raise ValueError(f"Motor de IA desconocido: {p.get('motor_ia')!r}")
        if p.get("motor_ia") == "gemini" and modo != "variante" and not config.gemini_api_key():
            raise ValueError("Configura la clave de Gemini en Ajustes")
        t = Trabajo(str(next(self._ids)), {**p, "modo": modo, "_tipo": "vista"})
        self.trabajos[t.id] = t
        self.cola.put(t)
        return t

    def _ejecutar_vista(self, p: dict) -> dict:
        carpeta = self.carpeta_salida(p["carpeta"])
        indice, vista, modo = int(p["indice"]), p["vista"], p["modo"]
        info, sprite = self._variante(carpeta, indice)
        extra: dict = {"modo": modo}
        if modo == "variante":
            v = next(x for x in info["variantes"] if x["indice"] == indice)
            ia = Image.open(carpeta / v["ia"]).convert("RGB")
        else:
            base = animar_ia.cargar_base(carpeta, indice, self.carpeta_presets, segmentar=self.segmentar)
            boceto = decodificar_imagen(p["boceto_pose"]) if modo == "dibujo" else None
            semilla = (int(p["semilla"]) if p.get("semilla") not in (None, "")
                       else int(time.time()) % 1_000_000)
            identidad = float(p["identidad"]) if p.get("identidad") not in (None, "") else None
            if p.get("motor_ia") == "gemini":
                clave = config.gemini_api_key()
                modelo = config.leer().get("gemini_modelo") or gemini_mod.modelo_por_defecto(clave)
                ia, sprite = direcciones_mod.vista_gemini(base, vista, clave, modelo, semilla, boceto)
                extra["motor_ia"] = f"gemini:{modelo}"
            elif p.get("motor_ia") == "qwen":
                ia, sprite = direcciones_mod.vista_qwen(base, vista, config.qwen_url(), semilla, boceto)
                extra["motor_ia"] = "qwen"
            else:
                texto = (p.get("texto") or "").strip()
                if texto:
                    from motor.traducir import a_ingles

                    texto = a_ingles(texto)[0]
                ia, sprite = self.generador_vistas(base, vista, semilla, boceto, identidad, texto or None)
                extra["motor_ia"] = "local"
            extra["semilla"] = semilla
        direcciones_mod.guardar(carpeta, indice, vista, ia, sprite, extra)
        return self.vistas(carpeta.name, indice)

    def vistas(self, nombre_carpeta: str, indice: int) -> dict:
        carpeta = self.carpeta_salida(nombre_carpeta)
        d = direcciones_mod.carpeta_de(carpeta, indice)
        marca = int(time.time() * 1000)
        base = f"/api/salidas/{carpeta.name}/{d.name}"
        return {"carpeta": carpeta.name, "indice": int(indice),
                "vistas": {v: {**m, "url": f"{base}/{v}.png?v={marca}",
                               "ia_url": f"{base}/ia_{v}.png?v={marca}"}
                           for v, m in direcciones_mod.listar(carpeta, indice).items()}}

    def _ejecutar_animacion(self, p: dict) -> dict:
        carpeta = self.carpeta_salida(p["carpeta"])
        indice = int(p["indice"])
        direccion = p.get("direccion") or None
        if direccion:
            sprite = direcciones_mod.sprite_de(carpeta, indice, direccion)
        else:
            _, sprite = self._variante(carpeta, indice)
        nombre, golpe = p["animacion"], p.get("golpe") or "deformacion"
        mira = {"izquierda": -1, "derecha": 1}.get(p.get("mira") or "")
        if direccion:
            mira = 1  # las vistas se guardan mirando a la derecha (o de frente/espaldas)
        clave, extra = None, {"golpe": golpe, "direccion": direccion}
        pasos = None
        if nombre == "caminar" and p.get("paso_ia") and p.get("motor_ia") == "qwen":
            # caminar con dos pasos dibujados por Qwen (la deformación no mueve piernas bajo una capa larga)
            vista = direcciones_mod.VISTA_DE_DIRECCION[direccion] if direccion else None
            base = animar_ia.cargar_base(carpeta, indice, self.carpeta_presets, segmentar=self.segmentar,
                                         vista=vista)
            semilla = (int(p["semilla"]) if p.get("semilla") not in (None, "")
                       else int(time.time()) % 1_000_000)
            (ia_a, paso_a), (ia_b, paso_b) = animar_ia.pasos_qwen(base, config.qwen_url(), semilla, direccion)
            destino = carpeta / f"claves_{indice:02d}"
            destino.mkdir(exist_ok=True)
            for etiqueta, ia, sp in (("a", ia_a, paso_a), ("b", ia_b, paso_b)):
                sp.save(destino / f"paso_{etiqueta}_{direccion or 'variante'}_{semilla}.png")
                ia.save(destino / f"ia_paso_{etiqueta}_{direccion or 'variante'}_{semilla}.png")
            pasos = (paso_a, paso_b)
            extra.update(motor_ia="qwen", semilla=semilla, pasos="ia")
        elif golpe != "deformacion":
            vista = direcciones_mod.VISTA_DE_DIRECCION[direccion] if direccion else None
            base = animar_ia.cargar_base(carpeta, indice, self.carpeta_presets, segmentar=self.segmentar,
                                         vista=vista)
            texto = (p.get("texto") or "").strip() or None
            if texto:
                from motor.traducir import a_ingles

                texto = a_ingles(texto)[0]
            accion = nombre if nombre in animar_ia.ACCIONES else "ataque"
            boceto = decodificar_imagen(p["boceto_pose"]) if golpe == "dibujo" else None
            semilla = (int(p["semilla"]) if p.get("semilla") not in (None, "")
                       else int(time.time()) % 1_000_000)
            identidad = float(p["identidad"]) if p.get("identidad") not in (None, "") else None
            if p.get("motor_ia") == "qwen":
                ia, clave = animar_ia.clave_qwen(base, accion, config.qwen_url(), semilla, boceto, texto)
                extra["motor_ia"] = "qwen"
            else:
                (ia, clave), = self.generador_claves(base, accion, 1, semilla, boceto, texto, identidad)
            destino = carpeta / f"claves_{indice:02d}"
            destino.mkdir(exist_ok=True)
            archivo = f"{nombre}_{semilla}.png"
            clave.save(destino / archivo)
            ia.save(destino / f"ia_{archivo}")
            extra.update(semilla=semilla, clave=f"{destino.name}/{archivo}", texto=texto)
        if pasos:
            fig, anim, frames = animar_mod.construir_caminata(sprite, *pasos, mira)
        else:
            fig, anim, frames = animar_mod.construir(sprite, nombre, mira, clave, direccion)
        destino = carpeta / f"animacion_{indice:02d}"
        guardado = f"{nombre}_{direccion}" if direccion else nombre
        anim.nombre = guardado
        animar_mod.guardar_animacion(destino, anim, frames, fig.mira, extra)
        if direccion == "derecha":  # la izquierda es el espejo: siempre van juntas
            espejo = animar_mod.Animacion(f"{nombre}_izquierda", anim.poses, anim.duracion_ms, anim.bucle,
                                          anim.descripcion, anim.duraciones)
            volteados = [f.transpose(Image.FLIP_LEFT_RIGHT) for f in frames]
            animar_mod.guardar_animacion(destino, espejo, volteados, -1,
                                         {**extra, "direccion": "izquierda", "espejo_de": guardado})
        return self.animaciones(carpeta.name, indice, recien=guardado)

    def animaciones(self, nombre_carpeta: str, indice: int, recien: str | None = None) -> dict:
        """Animaciones guardadas de una variante, con URLs de fotogramas, GIF y hoja."""
        carpeta = self.carpeta_salida(nombre_carpeta)
        destino = carpeta / f"animacion_{int(indice):02d}"
        base_url = f"/api/salidas/{carpeta.name}/{destino.name}"
        marca = int(time.time() * 1000)
        lista = []
        for m in animar_mod.animaciones_guardadas(destino):
            lista.append({**m, "gif_url": f"{base_url}/{m['nombre']}.gif?v={marca}",
                          "frames_url": [f"{base_url}/{m['nombre']}/frame_{i:02d}.png?v={marca}"
                                         for i in range(m["fotogramas"])],
                          "clave_url": (f"/api/salidas/{carpeta.name}/{m['clave']}"
                                        if m.get("clave") else None)})
        hay = bool(lista)
        return {"carpeta": carpeta.name, "indice": int(indice), "ruta": str(destino), "recien": recien,
                "animaciones": lista,
                "hoja_url": f"{base_url}/hoja.png?v={marca}" if hay else None,
                "hoja_x4_url": f"{base_url}/hoja_x4.png?v={marca}" if hay else None,
                "json_url": f"{base_url}/hoja.json" if hay else None}

    def borrar_animacion(self, p: dict) -> dict:
        carpeta = self.carpeta_salida(p.get("carpeta", ""))
        indice = int(p["indice"])
        nombre = p.get("animacion", "")
        if not re.fullmatch(r"[a-z_]{1,20}", nombre):
            raise ValueError(f"Animación inválida: {nombre!r}")
        destino = carpeta / f"animacion_{indice:02d}"
        animar_mod.borrar_animacion(destino, nombre)
        base, direccion = animar_mod.separar_direccion(nombre)
        pareja = {"derecha": "izquierda", "izquierda": "derecha"}.get(direccion or "")
        if pareja and (destino / f"{base}_{pareja}" / "meta.json").exists():
            animar_mod.borrar_animacion(destino, f"{base}_{pareja}")  # la izquierda es el espejo
        return self.animaciones(carpeta.name, indice)

    # --- proyectos: personajes y prompt ---------------------------------------

    def personajes(self, proyecto_id: str) -> list[dict]:
        """Personajes generados para un proyecto (lo más reciente primero)."""
        cargar_proyecto(proyecto_id, self.carpeta_proyectos)
        lista = []
        if not self.carpeta_salidas.is_dir():
            return lista
        for info_json in self.carpeta_salidas.glob("*/info.json"):
            try:
                info = json.loads(info_json.read_text())
            except json.JSONDecodeError:
                continue
            if info.get("proyecto") != proyecto_id:
                continue
            carpeta = info_json.parent
            indice, animaciones = self._variante_elegida(carpeta)
            final = carpeta / f"final_{indice:02d}.png"
            elegido = final.name if final.exists() else f"sprite_{indice:02d}.png"
            lista.append({
                "carpeta": carpeta.name, "fecha": info.get("fecha"),
                "descripcion": info.get("prompt_usuario"),
                "tipo": (info.get("tipo") or {}).get("nombre"), "variantes": len(info["variantes"]),
                "indice": indice, "sprite_url": f"/api/salidas/{carpeta.name}/{elegido}",
                "animaciones": animaciones,
            })
        return sorted(lista, key=lambda x: x["fecha"] or "", reverse=True)

    @staticmethod
    def _variante_elegida(carpeta: Path) -> tuple[int, list[str]]:
        """(variante con la que se sigue trabajando, sus animaciones): la última animada;
        si no hay animaciones, la primera con retoque guardado; si no, la 1."""
        animadas = []
        for d in carpeta.glob("animacion_??"):
            nombres = sorted(m["nombre"] for m in animar_mod.animaciones_guardadas(d))
            if nombres:
                animadas.append((d.stat().st_mtime, int(d.name[-2:]), nombres))
        if animadas:
            _, indice, nombres = max(animadas)
            return indice, nombres
        finales = sorted(carpeta.glob("final_??.png"))
        return (int(finales[0].stem[-2:]) if finales else 1), []

    @staticmethod
    def _opciones_boceto(p: dict) -> dict:
        simplificar = float(p["simplificar"]) if p.get("simplificar") not in (None, "") else 0.3
        grosor = float(p["grosor"]) if p.get("grosor") not in (None, "") else 0.006
        if not 0 <= simplificar <= 1 or not 0.002 <= grosor <= 0.02:
            raise ValueError("Opciones de boceto fuera de rango")
        return {"simplificar": simplificar, "grosor": grosor}

    def previsualizar_boceto(self, p: dict) -> dict:
        """El boceto tal como lo recibirá la IA (limpio y encajado en el lienzo de generación)."""
        if not p.get("boceto"):
            raise ValueError("Falta el boceto")
        proyecto = cargar_proyecto(p["proyecto"], self.carpeta_proyectos) if p.get("proyecto") else None
        preset = self.carpeta_preset(p.get("preset") or (proyecto or {}).get("preset", ""))
        estilo = json.loads((preset / "estilo.json").read_text())
        ancho, alto = gen.tamano_generacion(estilo["sprite"]["ancho"], estilo["sprite"]["alto"],
                                            estilo["generacion"].get("resolucion_generacion", 768))
        limpio = limpiar_boceto(decodificar_imagen(p["boceto"]), ancho, alto, **self._opciones_boceto(p))
        b = io.BytesIO()
        limpio.save(b, "PNG")
        return {"imagen": "data:image/png;base64," + base64.b64encode(b.getvalue()).decode(),
                "ancho": ancho, "alto": alto}

    def resultado(self, nombre_carpeta: str) -> dict:
        carpeta = self.carpeta_salida(nombre_carpeta)
        return self._describir(carpeta, json.loads((carpeta / "info.json").read_text()))

    def previsualizar_prompt(self, p: dict) -> dict:
        """Qué recibirá la IA (traducido) para estos textos, y qué no cabe."""
        proyecto = cargar_proyecto(p["proyecto"], self.carpeta_proyectos) if p.get("proyecto") else None
        if p.get("ambientacion") is not None:  # texto aún sin guardar en el editor del proyecto
            proyecto = {**(proyecto or {}), "ambientacion": p["ambientacion"],
                        "negativo": p.get("negativo", "")}
        preset = self.carpeta_preset(p.get("preset") or (proyecto or {}).get("preset", ""))
        estilo = json.loads((preset / "estilo.json").read_text())
        plantilla = tipo_proyecto(proyecto, p.get("tipo") or None)
        prompt_ia, _, proyecto_ia, tipo_ia = traducir_textos(str(p.get("prompt", "")), proyecto,
                                                             plantilla, True)
        return gen.previsualizar(estilo, prompt_ia, proyecto_ia, tipo_ia)

    def _ejecutar(self, p: dict) -> dict:
        def num(clave, tipo=float):
            return tipo(p[clave]) if p.get(clave) not in (None, "") else None

        proyecto = cargar_proyecto(p["proyecto"], self.carpeta_proyectos) if p.get("proyecto") else None
        preset = self.carpeta_preset(p.get("preset") or (proyecto or {}).get("preset", ""))
        r = forjar(
            decodificar_imagen(p["boceto"]), preset, p["prompt"], proyecto=proyecto,
            tipo=p.get("tipo") or None,
            variantes=int(p.get("variantes", 4)), semilla=num("semilla", int),
            modo=p.get("modo", gen.MODO_POR_DEFECTO),
            parecido_boceto=num("parecido_boceto"), fuerza_estilo=num("fuerza_estilo"),
            alto_figura=num("alto_figura", int), max_colores=num("colores", int),
            generador=self.generador if p.get("motor_ia", "local") == "local" else None,
            traducir=p.get("traducir", True) is not False,
            segmentar=self.segmentar, motor_ia=p.get("motor_ia", "local"),
            **self._opciones_boceto(p),
            carpeta_salidas=self.carpeta_salidas)
        return self._describir(r.carpeta, r.info)

    def _describir(self, carpeta: Path, info: dict) -> dict:
        base = f"/api/salidas/{carpeta.name}"
        marca = int(time.time() * 1000)  # evita que la app muestre una versión en caché
        return {
            "carpeta": carpeta.name,
            "ruta": str(carpeta),
            "boceto_limpio": f"{base}/boceto_limpio.png",
            "variantes": [{
                "indice": v["indice"], "semilla": v["semilla"], "colores": v["colores"],
                "ia": f"{base}/{v['ia']}", "sprite": f"{base}/{v['sprite']}?v={marca}",
                "sprite_vista": f"{base}/{v['sprite_vista']}?v={marca}",
                "nombre": v.get("nombre"), "origen": v.get("origen"),
            } for v in info["variantes"]],
            "info": info,
        }

    def trabajo(self, id_: str) -> dict:
        t = self.trabajos.get(id_)
        if t is None:
            raise KeyError(id_)
        posicion = None
        if t.estado == "en_cola":
            en_cola = [o for o in self.trabajos.values() if o.estado == "en_cola"]
            posicion = sum(1 for o in en_cola if o.creado <= t.creado)
        return {"id": t.id, "estado": t.estado, "posicion": posicion, "error": t.error,
                "resultado": t.resultado, "progreso": t.progreso}

    # --- operaciones rápidas (CPU) -------------------------------------------

    def repixelizar(self, p: dict) -> dict:
        """Vuelve a pixelizar una variante ya generada con otras opciones (sin IA)."""
        carpeta = self.carpeta_salida(p.get("carpeta", ""))
        info = json.loads((carpeta / "info.json").read_text())
        indice = int(p["indice"])
        v = next(x for x in info["variantes"] if x["indice"] == indice)
        cambios = {}
        if p.get("alto_figura") not in (None, ""):
            cambios["alto_figura"] = int(p["alto_figura"])
        if p.get("colores") not in (None, ""):
            cambios["max_colores"] = int(p["colores"])
        if p.get("tolerancia_fondo") not in (None, ""):
            cambios["tolerancia_fondo"] = float(p["tolerancia_fondo"])
        if p.get("contorno") is not None:
            cambios["contorno"] = bool(p["contorno"])
        op = Opciones.desde_preset(self.carpeta_preset(p.get("preset") or info["preset"]), **cambios)
        imagen = Image.open(carpeta / v["ia"])
        silueta = silueta_de_boceto(Image.open(carpeta / "boceto_limpio.png"), imagen.size)
        figura = segmentador.mascara(imagen) if self.segmentar and segmentador.disponible() else None
        px = pixelizar(imagen, op, silueta, figura)
        px.imagen.save(carpeta / v["sprite"])
        ampliar(px.imagen, AMPLIACION_VISTA).save(carpeta / v["sprite_vista"])
        v["colores"] = px.colores
        (carpeta / "info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n")
        return self._describir(carpeta, info)

    def guardar(self, p: dict) -> dict:
        """Guarda el sprite retocado en la app como final_XX.png (+ ampliado)."""
        carpeta = self.carpeta_salida(p.get("carpeta", ""))
        indice = int(p["indice"])
        img = decodificar_imagen(p["sprite"]).convert("RGBA")
        if img.width > 256 or img.height > 256:
            raise ValueError("El sprite es demasiado grande (máx. 256 px)")
        final = carpeta / f"final_{indice:02d}.png"
        img.save(final)
        ampliar(img, AMPLIACION_VISTA).save(carpeta / f"final_{indice:02d}_x{AMPLIACION_VISTA}.png")
        return {"ruta": str(final), "url": f"/api/salidas/{carpeta.name}/{final.name}"}


def crear_manejador(motor: Motor, origenes: set[str]):
    class Manejador(BaseHTTPRequestHandler):
        server_version = f"Forja/{__version__}"

        def log_message(self, formato, *args):  # al logger, no a stderr
            log.debug("%s - %s", self.address_string(), formato % args)

        # --- respuestas ---
        def _cors(self):
            origen = self.headers.get("Origin")
            if origen in origenes:
                self.send_header("Access-Control-Allow-Origin", origen)
                self.send_header("Vary", "Origin")

        def _json(self, datos, estado=HTTPStatus.OK):
            cuerpo = json.dumps(datos, ensure_ascii=False).encode()
            self.send_response(estado)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _error(self, estado, mensaje):
            self._json({"error": mensaje}, estado)

        def _archivo(self, ruta: Path, base: Path):
            try:
                ruta = ruta.resolve()
                ruta.relative_to(base.resolve())
            except ValueError:
                return self._error(HTTPStatus.FORBIDDEN, "Ruta no permitida")
            if not ruta.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "No existe")
            datos = ruta.read_bytes()
            self.send_response(HTTPStatus.OK)
            self._cors()
            self.send_header("Content-Type", mimetypes.guess_type(ruta.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(datos)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(datos)

        # --- verbos ---
        def do_OPTIONS(self):  # noqa: N802
            if self.headers.get("Origin") not in origenes:
                return self._error(HTTPStatus.FORBIDDEN, "Origen no permitido")
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", f"Content-Type, {CABECERA_PROPIA}")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()

        def do_GET(self):  # noqa: N802
            ruta = unquote(urlparse(self.path).path)
            try:
                if ruta == "/api/estado":
                    return self._json(motor.estado())
                if ruta == "/api/presets":
                    return self._json(motor.presets())
                if ruta == "/api/proyectos":
                    return self._json(motor.proyectos())
                if ruta == "/api/tipos":
                    return self._json(proyectos_mod.plantillas())
                if m := re.fullmatch(r"/api/proyectos/([a-z0-9\-]+)/personajes", ruta):
                    return self._json(motor.personajes(m[1]))
                if ruta == "/api/mapas-grandes/ambientes":
                    return self._json(motor.ambientes_mapa_grande())
                if m := re.fullmatch(r"/api/proyectos/([a-z0-9\-]+)/mapas", ruta):
                    return self._json(motor.mapas(m[1]))
                if m := re.fullmatch(r"/api/resultados/([\w.\-]+)", ruta):
                    return self._json(motor.resultado(m[1]))
                if m := re.fullmatch(r"/api/animaciones/([\w.\-]+)/(\d+)", ruta):
                    return self._json(motor.animaciones(m[1], int(m[2])))
                if m := re.fullmatch(r"/api/direcciones/([\w.\-]+)/(\d+)", ruta):
                    return self._json(motor.vistas(m[1], int(m[2])))
                if ruta == "/api/config":
                    return self._json(config.publico())
                if m := re.fullmatch(r"/api/presets/([\w.\-]+)/([\w.\-]+)", ruta):
                    carpeta = motor.carpeta_preset(m[1])
                    return self._archivo(carpeta / m[2], carpeta)
                if m := re.fullmatch(r"/api/trabajos/(\d+)", ruta):
                    return self._json(motor.trabajo(m[1]))
                if m := re.fullmatch(r"/api/salidas/([\w.\-]+)/((?:[\w.\-]+/){0,2}[\w.\-]+)", ruta):
                    carpeta = motor.carpeta_salida(m[1])
                    return self._archivo(carpeta / m[2], carpeta)
                if not ruta.startswith("/api/"):
                    destino = CARPETA_UI / (ruta.lstrip("/") or "index.html")
                    return self._archivo(destino, CARPETA_UI)
                return self._error(HTTPStatus.NOT_FOUND, "Ruta desconocida")
            except KeyError:
                return self._error(HTTPStatus.NOT_FOUND, "No existe")
            except ValueError as e:
                return self._error(HTTPStatus.BAD_REQUEST, str(e))

        def do_POST(self):  # noqa: N802
            if self.headers.get(CABECERA_PROPIA) != "1":
                return self._error(HTTPStatus.FORBIDDEN, f"Falta la cabecera {CABECERA_PROPIA}")
            largo = int(self.headers.get("Content-Length") or 0)
            if largo > MAX_CUERPO:
                return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Petición demasiado grande")
            try:
                datos = json.loads(self.rfile.read(largo) or b"{}")
            except json.JSONDecodeError:
                return self._error(HTTPStatus.BAD_REQUEST, "JSON inválido")
            ruta = urlparse(self.path).path
            try:
                if ruta == "/api/generar":
                    t = motor.encolar(datos)
                    return self._json({"trabajo": t.id}, HTTPStatus.ACCEPTED)
                if ruta == "/api/proyectos":
                    return self._json(motor.guardar_proyecto(datos))
                if ruta == "/api/tipos":
                    return self._json(proyectos_mod.guardar_plantillas(datos.get("tipos")))
                if ruta == "/api/presets":
                    return self._json(motor.crear_preset(datos))
                if m := re.fullmatch(r"/api/presets/([\w.\-]+)/borrar", ruta):
                    return self._json(motor.borrar_preset(m[1]))
                if m := re.fullmatch(r"/api/presets/([\w.\-]+)", ruta):
                    return self._json(motor.actualizar_preset(m[1], datos))
                if ruta == "/api/config":
                    return self._json(motor.guardar_config(datos))
                if ruta == "/api/mapas-grandes":
                    t = motor.encolar_mapa_grande(datos)
                    return self._json({"trabajo": t.id}, HTTPStatus.ACCEPTED)
                if ruta == "/api/salas":
                    t = motor.encolar_sala(datos)
                    return self._json({"trabajo": t.id}, HTTPStatus.ACCEPTED)
                if ruta == "/api/mapas":
                    t = motor.encolar_mapa(datos)
                    return self._json({"trabajo": t.id}, HTTPStatus.ACCEPTED)
                if ruta == "/api/generar-todo":
                    t = motor.encolar_todo(datos)
                    return self._json({"trabajo": t.id}, HTTPStatus.ACCEPTED)
                if ruta == "/api/direcciones":
                    t = motor.encolar_vista(datos)
                    return self._json({"trabajo": t.id}, HTTPStatus.ACCEPTED)
                if ruta == "/api/recolorear":
                    return self._json(motor.recolorear(datos))
                if ruta == "/api/redisenar":
                    t = motor.encolar_rediseno(datos)
                    return self._json({"trabajo": t.id}, HTTPStatus.ACCEPTED)
                if ruta == "/api/animar":
                    t = motor.encolar_animacion(datos)
                    return self._json({"trabajo": t.id}, HTTPStatus.ACCEPTED)
                if ruta == "/api/animaciones/borrar":
                    return self._json(motor.borrar_animacion(datos))
                if ruta == "/api/prompt":
                    return self._json(motor.previsualizar_prompt(datos))
                if ruta == "/api/boceto":
                    return self._json(motor.previsualizar_boceto(datos))
                if ruta == "/api/config/probar-gemini":
                    return self._json(motor.probar_gemini(datos))
                if ruta == "/api/config/probar-qwen":
                    return self._json(motor.probar_qwen(datos))
                if ruta == "/api/pixelizar":
                    return self._json(motor.repixelizar(datos))
                if ruta == "/api/guardar":
                    return self._json(motor.guardar(datos))
                if ruta == "/api/liberar":
                    motor.gestor.liberar()
                    return self._json({"modelos_cargados": motor.gestor.cargada})
                return self._error(HTTPStatus.NOT_FOUND, "Ruta desconocida")
            except (ValueError, KeyError, StopIteration) as e:
                return self._error(HTTPStatus.BAD_REQUEST, str(e) or type(e).__name__)
            except Exception as e:  # noqa: BLE001
                log.error("Error en %s:\n%s", ruta, traceback.format_exc())
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")

    return Manejador


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Que la app cierre o recargue a mitad de una respuesta no es un error.
        import sys

        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def crear_servidor(motor: Motor, puerto: int = 8765) -> ThreadingHTTPServer:
    servidor = Servidor(("127.0.0.1", puerto), None)
    puerto_real = servidor.server_address[1]
    origenes = {"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
                f"http://127.0.0.1:{puerto_real}", f"http://localhost:{puerto_real}"}
    servidor.RequestHandlerClass = crear_manejador(motor, origenes)
    return servidor


def main() -> None:
    ap = argparse.ArgumentParser(prog="forja-servidor", description="API local del motor.")
    ap.add_argument("--puerto", type=int, default=8765)
    ap.add_argument("--minutos-inactividad", type=float, default=5.0,
                    help="libera la VRAM tras estos minutos sin generar (0 = nunca)")
    ap.add_argument("--ahorro-vram", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    motor = Motor(minutos_inactividad=args.minutos_inactividad, ahorro_vram=args.ahorro_vram)
    servidor = crear_servidor(motor, args.puerto)
    log.info("Forja escuchando en http://127.0.0.1:%d", servidor.server_address[1])
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        motor.gestor.liberar()


if __name__ == "__main__":
    main()
