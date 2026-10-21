# Seguridad

## Claves y datos privados

- **Nunca subas claves** (Gemini u otras), `config.json`, `.env`, modelos ni tus
  proyectos privados. El `.gitignore` los excluye, pero revisa `git status` antes de
  cada commit.
- La clave de Gemini se guarda fuera del repositorio
  (`~/.config/forja-sprites/config.json`, permisos `600`) o en la variable
  `GEMINI_API_KEY`. Ver [docs/motores-ia.md](docs/motores-ia.md).
- Si una clave llega a publicarse: **revócala y crea otra** en tu cuenta de Google;
  borrarla del historial no la hace segura.
- Los tests usan claves falsas (`AIzaFAKE-…`).

## Servidor local

`forja-servidor` escucha en `127.0.0.1` y no tiene autenticación: no lo expongas a
internet ni a redes que no sean de confianza.

## Informar de una vulnerabilidad

Abre un aviso privado en la pestaña **Security** del repositorio (*Report a
vulnerability*) o, si no está disponible, un issue sin datos sensibles.
