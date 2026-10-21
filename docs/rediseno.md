# Rediseñar una criatura conservando su base

*Resultados → Rediseñar* crea **una variante nueva** de la misma criatura (la original no
se toca). Como es una variante más, se puede retocar, girar en las 4 direcciones,
animar y verla en la galería del proyecto. En la tarjeta aparece su nombre y «de la N».

| Quieres… | Camino | Cómo es |
|---|---|---|
| Otro color («ahora en azul») | **Recolorear**, sin IA | instantáneo, gratis, exacto: el resto del sprite no cambia |
| Elementos nuevos («con un casco y un cuerno», «con capa roja») | **Gemini** edita la imagen | misma criatura y pose, con lo que pidas |

## Cambiar el color

1. Toca en la paleta los colores del sprite que quieres cambiar (ninguno elegido = todos).
   Así tiñes el pelaje de azul y dejas los ojos verdes.
2. Elige el **nuevo color**, **cómo** y la **intensidad/brillo**. Se ve el resultado al
   momento, al lado del original.
   - **Teñir**: todos los colores elegidos toman ese tono y conservan sus claros y
     oscuros. Es lo que mejor se lee como «ahora en azul».
   - **Girar**: gira el tono y conserva la variedad de tonos (marrones y morados siguen
     siendo distintos entre sí); sirve para cambios suaves.
3. **Ajustar a la paleta del estilo** (activado): cada color pasa al más cercano de la
   paleta del juego, para que combine con el resto. Si el color nuevo se ve apagado,
   desactívalo (el sprite tendrá colores fuera de la paleta del estilo).
4. **Guardar como variante nueva**: se guarda el sprite y también la imagen de la IA con
   el mismo cambio (las direcciones y las poses parten de ella).

La saturación se refuerza sola si el color destino es más vivo que el pelaje (un
gris-marrón apenas se vería azul con solo cambiar el tono); «Intensidad» la multiplica.
El contorno casi negro no se tiñe.

## Añadir elementos

Escribe qué cambiar («ahora con un casco de hierro y un cuerno grande») y pulsa
**Rediseñar con Gemini**. Se traduce al inglés y Gemini redibuja la variante conservando
cuerpo, pose, proporciones y estilo; el resultado se pixeliza a la escala de la variante
con la **paleta completa del estilo** (no solo los colores de la base: el rediseño puede
traer otros). Es de pago y envía la imagen de la variante a Google.

Encadenable: se puede rediseñar un rediseño (azul → azul con casco).

**¿Por qué no con la IA local?** Se probó con una criatura de prueba («azul, con casco y cuerno»): con la
imagen de partida, su contorno y la identidad de la variante, SDXL conserva la criatura
pero **no aplica el cambio**, ni subiendo la fuerza al máximo (el IP-Adapter y el
contorno pesan más que el texto). Para el color no hace falta IA: se recolorea.

## Archivos

Cada variante nueva guarda `ia_NN.png`, `sprite_NN.png` y `sprite_NN_x8.png`, y su entrada
en `info.json` lleva `origen` (`{"variante": 2, "tipo": "color"|"ia", "cambio": …}`) y
`nombre`. API: `POST /api/recolorear` y `POST /api/redisenar` ([api.md](api.md)).

## Límites

- Con muchos colores elegidos y paletas pequeñas, «Ajustar a la paleta» puede fundir
  sombras parecidas.
- Gemini no siempre respeta la pose al milímetro: revisa la variante nueva antes de
  animarla.
