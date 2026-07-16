# Plan de Implementación — Módulo de Entrenamiento por Voz

**Proyecto:** Vex People Predictive — VEX I+D
**Modelo:** OpenAI `gpt-realtime-2.1-mini` (Realtime API GA)
**Principio rector:** módulo hermano e independiente del entrenamiento por chat. Cero modificaciones a `training.py`, `chat.py`, `scoring_modes.py`, `calculate_vex_profile` ni a las tablas existentes. Solo se **reutiliza por lectura** (escenarios, patrón de evaluación, decoradores, scoping por operativa).

---

## 1. Arquitectura general

```
┌─────────────┐   1. POST /api/voice/session/start          ┌──────────────┐
│  Navegador  │ ──────────────────────────────────────────► │ Flask (Render)│
│  (operador) │ ◄── token efímero ek_... + config sesión ── │ voice_training│
│             │                                              └──────┬───────┘
│  WebRTC     │   2. Audio bidireccional (voz)                      │ 2b. POST /v1/realtime/client_secrets
│  getUserMedia ◄═══════════════════════════════════════╗           ▼
│  + DataChannel   (el audio NUNCA pasa por Flask)      ║    ┌──────────────┐
│             │ ═════════════════════════════════════► ║════│ OpenAI        │
│             │   3. Eventos JSON (transcripciones,     ║    │ /v1/realtime  │
│             │      VAD, turnos) por el data channel   ║    │ gpt-realtime- │
│             │                                         ╚════│ 2.1-mini      │
│             │   4. POST /api/voice/turn (persistir            └──────────┘
│             │      transcripciones + métricas de tiempo)
│             │   5. POST /api/voice/end → evaluación LLM
└─────────────┘      (reutiliza chat.call_openai, como el chat)
```

**Decisiones clave:**

1. **WebRTC directo navegador↔OpenAI** con token efímero (`POST /v1/realtime/client_secrets` → token `ek_...` de corta vida). El backend solo acuña el token con la API key de servidor; el audio no toca Flask. Esto es crítico porque Gunicorn con workers sync no puede sostener WebSockets de audio, y así no cambiamos nada del deployment.
2. **La IA actúa de CLIENTE, el usuario de ASESOR** — idéntico al chat. Las `instructions` de la sesión Realtime se generan con el mismo patrón de system prompt de rol-play de `_create_interaction()` (persona del caso, sin leakage de otros casos).
3. **Las transcripciones son la fuente de verdad para el scoring.** La Realtime API transcribe ambos lados (входное audio del usuario vía modelo de transcripción configurable; la salida del modelo trae su propio transcript). El frontend recibe esos eventos por el data channel y los persiste vía AJAX. Al cerrar, evaluamos el transcript con `chat.call_openai()` — la misma vía que hoy evalúa el chat, con la rúbrica adaptada a voz.
4. **No se almacena audio en v1** — solo transcripciones y métricas. Evita problemas de disco/privacidad. (Grabación opcional queda como fase futura explícita.)
5. **Sin multichat en v1**: una sola conversación de voz por sesión (hablar dos llamadas a la vez no tiene análogo real). `VoiceSession` es plana, sin concepto de batch.

---

## 2. El modelo `gpt-realtime-2.1-mini` (investigado julio 2026)

- Lanzado el 6/7/2026 junto a `gpt-realtime-2.1`. Es el primer realtime "mini" **con razonamiento configurable**: `reasoning.effort` ∈ `minimal|low|medium|high|xhigh` (default `low`). Para rol-play usaremos `low` (o `minimal` si la latencia importa más que el matiz del personaje).
- Disponible en la Realtime API GA (`/v1/realtime`) vía WebRTC, WebSocket y SIP.
- **Precio**: audio de entrada **$10/M tokens**, salida **$20/M**, entrada cacheada **$0.30/M** (~⅓ del `gpt-realtime-2.1` grande: $32/$64). El audio consume ~600 tokens/minuto de entrada: una sesión de 5 minutos ronda **US$0.08–0.15**, comparable o menor al costo actual de un entrenamiento por chat con evaluación.
- OpenAI reporta ~25% menos latencia p95 en toda la familia realtime.
- El manejo de turnos (VAD) es server-side y configurable (`semantic_vad` — corta cuando semánticamente terminaste de hablar — o `server_vad` por silencios). Los eventos de VAD (`input_audio_buffer.speech_started/stopped`) nos dan los timestamps para medir tiempos de respuesta del asesor.

