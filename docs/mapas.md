# Mapas: suelos, transiciones, plataformas y fondos

El taller **Mapas** genera el terreno del juego: solo suelo y cielo, sin árboles,
rocas ni personajes. Qué se genera lo decide la **forma del juego**, que fija cada
proyecto en *Ajustes › Proyectos*:

| Forma (`perspectiva`) | Para juegos… | Piezas |
|---|---|---|
| `cenital` | con movimiento en todas direcciones (Zelda, Pokémon) | **Suelos sin costuras** (con variantes) y **transiciones** entre dos suelos |
| `lateral` | de plataformas, hacia delante y atrás (Mario) | **Bloques de plataforma** (superficie, relleno, bordes y esquinas) y **fondo en capas** (cielo, montañas lejanas, colinas cercanas) |

El proyecto también fija el lado de la **baldosa** (16, 32 o 48 px; 32 por defecto)
y la **paleta**: los colores salen del estilo de píxel del proyecto, así que el
terreno combina con sus personajes.

![Mapas de prueba generados](img/mapas.png)

*Mapas de prueba reales de un proyecto de mazmorras: vista cenital (pasto oscuro y
tierra, baldosas de 32 px) y vista lateral (pasto sobre tierra con piedras, cielo
nocturno, montañas y colinas).*

## Uso

1. En *Ajustes › Proyectos*, elige la forma del juego y el tamaño de baldosa.
2. En la pestaña **Mapas**, elige el proyecto y describe los materiales en español
   o inglés:
   - cenital: suelo principal («pasto oscuro») y, para las transiciones, un segundo
     suelo («tierra», «arena», «losas de mazmorra»…);
   - lateral: superficie («pasto»), relleno («tierra con piedras») y, si quieres, el
     cielo («cielo nocturno con estrellas»).
   Si el proyecto tiene **pisos** guardados (ambientes como «Catacumbas» o «Forja de
   lava»), elige uno y se rellenan los suelos; con «Guardar piso» guardas los suelos
   actuales como piso nuevo (o actualizas el del mismo nombre).
3. **Generar mapa** (~45 s en cenital, ~90 s en lateral con fondo; la primera vez con
   un tamaño nuevo la GPU compila sus kernels y tarda más).
4. Se muestran un **mapa de prueba** pintado con las piezas, el tileset y las capas.
   Todo queda en `salidas/mapas/<fecha>_<proyecto>-<material>/`.

## Sala completa con Qwen-Image (catacumbas, criptas, cavernas…)

Las baldosas que se repiten dan un suelo uniforme: sin paredes, charcos ni moho colocados donde toca. Para
una **sala con ambiente** hay una segunda vía: en *Mapas → Sala completa con Qwen-Image* describes la sala en
español y Qwen-Image la dibuja entera, vista desde arriba (muros de ladrillo, losas de piedra superpuestas,
moho entre ellas, charcos de agua, huesos…). Después se reduce a la resolución del juego (baldosa × baldosas) y
a la **paleta del proyecto**, así que casa con sus personajes. Necesita el servidor Qwen (ver
[docs/qwen.md](qwen.md)); ~35 s por sala.

- **Tamaños:** cuadrada 16×16 baldosas, ancha 24×16 y grande 24×24. **Detalle:** «Definido» (por defecto) o
  «Apagado» (menos contraste, para que los personajes destaquen).
- **Resultado** en `salidas/mapas/<fecha>_<proyecto>-sala/`: `sala_N.png` (y `_x2`), `sala_N.tmx` (mapa de
  Tiled de una sola capa cuyo tileset es la propia imagen, para añadirle capas de colisiones y objetos) y las
  imágenes originales de Qwen en `ia/`.
- **Es una sala única**, no un tileset que se repite: sirve de suelo y fondo de una sala concreta.
- **Medido (proyecto «Horror grotesco»):** Qwen dibuja las catacumbas muy oscuras (luminosidad media ~9/100) y,
  al pasarlas a una paleta llena de oscuros, el verde del agua caía en el negro y quedaban 10 colores. Antes de
  la paleta se aclara la luminosidad hasta una mediana de 0,20 y se refuerza el color (`escenas.realzar`).
