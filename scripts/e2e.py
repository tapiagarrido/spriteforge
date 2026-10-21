"""Prueba de punta a punta con los modelos reales (GPU).

Arranca un motor propio en otro puerto (o usa uno ya en marcha con --url) y
recorre el flujo completo como lo haría la app, comprobando cada resultado:

    estado → proyecto en español con tipos → vista previa de prompt y boceto →
    generar con IA → re-pixelizar → guardar retoque → 8 animaciones (incluidas
    las de golpe dibujado por la IA) → galería del proyecto → reabrir → borrar →
    mapa del proyecto (suelos y transiciones para Tiled)

Al terminar borra el proyecto de prueba (las salidas quedan en `salidas/` para
revisarlas) y escribe un informe JSON con tiempos en `salidas/e2e_<fecha>.json`.

Uso:
    python scripts/e2e.py                 # arranca su motor en el puerto 8766
    python scripts/e2e.py --url http://127.0.0.1:8765
    python scripts/e2e.py --rapido        # modo borrador y 2 variantes
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

RAIZ = Path(__file__).resolve().parent.parent
PROYECTO_ID = "e2e-prueba-automatica"


class Cliente:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def __call__(self, ruta: str, datos: dict | None = None, crudo: bool = False):
        req = urllib.request.Request(
            self.url + ruta, data=None if datos is None else json.dumps(datos).encode(),
            headers={"Content-Type": "application/json", "X-Forja": "1"},
            method="GET" if datos is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                cuerpo = r.read()
                return cuerpo if crudo else json.loads(cuerpo)
        except urllib.error.HTTPError as e:
            raise AssertionError(f"{ruta}: HTTP {e.code} {e.read()[:300]!r}") from None

    def esperar(self, trabajo: str, limite: float = 900) -> dict:
        inicio = time.time()
        while time.time() - inicio < limite:
            t = self(f"/api/trabajos/{trabajo}")
            if t["estado"] == "listo":
                return t["resultado"]
            if t["estado"] == "error":
                raise AssertionError(f"Trabajo {trabajo} falló: {t['error']}")
            time.sleep(1)
        raise AssertionError(f"Trabajo {trabajo}: tiempo agotado")

    def imagen(self, url: str) -> Image.Image:
        return Image.open(io.BytesIO(self(url, crudo=True))).convert("RGBA")


def data_url(ruta: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(ruta.read_bytes()).decode()


def colores(im: Image.Image) -> set:
    a = np.array(im)
    return {tuple(c) for c in a[a[..., 3] > 0][:, :3]}


class Informe:
    def __init__(self):
        self.pasos = []

    def paso(self, nombre: str):
        informe = self

        class Paso:
            def __enter__(self):
                self.t0 = time.time()
                print(f"• {nombre}…", end=" ", flush=True)
                return self

            def __exit__(self, tipo, valor, _):
                segundos = round(time.time() - self.t0, 1)
                ok = tipo is None
                informe.pasos.append({"paso": nombre, "ok": ok, "segundos": segundos,
                                      "error": None if ok else str(valor)})
                print(f"{'OK' if ok else 'FALLO'} ({segundos} s)" + ("" if ok else f"\n  {valor}"))
                return False

        return Paso()


def ejecutar(api: Cliente, rapido: bool) -> dict:
    inf = Informe()
    boceto = RAIZ / "ejemplos" / "bocetos" / "guerrero_foto.jpg"
    variantes = 2 if rapido else 4
    try:
        with inf.paso("Estado: GPU y modelos"):
            e = api("/api/estado")
            faltan = [m["alias"] for m in e["modelos"] if not m["descargado"]]
            assert not faltan, f"faltan modelos: {faltan}"
            assert e["gpu"].get("gpu"), f"sin GPU: {e['gpu'].get('motivo')}"

        with inf.paso("Proyecto en español con tipos de personaje"):
            espectro = {"nombre": "Espectro rúnico",
                        "prompt": "espectro translúcido con runas brillantes, cuerpo completo"}
            tipos = api("/api/tipos") + [espectro]
            p = api("/api/proyectos", {
                "id": PROYECTO_ID, "nombre": "Prueba E2E", "preset": "criaturas-64",
                "ambientacion": ("Un reino maldito por runas antiguas. Niebla, colores fríos y apagados, "
                                 "brillo verde."),
                "negativo": "colores alegres, aspecto tierno", "notas": "Proyecto creado por scripts/e2e.py",
                "tipos": tipos})
            assert p["id"] == PROYECTO_ID and any(t["id"] == "espectro-runico" for t in p["tipos"])

        with inf.paso("Vista previa del prompt (traducido, ≤ 77 tokens)"):
            r = api("/api/prompt", {"proyecto": PROYECTO_ID, "tipo": "espectro-runico",
                                    "prompt": "guardián encorvado con cráneo y garras largas"})
            assert r["tokens"] <= r["limite"] and "specter" in r["positivo"], r["positivo"]

        with inf.paso("Vista previa del boceto (limpio, al tamaño de generación)"):
            r = api("/api/boceto", {"boceto": data_url(boceto), "proyecto": PROYECTO_ID, "simplificar": 0.3})
            limpio = Image.open(io.BytesIO(base64.b64decode(r["imagen"].split(",", 1)[1])))
            assert limpio.size == (r["ancho"], r["alto"]) and 0.005 < (np.array(limpio) > 0).mean() < 0.3

        with inf.paso(f"Generar con IA ({variantes} variantes)"):
            t = api("/api/generar", {
                "boceto": data_url(boceto), "proyecto": PROYECTO_ID, "tipo": "espectro-runico",
                "prompt": "guardián encorvado con cráneo y garras largas", "variantes": variantes,
                "modo": "rapido" if rapido else "calidad", "semilla": 2024})
            res = api.esperar(t["trabajo"])
            carpeta = res["carpeta"]
            assert len(res["variantes"]) == variantes and res["info"]["tipo"]["id"] == "espectro-runico"
            for v in res["variantes"]:
                sprite = api.imagen(v["sprite"])
                assert sprite.size == (64, 64), sprite.size
                assert (np.array(sprite)[..., 3] > 0).mean() > 0.05, "sprite casi vacío"

        with inf.paso("Re-pixelizar con otras opciones (sin IA)"):
            r = api("/api/pixelizar", {"carpeta": carpeta, "indice": 1, "colores": 12})
            assert len(r["variantes"][0]["colores"]) <= 12

        with inf.paso("Guardar retoque"):
            sprite = api.imagen(res["variantes"][0]["sprite"].split("?")[0])
            b = io.BytesIO()
            sprite.save(b, "PNG")
            retoque = "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
            g = api("/api/guardar", {"carpeta": carpeta, "indice": 1, "sprite": retoque})
            assert g["ruta"].endswith("final_01.png")

        paleta = colores(api.imagen(g["url"]))
        animaciones = [("reposo", "deformacion"), ("caminar", "deformacion"), ("ataque", "deformacion"),
                       ("zarpazo", "ia"), ("rugido", "ia"), ("especial", "ia"), ("dano", "deformacion"),
                       ("muerte", "deformacion")]
        for nombre, golpe in animaciones:
            with inf.paso(f"Animación «{nombre}» (golpe: {golpe})"):
                datos = {"carpeta": carpeta, "indice": 1, "animacion": nombre, "golpe": golpe, "semilla": 7}
                if nombre == "especial":
                    datos["texto"] = "escupe ácido verde con la boca muy abierta"
                r = api.esperar(api("/api/animar", datos)["trabajo"])
                a = next(x for x in r["animaciones"] if x["nombre"] == nombre)
                frames = [api.imagen(u) for u in a["frames_url"]]
                assert len(frames) == a["fotogramas"] >= 4
                assert len({f.size for f in frames}) == 1, "fotogramas de distinto tamaño"
                if golpe == "deformacion":  # la deformación nunca inventa colores
                    for f in frames:
                        assert colores(f) <= paleta, "color nuevo en la animación"

        with inf.paso("Hoja de sprites + JSON (Aseprite)"):
            r = api(f"/api/animaciones/{carpeta}/1")
            meta = api(r["json_url"])
            etiquetas = [t["name"] for t in meta["meta"]["frameTags"]]
            esperadas = ["reposo", "caminar", "ataque", "zarpazo", "rugido", "dano", "muerte", "especial"]
            assert etiquetas == esperadas, etiquetas
            hoja = api.imagen(r["hoja_url"])
            assert hoja.size == (meta["meta"]["size"]["w"], meta["meta"]["size"]["h"])

        with inf.paso("Galería del proyecto y reabrir el personaje"):
            lista = api(f"/api/proyectos/{PROYECTO_ID}/personajes")
            assert lista and lista[0]["carpeta"] == carpeta and len(lista[0]["animaciones"]) == 8
            r = api(f"/api/resultados/{carpeta}")
            assert r["carpeta"] == carpeta and len(r["variantes"]) == variantes

        with inf.paso("Borrar una animación"):
            r = api("/api/animaciones/borrar", {"carpeta": carpeta, "indice": 1, "animacion": "rugido"})
            assert "rugido" not in [a["nombre"] for a in r["animaciones"]]

        with inf.paso("Mapa del proyecto (vista cenital: suelos + transiciones)"):
            t = api("/api/mapas", {"proyecto": PROYECTO_ID, "variantes": 2, "semilla": 11,
                                   "materiales": {"a": "pasto", "b": "tierra"}})
            m = api.esperar(t["trabajo"])
            assert m["perspectiva"] == "cenital" and m["baldosas"] == 16 + 1 + 1
            tileset = api.imagen(m["urls"]["tileset"])
            assert tileset.size == (8 * m["baldosa"], 3 * m["baldosa"])
            tsx = api(m["urls"]["tsx"], crudo=True).decode()
            assert 'type="corner"' in tsx and tsx.count("<wangtile") == 18
            assert api.imagen(m["urls"]["mapa_prueba"]).width == 16 * m["baldosa"]
            assert "dirt" in m["prompts"]["b"], m["prompts"]["b"]  # «tierra» traducido como suelo
    except AssertionError:
        pass  # el fallo ya quedó registrado en el informe
    finally:
        (RAIZ / "proyectos" / f"{PROYECTO_ID}.json").unlink(missing_ok=True)
    return {"fecha": datetime.now().isoformat(timespec="seconds"), "rapido": rapido, "pasos": inf.pasos,
            "ok": all(p["ok"] for p in inf.pasos) and len(inf.pasos) >= 19}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", help="motor ya en marcha (por defecto se arranca uno en el puerto 8766)")
    ap.add_argument("--rapido", action="store_true", help="modo borrador y 2 variantes")
    args = ap.parse_args()

    proceso = None
    url = args.url
    if not url:
        url = "http://127.0.0.1:8766"
        proceso = subprocess.Popen([sys.executable, "-m", "motor.servidor", "--puerto", "8766"], cwd=RAIZ,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                urllib.request.urlopen(url + "/api/estado", timeout=2)
                break
            except OSError:
                time.sleep(1)
    try:
        informe = ejecutar(Cliente(url), args.rapido)
    finally:
        if proceso:
            proceso.terminate()
            proceso.wait(timeout=30)
    destino = RAIZ / "salidas" / f"e2e_{datetime.now():%Y-%m-%d_%H%M%S}.json"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(json.dumps(informe, indent=2, ensure_ascii=False) + "\n")
    total = sum(p["segundos"] for p in informe["pasos"])
    estado = "TODO OK" if informe["ok"] else "HAY FALLOS"
    print(f"\n{estado} · {len(informe['pasos'])} pasos · {total:.0f} s · {destino}")
    sys.exit(0 if informe["ok"] else 1)


if __name__ == "__main__":
    main()
