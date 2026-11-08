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

El coach también felicita las buenas jugadas (`category: "praise"`):

| Jugada | Cuándo | Ejemplo |
| --- | --- | --- |
| Multiasesinato | Evento `Multikill` tuyo; si llegan varios juntos, gana el mayor | "¡Triple asesinato! Dominas la pelea…" / "¡PENTAKILL!…" |
| Primera sangre | Evento `FirstBlood` con tu nombre | "¡Primera sangre! Buen inicio…" |
| Ace | Tu equipo elimina a los cinco rivales | "¡Ace! Tu equipo eliminó a los cinco rivales…" |
| Torre o inhibidor | Lo destruyes o asistes | "¡Torre derribada!…" |
| Objetivo propio | Tú das el golpe final al dragón o al Barón | "¡Bien jugado! Aseguraste el dragón Infernal…" |
| Farmeo | Desde el minuto 10: 9 CS/min casi perfecto, 10 perfecto, 11 o más, más que perfecto | "¡Farmeo perfecto! 10.1 CS/min a los 11 min…" |

Las felicitaciones de jugadas salen al instante, sin esperar los 15 s entre consejos. La de farmeo se repite al subir de nivel o cada 5 minutos si se mantiene.

Cada regla tiene su enfriamiento y hay al menos 15 s entre consejos, salvo los urgentes (vida crítica y objetivos). Los objetivos anteriores a abrir la app sirven para los temporizadores, pero no se anuncian. Tilt y caída de CS siguen en `RulesEvaluator` y se redactan con Groq si `GROQ_API_KEY` está definida (modelo por defecto `openai/gpt-oss-20b`), con Gemini como respaldo opcional y, si no, con un texto heurístico. El prompt de Groq es una sola línea de métricas con tope de 48 tokens. No se reenvía el JSON de la partida.

Cada consejo trae `category` (`recall`, `death`, `objective`, `economy`, `vision`, `tilt`, `farm`), que el escritorio usa para elegir el sonido, y se guarda en `coach_advice_logs` con su fuente (`rules`, `groq`, `gemini` o `heuristic`). Cualquier modelo que no sea de Gemini (por ejemplo `openai/gpt-oss-120b`) se guarda como `groq`. `GET /api/coach/metrics` devuelve `bySource` con el total por fuente; `fromGemini` y `fromHeuristic` se mantienen para la app 1.0.

## Ejecutar

```powershell
pip install -r requirements-dev.txt
copy .env.example .env
uvicorn app.main:app --reload
```

`GET /health` responde aunque el cliente de League no esté abierto.

## Despliegue: nube y agente local

El mismo código se despliega de tres formas según `APP_MODE`:

| Modo | Dónde corre | Qué expone |
| --- | --- | --- |
| `full` (por defecto) | Tu PC, en desarrollo | Todo |
| `cloud` | Vercel | Cuentas, perfil, API de Riot, historial guardado, análisis post-partida, métricas del coach y plan de línea con IA |
| `local` | Dentro del ejecutable de escritorio (`lolanalyzer-agent.exe`) | Selección de campeón, importación de runas, objetos y hechizos, coach en vivo e historial leído del cliente |

La nube no puede leer el cliente de League (vive en `127.0.0.1` del jugador) y Vercel no guarda estado entre peticiones ni admite WebSocket. Por eso esas partes van en el agente local. El agente **no lleva claves ni la URL de la base de datos**: guarda el historial, registra los consejos y pide el plan de línea a la nube con el JWT del jugador (`/api/history/import`, `/api/coach/advice`, `/api/ai/matchup-plan`).

**Seguridad del agente.** Escucha solo en `127.0.0.1` y exige `X-Agent-Token`, un token aleatorio que Electron genera en cada arranque; una página web abierta en el navegador no lo conoce. Desde la 1.1.0 también lo exigen las rutas de solo lectura (`/api/champ-select/status`, `session`, `summoner` y `gameflow`); en modo `full` siguen abiertas para desarrollo. `POST /api/history/import` acepta partidas de cualquier cuenta de League abierta en el cliente: las guarda con su `puuid` y guarda el perfil (`profile`: nombre, nivel, icono y rango) en `riot_profiles`. `gameflow` incluye `connected`, que dice si el cliente respondió, para que el escritorio sepa cuándo se abrió League. Se cierra solo si el proceso de escritorio desaparece.

### Vercel (modo nube)

En producción: **https://lolanalyzer-api.vercel.app** (`/health` responde `"mode": "cloud"`).

Vercel detecta FastAPI y usa `app/main.py` como entrada; `APP_MODE=cloud` hace que solo se monten las rutas de nube. `.vercelignore` deja fuera `.env`, bases de datos, pruebas y el agente. En Vercel el caché de Riot es un SQLite en `/tmp` que se crea solo, y el esquema lo crean las migraciones del repositorio de BD (`INIT_DB_ON_STARTUP=false`).

