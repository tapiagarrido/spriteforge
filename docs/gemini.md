# Motor de IA externo: Gemini (opcional)

Además de la IA local (tu GPU), la generación puede hacerse con la **API de Gemini**
de Google. Es opcional, externa y **de pago** según tu cuenta.

## Qué se hace en Gemini y qué sigue en local

| Paso | Local | Gemini |
|---|---|---|
| Limpiar el boceto | ✔ | ✔ (local) |
| Traducir la descripción | ✔ | ✔ (local) |
| Generar la imagen | GPU propia (SDXL) | **API de Google** |
| Recortar la figura (segmentador) | ✔ | ✔ (local) |
| Pixelizar con la paleta del preset | ✔ | ✔ (local) |

Como el recorte y el pixelizado siguen siendo locales, los personajes generados con
Gemini usan la **misma paleta, tamaño y contorno** que los locales: el juego no se
mezcla de estilos.

A Gemini se envían: el boceto (trazo negro sobre blanco) como guía de pose, la
rejilla de ejemplos del preset como referencia de estilo, y un texto con la
descripción, la ambientación del proyecto y lo que hay que evitar. Las imágenes de
Gemini llevan la marca de agua invisible SynthID.

## Configurar la clave

1. Crea una clave en Google AI Studio.
2. En la app: **Ajustes** → pega la clave → **Guardar clave** → **Probar conexión**.
   "Probar conexión" pregunta a Google qué modelos de imagen tiene tu cuenta y los
   muestra para elegir.
3. En **Especificaciones → Motor de IA**, elige **Gemini**.

Alternativa sin interfaz: la variable de entorno `GEMINI_API_KEY` (tiene prioridad
sobre la guardada).

### Dónde se guarda y cómo se protege

- Archivo `~/.config/forja-sprites/config.json` (o `FORJA_CONFIG`), **fuera del
  repositorio**, creado con permisos `600` (solo tu usuario).
- La API local nunca devuelve la clave: la interfaz solo ve si existe y sus 4
  últimos caracteres.
- La clave viaja a Google en la cabecera `x-goog-api-key`, nunca en la URL.
- No se escribe en logs ni en `info.json` (que solo registra `motor_ia: gemini:<modelo>`).

## Terminal

```bash
.venv/bin/forja-generar boceto.jpg --proyecto mazmorra-oscura \
    --prompt "esqueleto con lanza" --motor gemini [--modelo-gemini <id>]
```

## Detalles técnicos

- Cliente propio con `urllib` (sin dependencias nuevas), en `motor/gemini.py`.
- Una petición por variante, hasta 4 en paralelo; la semilla se envía por variante.
- Proporción de la imagen: la más cercana a la del sprite (`9:16`, `1:1`…).
- La API de imágenes de Gemini ha cambiado de versión y de nombres de campo: el
  cliente prueba `v1beta` y `v1`, y si rechaza las opciones de imagen reintenta con
  la petición mínima. Lee la imagen tanto de `inlineData` como de `inline_data`.
- La imagen recibida se escala al tamaño de generación local para que la silueta
  del boceto coincida en el pixelizado.
- Errores traducidos a mensajes claros: clave rechazada (401/403), cuota (429),
  modelo inexistente (404), respuesta sin imagen (con el motivo de Gemini).

> Estado: implementado y cubierto por tests con la API simulada. Pendiente de
> verificar con una clave real.
