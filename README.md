# LolAnalyzer Backend

API FastAPI del asistente.

## Coach en vivo

`GET /api/coach/live` lee el Live Client (`127.0.0.1:2999`) y devuelve un consejo solo cuando pasa algo concreto. Si no pasa nada relevante, no inventa un consejo.

Reglas de situación (`app/engine/situations.py`), con los números de la partida y sin modelo de lenguaje:

| Situación | Cuándo | Ejemplo |
| --- | --- | --- |
| Vida baja | Menos de 30% durante 6 s, sin curarse | "Llevas 8 s con 22% de vida: vuelve a base y compra: tienes 1200 de oro." |
| Muerte | Al morir con 8 s o más de reaparición | "Reapareces en 24 s. Aprovecha para comprar: tienes 1500 de oro." |
| Objetivo cerca | 60 s antes del dragón (5:00, reaparece a los 5 min) o del Barón (20:00, 6 min) | "El dragón aparece en 0:55: empuja tu oleada y entra al río con visión." |
| Objetivo tomado | Dragón, Barón, Heraldo o larvas, según el equipo | "El enemigo tomó el dragón Infernal. El próximo sale a las 10:30…" |
| Sin maná | Menos de 15% durante 10 s (solo campeones con maná) | "Te queda 10% de maná: vuelve a base o juega pasivo…" |
| Oro sin gastar | 1300 o más durante 40 s, desde el minuto 3 | "Tienes 1500 de oro sin gastar: empuja la oleada y vuelve a comprar." |
| Visión baja | Desde el minuto 8, menos de 0.6 de visión por minuto | "Tu visión es baja (3 a los 12 min)…" |

Cada regla tiene su enfriamiento y hay al menos 15 s entre consejos, salvo los urgentes (vida crítica y objetivos). Los objetivos anteriores a abrir la app sirven para los temporizadores, pero no se anuncian. Tilt y caída de CS siguen en `RulesEvaluator` y se redactan con Groq si `GROQ_API_KEY` está definida (modelo por defecto `openai/gpt-oss-20b`), con Gemini como respaldo opcional y, si no, con un texto heurístico. El prompt de Groq es una sola línea de métricas con tope de 48 tokens. No se reenvía el JSON de la partida.

Cada consejo trae `category` (`recall`, `death`, `objective`, `economy`, `vision`, `tilt`, `farm`), que el escritorio usa para elegir el sonido, y se guarda en `coach_advice_logs` con su fuente (`rules`, `groq`, `gemini` o `heuristic`).

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
- `GROQ_MODEL`: modelo de Groq. Por defecto `openai/gpt-oss-20b`. Los modelos `gpt-oss` razonan antes de responder: el backend les pide `reasoning_effort: low` y suma 400 tokens de margen, para que la respuesta no llegue vacía.

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

## Análisis post-partida

Al terminar cada partida el escritorio llama a `POST /api/analysis/latest`. El backend sincroniza el historial del cliente y analiza la última partida de La Grieta de las últimas 6 horas (las de menos de 5 minutos, como los remakes, se ignoran). Usa el resumen final de la partida, no la telemetría en vivo:

- Las métricas se calculan aquí frente a una referencia por rol: CS/min, visión/min, muertes cada 10 min, KDA, daño/min y oro/min. Cada una trae valor, meta y veredicto.
- Groq solo redacta tres frases (`strengths`, `improvements`, `focus`) a partir de una línea con esas métricas: unos 280 tokens de entrada y 220 de salida. Sin clave o si falla, las mismas tres frases salen de reglas (`source: "rules"`).
- Se guarda en `match_analyses` con clave única por usuario y partida, así cada partida se analiza y se cobra una sola vez.

`GET /api/analysis/{match_id}` devuelve el análisis guardado (404 si no hay) y `POST /api/analysis/{match_id}` analiza una partida antigua a pedido.

## Runas y objetos

Durante la selección de campeón el escritorio pide `GET /api/champ-select/loadout?champion_id=&position=` (con sesión; `opponent_id` es opcional).

- **Fuente:** datos meta de OP.GG a través de su endpoint MCP público (`OPGG_MCP_URL`, sin clave). Si la selección ya muestra al rival de línea, se usa `lol_get_lane_matchup_guide` (runas, hechizos, objetos y un consejo contra ese campeón). Si no hay rival (partida de práctica, a ciegas o antes de que elija), se usa `lol_get_champion_analysis` con la build general de esa línea.
- **Rival de línea:** las posiciones enemigas no son públicas, así que cada pick enemigo se ubica en la línea principal que recomienda el propio cliente (`recommended-pages`, `isDefaultPosition`) y se compara con la tuya. Sin línea asignada, se usa la línea principal de tu campeón.
- **Respuesta:** `runePages` (la página meta primero y después las del cliente como alternativa), `itemBlocks` (Inicio, Build principal y Situacionales), hechizos, orden de habilidades y consejo. Si OP.GG no responde, quedan las runas del cliente y `notice` lo explica.
- Las respuestas de OP.GG se guardan 30 minutos en memoria (`OPGG_CACHE_MINUTES`).
- **Plan de línea (`matchupPlan`):** solo cuando se conoce al rival. Groq convierte los datos del matchup (consejo de OP.GG, ventaja de línea, asesinatos en solitario y estilo recomendado) en tres frases: `lane`, `fight` y `avoid`. Se cachea por campeón, rival y línea, así un matchup cuesta una sola llamada. Sin Groq, el plan sale de esos mismos datos.

`POST /api/champ-select/loadout/runes` crea la página "LolAnalyzer - <campeón>" y la deja activa. Reemplaza la página anterior de LolAnalyzer; nunca borra páginas del jugador. Si no hay espacio, responde 409.

`POST /api/champ-select/loadout/spells` recibe `spell_ids` en orden (el primero va en D y el segundo en F) y solo funciona con la selección abierta; fuera de ella responde 409. El escritorio decide el orden según la tecla de Destello que eligió el jugador.

`POST /api/champ-select/loadout/items` recibe bloques (`title`, `item_ids`) y escribe el conjunto "LolAnalyzer - <campeón>" en la tienda, asociado al campeón y a La Grieta. Los objetos repetidos (dos pociones) pasan a ser una cantidad. Repetir la importación reemplaza ese conjunto.
