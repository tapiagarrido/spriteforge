"""Generación con IA: boceto limpio + preset + prompt -> imágenes de 512-1024 px.

Combina:
    - SDXL 1.0 (fp16) como modelo base.
    - ControlNet scribble: obliga a respetar el trazo del boceto.
    - IP-Adapter plus en modo "solo estilo" (InstantStyle): toma el estilo de los
      ejemplos del preset sin copiar su contenido.
    - LoRA pixel-art-xl: empuja hacia pixel art.
    - LoRA SDXL-Lightning (modo rápido): 8 pasos en lugar de ~30.

Los modelos se cargan bajo demanda y se liberan tras un rato sin uso
(`GestorModelos`), para no ocupar la VRAM mientras no se genera.

PyTorch y diffusers son dependencias opcionales (`pip install -e '.[ia]'`); este
módulo se puede importar sin ellas, solo fallan las funciones que las usan.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from motor.modelos import CATALOGO
from motor.pixelizar import ampliar

log = logging.getLogger(__name__)

# Atención eficiente (flash / mem-efficient) en GPUs AMD recientes: PyTorch la marca
# como experimental y la desactiva por defecto; sin ella la atención usa la ruta
# "math", mucho más lenta y con más VRAM. Debe fijarse antes de importar torch.
os.environ.setdefault("TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL", "1")

# Nota: NO activar PYTORCH_*_ALLOC_CONF=expandable_segments:True. Con ROCm 7.2 y
# la RX 9060 XT provoca "illegal memory access" al mover los modelos a la GPU.


@dataclass(frozen=True)
class Modo:
    pasos: int
    guia: float  # classifier-free guidance; 1.0 = desactivada (Lightning)
    lightning: bool


MODOS = {
    # Borrador: ~2 s/imagen. Sin prompt negativo (CFG 1), así que la IA suele
    # pintar fondos grises con sombras y la separación figura/fondo empeora.
    "rapido": Modo(pasos=8, guia=1.0, lightning=True),
    # Por defecto: ~11 s/imagen. El negativo consigue fondos blancos limpios.
    "calidad": Modo(pasos=28, guia=6.0, lightning=False),
}
MODO_POR_DEFECTO = "calidad"


@dataclass
class Peticion:
    """Todo lo necesario para una generación."""

    boceto: Image.Image  # ya limpio: trazo blanco sobre negro, de ancho x alto
    referencia: Image.Image  # rejilla con los ejemplos del preset
    prompt: str
    prompt_negativo: str
    ancho: int
    alto: int
    variantes: int = 4
    semilla: int = 0
    modo: str = "calidad"
    fuerza_boceto: float = 0.68
    fin_boceto: float = 0.65  # fracción de los pasos en que actúa ControlNet (1 = todos)
    fuerza_estilo: float = 0.3
    fuerza_pixel_art: float = 0.8
    extra: dict = field(default_factory=dict)


# --- preparación (sin IA, testeable) -------------------------------------------

def tamano_generacion(ancho_sprite: int, alto_sprite: int, resolucion: int) -> tuple[int, int]:
    """Lienzo de generación con la proporción del sprite y área ~resolucion², múltiplo de 64."""
    proporcion = ancho_sprite / alto_sprite
    ancho = (resolucion * proporcion ** 0.5)
    alto = (resolucion / proporcion ** 0.5)

    def a64(v: float) -> int:
        return max(64, int(round(v / 64)) * 64)

    return a64(ancho), a64(alto)


FONDO_PROMPT = "plain white background"
LIMITE_TOKENS = 77  # CLIP ignora lo que pase de aquí


def controlnet_desde_parecido(parecido: float) -> tuple[float, float]:
    """'Parecido al boceto' (0 libre … 1 calcado) -> (fuerza, fin) de ControlNet.

    Dos palancas a la vez:
    - la fuerza con que el boceto empuja a la IA;
    - hasta qué fracción de los pasos actúa. Los primeros pasos fijan pose y
      composición, los últimos proporciones y detalle. Soltar el boceto pronto
      conserva la pose pero deja a la IA adaptar el cuerpo al estilo (chibi).
    """
    p = min(1.0, max(0.0, float(parecido)))
    return round(0.35 + 0.65 * p, 3), round(0.3 + 0.7 * p, 3)


_tokenizador = None


def contar_tokens(texto: str) -> int:
    """Tokens CLIP de `texto` (con el tokenizador de SDXL si está descargado)."""
    global _tokenizador
    if _tokenizador is None:
        carpeta = CATALOGO["sdxl"].carpeta / "tokenizer"
        try:
            from transformers import CLIPTokenizer

            _tokenizador = CLIPTokenizer.from_pretrained(carpeta)
        except Exception:  # noqa: BLE001 - sin modelos o sin transformers: estimación
            _tokenizador = False
    if _tokenizador:
        return len(_tokenizador(texto).input_ids)
    return int(len(re.findall(r"\w+|[^\w\s]", texto)) * 1.2) + 2


def unir_con_limite(obligatorias: list[str], opcionales: list[str], limite: int = LIMITE_TOKENS) -> str:
    """Une las partes y descarta opcionales desde el final hasta caber en `limite` tokens."""
    opcionales = list(opcionales)
    while True:
        texto = ", ".join(p for p in [*obligatorias, *opcionales] if p)
        if contar_tokens(texto) <= limite or not opcionales:
            return texto
        opcionales.pop()


def _partes(texto: str | None) -> list[str]:
    return [t.strip() for t in (texto or "").split(",") if t.strip()]


def construir_prompt(estilo: dict, prompt_usuario: str, proyecto: dict | None = None,
                     tipo: dict | None = None) -> tuple[str, str]:
    """Prompt final, por prioridad:

        "pixel art", fondo blanco, tipo de personaje, descripción del usuario,
        ambientación del proyecto, resto del estilo del preset

    CLIP da más peso a lo primero y descarta lo que pase de 77 tokens. El tipo y la
    descripción nunca se recortan; si no cabe todo, se sueltan primero matices del
    estilo del preset y después el final de la ambientación (ver `previsualizar`).

    El fondo se pide blanco y sin nombrar otro color: en las pruebas, "solid blue
    background" teñía de azul la armadura y la piel del personaje.

    El negativo es el del tipo + el del proyecto + el del preset.

    Un tipo con `usar_ambientacion: false` (efectos como una bola de fuego) no recibe
    la ambientación: en las pruebas, el "brillo verde, colores fríos" de un mundo
    convertía la bola de fuego en un orbe verde.
    """
    gen = estilo["generacion"]
    base = _partes(gen["prompt_base"])
    disparador = [t for t in base if t.lower() == "pixel art"] or ["pixel art"]
    resto = [t for t in base if t.lower() != "pixel art"]
    obligatorias = [*disparador, FONDO_PROMPT, *_partes((tipo or {}).get("prompt")), prompt_usuario.strip()]
    positivo = unir_con_limite(obligatorias, _ambientacion(proyecto, tipo) + resto)
    negativo_propio = _partes((tipo or {}).get("negativo")) + _partes((proyecto or {}).get("negativo"))
    negativo = unir_con_limite(negativo_propio, _partes(gen["prompt_negativo"]))
    return positivo, negativo


def _ambientacion(proyecto: dict | None, tipo: dict | None) -> list[str]:
    if not (tipo or {}).get("usar_ambientacion", True):
        return []
    return _partes((proyecto or {}).get("ambientacion"))


def previsualizar(estilo: dict, prompt_usuario: str, proyecto: dict | None = None,
                  tipo: dict | None = None) -> dict:
    """Qué recibirá la IA y qué se queda fuera por el límite de tokens."""
    positivo, negativo = construir_prompt(estilo, prompt_usuario, proyecto, tipo)
    incluidas = set(_partes(positivo))
    ambientacion = _ambientacion(proyecto, tipo)
    return {
        "positivo": positivo, "negativo": negativo,
        "tokens": contar_tokens(positivo), "limite": LIMITE_TOKENS,
        "ambientacion_fuera": [p for p in ambientacion if p not in incluidas],
        "estilo_fuera": [p for p in _partes(estilo["generacion"]["prompt_base"]) if p not in incluidas],
    }


def rejilla_referencia(carpeta_preset: Path, estilo: dict, lado: int = 512) -> Image.Image:
    """Ejemplos del preset en una rejilla 2x2 cuadrada, ampliados sin suavizado.

    Cuadrada porque el codificador de imágenes de IP-Adapter recorta al centro:
    una tira horizontal perdería los ejemplos de los extremos. El fondo es el de
    generación, que así también se "contagia" como parte del estilo.
    """
    ejemplos = [Image.open(Path(carpeta_preset) / e).convert("RGBA") for e in estilo["ejemplos"]][:4]
    celda = lado // 2
    lienzo = Image.new("RGBA", (lado, lado), estilo["fondo_generacion"])
    for i, ej in enumerate(ejemplos):
        factor = max(1, int(celda * 0.85 / max(ej.size)))
        grande = ampliar(ej, factor)
        x = (i % 2) * celda + (celda - grande.width) // 2
        y = (i // 2) * celda + (celda - grande.height) // 2
        lienzo.alpha_composite(grande, (x, y))
    return lienzo.convert("RGB")


# --- modelos ---------------------------------------------------------------------

def dispositivo() -> tuple[str, object]:
    """("cuda", fp16) si hay GPU; si no, CPU en fp32 (o bf16 con FORJA_CPU_BF16=1, la mitad de RAM)."""
    import torch

    if torch.cuda.is_available():  # en ROCm la GPU AMD también se expone como "cuda"
        return "cuda", torch.float16
    return "cpu", torch.bfloat16 if os.environ.get("FORJA_CPU_BF16") == "1" else torch.float32


def info_gpu() -> dict:
    """Estado de PyTorch y la GPU, sin cargar modelos."""
    try:
        import torch
    except ImportError:
        return {"torch": None, "gpu": None, "motivo": "PyTorch no está instalado (pip install -e '.[ia]')"}
    datos = {"torch": torch.__version__, "rocm": getattr(torch.version, "hip", None),
             "cuda": torch.version.cuda, "gpu": None}
    if torch.cuda.is_available():
        libre, total = torch.cuda.mem_get_info()
        datos.update(gpu=torch.cuda.get_device_name(0), vram_total_gb=round(total / 2**30, 1),
                     vram_libre_gb=round(libre / 2**30, 1))
    else:
        datos["motivo"] = ("PyTorch no ve ninguna GPU. En AMD: ¿tu usuario está en el grupo "
                           "'render'? (ver docs/gpu-amd.md)")
    return datos


def cargar_tuberia(ahorro_vram: bool = False):
    """Construye la tubería SDXL + ControlNet + IP-Adapter + LoRAs desde `modelos/`."""
    from diffusers import (
        AutoencoderKL,
        ControlNetModel,
        EulerDiscreteScheduler,
        StableDiffusionXLControlNetPipeline,
    )
    from transformers import CLIPVisionModelWithProjection

    faltan = [m.alias for m in CATALOGO.values() if not m.descargado()]
    if faltan:
        raise RuntimeError(f"Faltan modelos: {', '.join(faltan)}. Ejecuta: forja-modelos descargar")

    disp, dtype = dispositivo()
    m = CATALOGO
    t0 = time.perf_counter()
    controlnet = ControlNetModel.from_pretrained(m["controlnet"].carpeta, torch_dtype=dtype)
    vae = AutoencoderKL.from_pretrained(m["vae"].carpeta, torch_dtype=dtype)
    codificador = CLIPVisionModelWithProjection.from_pretrained(
        m["ip_adapter"].carpeta / "models" / "image_encoder", torch_dtype=dtype)
    tuberia = StableDiffusionXLControlNetPipeline.from_pretrained(
        m["sdxl"].carpeta, controlnet=controlnet, vae=vae, image_encoder=codificador,
        torch_dtype=dtype, variant="fp16")  # solo se descargan los pesos fp16 (ver modelos.py)
    tuberia.scheduler = EulerDiscreteScheduler.from_config(
        tuberia.scheduler.config, timestep_spacing="trailing")
    tuberia.load_ip_adapter(m["ip_adapter"].carpeta, subfolder="sdxl_models",
                            weight_name="ip-adapter-plus_sdxl_vit-h.safetensors",
                            image_encoder_folder=None)
    tuberia.load_lora_weights(m["lightning"].carpeta, weight_name="sdxl_lightning_8step_lora.safetensors",
                              adapter_name="lightning")
    tuberia.load_lora_weights(m["pixel_art"].carpeta, weight_name="pixel-art-xl.safetensors",
                              adapter_name="pixel_art")
    if ahorro_vram and disp == "cuda":
        tuberia.enable_model_cpu_offload()
    else:
        tuberia.to(disp)
    # Decodificar de una en una (slicing) y por mosaicos (tiling): con la UNet y
    # el ControlNet en VRAM, decodificar 576x1024 de golpe pide 2.5 GB más.
    tuberia.vae.enable_slicing()
    tuberia.vae.enable_tiling()
    tuberia.set_progress_bar_config(disable=True)
    log.info("Tubería cargada en %s (%s) en %.1f s", disp, dtype, time.perf_counter() - t0)
    return tuberia


def _vaciar_cache_gpu() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


class GestorModelos:
    """Carga la tubería al pedirla y la libera tras `minutos_inactividad` sin uso."""

    def __init__(self, minutos_inactividad: float = 5.0, ahorro_vram: bool = False, cargador=None):
        self.minutos_inactividad = minutos_inactividad
        self.ahorro_vram = ahorro_vram
        self._cargador = cargador or (lambda: cargar_tuberia(self.ahorro_vram))
        self._tuberia = None
        self._ultimo_uso = 0.0
        self._lock = threading.RLock()
        self._vigilante: threading.Thread | None = None

    @property
    def cargada(self) -> bool:
        return self._tuberia is not None

    def obtener(self):
        with self._lock:
            if self._tuberia is None:
                _vaciar_cache_gpu()
                self._tuberia = self._cargador()
                self._iniciar_vigilante()
            self._ultimo_uso = time.monotonic()
            return self._tuberia

    def liberar(self) -> None:
        with self._lock:
            if self._tuberia is None:
                return
            self._tuberia = None
            # Las tuberías derivadas (img2img de las poses, texto a imagen de los mapas)
            # comparten los componentes: si siguieran vivas, la VRAM no se liberaría.
            for derivada in ("_img2img", "_img2img_de", "_texto", "_texto_de"):
                if hasattr(self, derivada):
                    delattr(self, derivada)
            from motor import segmentar

            segmentar.liberar()
            try:
                import gc

                import torch
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
            log.info("Modelos liberados de la memoria")

    def _iniciar_vigilante(self) -> None:
        if self.minutos_inactividad <= 0 or (self._vigilante and self._vigilante.is_alive()):
            return

        def vigilar():
            while True:
                time.sleep(15)
                with self._lock:
                    if self._tuberia is None:
                        return
                    if time.monotonic() - self._ultimo_uso > self.minutos_inactividad * 60:
                        self.liberar()
                        return

        self._vigilante = threading.Thread(target=vigilar, daemon=True, name="liberar-vram")
        self._vigilante.start()


# --- generación ------------------------------------------------------------------

def generar(gestor: GestorModelos, p: Peticion) -> list[Image.Image]:
    """Ejecuta la tubería y devuelve `p.variantes` imágenes RGB."""
    import torch

    modo = MODOS[p.modo]
    with gestor._lock:  # una generación a la vez sobre la misma tubería
        tuberia = gestor.obtener()
        if modo.lightning:
            tuberia.set_adapters(["lightning", "pixel_art"], adapter_weights=[1.0, p.fuerza_pixel_art])
        else:
            tuberia.set_adapters(["pixel_art"], adapter_weights=[p.fuerza_pixel_art])
        # InstantStyle: el IP-Adapter solo actúa en el bloque que codifica el estilo.
        tuberia.set_ip_adapter_scale({"up": {"block_0": [0.0, p.fuerza_estilo, 0.0]}})

        disp = "cuda" if torch.cuda.is_available() else "cpu"
        generadores = [torch.Generator(disp).manual_seed(p.semilla + i) for i in range(p.variantes)]
        t0 = time.perf_counter()
        salida = tuberia(
            prompt=p.prompt,
            negative_prompt=p.prompt_negativo if modo.guia > 1 else None,
            image=p.boceto.convert("RGB"),
            controlnet_conditioning_scale=p.fuerza_boceto,
            control_guidance_end=p.fin_boceto,
            ip_adapter_image=p.referencia,
            num_inference_steps=modo.pasos,
            guidance_scale=modo.guia,
            width=p.ancho,
            height=p.alto,
            num_images_per_prompt=p.variantes,
            generator=generadores,
        )
        gestor._ultimo_uso = time.monotonic()
        log.info("%d imágenes %dx%d en %.1f s (modo %s)", p.variantes, p.ancho, p.alto,
                 time.perf_counter() - t0, p.modo)
        return salida.images


def peticion_desde_preset(carpeta_preset: Path, boceto_limpio: Image.Image, prompt: str,
                          proyecto: dict | None = None, parecido_boceto: float | None = None,
                          tipo: dict | None = None, prompt_base: str | None = None, **cambios) -> Peticion:
    """Peticion con los valores del preset y del proyecto, sobrescribibles con `cambios`.

    `prompt_base` sustituye al texto de estilo del preset (los decorados no deben llevar el
    "gen 4 pokemon style sprite, full body" de las criaturas: salían con cara).

    `parecido_boceto` (0 libre … 1 calcado) tiene prioridad sobre el del proyecto
    y el del preset; se traduce a fuerza y duración de ControlNet.
    """
    carpeta_preset = Path(carpeta_preset)
    estilo = json.loads((carpeta_preset / "estilo.json").read_text())
    if prompt_base is not None:
        estilo = {**estilo, "generacion": {**estilo["generacion"], "prompt_base": prompt_base}}
    gen = estilo["generacion"]
    positivo, negativo = construir_prompt(estilo, prompt, proyecto, tipo)
    if parecido_boceto is None:
        parecido_boceto = (proyecto or {}).get("parecido_boceto", gen.get("parecido_boceto", 0.5))
    fuerza_boceto, fin_boceto = controlnet_desde_parecido(parecido_boceto)
    base = dict(
        boceto=boceto_limpio, referencia=rejilla_referencia(carpeta_preset, estilo),
        prompt=positivo, prompt_negativo=negativo,
        ancho=boceto_limpio.width, alto=boceto_limpio.height,
        fuerza_boceto=fuerza_boceto, fin_boceto=fin_boceto,
        fuerza_estilo=gen.get("fuerza_estilo", 0.3), fuerza_pixel_art=gen.get("fuerza_pixel_art", 0.8),
    )
    base.update({k: v for k, v in cambios.items() if v is not None})
    return Peticion(**base)