> ⚠️ Nota para el implementador: la red del entorno de desarrollo remoto bloquea `developers.openai.com`; al implementar, verificar contra la doc oficial el payload exacto de `client_secrets` y los nombres de eventos vigentes (`conversation.item.input_audio_transcription.completed`, `response.output_audio_transcript.done`, etc.).

---

## 3. Estructura de archivos (todo NUEVO, nada existente se toca salvo 3 puntos de enganche)

```
vexpredictive/
├── voice_training.py              # NUEVO blueprint voice_bp (rutas + lógica)
├── realtime_client.py             # NUEVO: acuñar client secrets (urllib, sin SDK, patrón chat.py)
├── voice_scoring.py               # NUEVO: rúbrica de evaluación de voz + agregación de métricas
├── migrate_v10.py                 # NUEVO: tablas voice_sessions / voice_turns (patrón migrate_vN)
├── static/
│   ├── js/voice_training.js       # NUEVO: WebRTC, data channel, UI de llamada
│   └── css/voice_training.css     # NUEVO
├── templates/voice/               # NUEVO directorio
│   ├── index.html                 # lista de escenarios + historial de sesiones de voz
│   ├── session.html               # pantalla de llamada en vivo
│   └── result.html                # resultado y métricas de la sesión
└── templates/admin/
    └── voice_dashboard.html       # NUEVO: dashboard admin de voz
```

**Únicos 3 puntos de contacto con código existente:**

| Archivo | Cambio | Riesgo |
|---|---|---|
| `app.py` | +2 líneas: `from voice_training import voice_bp` + `app.register_blueprint(voice_bp)` | Nulo |
| `Dockerfile` | añadir `python migrate_v10.py` a la cadena de migraciones | Nulo (idempotente) |
| `templates/base.html` | ítem de menú "Entrenamiento por Voz" | Nulo |

**Reutilización por import (sin modificar):** `chat.call_openai` (evaluación), `training.parse_cases`/`get_case` (casos del escenario), `training.safe_elapsed`, `decorators.*` (roles y scoping), `scoring_modes.get_effective_mode` (hint del modo activo para la rúbrica), modelo `TrainingScenario` (lectura).

---

## 4. Modelo de datos (tablas nuevas, espejo conceptual de las de chat)

### `voice_sessions`
| Campo | Tipo | Notas |
|---|---|---|
| `id` | Integer PK | |
| `user_id` | FK users, NOT NULL | scope por operativa vía user (mismo patrón que TrainingSession) |
| `scenario_id` | FK training_scenarios, NOT NULL | **reutiliza los escenarios del chat** — se autoran una sola vez |
| `case_index` | Integer default 0 | caso aleatorio, igual que chat |
| `scoring_mode` | String(20) NULL | snapshot del modo del escenario al iniciar (patrón TrainingBatch) |
| `status` | String(20) | `active` / `completed` / `abandoned` / `error` |
| `started_at`, `ended_at` | DateTime | usar `datetime.now(timezone.utc)` + `safe_elapsed` |
| `duration_seconds` | Integer | |
| `openai_session_id` | String(100) NULL | trazabilidad/debug |
| **Métricas de conversación** | | |
| `total_turns` | Integer | turnos del asesor |
| `total_words_user` | Integer | palabras habladas por el asesor (del transcript) |
| `talk_ratio` | Float | % del tiempo hablado por el asesor vs cliente |
| `avg_response_latency` | Float | **análogo del ART**: seg. promedio entre fin de habla del cliente e inicio de respuesta del asesor (medido por eventos VAD en el cliente) |
| `speech_rate_wpm` | Float | palabras/min habladas (análogo del WPM) |
| `interruptions` | Integer | veces que el asesor pisó al cliente (barge-in) |
| `long_silences` | Integer | silencios > 5s |
| **Evaluación (idéntica filosofía al chat)** | | |
| `nps_score` | Integer 0-10 | misma rúbrica |
| `response_correct` | Boolean | vs `expected_response` del caso |
| `filler_words` | Integer | muletillas ("este...", "eh...") — **reemplaza a `spelling_errors`** |
| `ai_feedback` | Text (JSON) | `{feedback, strengths, improvements, empathy_breakdown, voice_breakdown}` |
| `tokens_used` | Integer | tokens realtime (usage del data channel) + tokens de evaluación |
| `estimated_cost_usd` | Float | control de gasto visible para admins |

