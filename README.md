# LolAnalyzer Backend

API FastAPI del asistente. El coach en vivo usa Groq si `GROQ_API_KEY` está definida (modelo por defecto `llama-3.1-8b-instant`). Si no hay clave, usa un consejo heurístico. Gemini sigue en el código como respaldo opcional (`GEMINI_API_KEY`), pero la plantilla ya no lo trae. El prompt de Groq es una sola línea de métricas (campeón, fase, KDA, CS/min, muertes recientes) con tope de 48 tokens. No se reenvía el JSON de la partida.

## Ejecutar

```powershell
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

`GET /health` responde aunque el cliente de League no esté abierto.

## Variables

Copia `.env.example`. No subas `.env`.

- `DATABASE_URL`: Postgres/Supabase. Vacío usa SQLite local.
- `SECRET_KEY`: firma JWT. Cámbiala fuera de desarrollo.
- `ALLOWED_ORIGINS`: orígenes del desktop, separados por coma. No uses `*`.
- `RIOT_API_KEY`: clave de desarrollador de [developer.riotgames.com](https://developer.riotgames.com/). Solo vive en el servidor. El historial no la necesita si el cliente local ya sincronizó.
- `GROQ_API_KEY`: clave de Groq. Solo vive en el servidor. No la pongas en el cliente.
- `GROQ_MODEL`: modelo de Groq. Por defecto `llama-3.1-8b-instant`.

Riot Sign On (RSO) es el login oficial del jugador, pero Riot solo lo entrega a aplicaciones de producción aprobadas. En local la cuenta se vincula con el Riot ID (`nombre#tag`) y la API oficial (`account-v1`, `summoner-v4`, `league-v4`, `match-v5`). Nunca se pide la contraseña de Riot.

## Auth

| Método | Ruta | Auth |
| --- | --- | --- |
| POST | `/api/auth/register` | no |
| POST | `/api/auth/login` | no |
| POST | `/api/auth/logout` | bearer |
| POST | `/api/auth/refresh` | refresh token |
| GET | `/api/auth/me` | bearer |
| PUT | `/api/profile` | bearer. Incluye `theme_preference`: `light`, `system` o `dark`. |
| GET | `/api/auth/recovery-options` | no |
| POST | `/api/auth/forgot-password` | no |
| POST | `/api/auth/reset-password` | no |
| POST | `/api/riot/connect` | bearer |
| GET | `/api/riot/profile` | bearer |
| GET | `/api/riot/matches` | bearer |
| GET | `/api/riot/stats` | bearer |

Las contraseñas y las respuestas de recuperación se guardan con Bcrypt. Tras 5 fallos hay un bloqueo de 15 minutos. Cambiar la contraseña invalida los JWT anteriores (`session_version`).

Las partidas se leen primero del cliente de League (`/lol-match-history/v1/products/lol/current-summoner/matches` y, si hace falta el detalle, `/lol-match-history/v1/games/{gameId}`), con el lockfile del cliente y solo contra `127.0.0.1`. El resumen se guarda en `local_match_history`. Si el cliente está cerrado, `GET /api/riot/matches` devuelve esa tabla. La API de Riot (`match-v5`) solo se usa cuando no hay archivo local; si además falta o caducó la clave, la ruta responde el error de Riot de siempre. `riot_cache` sigue siendo un caché corto y `match_records` no se toca.

Solo se guardan y se muestran partidas de La Grieta del Invocador (colas 400, 420, 430, 440, 490 y 700, o modo `CLASSIC`). ARAM, Arena y URF se omiten sin borrar filas. `GET /api/riot/profile` lee nivel y rango del cliente abierto solo si su cuenta es la vinculada; si no, usa la API de Riot. El cliente nunca vincula ni desvincula cuentas por su cuenta: `POST /api/riot/connect` lo usa solo cuando el Riot ID pedido coincide, y un Riot ID que ya tiene otro usuario responde 409.

## Runas y objetos

Durante la selección de campeón el escritorio pide `GET /api/champ-select/loadout?champion_id=&champion_name=&position=` (con sesión):

- `runePages`: las páginas que recomienda el propio cliente de League (`/lol-perks/v1/recommended-pages/...`), con la posición pedida primero.
- `build`: los objetos que más usaste con ese campeón en tus partidas de La Grieta guardadas en `local_match_history`. Sin partidas, la lista viene vacía.

`POST /api/champ-select/loadout/runes` crea la página "LolAnalyzer - <campeón>" y la deja activa. Si ya existía una de LolAnalyzer, la reemplaza; nunca borra páginas propias del jugador. Si no hay espacio, responde 409. También aplica los hechizos de invocador cuando la selección está abierta.

`POST /api/champ-select/loadout/items` escribe el conjunto "LolAnalyzer - <campeón>" en el cliente, asociado al campeón y a La Grieta. Repetir la importación reemplaza ese conjunto.
