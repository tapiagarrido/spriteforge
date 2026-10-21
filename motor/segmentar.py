"""Separación figura/fondo con un modelo de segmentación (BiRefNet lite, MIT).

La separación por color (`pixelizar.mascara_figura`) falla cuando la IA pinta
fondos parecidos a la figura: personajes oscuros sobre fondos oscuros (algo
habitual en proyectos sombríos), fondos con trama, figuras claras de contorno
suave. Un modelo entrenado para recortar sujetos no depende del color.

El modelo trae su propio código (`birefnet.py`); se descarga fijado a un commit
concreto (ver `modelos.py`) y se carga desde `modelos/segmentador`, nunca desde
internet. Pesa ~180 MB; en GPU tarda ~0.1 s por imagen y en CPU ~1-2 s.
"""

from __future__ import annotations

import logging
import threading

import numpy as np
from PIL import Image

from motor.modelos import CATALOGO

log = logging.getLogger(__name__)

LADO = 1024  # resolución de entrada con la que se entrenó
MEDIA = np.array([0.485, 0.456, 0.406])
DESVIACION = np.array([0.229, 0.224, 0.225])

_estado: dict = {}
_lock = threading.Lock()


def disponible() -> bool:
    if not CATALOGO["segmentador"].descargado():
        return False
    try:
        import einops  # noqa: F401
        import kornia  # noqa: F401
        import timm  # noqa: F401
        import torchvision  # noqa: F401
    except ImportError:
        return False
    return True


def _cargar():
    with _lock:
        if "modelo" not in _estado:
            import torch
            from transformers import AutoModelForImageSegmentation

            disp = "cuda" if torch.cuda.is_available() else "cpu"
            dtype = torch.float16 if disp == "cuda" else torch.float32
            modelo = AutoModelForImageSegmentation.from_pretrained(
                CATALOGO["segmentador"].carpeta, trust_remote_code=True)
            modelo.to(disp, dtype).eval()
            _estado.update(modelo=modelo, disp=disp, dtype=dtype)
            log.info("Segmentador cargado en %s", disp)
        return _estado["modelo"], _estado["disp"], _estado["dtype"]


def liberar() -> None:
    with _lock:
        if _estado.pop("modelo", None) is not None:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()


def probabilidad_figura(imagen: Image.Image) -> np.ndarray:
    """Mapa 0..1 (del tamaño de `imagen`) de probabilidad de ser figura."""
    import torch

    modelo, disp, dtype = _cargar()
    rgb = np.asarray(imagen.convert("RGB").resize((LADO, LADO), Image.BILINEAR), dtype=np.float32) / 255
    x = torch.from_numpy(((rgb - MEDIA) / DESVIACION).transpose(2, 0, 1)[None].astype(np.float32))
    with _lock, torch.no_grad():
        try:
            salida = modelo(x.to(disp, dtype))[-1]
        except torch.OutOfMemoryError:
            # La GPU está llena (p. ej. otro proceso con SDXL cargado): se sigue en CPU.
            log.warning("Sin VRAM para el segmentador: se usa la CPU")
            torch.cuda.empty_cache()
            modelo.to("cpu", torch.float32)
            _estado.update(disp="cpu", dtype=torch.float32)
            salida = modelo(x)[-1]
        salida = salida.sigmoid()[0, 0].float().cpu().numpy()
        if _estado["disp"] == "cuda":
            torch.cuda.empty_cache()  # no retener VRAM que necesita la tubería de SDXL
    return np.asarray(Image.fromarray((salida * 255).astype(np.uint8)).resize(imagen.size, Image.BILINEAR),
                      dtype=np.float32) / 255


def mascara(imagen: Image.Image, umbral: float = 0.5) -> np.ndarray:
    """True donde está la figura, según el modelo."""
    return probabilidad_figura(imagen) >= umbral