### `voice_turns`
| Campo | Tipo | Notas |
|---|---|---|
| `id`, `session_id` FK | | |
| `role` | String(20) | `user` (asesor) / `client` (IA) — misma convención que TrainingMessage |
| `transcript` | Text | |
| `started_at_ms`, `ended_at_ms` | BigInteger | timestamps relativos de audio para métricas de ritmo/latencia |
| `word_count` | Integer | |
| `created_at` | DateTime | |

**Por qué tablas nuevas y no reutilizar `training_sessions`:** los dashboards, insights y `calculate_vex_profile` consultan `TrainingSession` sin filtro de canal; mezclar sesiones de voz alteraría silenciosamente todas las estadísticas y el índice predictivo existentes. Tablas separadas = riesgo cero. La integración al VexProfile es una decisión explícita de la Fase 6, no un efecto colateral.

---

## 5. Rutas del blueprint `voice_bp` (convención kebab-case, sin url_prefix, como el resto)

**Usuario (`@login_required`):**
- `GET /voice-training` — lista de escenarios activos (scope operativa) + historial propio de sesiones de voz.
- `POST /api/voice/session/start/<scenario_id>` — valida (sin otra sesión de voz activa; escenario de su operativa), elige caso aleatorio, crea `VoiceSession`, construye las `instructions` de rol-play, llama a `realtime_client.mint_client_secret(...)` y devuelve `{session_id, client_secret, model, expires_at}`. **La API key de servidor jamás llega al navegador.**
- `GET /voice-training/session/<id>` — pantalla de llamada.
- `POST /api/voice/turn` — persiste cada transcripción confirmada `{session_id, role, transcript, started_at_ms, ended_at_ms}` (el frontend la envía al recibir el evento del data channel).
- `POST /api/voice/end/<id>` — cierra, calcula métricas desde `voice_turns` + métricas de tiempo reportadas por el cliente, corre la evaluación LLM, marca `completed`.
- `POST /api/voice/heartbeat/<id>` — cada 30s; sesiones sin heartbeat > 3 min se marcan `abandoned` (el usuario cerró la pestaña y la sesión Realtime muere sola al cortarse el WebRTC).
- `GET /voice-training/result/<id>` — resultado con métricas y feedback.

**Admin:**
- `GET /admin/voice` (`@can_view_training` — se importa el decorador existente) — dashboard: sesiones recientes, NPS promedio, latencia de respuesta promedio, ranking por usuario, costo acumulado estimado, filtrado por operativa con el mismo patrón `operativa_user_ids`.
- `GET /admin/voice/session/<id>/detail` — transcript completo + métricas + feedback (análogo a `admin_session_detail`).

**Configuración de la sesión Realtime** (en `mint_client_secret`):
```json
{
  "expires_after": {"anchor": "created_at", "seconds": 120},
  "session": {
    "type": "realtime",
    "model": "gpt-realtime-2.1-mini",
    "instructions": "<system prompt de rol-play del caso>",
    "audio": {
      "input":  {"transcription": {"model": "<modelo de transcripción vigente>", "language": "es"},
                 "turn_detection": {"type": "semantic_vad"}},
      "output": {"voice": "<voz elegida>", "speed": 1.0}
    },
    "reasoning": {"effort": "low"},
    "max_output_tokens": <límite por respuesta>
  }
}
```
Más límite duro de duración del lado del cliente (**timer de corte a los 10 min** con aviso a los 8) para acotar costo.

---

## 6. Métricas: mapeo chat → voz (misma filosofía de medición)

| Métrica chat (hoy) | Equivalente voz | Cómo se mide |
|---|---|---|
| NPS 0-10 (LLM) | **Igual** | misma rúbrica sobre el transcript, vía `call_openai` |
| `response_correct` (LLM) | **Igual** | vs `expected_response` del caso |
| `spelling_errors` | `filler_words` (muletillas) + claridad | LLM sobre transcript |
| `words_per_minute` (tipeo) | `speech_rate_wpm` (ritmo de habla) | palabras del transcript / tiempo hablado (timestamps) |
| `avg_response_time` (ART) | `avg_response_latency` | eventos VAD: fin de habla del cliente → inicio de habla del asesor |
| empathy_breakdown (4 pilares) | **Igual** + tono percibido | LLM sobre transcript |
| — (nuevo, solo voz) | `talk_ratio`, `interruptions`, `long_silences` | data channel + timestamps |
| Auto-fail (<2 msgs o <8 palabras) | Auto-fail (<2 turnos o <15 palabras habladas) | sin gastar tokens de evaluación |
| Modo flexible/standard/exigente | **Igual** (hint del modo en la rúbrica) | `get_effective_mode(session.scoring_mode)` |

