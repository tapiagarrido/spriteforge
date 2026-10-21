"""Configuración local del usuario (clave de la API de Gemini, dirección del servidor Qwen).

Se guarda **fuera del repositorio**, en `~/.config/forja-sprites/config.json`
(o donde indique `FORJA_CONFIG`), con permisos 600: solo tu usuario puede leerla.
Las claves nunca se devuelven completas a la interfaz ni se escriben en logs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CLAVES_SECRETAS = {"gemini_api_key"}
CAMPOS = {"gemini_api_key", "gemini_modelo", "qwen_url"}


def ruta() -> Path:
    if os.environ.get("FORJA_CONFIG"):
        return Path(os.environ["FORJA_CONFIG"])
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "forja-sprites" / "config.json"


def leer() -> dict:
    archivo = ruta()
    if not archivo.exists():
        return {}
    try:
        return json.loads(archivo.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def guardar(cambios: dict) -> dict:
    """Actualiza los campos indicados. Un valor vacío o None borra el campo."""
    desconocidos = set(cambios) - CAMPOS
    if desconocidos:
        raise ValueError(f"Campos de configuración desconocidos: {', '.join(sorted(desconocidos))}")
    datos = leer()
    for clave, valor in cambios.items():
        valor = (valor or "").strip() if isinstance(valor, str) or valor is None else valor
        if valor and clave == "qwen_url":
            if not valor.startswith(("http://", "https://")) or any(c.isspace() for c in valor):
                raise ValueError("La dirección de Qwen debe empezar por http:// o https://")
            datos[clave] = valor.rstrip("/")
        elif valor:
            formato_valido = 20 <= len(valor) <= 200 and not any(c.isspace() for c in valor)
            if clave == "gemini_api_key" and not formato_valido:
                raise ValueError("La clave de Gemini no tiene un formato válido")
            datos[clave] = valor
        else:
            datos.pop(clave, None)
    archivo = ruta()
    archivo.parent.mkdir(parents=True, exist_ok=True)
    # Crear con permisos 600 desde el principio (no escribir y luego restringir).
    fd = os.open(archivo, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(datos, f, indent=2)
    os.chmod(archivo, 0o600)
    return datos


def gemini_api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or leer().get("gemini_api_key")


def qwen_url() -> str:
    from motor.qwen import URL_POR_DEFECTO

    return os.environ.get("QWEN_URL") or leer().get("qwen_url") or URL_POR_DEFECTO


def publico() -> dict:
    """Estado apto para mostrar en la interfaz: nunca incluye las claves."""
    datos = leer()
    clave = gemini_api_key()
    return {
        "gemini": {
            "configurada": bool(clave),
            "terminacion": clave[-4:] if clave else None,
            "desde_entorno": bool(os.environ.get("GEMINI_API_KEY")),
            "modelo": datos.get("gemini_modelo"),
        },
        "qwen": {"url": qwen_url(), "desde_entorno": bool(os.environ.get("QWEN_URL"))},
        "archivo": str(ruta()),
    }
