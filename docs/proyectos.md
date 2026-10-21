# Proyectos de juego (dirección de arte)

Un **proyecto** reúne lo que deben compartir todos los personajes de un mismo
juego, para que no parezcan sacados de juegos distintos. Se elige una vez en la
app (o con `--proyecto` en la terminal) y todo lo que se genera lo hereda.

## Qué aporta cada campo

| Campo | Efecto | Ejemplo |
|---|---|---|
| `preset` | Estilo de píxel: tamaño, **paleta exacta**, contorno, nº de colores. El pixelizador lo impone a la fuerza: es la garantía más fuerte de coherencia | `heroes-mazmorra` |
| `ambientacion` | Se añade al prompt de cada personaje, justo después de su descripción | `dark gothic dungeon, grim and eerie mood, muted desaturated colors` |
| `negativo` | Se antepone al prompt negativo del preset: lo que no encaja en el juego | `bright colors, cheerful, cute, pastel` |
| `parecido_boceto` | Cuánto se ajusta la IA a los bocetos, de 0 (libre) a 1 (calcado) | `0.5` |
| `tipos` | Plantillas por tipo de personaje (ver abajo) | monstruo, NPC, árbol… |
| `notas` | Historia y notas del mundo; **no** se envía a la IA | |

**Todo puede escribirse en español** (ambientación, negativo, plantillas y
descripciones): se traduce al inglés al generar, por frases, con un modelo local
y un pequeño glosario de términos de juego (espectro → *specter*, no muerto →
*undead*…). La ambientación admite hasta 1500 caracteres.

## Pisos y decorados

`pisos` guarda los ambientes del mapa (dos suelos y, si hace falta, cielo). Cada piso puede
llevar su `decorado` (cómo son su roca, su arbusto y su árbol), que se guarda con el proyecto.

## Forma del juego y mapas

`perspectiva` (`cenital` o `lateral`) y `baldosa` (16, 32 o 48 px) deciden qué genera
el taller de mapas: suelos y transiciones, o plataformas y fondo en capas. Ver
[mapas.md](mapas.md).

## Tipos de personaje (plantillas)

Hay una **biblioteca común** de plantillas para todos los proyectos
(`plantillas/tipos.json`, editable en **Ajustes › Plantillas de personaje**) y cada
proyecto puede añadir **tipos propios** (p. ej. "Espectro" en
tu proyecto, en **Ajustes › Proyectos**). Al generar se ofrecen ambas; si un
tipo propio se llama igual que uno común, en ese proyecto manda el propio. Su
fragmento se coloca antes de la descripción del personaje. Las de serie:

| Tipo | Plantilla predeterminada | Evitar |
|---|---|---|
| Monstruo | una sola criatura monstruosa, cuerpo completo, vista de tres cuartos, amenazante | |
| Jefe | jefe final enorme e imponente, una sola criatura… | pequeño, adorable |
| Héroe / humano | personaje humano aventurero, cuerpo completo, postura de combate… | monstruo |
| NPC (aldeano) | aldeano pacífico, personaje no jugable, de pie, vista frontal | arma, postura de combate |
| Animal | un solo animal, cuerpo completo, vista lateral | humano, ropa |
| Árbol / planta | un solo árbol o planta completa, sin personajes | personaje, criatura, cara |
| Objeto | un solo objeto, icono de inventario, centrado | personaje, criatura, manos |

Los proyectos anteriores guardaban una copia completa de estas plantillas: al
cargarlos se quitan las copias sin cambios y se conservan solo los tipos propios.
Los presets solo aportan el estilo
de píxel; lo propio de cada clase de personaje (criatura, árbol, objeto) lo pone
la plantilla, para que un árbol no salga como un monstruo.

### Opciones de cada plantilla

- **Parecido al boceto** propio (opcional): al elegir la plantilla, el deslizador
  toma ese valor. Efectos (fuego, ácido, humo): *libre* (0.15), porque con más
  parecido la IA calcaba el contorno y la estela salía como un marco de alambre.
  Objetos con forma exacta (pociones, monedas, armas): *fiel* (0.8).
- **Usar la ambientación del proyecto** (activada por defecto): desactívala en
  efectos que la ambientación tiñe; si la ambientación dice "brillo verde, colores
  fríos", convertía una bola de fuego en un orbe verde.

