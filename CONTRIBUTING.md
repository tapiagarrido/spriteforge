# Contribuir

¡Gracias por querer ayudar! Español o inglés, como prefieras.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/ruff check .
```

- Los tests no necesitan GPU ni claves: usan una IA falsa y APIs simuladas. Añade
  tests con cada cambio.
- Mantén el estilo del código existente (nombres y comentarios en español, `ruff`).
- **Sprites y assets:** solo contenido propio o con licencia libre compatible (CC0,
  CC-BY…). Anota autor, enlace y licencia en [CREDITOS.md](CREDITOS.md).
- **No incluyas** claves, modelos (`modelos/`), salidas (`salidas/`) ni
  configuración local. Ver [SECURITY.md](SECURITY.md).
- Para un motor de IA nuevo, sigue [docs/motores-ia.md](docs/motores-ia.md#añadir-tu-propio-motor).
