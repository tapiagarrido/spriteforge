# Motor de IA externo: Qwen-Image (opcional)

Tercera vía de generación, junto a la IA local (SDXL en la GPU de esta máquina) y
Gemini: **Qwen-Image** servido por un servidor propio (probado con
[stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp)). Es gratis, no
tiene clave y nada sale de tu red, pero necesita que ese servidor esté arrancado y
tarda más que SDXL. Visión general de los tres motores: [motores-ia.md](motores-ia.md).

El servidor debe ofrecer `GET /api/health` y `POST /v1/images/edits` (formato
OpenAI, ver [motores-ia.md](motores-ia.md#2-servidor-propio-qwen-image-u-otro)).

## Qué se hace en Qwen y qué sigue en local

| Paso | Local | Gemini | Qwen |
|---|---|---|---|
| Limpiar el boceto, traducir | ✔ | ✔ (local) | ✔ (local) |
| Generar la imagen | GPU propia (SDXL) | API de Google | **Servidor Qwen-Image** |
| Recortar la figura y pixelizar con la paleta del preset | ✔ | ✔ (local) | ✔ (local) |

Como el recorte y el pixelizado siguen siendo locales, los sprites usan la **misma
paleta, tamaño y contorno** que con los otros motores.

## Usarlo

1. Arranca tu servidor Qwen-Image y espera a que cargue el modelo (~1 min).
2. En la app: **Ajustes → Qwen** → **Probar conexión**. La dirección por defecto es
   `http://127.0.0.1:1234`; cámbiala si el servidor está en otra máquina
   (o usa la variable `QWEN_URL`, que tiene prioridad).
3. En **Especificaciones → Motor de IA** elige **Qwen-Image**.

También por línea de comandos: `forja-generar boceto.png --proyecto … --motor qwen`.

Sirve para: generar variantes, **girar al personaje** (vistas de frente/espalda/lado) y
**añadir elementos** a una variante («con un casco de hierro»; con el motor «Local» se
usa Gemini).

## Poses y vistas a partir del personaje

El flujo es: boceto → variantes → eliges una → de ella salen sus vistas y poses. Con el
motor Qwen, en ambos pasos Qwen parte de la **imagen del propio personaje** (conserva su
diseño y colores) y la redibuja. Medido con un caballero de ejemplo, ~30 s por imagen:

| Qué se pide | Resultado |
|---|---|
| **De espaldas** | Muy bueno: mismo casco, escudo y espada, sin cara |
| **Tres cuartos de espaldas** | Bueno |
| **Poses de acción** (ataque, salto con la espada en alto…) | Muy bueno; sirve cualquier texto |
| **De frente** | Bueno (si la variante ya está de frente, queda igual) |
| **De lado (perfil)** | El más difícil. Desde una sola imagen de frente devuelve la misma. Por eso se le envían **frente + espalda** a la vez: con la espalda ya generada, 2 de 3 semillas dieron un perfil real. Sin espalda se usa una copia comprimida a lo ancho como pista y sale un paso de lado, menos girado. **Genera primero la espalda y luego el lado**, y pulsa varias veces (cada pulsación usa otra semilla) |

- **Orden recomendado para las direcciones:** frente (la propia variante) → espalda → lado.
- **Direcciones** (Animación → Direcciones): elige «Qwen-Image» en Motor de IA y pulsa la vista.
- **Poses de ataque** (Animación → ataque o «Ataque especial» → *Pose del golpe*: «Dibujada por la
  IA» o «Mi dibujo del lienzo»): con Qwen puedes describir cualquier pose en el campo de texto;
  si dibujas la pose en el lienzo se envía como guía. «Parecido al personaje» no se usa con
  Qwen: ya parte de la imagen del personaje.

## Cómo se usa Qwen para generar desde el boceto (y por qué)

Qwen-Image funciona en modo *edición*: recibe imágenes y un prompt. Medido con el
boceto de ejemplo:

- Con el boceto como primera imagen **conserva su estructura**: el resultado sigue la
  pose y las proporciones. Ningún prompt consigue que además cambie el trazo por otro
  distinto; el prompt de `motor/qwen.py` pide sustituir el lápiz por contorno de píxeles
  y redibujar en bloques, y según la semilla sale pixel art o el boceto coloreado.
- Si se le añade la **hoja de ejemplos del preset** como referencia, copia los
  personajes de ejemplo dentro del resultado y el recorte falla. Por eso **solo se
  envía el boceto**; el estilo del preset viaja en el prompt.
- Referencia a baja resolución (`vae_input_max_pixels`): mismo resultado y ~40 % más
  rápido.

Consejo: genera 4 variantes y quédate con la mejor; un boceto con volúmenes
(no solo líneas finas) da mejores resultados.

## Rendimiento (GPU de 12 GB, referencia)

~25-50 s por variante con el modelo cargado; la primera imagen tras arrancar el
servidor tarda más (carga de modelos). El servidor atiende **una petición a la vez**,
así que las variantes se generan en serie y compartir la GPU con otra generación las
encola.