## Ver qué recibe la IA

CLIP solo lee 77 tokens. El botón **Ver qué recibe la IA** muestra el prompt final
ya traducido, cuántos tokens usa y qué parte de la ambientación no cabe (para
acortarla o reordenarla: lo primero es lo que más pesa).

## Seguir trabajando en un proyecto

Los proyectos se guardan en `proyectos/<id>.json` y la app recuerda el último
abierto. **Personajes del proyecto** (arriba de los resultados) lista todo lo
generado en él, con su tipo y sus animaciones; un clic lo reabre con sus
variantes, retoques y animaciones.

## Archivos

Cada proyecto es un JSON en `proyectos/<id>.json`:

```json
{
  "nombre": "Mazmorra oscura",
  "preset": "criaturas-64",
  "ambientacion": "dark gothic dungeon, grim and eerie mood, muted desaturated colors, deep shadows",
  "negativo": "bright colors, cheerful, cute, pastel, neon, happy, saturated",
  "parecido_boceto": 0.5,
  "notas": "..."
}
```

Incluidos como ejemplo: `fantasia-rpg` (RPG de aventuras colorido, 16×16, con el pack
Tiny Dungeon de Kenney; **el mejor punto de partida**), `mazmorra-oscura` (sombrío,
sprites de 64×64), `horror-grotesco` (monstruos intimidantes, 64×64) y
`aventura-clasica` (colorido, héroes de 16×28).

## Escribir buenas descripciones

Pon la atmósfera común en el proyecto y, en la descripción, solo lo propio de cada
personaje: **qué es + un rasgo distintivo + material o textura + un acento de
color**. Por ejemplo:

```
crawling ghoul with an enormous gaping maw full of teeth, long clawed limbs, rotting grey flesh, glowing red eyes
```

A 64 px funcionan bien palabras de forma (`hunched`, `elongated limbs`, `bone
spikes`), de textura (`rotting`, `stitched`, `cracked chitin`) y un solo color
brillante sobre tonos apagados. Evita `photorealistic`, `highly detailed` o
`intricate`: añaden ruido que el pixelizado convierte en manchas.

```bash
.venv/bin/forja-proyectos listar
.venv/bin/forja-generar boceto.jpg --proyecto mazmorra-oscura --prompt "esqueleto con lanza"
```

En la app: selector **Proyecto (juego)** y, debajo, **Dirección de arte del
proyecto** para editarlo, guardarlo o crear uno nuevo.

## Parecido al boceto

El boceto guía a la IA mediante ControlNet. Un único valor controla dos cosas:

| Parecido | Fuerza de ControlNet | Actúa durante | Resultado |
|---|---|---|---|
| 0 (libre) | 0.35 | el primer 30 % de los pasos | conserva la pose general; la IA rehace proporciones y detalle |
| 0.5 (por defecto) | 0.68 | el primer 65 % | parecido claro, adaptado al estilo del juego |
| 1 (calcado) | 1.0 | todos los pasos | sigue el trazo casi literalmente |

Los primeros pasos de la generación deciden la composición y la pose; los
últimos, las proporciones y el detalle. Soltar el boceto antes deja que la IA
adapte el cuerpo al estilo (por ejemplo, chibi) sin perder la idea del dibujo.

## Orden del prompt

```
pixel art, plain white background, <plantilla del tipo>, <descripción>, <ambientación>, <estilo del preset…>
```

CLIP da más peso a lo primero. Si no cabe todo, se sueltan primero matices del
estilo del preset y después el final de la ambientación; la plantilla y la
descripción nunca se recortan. El negativo es el del tipo + el del proyecto + el
del preset.

Con un tipo elegido la **descripción es opcional**: la plantilla basta para generar
(p. ej. "Árbol / planta" sin más genera un árbol del mundo). Sin tipo ("Sin
plantilla") la descripción es obligatoria.

## Comprobado en GPU

El mismo boceto y la misma semilla en dos proyectos: en *Mazmorra oscura* los
personajes salen sombríos y desaturados; en *Aventura clásica*, luminosos y de
colores vivos. En ambos casos la paleta final es la del preset.

