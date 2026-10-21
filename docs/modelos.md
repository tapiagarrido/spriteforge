# Modelos de IA

Los pesos **no** están en el repositorio: se descargan de Hugging Face a
`modelos/` (ignorada por git). Solo se bajan los archivos necesarios, en fp16
cuando existe: **~14 GB** en total.

```bash
.venv/bin/forja-modelos estado
.venv/bin/forja-modelos descargar            # todos los que falten
.venv/bin/forja-modelos descargar controlnet # uno concreto
```

La carpeta se puede cambiar con la variable `FORJA_MODELOS=/ruta/a/modelos`.

| Alias | Repositorio | Tamaño | Uso | Licencia |
|---|---|---|---|---|
| `sdxl` | [stabilityai/stable-diffusion-xl-base-1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0) | 6,8 GB | Modelo base | CreativeML Open RAIL++-M |
| `vae` | [madebyollin/sdxl-vae-fp16-fix](https://huggingface.co/madebyollin/sdxl-vae-fp16-fix) | 0,33 GB | Decodificador estable en fp16 | MIT |
| `controlnet` | [xinsir/controlnet-scribble-sdxl-1.0](https://huggingface.co/xinsir/controlnet-scribble-sdxl-1.0) | 2,5 GB | Respetar el boceto | Apache-2.0 |
| `ip_adapter` | [h94/IP-Adapter](https://huggingface.co/h94/IP-Adapter) (plus ViT-H) | 3,4 GB | Estilo de los ejemplos | Apache-2.0 |
| `lightning` | [ByteDance/SDXL-Lightning](https://huggingface.co/ByteDance/SDXL-Lightning) (LoRA 8 pasos) | 0,39 GB | Generación rápida | CreativeML Open RAIL++-M |
| `traductor` | [Helsinki-NLP/opus-mt-es-en](https://huggingface.co/Helsinki-NLP/opus-mt-es-en) | 0,31 GB | Traducir descripciones del español | Apache-2.0 |
| `segmentador` | [ZhengPeng7/BiRefNet_lite](https://huggingface.co/ZhengPeng7/BiRefNet_lite) (commit fijado) | 0,18 GB | Separar figura y fondo | MIT |
| `pixel_art` | [nerijs/pixel-art-xl](https://huggingface.co/nerijs/pixel-art-xl) | 0,17 GB | Estética pixel art | CreativeML Open RAIL-M |

## Código de terceros

BiRefNet incluye su propio código Python (`birefnet.py`), que `transformers` ejecuta
al cargarlo (`trust_remote_code`). Para que no pueda cambiar sin control, se
descarga **fijado a un commit concreto** (`revision` en `motor/modelos.py`) y se
carga siempre desde `modelos/segmentador`, nunca desde internet. Requiere
`torchvision` (del mismo índice que PyTorch), `timm`, `kornia` y `einops`.

## Sobre las licencias

Las licencias *Open RAIL* permiten uso comercial de las imágenes generadas, pero
incluyen **restricciones de uso** (no generar contenido ilegal, dañino, etc.).
Antes de distribuir la app con los modelos incluidos, revisa cada licencia; la
forma prevista en este proyecto es que cada usuario los descargue con
`forja-modelos descargar`.

## Cambiar de modelo

El catálogo está en `motor/modelos.py` (`CATALOGO`) y la tubería en
`motor/generar.py` (`cargar_tuberia`). Para probar otra LoRA de estilo, añade una
entrada al catálogo y cárgala con `load_lora_weights` junto a las existentes.
