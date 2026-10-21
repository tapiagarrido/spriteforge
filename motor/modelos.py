"""Catálogo y descarga de los modelos de IA.

Los pesos se guardan en `modelos/` (fuera de git), una subcarpeta por modelo,
conservando la estructura del repositorio de Hugging Face. Solo se descargan
los archivos necesarios (fp16 cuando existe), unos 14 GB en total.

Uso:
    python -m motor.modelos estado
    python -m motor.modelos descargar            # todos
    python -m motor.modelos descargar sdxl vae   # algunos
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CARPETA_MODELOS = Path(os.environ.get("FORJA_MODELOS", RAIZ / "modelos"))


@dataclass(frozen=True)
class Modelo:
    alias: str
    repo: str
    archivos: tuple[str, ...]
    uso: str
    licencia: str
    gb: float
    revision: str | None = None  # commit fijo: para modelos que incluyen código propio

    @property
    def carpeta(self) -> Path:
        return CARPETA_MODELOS / self.alias

    def ruta(self, archivo: str) -> Path:
        return self.carpeta / archivo

    def descargado(self) -> bool:
        return all(self.ruta(a).exists() for a in self.archivos)


CATALOGO: dict[str, Modelo] = {m.alias: m for m in [
    Modelo(
        "sdxl", "stabilityai/stable-diffusion-xl-base-1.0",
        ("model_index.json", "scheduler/scheduler_config.json",
         "text_encoder/config.json", "text_encoder/model.fp16.safetensors",
         "text_encoder_2/config.json", "text_encoder_2/model.fp16.safetensors",
         "tokenizer/merges.txt", "tokenizer/special_tokens_map.json",
         "tokenizer/tokenizer_config.json", "tokenizer/vocab.json",
         "tokenizer_2/merges.txt", "tokenizer_2/special_tokens_map.json",
         "tokenizer_2/tokenizer_config.json", "tokenizer_2/vocab.json",
         "unet/config.json", "unet/diffusion_pytorch_model.fp16.safetensors"),
        "Modelo base de imagen (SDXL 1.0)", "CreativeML Open RAIL++-M", 6.8),
    Modelo(
        "vae", "madebyollin/sdxl-vae-fp16-fix",
        ("config.json", "diffusion_pytorch_model.safetensors"),
        "Decodificador de imagen estable en fp16", "MIT", 0.33),
    Modelo(
        "controlnet", "xinsir/controlnet-scribble-sdxl-1.0",
        ("config.json", "diffusion_pytorch_model.safetensors"),
        "Respeta el trazo del boceto (scribble)", "Apache-2.0", 2.5),
    Modelo(
        "ip_adapter", "h94/IP-Adapter",
        ("sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors",
         "models/image_encoder/config.json", "models/image_encoder/model.safetensors"),
        "Toma el estilo de los ejemplos del preset", "Apache-2.0", 3.4),
    Modelo(
        "lightning", "ByteDance/SDXL-Lightning",
        ("sdxl_lightning_8step_lora.safetensors",),
        "Generación en 8 pasos en lugar de 30", "CreativeML Open RAIL++-M", 0.39),
    Modelo(
        "traductor", "Helsinki-NLP/opus-mt-es-en",
        ("config.json", "generation_config.json", "pytorch_model.bin", "source.spm",
         "target.spm", "tokenizer_config.json", "vocab.json"),
        "Traduce al inglés las descripciones en español", "Apache-2.0", 0.31),
    Modelo(
        "segmentador", "ZhengPeng7/BiRefNet_lite",
        ("config.json", "BiRefNet_config.py", "birefnet.py", "model.safetensors"),
        "Separa el personaje del fondo (cualquier fondo)", "MIT", 0.18,
        revision="aa62cd87eafb9cc43056d08ef3615a14628b831d"),
    Modelo(
        "pixel_art", "nerijs/pixel-art-xl",
        ("pixel-art-xl.safetensors",),
        "Empuja la salida hacia pixel art", "CreativeML Open RAIL-M", 0.17),
]}


def descargar(alias: list[str] | None = None) -> None:
    from huggingface_hub import hf_hub_download  # solo hace falta al descargar

    for m in (CATALOGO[a] for a in (alias or CATALOGO)):
        print(f"[{m.alias}] {m.repo} (~{m.gb} GB)")
        for archivo in m.archivos:
            if m.ruta(archivo).exists():
                continue
            hf_hub_download(m.repo, archivo, local_dir=m.carpeta, revision=m.revision)
        print(f"[{m.alias}] listo")


def estado() -> list[tuple[Modelo, bool]]:
    return [(m, m.descargado()) for m in CATALOGO.values()]


def main() -> None:
    ap = argparse.ArgumentParser(prog="motor.modelos")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("estado", help="qué modelos están descargados")
    d = sub.add_parser("descargar", help="descarga los modelos que falten")
    d.add_argument("alias", nargs="*", help=f"uno o varios de: {', '.join(CATALOGO)}")
    args = ap.parse_args()
    desconocidos = set(getattr(args, "alias", [])) - set(CATALOGO)
    if desconocidos:
        ap.error(f"modelos desconocidos: {', '.join(sorted(desconocidos))}")

    if args.cmd == "descargar":
        descargar(args.alias or None)
    print(f"Carpeta: {CARPETA_MODELOS}")
    for m, ok in estado():
        print(f"  {'✓' if ok else '·'} {m.alias:11s} {m.gb:5.2f} GB  {m.uso}  [{m.licencia}]")


if __name__ == "__main__":
    main()