- **Escribe cada elemento con detalle:** «varios charcos redondos de agua verde turbia con ondas» sale como agua;
  «agua estancada» a secas suele salir como fosos oscuros. Cada sala es distinta (semilla): genera 2-4.

## Legibilidad: que los personajes no se pierdan

El suelo debe decir en qué ambiente estás sin competir con quien camina encima. Por eso los
suelos se **apagan**: menos contraste, menos color y una luminosidad dentro de una franja media
(ni los oscuros de los contornos de los personajes ni los claros de sus luces), **conservando el
matiz** (el oasis sigue verde, la lava rojiza, la cripta azulada). Se hace con la misma función
sobre la textura de la IA y sobre la paleta del estilo, así que los colores del suelo son los del
estilo en versión apagada (`suavizar`, `paleta_suelo` en `motor/mapas.py`).

- **Nivel** (0 = tal cual lo pinta la IA, 1 = lo más apagado): en la app, *Legibilidad del suelo*.
  Por defecto es **automática**: con los personajes del proyecto se prueban los niveles 0,3 a 1
  y se elige el **más bajo** (el suelo con más carácter) que cumpla el objetivo o quede a menos
  de 0,02 de lo mejor posible; apretar más aplana el suelo sin que ganen los personajes.
- **Se mide**: para cada personaje, la fracción de sus colores que está a menos de ΔE 14 (CIELAB)
  de algún color del suelo (`camuflaje`). En las catacumbas con los 6 personajes de ejemplo bajó de
  **35 % a 21 %**. La app lo muestra junto a la **escena con tus personajes encima**
  (`escena_prueba.png`).
- **Sin personajes** en el proyecto se usa el nivel 0,7.
- El borde entre dos suelos usa el segundo color más oscuro, no el más oscuro: no debe parecerse al
  contorno casi negro de un personaje. También se apagan las capas del fondo lateral.

**Lo que no funciona**: limitar el suelo a los colores de la franja media de la paleta dejaba solo
marrones y grises (campos planos sin ambiente); por eso se apagan todos los colores en lugar de
descartar los demás. Y bajar el camuflaje del todo (a ~9 %) exigía un suelo casi liso.

**El agua se genera a mano** (`textura_agua`): el modelo de pixel art dibuja agua como losas
marrones o mosaicos rojos y azules. Si el material del suelo menciona agua, charcos, lago o río
(y no hielo ni lava) se usa una textura procedural azul con ondas, periódica, que pasa por el mismo
apagado y paleta. «oscura» o «profunda» la oscurece y «clara» la aclara.

## Mapas de todos los pisos

```bash
sg render -c ".venv/bin/python -m motor.mapas -p mi-proyecto"                    # los 7
sg render -c ".venv/bin/python -m motor.mapas -p mi-proyecto --pisos forja-de-lava --legibilidad 0.5"
```

Genera un mapa por piso (sus dos suelos, 4 variantes, las 16 transiciones) y los personajes del
proyecto como muestra. Se guardan en `assets/mapas/<proyecto>/<piso>/`
(`tileset.png`, `tileset.tsx`, `mapa_prueba.png`, `escena_prueba.png`, `info.json`).

## Pisos

Ejemplos de ambientes (cada proyecto define los suyos): catacumbas (losas con huesos y polvo, sobre tierra), oasis subterráneo (pasto húmedo, agua poco profunda), forja de lava (roca volcánica y lava), cripta helada (losas con escarcha, hielo azul).

Se guardan en el proyecto (`pisos`: nombre, `a`, `b` y `cielo` opcional; en vista
lateral, `a` es la superficie y `b` el relleno).

## Archivos

