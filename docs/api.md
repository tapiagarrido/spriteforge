# API local del motor

`motor/servidor.py` expone el motor por HTTP para la app. Escucha **solo en
127.0.0.1** (puerto 8765 por defecto) y usa únicamente la biblioteca estándar.

```bash
.venv/bin/forja-servidor --puerto 8765 --minutos-inactividad 5
```

Además sirve la interfaz (`app/ui/`) en `/`: abriendo http://127.0.0.1:8765 en el
navegador tienes la app completa sin Tauri.

## Seguridad

- Solo escucha en la interfaz local.
- Todo `POST` exige la cabecera `X-Forja: 1`. Un formulario o script de otra web
  no puede enviarla sin una petición *preflight* CORS, y el servidor solo acepta
  los orígenes de la app (`tauri://localhost`, `http://tauri.localhost`) y el suyo.
- Los nombres de preset y de carpeta de salida se validan (sin `..` ni `/`) y los
  archivos servidos deben estar dentro de su carpeta.
- Cuerpo máximo: 30 MB. Sprites guardados: máximo 256 px de lado.

## Endpoints

### `GET /api/estado`
```json
{"version": "0.2.0",
 "gpu": {"torch": "2.14.1+rocm7.2", "rocm": "7.2...", "gpu": "AMD Radeon RX 9060 XT",
         "vram_total_gb": 15.9, "vram_libre_gb": 15.2},
 "modelos": [{"alias": "sdxl", "uso": "...", "gb": 6.8, "descargado": true}],
 "modelos_cargados": false, "trabajos_pendientes": [], "modos": ["rapido", "calidad"]}
```
Si no hay GPU, `gpu.gpu` es `null` y `gpu.motivo` explica por qué.

### `GET /api/presets`
Lista de presets con `id`, `nombre`, `sprite`, `contorno`, `colores_por_sprite`,
`paleta`, `fondo_generacion`, `generacion`, `ejemplos` y la URL de `vista_previa`.

### `POST /api/presets`
Crea un preset: `{"nombre", "ejemplos": [dataURL PNG ×2-5], "estilo"?, "max_paleta"?}` →
el preset (mismo formato que en la lista, con `creado_en_app: true`). 400 si los
ejemplos no tienen transparencia, son más de 512 px o hay menos de 2.

### `POST /api/presets/<id>`
`{"nombre"?, "prompt_base"?, "prompt_negativo"?}` → el preset actualizado (regenera
la vista previa; lo medido de los ejemplos no cambia).

### `POST /api/presets/<id>/borrar`
Borra un preset creado en la app. 400 si es de los incluidos o lo usa un proyecto.

### `GET /api/presets/<id>/<archivo>`
Archivos del preset (vista previa, ejemplos).

### `POST /api/generar` → `202 {"trabajo": "1"}`
```json
{"boceto": "data:image/png;base64,...", "proyecto": "mazmorra-oscura",
 "prompt": "caballero con armadura roja", "variantes": 4, "modo": "calidad",
 "semilla": 123, "parecido_boceto": 0.5, "fuerza_estilo": 0.3,
 "tipo": "monstruo", "alto_figura": 20, "colores": 7, "traducir": true, "motor_ia": "local"}
```
Obligatorios: `boceto` y `proyecto` o `preset` (si se dan ambos, el preset indicado
gana), y `prompt` **o** `tipo`: con un tipo de personaje su plantilla ya describe al
personaje y `prompt` solo añade lo propio (la carpeta toma entonces el nombre del tipo). El resto es opcional (vacío = valor del proyecto o del preset).
`variantes` entre 1 y 8. `info.prompt_traducido` indica la traducción usada.
`motor_ia`: `"local"` (por defecto) o `"gemini"` (requiere clave en Ajustes).

### `GET /api/proyectos`
Lista de proyectos: `id`, `nombre`, `preset`, `ambientacion`, `negativo`,
`parecido_boceto`, `notas`.

### `POST /api/proyectos`
Crea (sin `id`) o actualiza (con `id`) un proyecto. Devuelve el proyecto guardado.
```json
{"nombre": "Cripta helada", "preset": "heroes-mazmorra",
 "ambientacion": "frozen crypt, cold blue tones", "negativo": "warm colors",
 "parecido_boceto": 0.5}
```

### `GET /api/config`
Ajustes, **sin claves**:
```json
{"gemini": {"configurada": true, "terminacion": "wxyz", "desde_entorno": false,
            "modelo": "gemini-2.5-flash-image"},
 "archivo": "/home/<usuario>/.config/forja-sprites/config.json"}
```