La evaluación produce el mismo shape de JSON que el chat (`{nps_score, response_correct, ..., empathy_breakdown{...}}`) más un bloque `voice_breakdown` (claridad, tono, ritmo, escucha activa). Esto deja la puerta abierta a la Fase 6 sin re-trabajo.

---

## 7. Fases de implementación

| Fase | Contenido | Entregable verificable | Estimación |
|---|---|---|---|
| **F0 — Spike técnico** | Página mínima oculta (solo superadmin): acuñar token efímero, conectar WebRTC, hablar con una persona hardcodeada, ver transcripciones en consola. Valida modelo, eventos, VAD y voces en español **antes** de construir nada encima. | Llamada de voz funcionando end-to-end en Render | 1–2 días |
| **F1 — Datos + backend** | `migrate_v10.py`, modelos `VoiceSession`/`VoiceTurn`, `realtime_client.py`, rutas start/turn/end/heartbeat con validaciones y scoping | Sesión completa persistida vía curl/tests | 2–3 días |
| **F2 — Frontend de llamada** | `voice/index.html` + `session.html` + `voice_training.js`: permiso de mic, indicadores de estado (conectando/escuchando/hablando), transcript en vivo, timer con corte a 10 min, manejo de errores (mic denegado, conexión caída) | Un operador entrena por voz de punta a punta | 3–4 días |
| **F3 — Evaluación + resultado** | `voice_scoring.py` (rúbrica + métricas), auto-fail, `result.html` con NPS, pilares de empatía, voice_breakdown y feedback | Resultados consistentes con la filosofía del chat | 2–3 días |
| **F4 — Dashboard admin** | `/admin/voice` con stats por operativa, detalle de sesión con transcript, costo estimado acumulado, menú en `base.html` | Coordinadores ven y auditan sesiones de voz | 2 días |
| **F5 — Piloto y hardening** | Piloto con una operativa, ajuste de rúbrica/latencias/voz, límites de uso (sesiones/día por usuario si hace falta), pruebas en móvil (el mic en Chrome/Safari móvil tiene sus mañas) | Go/no-go para rollout general | 1 semana calendario |
| **F6 — (Opcional, decisión aparte) Integración VexProfile** | Alimentar el índice predictivo con sesiones de voz (peso por canal, o índice de voz paralelo). **Requiere decisión de negocio** porque cambia el significado del índice existente. | Propuesta + implementación separada | a definir |

Total estimado hasta piloto (F0–F5): **~3 semanas de desarrollo efectivo**.

## 8. Riesgos y previsiones

1. **Doc oficial bloqueada desde el entorno de desarrollo remoto** — verificar payloads/eventos exactos contra la doc al implementar F0 (o desarrollar F0 localmente).
2. **Micrófono en el navegador**: requiere HTTPS (Render ✓) y permiso del usuario; UI debe guiar cuando se deniega. Probar Chrome/Edge/Safari, desktop y móvil, y auriculares vs parlante (eco → el echo cancellation de WebRTC lo maneja, pero probarlo).
3. **Costo**: mini a $10/$20 por M tokens de audio ≈ US$0.08–0.15 por sesión de 5 min. Mitigaciones ya en el plan: corte a 10 min, `max_output_tokens`, reasoning `low`, una sesión activa por usuario, `estimated_cost_usd` visible en el dashboard, y auto-fail sin evaluación LLM para sesiones vacías.
4. **Token efímero**: TTL corto (120s para iniciar; la sesión ya iniciada continúa). Nunca loguear el `ek_...`.
5. **Calidad del español rioplatense/paraguayo de la voz**: elegir voz en F0 escuchándolas; el `instructions` debe fijar acento/registro neutro-cercano.
6. **Sesiones zombie**: heartbeat + barrido de `abandoned`; el costo se corta solo porque el WebRTC muere al cerrar la pestaña.
7. **Compatibilidad futura**: si OpenAI deprecia snapshots, el nombre del modelo vive en una sola constante en `realtime_client.py` (no hardcodeado en N lugares).

---

*Documento generado como plan de trabajo — VEX I+D. Actualizar al cierre de cada fase.*
