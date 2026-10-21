# Presets de estilo

Un preset le dice al sistema **cómo** deben verse los sprites: tamaño, paleta,
contorno, sombreado. Se define con sprites de ejemplo, sin entrenar modelos.

## Estructura

```
presets/heroes-mazmorra/
  ejemplo1_caballero.png
  ejemplo2_elfa.png
  ejemplo3_mago.png
  estilo.json          ← generado por el analizador (con campos editables)
  vista_previa.png     ← ejemplos ampliados + paleta, para revisar
```

Los ejemplos son los archivos `ejemplo*.png` de la carpeta (PNG con transparencia).

## Elegir buenos ejemplos

- **Coherentes entre sí**: mismo tamaño de lienzo, misma vista, mismo estilo. Si se
  mezclan estilos, el resultado sale "promediado".
- **Variados en contenido**: personajes distintos (armadura, ropa, túnica) para que
  la IA aprenda el estilo y no copie un personaje.
- **Con derechos de uso**: sprites tuyos, packs CC0 o con licencia que lo permita.
- Pueden estar ampliados (×2, ×4…): el analizador detecta la escala y los reduce.

## Crear un preset

**Desde la app** (Ajustes › Estilos de píxel › *Crear un estilo nuevo*): nombre,
2-5 sprites de ejemplo (PNG con fondo transparente, hasta 512 px), una descripción
del estilo opcional (por defecto una neutra: *pixel art, game sprite, full body…*)
y, si quieres, un máximo de colores para la paleta. Se crea `presets/<id>/` con los
ejemplos, `estilo.json` y la vista previa. Ahí mismo se cambian el nombre y los
textos para la IA de cualquier estilo, y se borran los creados en la app (no los
incluidos, ni uno que use algún proyecto).

**Desde la terminal**, para más control (paleta de otra carpeta, nombres de archivo):

```bash
mkdir presets/mi-estilo
cp mis_sprites/*.png presets/mi-estilo/   # renombrados a ejemplo1.png, ejemplo2.png…
.venv/bin/forja-preset analizar presets/mi-estilo --nombre "Mi estilo"
.venv/bin/forja-preset vista presets/mi-estilo
```

Si los ejemplos están en una hoja de sprites, córtala primero:

```bash
.venv/bin/python scripts/recortar_hoja.py hoja.png carpeta_sprites --celda 64 64
```

Opcionalmente, la paleta puede sacarse de un conjunto más amplio que los ejemplos,
y reducirse a N colores representativos con `--max-paleta` (k-medias perceptual
ponderado por uso; solo conserva colores reales del pack):

```bash
.venv/bin/forja-preset analizar presets/mi-estilo \
    --paleta-de ruta/a/todos/los/sprites --patron '*_idle_*.png' --max-paleta 48
```

Una paleta de cientos de colores deja de unificar el estilo: conviene reducirla.

## `estilo.json`

| Campo | Origen | Significado |
|---|---|---|
| `nombre` | editable | Nombre visible del preset |
| `fuente` | editable | Pack de origen, URL y licencia de los ejemplos |
| `ejemplos` | calculado | Archivos de ejemplo usados |
| `sprite.ancho`, `sprite.alto` | calculado | Lienzo del sprite (el más común entre ejemplos) |
| `sprite.alto_figura` | calculado | Alto máximo del personaje dentro del lienzo |
| `sprite.anclaje` | calculado | Dónde se coloca la figura: `abajo-centro` |
| `contorno.tiene/grosor/color` | calculado | Contorno por mayoría entre los ejemplos |
| `colores_por_sprite` | calculado | Mínimo, máximo y objetivo (media) de colores por sprite |
| `niveles_sombreado` | calculado | Tonos por familia de color (mediana) |
| `paleta` | calculado | Paleta fija del preset (de los ejemplos o de `--paleta-de`) |
| `paleta_ejemplos` | calculado | Colores presentes solo en los ejemplos |
| `fondo_generacion` | editable | Fondo de la referencia de estilo (blanco por defecto) |
| `generacion.*` | editable | Prompt base, prompt negativo, fuerzas, resolución |
| `detalle_ejemplos` | calculado | Medidas de cada ejemplo (caja, colores, contorno…) |