### `POST /api/config`
Guarda `gemini_api_key` y/o `gemini_modelo`. Un valor vacío lo borra. Devuelve lo
mismo que `GET /api/config`.

### `POST /api/config/probar-gemini`
Comprueba la clave indicada (`gemini_api_key`) o la guardada y devuelve los modelos
de imagen disponibles: `{"ok": true, "modelos": ["..."]}`.

### `GET /api/trabajos/<id>`
```json
{"id": "1", "estado": "listo", "posicion": null, "error": null,
 "resultado": {"carpeta": "2026-10-16_153005_knight", "ruta": "/.../salidas/...",
               "boceto_limpio": "/api/salidas/.../boceto_limpio.png",
               "variantes": [{"indice": 1, "semilla": 123, "colores": ["#222222", "..."],
                              "ia": "/api/salidas/.../ia_01.png",
                              "sprite": "/api/salidas/.../sprite_01.png?v=...",
                              "sprite_vista": "/api/salidas/.../sprite_01_x8.png?v=..."}],
               "info": {"...": "contenido de info.json"}}}
```
`estado`: `en_cola` (con `posicion`), `generando`, `listo` o `error` (con `error`).

### `POST /api/pixelizar`
Re-pixeliza una variante ya generada con otras opciones, **sin volver a usar la
IA** (milisegundos).
```json
{"carpeta": "2026-10-16_153005_knight", "indice": 1,
 "alto_figura": 18, "colores": 6, "contorno": true}
```
Devuelve el mismo objeto que `resultado` en `/api/trabajos/<id>`.

### `POST /api/guardar`
Guarda el sprite retocado en el editor como `final_XX.png` (y `final_XX_x8.png`).
```json
{"carpeta": "2026-10-16_153005_knight", "indice": 1, "sprite": "data:image/png;base64,..."}
```
→ `{"ruta": "/.../final_01.png", "url": "/api/salidas/.../final_01.png"}`

### `POST /api/animar` → `202 {"trabajo": "7"}`
Genera **una** animación de una variante y la añade a su hoja (va a la cola).
```json
{"carpeta": "...", "indice": 2, "animacion": "ataque", "golpe": "ia", "identidad": 0.5,
 "mira": "", "semilla": 404, "texto": "solo para «especial»", "boceto_pose": "data:... (golpe «dibujo»)"}
```
`animacion`: `reposo`, `caminar`, `ataque`, `zarpazo`, `rugido`, `especial`, `dano`, `muerte`.
`golpe` (solo ataques): `deformacion` (por defecto), `ia` o `dibujo`. El resultado
del trabajo es el mismo objeto que `GET /api/animaciones/...`, con `recien` = la
animación generada.

### `GET /api/animaciones/<carpeta>/<indice>`
Animaciones guardadas de la variante: por cada una `nombre`, `fotogramas`,
`duraciones_ms`, `bucle`, `golpe`, `frames_url` (la secuencia), `gif_url` y
`clave_url` (pose del golpe); además `hoja_url`, `hoja_x4_url`, `json_url`, `ruta`.

### `POST /api/animaciones/borrar`
`{"carpeta": "...", "indice": 2, "animacion": "caminar"}` → la lista actualizada.

### `GET /api/tipos` · `POST /api/tipos`
Biblioteca común de plantillas de tipo de personaje (`plantillas/tipos.json`; si no
existe, las de serie). `POST {"tipos": [{"nombre", "prompt", "negativo"}]}` la
reemplaza. Los `tipos` de un proyecto son solo los propios y se suman a estas.

### `POST /api/mapas` → `202 {"trabajo": "1"}`
Encola el mapa de un proyecto; la forma (`perspectiva`) y la `baldosa` las fija el proyecto.
```json
{"proyecto": "mazmorra-oscura", "materiales": {"a": "pasto oscuro", "b": "tierra"},
 "piezas": ["suelos", "transiciones"], "variantes": 4, "semilla": 7, "legibilidad": "auto"}
```
Lateral: `"materiales": {"superficie": "pasto", "relleno": "tierra", "cielo": "atardecer"}`,
`"piezas": ["plataformas", "fondo"]`. El resultado del trabajo trae `perspectiva`,
`baldosa`, `baldosas`, `prompts`, `archivos` y sus `urls` (`tileset`, `tileset_x4`,
`tsx`, `fondo_cielo`, `fondo_lejos`, `fondo_cerca`, `mapa_prueba`, `mapa_prueba_x2`).
`legibilidad`: `"auto"` (por defecto) o 0…1; apaga el suelo para que los personajes destaquen (ver
[mapas.md](mapas.md)). El resultado trae `legibilidad` (nivel, `automatica`, `luminosidad_suelo`,
`camuflaje`) y, si el proyecto tiene personajes, `escena_prueba`/`escena_prueba_x2` en `urls`;
`piso` (id) pone nombre a la carpeta.
Si faltan materiales para las piezas pedidas, el trabajo termina en error con el motivo.