| Archivo | Contenido |
|---|---|
| `tileset.png` (+ `tileset_x4.png`) | Las baldosas en filas de 8 |
| `tileset.tsx` | Tileset de [Tiled](https://www.mapeditor.org/) con un *Wang set de esquinas*: con la herramienta de terreno, Tiled elige solo la baldosa de borde o esquina correcta |
| `fondo_cielo.png`, `fondo_lejos.png`, `fondo_cerca.png` | Capas del fondo (lateral), de 9 baldosas de alto; se repiten en horizontal. Las dos últimas, con el cielo transparente |
| `mapa_prueba.png` (+ `_x2`) | Escena de ejemplo para ver cómo encaja todo |
| `escena_prueba.png` (+ `_x2`) | El mapa de prueba con los personajes del proyecto encima (legibilidad) |
| `ia/` | Las imágenes originales de la IA |
| `info.json` | Proyecto, piso, forma, baldosa, materiales, prompts, legibilidad (nivel, luminosidad del suelo, camuflaje) y tiempos |

En **Tiled**: *Mapa › Añadir tileset externo* → `tileset.tsx`; en el panel
*Terrenos* aparece el conjunto con sus colores (los dos suelos, o el suelo de las
plataformas). Godot, Unity y GameMaker importan tilesets de Tiled con complementos.

## Cómo funciona (`motor/mapas.py`)

**Texturas sin costuras.** Durante la generación, las convoluciones de la UNet
"dan la vuelta" por los bordes (relleno circular), así que la textura es periódica:
lo que sale por la derecha entra por la izquierda. El VAE no se parchea: se añade
un margen circular al latente, se decodifica por mosaicos (memoria acotada) y se
recorta el centro.

**De textura a baldosas.** Cada material es **una sola** imagen de 768 px que se
reduce a una superficie de ~96 px (la escala a la que el LoRA de pixel art pinta sus
"píxeles"), con los 6 colores de la paleta del proyecto que mejor la representan.
Las baldosas son recortes de esa superficie, convertidos en periódicos fundiendo sus
últimas filas y columnas con las que siguen. Las variantes comparten el borde de la
primera (con el límite interior irregular), así que todas encajan entre sí en
cualquier orden.

**Transiciones (cenital).** Las 16 combinaciones de esquinas (cada una, suelo A o B).
La zona de B sale de interpolar las esquinas más un ruido suave que es el mismo en
todas las baldosas: el borde de cada baldosa solo depende de sus dos esquinas, así
que dos vecinas siempre casan. El borde de B lleva su color más oscuro como contorno.

**Plataformas (lateral).** Las 15 combinaciones con algo de suelo (el aire es
transparente): relleno, una capa de superficie donde hay aire encima y contorno
oscuro hacia el aire.

**Fondo en capas (lateral).** La IA pinta paisajes completos aunque se le pida un
cielo liso, así que separar el cielo después no es fiable (se probó por color y con
el segmentador). En su lugar, el motor genera la **silueta** (montañas: ondas
triangulares; colinas: senos; frecuencias enteras para que se repita sin costura),
se la pasa a la IA como boceto (ControlNet, también en mosaico) y recorta la capa
por ella. El cielo usa la descripción del cielo y no la ambientación del proyecto
(su "brillo verde de las runas" lo teñía de verde); las montañas y colinas sí la usan.

**Prompts.** Los materiales se traducen siempre (textos cortos como «pasto oscuro»
no se detectan como español) con un glosario de terrenos (tierra → *dirt*, pasto →
*grass*, losas → *flagstones*…). El suelo **no** usa la ambientación del proyecto:
sus "ruinas" convertían el pasto en losas con musgo; el color del mundo ya lo pone la
paleta. Tampoco usa la referencia de estilo (los ejemplos del preset, sprites sobre
blanco, metían objetos sueltos en el suelo).

## API

`POST /api/mapas` encola un mapa (ver [api.md](api.md)) y
`GET /api/proyectos/<id>/mapas` lista los del proyecto.

## Limitaciones

- La calidad varía con la semilla: si un suelo no convence, genera otra vez.
- Los fondos se cuantizan a 8 colores de la paleta del proyecto: con paletas de
  personajes, las montañas pueden quedar algo planas.
- Aún no hay vista isométrica, decorados (árboles, rocas) ni agua animada.