```bash
npx vercel link --yes --project lolanalyzer-api
npx vercel env add DATABASE_URL production
npx vercel deploy --prod
```

Variables de producción: `APP_MODE=cloud`, `DATABASE_URL` (pooler de Supabase), `SECRET_KEY` (propia de producción, distinta de la de desarrollo), `GROQ_API_KEY`, `GROQ_MODEL`, `RIOT_API_KEY`, `ALLOWED_ORIGINS` (`app://lolanalyzer` para el escritorio), `INIT_DB_ON_STARTUP=false`, `LCU_AUTO_CONNECT=false` y `DEBUG=false`.

**Cambios de esquema.** Como Vercel no crea ni altera tablas al arrancar, una versión con tablas o columnas nuevas se despliega en dos pasos: primero se aplica el esquema en Supabase y después se despliega la API. Desde esta carpeta, con `DATABASE_URL` de Supabase en `.env`:

```bash
python -c "import asyncio; from app.db.session import init_db; asyncio.run(init_db())"
npx vercel deploy --prod
```

`init_db` crea las tablas que falten y `app/db/schema_compat.py` agrega columnas e índices nuevos de forma idempotente (las migraciones Alembic del repositorio de BD describen los mismos cambios). Si se despliega la API antes, las rutas que leen las columnas nuevas responden 500 hasta aplicar el esquema.

### Agente local (PyInstaller)

```bash
python -m pip install -r requirements-dev.txt
python agent/build_agent.py
```

El resultado es `dist/lolanalyzer-agent/lolanalyzer-agent.exe`, que el instalador de escritorio copia dentro de la app. No incluye `.env`, bases de datos ni pruebas. Para probarlo a mano: `LOLANALYZER_AGENT_TOKEN=<token de 64 caracteres> dist/lolanalyzer-agent/lolanalyzer-agent.exe` (puerto `8765` por defecto).

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

Las partidas se leen primero del cliente de League (`/lol-match-history/v1/products/lol/current-summoner/matches` y, si hace falta el detalle, `/lol-match-history/v1/games/{gameId}`), con el lockfile del cliente y solo contra `127.0.0.1`. El resumen se guarda en `local_match_history`. Si el cliente está cerrado, `GET /api/riot/matches` devuelve esa tabla. Cada fila guarda el `riot_puuid` de la cuenta de League con la que se jugó: una cuenta de LolAnalyzer puede tener varios perfiles. `GET /api/riot/profiles` los lista con `default` (el Riot ID vinculado o, sin vínculo, el último perfil abierto); `matches` y `stats` aceptan `?profile=<puuid>` o `?profile=all`. Un mismo Riot ID puede vincularse a varias cuentas (ya no hay índice único en `users.riot_puuid`). Los planes de línea que pide el agente quedan en `matchup_reports` (uno por matchup y cuenta) y se listan en `GET /api/ai/matchup-plans`. La API de Riot (`match-v5`) solo se usa cuando no hay archivo local; si además falta o caducó la clave, la ruta responde el error de Riot de siempre. `riot_cache` sigue siendo un caché corto y `match_records` no se toca.

Solo se guardan y se muestran partidas de La Grieta del Invocador (colas 400, 420, 430, 440, 490 y 700, o modo `CLASSIC`). ARAM, Arena y URF se omiten sin borrar filas. `GET /api/riot/profile` lee nivel y rango del cliente abierto solo si su cuenta es la vinculada; si no, usa la API de Riot. El cliente nunca vincula ni desvincula cuentas por su cuenta: `POST /api/riot/connect` lo usa solo cuando el Riot ID pedido coincide, y un Riot ID que ya tiene otro usuario responde 409.

## Análisis post-partida

Al terminar cada partida el escritorio llama a `POST /api/analysis/latest`. El backend sincroniza el historial del cliente y analiza la última partida de La Grieta de las últimas 6 horas (las de menos de 5 minutos, como los remakes, se ignoran). Usa el resumen final de la partida, no la telemetría en vivo:

- Las métricas se calculan aquí frente a una referencia por rol: CS/min, visión/min, muertes cada 10 min, KDA, daño/min y oro/min. Cada una trae valor, meta y veredicto.
- Groq solo redacta tres frases (`strengths`, `improvements`, `focus`) a partir de una línea con esas métricas: unos 280 tokens de entrada y 220 de salida. Sin clave o si falla, las mismas tres frases salen de reglas (`source: "rules"`).
- Se guarda en `match_analyses` con clave única por usuario y partida, así cada partida se analiza y se cobra una sola vez.

`GET /api/analysis` lista todos los análisis de la cuenta (más nuevos primero). `GET /api/analysis/{match_id}` devuelve el análisis guardado (404 si no hay) y `POST /api/analysis/{match_id}` analiza una partida antigua a pedido.

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