### `GET /api/proyectos/<id>/mapas`
Mapas generados para el proyecto (lo más reciente primero), con el mismo formato.

### `POST /api/recolorear`
Otro color de una variante, sin IA. Vista previa (por defecto) →
`{"imagen": dataURL, "colores": [...]}`; con `"guardar": true` crea una variante nueva →
el resultado completo + `"nueva": <índice>`.
```json
{"carpeta": "...", "indice": 1, "objetivo": "#3a6fd8", "modo": "tenir", "colores": ["#966e46"],
 "saturacion": 1.0, "brillo": 1.0, "ajustar": true, "guardar": true, "nombre": "Lobo azul"}
```
`objetivo` lleva los `colores` elegidos (todos si se omite) hacia ese color (`modo`: `tenir`
o `girar`). Sin `objetivo`, `tono` (−180…180), `saturacion` (0…2.5) y `brillo` (0.3…2).

### `POST /api/redisenar` → `202 {"trabajo": "1"}`
`{"carpeta", "indice", "cambio": "con un casco de hierro y un cuerno", "nombre"?, "semilla"?}`:
Gemini redibuja la variante con los cambios y se guarda como variante nueva (resultado
completo + `nueva`). Requiere la clave de Gemini; `motor_ia: "local"` se rechaza.
Las variantes derivadas traen `origen` y `nombre` en el resultado.

### `POST /api/direcciones` → `202 {"trabajo": "1"}`
Una vista de dirección de una variante:
`{"carpeta", "indice", "vista": "abajo"|"arriba"|"lado", "modo": "ia"|"dibujo"|"variante",
"motor_ia"?: "local"|"gemini", "boceto_pose"? (dataURL, modo dibujo), "semilla"?, "identidad"?,
"texto"? (descripción de la criatura, ayuda a la IA local)}`.
El resultado es el de `GET /api/direcciones/<carpeta>/<n>`: `{"vistas": {vista: {modo, motor_ia,
semilla, url, ia_url}}}`. La vista de lado se guarda mirando a la derecha.

`POST /api/animar` acepta `"direccion": "abajo"|"arriba"|"derecha"` (requiere la vista);
la animación se guarda como `<animacion>_<direccion>` y con `derecha` también
`<animacion>_izquierda` (espejo). Borrar una de las dos borra la otra.

### `GET /api/proyectos/<id>/personajes`
Personajes generados en el proyecto: `carpeta`, `fecha`, `descripcion`, `tipo`,
`variantes`, `indice`, `sprite_url`, `animaciones`. `indice` es la variante con la que
se sigue trabajando (la última animada; si no, la primera con retoque guardado; si
no, la 1): `sprite_url` y `animaciones` son los de esa variante.

### `GET /api/resultados/<carpeta>`
Reabre un resultado (mismo objeto que el `resultado` de un trabajo de generación).

### `POST /api/prompt`
Qué recibirá la IA: `{"proyecto", "preset", "tipo", "prompt"}` (y opcionalmente
`ambientacion`/`negativo` sin guardar). Devuelve `positivo`, `negativo` (ya
traducidos), `tokens`, `limite`, `ambientacion_fuera` y `estilo_fuera`.

### `POST /api/boceto`
El boceto tal como lo recibirá ControlNet, sin usar la GPU:
`{"boceto": dataURL, "proyecto"?, "preset"?, "simplificar"? (0–1, 0.3), "grosor"? (0.002–0.02, 0.006)}`
→ `{"imagen": dataURL PNG (trazo blanco sobre negro), "ancho", "alto"}` al tamaño de
generación del preset. `simplificar` y `grosor` también se aceptan en `POST /api/generar`.

### `POST /api/liberar`
Libera los modelos de la VRAM ahora. → `{"modelos_cargados": false}`

### `GET /api/salidas/<carpeta>/[<subcarpeta>/]<archivo>`
Cualquier archivo de una carpeta de resultados (incluidas `claves_XX/` y `animacion_XX/`).
