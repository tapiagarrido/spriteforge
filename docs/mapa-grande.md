# Mapa grande de mazmorra (superficies de Qwen-Image)

Un tile suelto de 32 px no puede llevar mucho detalle. Una **superficie periódica de 8×8 baldosas**
(256 px) sí, y se repite sin costura. Por eso el mapa grande se pinta con superficies, no con
baldosas sueltas.

```bash
.venv/bin/forja-mapa-grande catacumbas --semilla 3          # 64×48 baldosas (2048×1536 px)
.venv/bin/forja-mapa-grande forja-de-lava --ancho 96 --alto 64 --salas 14 --semilla 7
```

**Ambientes:** catacumbas, cavernas-fungicas, cripta-helada, forja-de-lava, fortaleza, jardin-de-hongos,
oasis-subterraneo y rocas-resbaladizas. Cada uno define en `AMBIENTES` (`motor/mapa_grande.py`) su piedra de suelo,
su muro, su cobertura (moho, escarcha, ceniza, hongos, hierba…) y su líquido (agua azul, hielo, lava, agua turbia…).

Resultado (por defecto en `salidas/mapas_grandes/<fecha>_<ambiente>-grande/`):
`mapa.png` (suelo y muros), **`mapa_decorado.png`** (con los decorados puestos), `mapa.tmx` (Tiled: capa «mapa» con la
imagen partida en baldosas de 32 px, capa «colision» con 1 = muro o roca, y las capas de objetos «decorados» y
«colisiones_decorados»), `decorados_colocados.json` (cada objeto con su posición, archivo y caja de colisión),
`colision.png`, `plano.json` (salas y rejilla suelo/muro) e `info.json`. Ejemplos en `assets/mapas/grande/<ambiente>/ejemplos/`.

## Cómo se hace

1. **Superficies** (una vez por ambiente, ~3 min de GPU; se guardan en
   `assets/mapas/grande/<ambiente>/superficies/` y se reutilizan: otro mapa con otra semilla no gasta GPU):
   - **Suelo y muro:** el trazado de las juntas (losas irregulares con Voronoi periódico, o ladrillos a soga y tizón)
     se genera por código, así que **el repetido está garantizado**. Qwen lo convierte en piedra trabajada
     conservando exactamente ese trazado: cada losa o ladrillo distinto, con grietas, bordes saltados, bisel,
     suciedad y moho. Después se reduce a 256 px y a la paleta del proyecto (20 colores).
   - **Musgo:** textura de Qwen, repetible (`qwen.periodizar`).
   - **Agua:** igual que la piedra: un patrón de ondas irregulares periódico (por código) que Qwen pinta como agua verde
     azul turbia con reflejos, algas y burbujas. Solo con texto Qwen la dibuja como un plano liso (medido). El agua usa
     4 azules apagados (`RAMPA_AGUA`) que se añaden a la paleta **solo en el mapa**: los azules de la paleta del
     proyecto son demasiado saturados (agua violeta). Casi todo es azul oscuro liso, con ondas más claras y destellos escasos.
2. **Plano:** salas rectangulares unidas por pasillos de 3 baldosas, siempre conectadas, con ≥ 5 baldosas de roca
   entre salas y el borde del mapa en roca.
3. **Pintado:** suelo y muros (2 baldosas de grosor alrededor de las salas) tomados de las superficies por
   coordenadas del mundo; **moho** en parches (más hacia las paredes); **charcos** con orilla oscura en las
   salas grandes; **sombra** de los muros sobre el suelo y borde oscuro en el encuentro. Todo se cuantiza de nuevo
   a la paleta del proyecto: el mapa solo usa sus colores.

## Lo que se midió

- Con baldosas sueltas (la cadena antigua de `motor.mapas`, aunque se le suba el detalle) el suelo salía como
  «gusanos» saturados que se repiten y el muro como un plano vacío: **no sirve** para esta calidad.
- Qwen-Image sin trazado (solo texto) no sale periódico y repite poco; con el trazado por código sí.
- El agua de Qwen sale lisa, sin ondas.
- El tono se pide con el texto del ambiente (`AMBIENTES` en `motor/mapa_grande.py`); en catacumbas, gris frío.

## Desde la app

En el taller **Mapas**, el panel **«Mapa grande de mazmorra»**: eliges proyecto, ambiente, tamaño (48×36 a 128×96 baldosas),
número de salas y si quieres decorados, y pulsas **Generar mapa grande** (unos segundos si las texturas del ambiente ya
están hechas; la semilla del panel de abajo da otra distribución). El resultado se ve en la columna derecha con botones
para alternar **con decorados / solo suelo y muros**, pulsar la imagen para verla a tamaño real, y enlaces para bajar
`mapa_decorado.png`, `mapa.png`, `mapa.tmx` (Tiled), `decorados_colocados.json`, `plano.json` y `colision.png`; también
aparece en «Mapas del proyecto». **Rehacer texturas (Qwen)** vuelve a pedir a Qwen las texturas del ambiente (~4 min,
necesita el servidor Qwen); un ambiente sin texturas las genera solo la primera vez.

API: `GET /api/mapas-grandes/ambientes` y `POST /api/mapas-grandes` (`proyecto`, `ambiente`, `ancho`, `alto`, `salas`,
`semilla`, `decorar`, `rehacer_superficies`) → trabajo → resultado con `urls`.

## Decorados sobre el mapa (opcional)

Es opcional: esta edición pública no incluye la biblioteca de decorados, así que el mapa se genera solo con suelo y muros. Lo que sigue describe el módulo completo.
Cada sala recibe, según el ambiente (`PERFILES`): antorchas en el muro norte, 0-2 objetos grandes, objetos medianos,
cosas en el suelo (huesos, escombros, decals) y telarañas en las esquinas; la primera sala lleva la escalera de
subida y la última la de bajada. Para que el mapa siga siendo jugable, lo que **bloquea** no se coloca en una cruz
central de 3 baldosas de cada sala (por ahí pasan los pasillos), ni a menos de 3 de una puerta, ni en el agua, ni
pegado al muro, y no cubre más del 12 % de la sala. Con `decorar=False` (o sin la biblioteca) se genera solo el suelo.

## Pendiente

- Muros planos (sin cara frontal), salas rectangulares y pasillos en L; luces con halo sobre el mapa.
