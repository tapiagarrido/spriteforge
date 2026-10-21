# App de escritorio

La app es una capa fina sobre el motor:

- **Interfaz** (`app/ui/`): HTML, CSS y JavaScript sin frameworks ni dependencias.
- **Ventana** (`app/src-tauri/`): Tauri 2. Al abrirse arranca el motor
  (`python -m motor.servidor`) como proceso hijo y lo cierra al salir. Si ya hay
  un motor escuchando en el puerto 8765, lo reutiliza.

## Usarla sin Tauri (navegador)

No requiere nada más que el motor:

```bash
.venv/bin/forja-servidor
```

y abrir http://127.0.0.1:8765.

## Tamaños de ventana

La interfaz se adapta al ancho (comprobado a 375, 768, 900 y 1440 px sin
desplazamiento horizontal):

| Ancho | Disposición |
|---|---|
| > 1000 px | Tres columnas: boceto · especificaciones · resultados |
| 700–1000 px (incluye el mínimo de la app, 900) | Boceto y especificaciones arriba; resultados y animación debajo a todo el ancho |
| < 700 px (navegador en móvil) | Una columna; el lienzo no pasa del 60 % del alto de la pantalla |

El lienzo conserva su proporción y nunca supera el 78 % del alto de la ventana, así
que se puede dibujar sin desplazarse. Al terminar una generación o abrir un
personaje, si los resultados quedan fuera de la vista se desplaza hasta ellos.

## Compilar la app Tauri (Linux)

1. Bibliotecas del sistema (una vez):

```bash
sudo apt install -y libwebkit2gtk-4.1-dev build-essential libssl-dev libayatana-appindicator3-dev librsvg2-dev
```

2. CLI de Tauri (una vez). La 2.11.5 es la última que funciona con Rust 1.89; con
   Rust 1.90 o superior puede usarse `--version "^2"`:

```bash
cargo install tauri-cli --version "=2.11.5" --locked
```

`Cargo.toml` declara `rust-version = "1.89"` y `.cargo/config.toml` hace que Cargo
elija versiones de dependencias compatibles con ella.

3. Desarrollo (abre la ventana usando el `.venv` del proyecto; compila en ~1 min):

```bash
cd app/src-tauri && cargo tauri dev
```

4. Paquetes `.deb` y AppImage:

```bash
cd app/src-tauri && cargo tauri build
```

Variables de entorno:

| Variable | Por defecto | Uso |
|---|---|---|
| `FORJA_RAIZ` | raíz del repositorio (en desarrollo) | Dónde están `motor/`, `presets/`, `modelos/` |
| `FORJA_PYTHON` | `$FORJA_RAIZ/.venv/bin/python` | Intérprete con el motor instalado |

> El motor necesita acceso a la GPU: tu usuario debe estar en el grupo `render`
> (ver [gpu-amd.md](gpu-amd.md)).
>
> El paquete generado todavía **no incluye** Python ni los modelos: necesita el
> repositorio con su `.venv` y `modelos/`. Empaquetar el motor (p. ej. con
> PyInstaller como *sidecar*) queda pendiente.

## Uso

1. **Boceto**: dibuja con el lápiz (goma, grosor, deshacer con Ctrl+Z) o importa
   una foto o imagen. Al importar se abre **Preparar imagen**:
   - arrastra sobre la foto para **recortar** solo el dibujo (fuera la mesa, las
     anillas del cuaderno, el texto) y **gira 90°** si hace falta;
   - **Igualar iluminación** quita sombras y el tono del papel;
   - **Nivel del trazo** decide desde qué gris es línea (el automático usa el
     método del triángulo, pensado para lápiz suave sobre mucho papel);
     **Blanco y negro puro** o, desactivado, grises con **Contraste**;
   - a la derecha se ve el resultado; «Usar en el lienzo» lo pasa al boceto y
     «Usar sin tratar» solo aplica recorte y giro. Todo ocurre en el navegador.
2. **Especificaciones**: elige proyecto, estilo y tipo de personaje (se preparan en
   Ajustes), describe el personaje (opcional si hay tipo),
   número de variantes y modo. Los deslizadores ajustan cuánto se respeta el
   boceto y cuánto se imita el estilo. En *Avanzado*: semilla, alto de la figura
   y número de colores.
3. **Generar**: la primera vez tarda más porque carga los modelos en la GPU.
4. **Resultados**: haz clic en una variante para abrirla en el editor:
   - Pintar, borrar y cuentagotas con la paleta del preset.
   - *Re-pixelizar*: cambia alto, colores o contorno sin volver a usar la IA.
   - *Guardar sprite*: lo guarda como `final_XX.png` en la carpeta del resultado.
   - *Descargar PNG*: descarga el sprite tal como está en el editor.

La barra superior muestra la GPU y la VRAM libre, si los modelos están en memoria
y un botón para liberarlos.

## Taller de mapas

La pestaña **Mapas** (junto a **Personajes**) genera el terreno del proyecto elegido
según su forma (cenital o lateral): materiales, piezas, variantes y semilla; muestra
el mapa de prueba, el tileset y las capas, con enlaces a los archivos (PNG y `.tsx`
de Tiled), y la lista de mapas del proyecto. Ver [mapas.md](mapas.md).

## Ajustes

Lo que se prepara una vez y se reutiliza está en **Ajustes** (botón arriba a la
derecha; ocupa la pantalla y «Volver al taller» regresa). En el taller solo se
elige proyecto, estilo y tipo, y los enlaces bajo cada selector llevan a su pestaña.

| Pestaña | Qué se configura |
|---|---|
| Proyectos | Nombre, estilo de píxel, forma del juego (cenital o lateral) y baldosa del mapa, ambientación, qué evitar, parecido al boceto por defecto, notas y **tipos propios** del proyecto. «Nuevo proyecto» y «Guardar proyecto» (queda elegido en el taller). |
| Plantillas de personaje | Biblioteca común de tipos (monstruo, NPC, árbol…) para todos los proyectos. |
| Estilos de píxel | Los presets con su vista previa, tamaño y paleta; editar nombre y textos para la IA; **crear un estilo** a partir de 2-5 sprites de ejemplo; borrar los creados en la app. |
| Gemini | Clave y modelo de la API externa (opcional). |
| Qwen | Dirección del servidor Qwen-Image (opcional). |

## Mapa grande de mazmorra

En el taller **Mapas**, el panel «Mapa grande de mazmorra» genera un mapa entero de uno de los 8 ambientes (salas, pasillos, muros y decorados) y lo exporta a Tiled. Ver [mapa-grande.md](mapa-grande.md).
