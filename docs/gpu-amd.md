# GPU AMD (ROCm)

La generación con IA usa PyTorch. En GPUs AMD, PyTorch funciona sobre **ROCm** en
lugar de CUDA.

## Equipo de referencia

- GPU: AMD Radeon RX 9060 XT, 16 GB de VRAM (arquitectura RDNA 4, `gfx1200`).
- Sistema: Ubuntu 24.04, controlador `amdgpu` del kernel.

## 1. Permisos de acceso a la GPU

El cómputo en AMD usa `/dev/kfd` y `/dev/dri/renderD*`, que pertenecen al grupo
`render`. Tu usuario debe estar en ese grupo:

```bash
groups                              # ¿aparece "render"?
sudo usermod -aG render,video $USER # si no aparece
```

Cierra sesión y vuelve a entrar para que el cambio surta efecto. Para probar sin
cerrar sesión, ejecuta los comandos dentro del grupo con `sg render -c "..."`.

## 2. PyTorch con ROCm

Las ruedas de PyTorch para ROCm incluyen las bibliotecas necesarias, por lo que no
hace falta instalar ROCm completo en el sistema. Versión usada en el proyecto:
**PyTorch 2.14.1 + ROCm 7.2** (la rueda pesa ~6 GB):

```bash
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/rocm7.2
```

Si en el futuro hay una versión más nueva, consulta el selector de pytorch.org y la
tabla de compatibilidad de AMD para confirmar que soporta `gfx1200`.

Comprobación:

```bash
.venv/bin/python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

En ROCm, PyTorch expone la GPU con la API `torch.cuda`, así que `True` y el nombre
de la tarjeta indican que todo está listo.

## 3. Ajustes necesarios (ya aplicados en `motor/generar.py`)

| Ajuste | Por qué |
|---|---|
| `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1` | Activa la atención eficiente en esta GPU. Sin ella: **55 s/imagen** y errores de memoria; con ella: **~2 s/imagen** en modo rápido |
| **No** usar `PYTORCH_*_ALLOC_CONF=expandable_segments:True` | Con ROCm 7.2 provoca `illegal memory access` al mover los modelos a la GPU |
| VAE con *slicing* y *tiling* | Decodificar 4 imágenes 576×1024 a la vez se queda sin VRAM |

## Rendimiento medido

| Operación | Tiempo |
|---|---|
| Cargar todos los modelos | ~14 s |
| 4 imágenes, modo calidad (28 pasos) | ~44 s |
| 4 imágenes, modo rápido (8 pasos) | ~9 s |
| Primera generación con un tamaño o lote nuevo | 1–6 min (MIOpen compila y ajusta kernels; queda en caché en `~/.cache/miopen`) |

VRAM libre con todo cargado: ~2–4 GB de 16.

## Problemas frecuentes

| Síntoma | Causa probable |
|---|---|
| `torch.cuda.is_available()` es `False` | Usuario fuera del grupo `render`, o rueda de PyTorch sin ROCm |
| `HIP error: invalid device function` | La versión de ROCm de la rueda no soporta `gfx1200` |
| Memoria insuficiente | Usar `--ahorro-vram`, menos variantes o bajar `resolucion_generacion` |
| `illegal memory access` al cargar | Quitar `expandable_segments` de `PYTORCH_CUDA_ALLOC_CONF` / `PYTORCH_HIP_ALLOC_CONF` |
| La primera generación tarda minutos | Normal: compilación de kernels. Las siguientes son rápidas |
