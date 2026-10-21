"""Utilidades de color: conversión hex/RGB y distancia perceptual (CIELAB)."""

from __future__ import annotations

import numpy as np

# sRGB (D65) -> XYZ
_M_XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])
_BLANCO_D65 = np.array([0.95047, 1.0, 1.08883])


def hex_a_rgb(hx: str) -> tuple[int, int, int]:
    hx = hx.lstrip("#")
    return int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)


def rgb_a_hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(int(c) for c in rgb))


def luminancia(rgb: np.ndarray) -> np.ndarray:
    """Luminancia aproximada (Rec. 601) en 0..255."""
    return np.asarray(rgb, dtype=float)[..., :3] @ np.array([0.299, 0.587, 0.114])


def rgb_a_lab(rgb: np.ndarray) -> np.ndarray:
    """Convierte RGB 0..255 (forma ...x3) a CIELAB."""
    c = np.asarray(rgb, dtype=float)[..., :3] / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = (c @ _M_XYZ.T) / _BLANCO_D65
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([
        116 * f[..., 1] - 16,
        500 * (f[..., 0] - f[..., 1]),
        200 * (f[..., 1] - f[..., 2]),
    ], axis=-1)


def color_mas_cercano(lab: np.ndarray, paleta_lab: np.ndarray) -> np.ndarray:
    """Índice del color de `paleta_lab` (Kx3) más cercano a cada píxel de `lab` (...x3).

    Distancia: delta E 76 (euclídea en Lab), suficiente para paletas pequeñas.
    """
    plano = lab.reshape(-1, 3)
    p2 = (paleta_lab ** 2).sum(1)
    salida = np.empty(len(plano), dtype=np.int32)
    # Por bloques para acotar memoria: |x-p|² = |x|² - 2x·p + |p|² (|x|² no cambia el argmin).
    for i in range(0, len(plano), _BLOQUE):
        trozo = plano[i:i + _BLOQUE]
        salida[i:i + _BLOQUE] = (p2[None, :] - 2 * trozo @ paleta_lab.T).argmin(1)
    return salida.reshape(lab.shape[:-1])


_BLOQUE = 65536



def lab_a_rgb(lab: np.ndarray) -> np.ndarray:
    """CIELAB -> RGB 0..255 (inversa de `rgb_a_lab`)."""
    lab = np.asarray(lab, dtype=float)
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], axis=-1)
    xyz = np.where(f > 0.206893, f ** 3, (f - 16 / 116) / 7.787) * _BLANCO_D65
    c = xyz @ np.linalg.inv(_M_XYZ).T
    c = np.where(c > 0.0031308, 1.055 * np.power(np.clip(c, 0, None), 1 / 2.4) - 0.055, 12.92 * c)
    return np.clip(c * 255, 0, 255)


def transferir_color(rgb: np.ndarray, mascara: np.ndarray, ref_rgb: np.ndarray,
                     ref_mascara: np.ndarray) -> np.ndarray:
    """Ajusta los colores de `rgb` (en la máscara) a la distribución de la referencia.

    Transferencia de Reinhard en CIELAB: se igualan media y desviación de cada
    canal de la figura a las de la figura de referencia. Útil para que una pose
    redibujada conserve los tonos del personaje original.
    """
    lab = rgb_a_lab(rgb)
    ref = rgb_a_lab(ref_rgb)[ref_mascara]
    origen = lab[mascara]
    if not len(origen) or not len(ref):
        return rgb
    media_o, desv_o = origen.mean(0), origen.std(0) + 1e-6
    media_r, desv_r = ref.mean(0), ref.std(0)
    salida = lab.copy()
    salida[mascara] = (origen - media_o) / desv_o * desv_r + media_r
    resultado = rgb.astype(float).copy()
    resultado[mascara] = lab_a_rgb(salida[mascara])
    return resultado.astype(np.uint8)
