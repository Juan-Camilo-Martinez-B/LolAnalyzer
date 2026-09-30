# LolAnalyzer Backend

API FastAPI del asistente. La capa de IA (Gemini) sigue en el código y no se invoca desde el flujo de cuenta ni desde Riot.

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
- `RIOT_API_KEY`: clave de desarrollador de [developer.riotgames.com](https://developer.riotgames.com/). Solo vive en el servidor.

Riot Sign On (RSO) es el login oficial del jugador, pero Riot solo lo entrega a aplicaciones de producción aprobadas. En local la cuenta se vincula con el Riot ID (`nombre#tag`) y la API oficial (`account-v1`, `summoner-v4`, `league-v4`, `match-v5`). Nunca se pide la contraseña de Riot.

## Auth

| Método | Ruta | Auth |
| --- | --- | --- |
| POST | `/api/auth/register` | no |
| POST | `/api/auth/login` | no |
| POST | `/api/auth/logout` | bearer |
| POST | `/api/auth/refresh` | refresh token |
| GET | `/api/auth/me` | bearer |
| GET | `/api/auth/recovery-options` | no |
| POST | `/api/auth/forgot-password` | no |
| POST | `/api/auth/reset-password` | no |
| POST | `/api/riot/connect` | bearer |
| GET | `/api/riot/profile` | bearer |
| GET | `/api/riot/matches` | bearer |
| GET | `/api/riot/stats` | bearer |

Las contraseñas y las respuestas de recuperación se guardan con Bcrypt. Tras 5 fallos hay un bloqueo de 15 minutos. Cambiar la contraseña invalida los JWT anteriores (`session_version`).

Las partidas de Riot se consultan bajo demanda y se cachean unos minutos en `riot_cache`. No se copian a `match_records`.