Los campos **editables** (`nombre`, `fuente`, `fondo_generacion`, `generacion`) se
conservan al volver a ejecutar `analizar`; el resto se recalcula.

### `generacion`

| Clave | Por defecto | Uso |
|---|---|---|
| `prompt_base` | pixel art chibi de mazmorra, poco detalle | Estilo; se combina con la descripción del usuario |
| `prompt_negativo` | fondos grises/degradados, marcos, suelos, detalle… | Lo que la IA debe evitar (solo modo calidad) |
| `fuerza_estilo` | 0.3 | Peso de IP-Adapter. Más de ~0.5 copia contenido de los ejemplos |
| `parecido_boceto` | 0.5 | 0 libre … 1 calcado; un proyecto puede sobrescribirlo ([proyectos.md](proyectos.md)) |
| `fuerza_pixel_art` | 0.8 | Peso de la LoRA pixel-art-xl |
| `resolucion_generacion` | 768 | Área de la imagen de la IA (~768² px, con la proporción del sprite) |

## Presets incluidos

| Preset | Tamaño | Estilo | Origen |
|---|---|---|---|
| `heroes-mazmorra` | 16×28 | Héroes chibi de mazmorra, 6–8 colores | 0x72 DungeonTileset II (CC0) |
| `criaturas-64` | 64×64 | Sprites de combate tipo Pokémon Gen 4–5, sombreado rico, ~23 colores | Openmon Set 1 (CC0) |

## `criaturas-64`

- Origen: [Openmon Monster Sprites Set 1](https://screensmith.itch.io/openmon-monster-sprites-set-1)
  de Screen Smith, CC0, dibujados por artistas humanos. El autor indica que el
  sprite del barco no tiene los derechos transferidos: se excluye por completo
  (tampoco aporta colores a la paleta).
- Ejemplos: raptor, mantis y escarabajo acorazado (los tres casi a 64 px).
- Medido: contorno `#3f372e` de 1 px, 12–43 colores por sprite (objetivo 23),
  4 tonos de sombreado, paleta reducida a 48 colores de los 17 sprites.
- A este tamaño el sprite conserva caras, ropa y detalles que a 16×28 se pierden.

Para regenerarlo, descarga `OpenmonSprites1.png` en `assets/referencias/openmon_set_1/`,
córtalo con `recortar_hoja.py --celda 64 64 --prefijo openmon`, borra
`openmon_0_5.png` (el barco) y ejecuta:

```bash
.venv/bin/forja-preset analizar presets/criaturas-64 \
    --paleta-de assets/referencias/openmon_set_1/sprites --max-paleta 48
```

## `heroes-mazmorra`

- Origen: [16x16 DungeonTileset II](https://0x72.itch.io/dungeontileset-ii) de 0x72, CC0.
- Ejemplos: caballero, elfa y mago (16×28).
- Medido: contorno `#222222` de 1 px, 6–8 colores por sprite, 2 tonos de sombreado,
  alto de figura 20 px, paleta de 38 colores (todos los personajes del pack).

### Regenerarlo desde el pack original

El pack no se incluye en el repositorio (`assets/referencias/` está en `.gitignore`).
Para reproducir la paleta completa:

1. Descarga `0x72_DungeonTilesetII_v1.7.zip` desde la página del pack.
2. Descomprímelo en `assets/referencias/0x72_dungeontileset_ii/`.
3. Ejecuta:

```bash
.venv/bin/forja-preset analizar presets/heroes-mazmorra \
    --paleta-de assets/referencias/0x72_dungeontileset_ii/0x72_DungeonTilesetII_v1.7/frames \
    --patron '*_idle_anim_f*.png'
```
