# Registro de cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/);
versionado [semántico](https://semver.org/lang/es/).

## [0.38.1] — primera publicación

Primera versión pública. Incluye:

- Presets de estilo a partir de sprites de ejemplo (paleta, tamaño, contorno).
- Pixelizador: imagen grande → sprite que cumple el preset.
- Limpieza de bocetos (dibujo digital o foto de papel).
- Generación con IA con **tres motores intercambiables**: librerías locales (SDXL +
  ControlNet + IP-Adapter), servidor propio (Qwen-Image) y Gemini. Ver
  [docs/motores-ia.md](docs/motores-ia.md).
- Proyectos de juego (ambientación, estilo, tipos de personaje) y plantillas.
- Animación (reposo, caminar, ataque, daño, muerte), direcciones y hoja de sprites
  PNG + JSON (formato Aseprite).
- Mapas: suelos, transiciones, plataformas, fondos en capas y mapa grande, con
  exportación a Tiled.
- Rediseño de variantes (color y elementos nuevos).
- API local + interfaz web y ventana de escritorio (Tauri).
- Ejemplos con sprites de licencia libre (CC0): presets `heroes-mazmorra` (0x72),
  `criaturas-64` (Openmon) y `aventura-16` (Kenney), y el proyecto `fantasia-rpg`.
