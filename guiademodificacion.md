# Guía de Modificación — Replicar cambios en otro proyecto

Este documento describe **paso a paso** todos los cambios aplicados sobre Vex
People Predictive en la rama `claude/review-scoring-adjustments-JI7KW` para
que puedas replicarlos en otro proyecto que comparta arquitectura similar
(Flask + SQLAlchemy + Jinja2 + PostgreSQL + chat IA).

> Si el otro proyecto NO comparte arquitectura, usá esta guía como referencia
> conceptual y adaptá las rutas y nombres de archivo a tu stack.

**Resumen ejecutivo:** 4 frentes de cambio
1. **UX de chat** — inputs que envuelven texto en lugar de scrollear
2. **UX de admin** — textareas de creación de escenarios con auto-grow
3. **Scoring** — suavizado, ART, rúbrica jerárquica de empatía
4. **Modelo IA** — migración a `gpt-5.4-mini`

Total de archivos modificados: **13** · 1 migración nueva · 2 docs nuevos.

---

## 1. Chat inputs — texto que envuelve hacia abajo

**Problema:** Los inputs de chat eran `<input type="text">`, así que al
escribir mucho el texto se desplaza horizontalmente y el usuario pierde
contexto de lo que ya escribió.

**Solución:** Reemplazar por `<textarea>` con auto-resize en JS y CSS que
permita word-wrap vertical.

### 1.1 HTML — Cambiar `<input>` por `<textarea>`

**Archivo:** `templates/training/session.html`

```diff
- <input type="text" id="trainInput" placeholder="..." autocomplete="off" autofocus>
+ <textarea id="trainInput" placeholder="..." autocomplete="off" autofocus rows="1"></textarea>
```

**Archivo:** `templates/base.html` (widget de asistente)

```diff
- <input type="text" id="chatInput" placeholder="..." autocomplete="off">
+ <textarea id="chatInput" placeholder="..." autocomplete="off" rows="1"></textarea>
```

### 1.2 CSS — word-wrap + auto-resize

**Archivo:** `static/css/training.css` — bloque `.training-input-area`:

```css
.training-input-area {
    /* ... */
    align-items: flex-end;   /* antes era center */
}
.training-input-area textarea {       /* antes era 'input' */
    flex: 1; padding: 12px 18px; border: 2px solid #e0e0e0;
    border-radius: 22px; font-size: 15px; outline: none;
    font-family: inherit; transition: border-color 0.3s;
    resize: none; line-height: 1.4;
    min-height: 44px; max-height: 140px;
    overflow-y: auto; word-wrap: break-word; overflow-wrap: break-word;
}
.training-input-area textarea:focus { border-color: #E6332A; }
```

**Archivo:** `static/css/chat.css` — análogo para el widget global:

```css
.chat-input-area {
    /* ... */
    align-items: flex-end;
}
.chat-input-area textarea {
    flex: 1; padding: 11px 16px; border: 2px solid #e0e0e0;
    border-radius: 22px; font-size: 14px; outline: none;
    font-family: inherit; transition: border-color 0.3s;
    background: #f8f9fa;
    resize: none; line-height: 1.4;
    min-height: 42px; max-height: 120px;
    overflow-y: auto; word-wrap: break-word; overflow-wrap: break-word;
}
.chat-input-area textarea:focus { border-color: #E6332A; background: #fff; }

/* Media query mobile: misma regla con textarea en lugar de input */
@media (max-width: 480px) {
    .chat-input-area textarea { font-size: 16px; }
}
```

### 1.3 JS — auto-resize + Enter envía / Shift+Enter nueva línea

**Archivo:** `static/js/training.js`

```js
// Auto-resize textarea (texto se envuelve verticalmente)
function autoResize() {
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 140) + 'px';
}
chatInput.addEventListener('input', autoResize);

// Enter envía; Shift+Enter inserta salto manual
chatInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMsg(); }
});

// Después de enviar, resetear el alto:
// chatInput.value = ''; autoResize();
```

**Archivo:** `static/js/chat.js` — análogo:

```js
function autoResizeInput() {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
}
inputEl.addEventListener('input', autoResizeInput);
// Llamar autoResizeInput() después de inputEl.value = '' al enviar.
```

---

## 2. Textareas de admin con auto-grow

**Problema:** En el formulario de "Crear Escenario" los textareas de
"Persona del Cliente" y "Resolución Esperada" no crecen con el contenido,
forzando al usuario a hacer scroll dentro del textarea.

**Solución:** CSS con `min-height` razonable + JS que ajusta el alto al
`scrollHeight` en cada `input` event y después de cargar contenido
programático (modal de edición, AI enhance).

**Archivo:** `templates/admin/training_scenarios.html`

### 2.1 CSS

```css
.case-card textarea {
    width: 100%; padding: 10px; border: 2px solid #ddd; border-radius: 8px;
    font-family: inherit; font-size: 14px; resize: vertical;
    line-height: 1.5; min-height: 80px; max-height: 600px;
    overflow-y: auto; word-wrap: break-word; overflow-wrap: break-word;
    box-sizing: border-box;
}
.case-card textarea.case-response { min-height: 64px; }
```

### 2.2 JS — función reutilizable

```js
function autoGrow(el) {
    if (!el) return;
    el.style.height = 'auto';
    var max = parseInt(getComputedStyle(el).maxHeight, 10) || 600;
    el.style.height = Math.min(el.scrollHeight + 2, max) + 'px';
}
function bindAutoGrow(scope) {
    var nodes = (scope || document).querySelectorAll('.case-persona, .case-response');
    nodes.forEach(function(t) {
        if (t.dataset.autogrow) return;          // evita doble bind
        t.dataset.autogrow = '1';
        t.addEventListener('input', function() { autoGrow(t); });
        autoGrow(t);                              // ajuste inicial
    });
}
document.addEventListener('DOMContentLoaded', function() { bindAutoGrow(document); });
```

### 2.3 Llamar `bindAutoGrow(...)` después de:

- `addCase()` agrega un caso → `bindAutoGrow(document.getElementById('casesContainer'))`
- `addEditCase()` agrega un caso al modal de edición → `bindAutoGrow(document.getElementById('editCasesList'))`
- `editScenario()` carga datos en el modal → `bindAutoGrow(document.getElementById('editCasesList'))`
- `enhanceText()` reemplaza el contenido tras "Mejorar con IA" → `autoGrow(textarea)` directamente

---

## 3. Modelo IA → `gpt-5.4-mini`

**Archivo:** `chat.py` — función `call_openai`:

```diff
  payload = json.dumps({
-     'model': 'gpt-4o-mini',
+     'model': 'gpt-5.4-mini',
      'messages': messages,
-     'max_tokens': 1200,
+     'max_completion_tokens': 1200,
      'temperature': 0.2
  }).encode('utf-8')
```

> ⚠ **Crítico:** GPT-5.x rechaza el parámetro `max_tokens` con
> **HTTP 400**. El nombre correcto en chat completions para esta
> familia es `max_completion_tokens`. Si solo cambiás el modelo sin
> cambiar el parámetro, las llamadas fallan en silencio (excepto en
> los logs).

Esa función se reusa para: cliente simulado, evaluación final de
sesiones, y asistente VEX AI. Verificá en tu proyecto si tenés más
de un punto donde se hardcodea el modelo y en cada uno corregí
también el parámetro de tokens.

### 3.1 Logging robusto del error de OpenAI

Captura `HTTPError` aparte de `URLError` para poder leer el body de
la respuesta cuando algo falla. Sin esto, un 400 se loguea como
`HTTP Error 400: Bad Request` sin pista de qué rechazó el API.

```python
from urllib.error import URLError, HTTPError

try:
    with urlopen(req, timeout=30) as resp:
        # ...
except HTTPError as e:
    body = ''
    try:
        body = e.read().decode('utf-8', errors='replace')[:500]
    except Exception:
        pass
    print(f"[CHAT] OpenAI HTTP {e.code}: {body}", flush=True)
    return "Error al procesar.", 0
except URLError as e:
    print(f"[CHAT] OpenAI error: {e}", flush=True)
    return "Error al procesar.", 0
```

### 3.2 Errores comunes y diagnóstico

| Mensaje en logs                              | Causa                                              | Solución                              |
|----------------------------------------------|----------------------------------------------------|---------------------------------------|
| `Unsupported parameter: 'max_tokens'`        | Modelo GPT-5.x no acepta el nombre viejo           | Renombrar a `max_completion_tokens`   |
| `model_not_found` o `does not have access`   | El slug `gpt-5.4-mini` no está habilitado en la cuenta | Solicitar acceso o usar `gpt-4o-mini` |
| `Unsupported parameter: 'temperature'`       | Algunos reasoning models de GPT-5 no aceptan temp custom | Quitar `temperature` del payload      |
| `max_completion_tokens too low`              | Reasoning model consume tokens internos            | Subir el límite (ej: 2000)            |

---

## 4. Scoring — la parte gruesa

### 4.1 Modelo de datos: nueva columna `avg_response_time`

**Archivo:** `models.py` — clase `TrainingSession`:

```diff
  spelling_errors = db.Column(db.Integer, default=0)
  words_per_minute = db.Column(db.Float, default=0)
+ avg_response_time = db.Column(db.Float, default=0)  # ART en segundos
  nps_score = db.Column(db.Integer)
```

### 4.2 Migración SQL idempotente

**Archivo nuevo:** `migrate_v5.py`

```python
"""Migration v5: Add avg_response_time (ART) column to training_sessions."""
from app import app
from models import db


def migrate_v5():
    with app.app_context():
        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'training_sessions'
                      AND column_name = 'avg_response_time'
                ) THEN
                    ALTER TABLE training_sessions
                        ADD COLUMN avg_response_time DOUBLE PRECISION DEFAULT 0;
                END IF;
            END $$;
        """))
        db.session.commit()


if __name__ == '__main__':
    migrate_v5()
```

**Archivo:** `Dockerfile` — agregar al CMD:

```diff
    python migrate_v2.py && \
    python migrate_v3.py && \
+   python migrate_v4.py && \
+   python migrate_v5.py && \
    gunicorn ...
```

### 4.3 Cálculo de ART al cerrar sesión

**Archivo:** `training.py` — endpoint `end_session`. Reemplazar el bloque
de cálculo de WPM por uno que también calcule ART:

```python
# WPM + ART (Average Response Time)
user_messages = [m for m in session.messages if m.role == 'user']
response_gaps = []
if user_messages and session.total_words_user:
    typing_seconds = 0
    prev_client_time = None
    for msg in session.messages:
        if msg.role == 'client':
            prev_client_time = msg.created_at
        elif msg.role == 'user':
            if prev_client_time and msg.created_at:
                gap = (msg.created_at.replace(tzinfo=None)
                       - prev_client_time.replace(tzinfo=None)).total_seconds()
                capped = max(0, min(gap, 600))   # cap 10min para idle extremo
                response_gaps.append(capped)
                typing_seconds += min(capped, 120)   # WPM cap 120s
            else:
                typing_seconds += 10
    typing_minutes = max(typing_seconds / 60, 0.1)
    session.words_per_minute = round(session.total_words_user / typing_minutes, 1)
elif session.duration_seconds > 0 and session.total_words_user:
    session.words_per_minute = round(session.total_words_user / (session.duration_seconds / 60), 1)

# ART: promedio de gaps cliente→asesor. Si no hay gaps, queda en 0 (no penaliza).
session.avg_response_time = round(sum(response_gaps) / len(response_gaps), 1) if response_gaps else 0
```

### 4.4 Prompt de evaluación IA — empatía jerárquica + ortografía leniente

**Archivo:** `training.py` — función `end_session`, variable `eval_prompt`.
Cambios clave:

**(a)** Agregar la rúbrica de empatía:

```
EMPATÍA — RÚBRICA JERÁRQUICA (evaluá EN ORDEN, cada paso vale):
1. NOMBRE: ¿El asesor mencionó el nombre del cliente al menos una vez?
2. CONTEXTO: ¿Demostró comprender el problema?
3. CALIDEZ: ¿Usó un tono amable, humano, o emojis adecuados?
4. RESOLUCIÓN: ¿Se enfocó genuinamente en ayudar al cliente?
```

**(b)** Reemplazar la regla de ortografía por una versión leniente:

```
ORTOGRAFÍA — REGLAS LENIENTES:
- NO contar: tildes omitidas, mayúsculas iniciales en chat informal,
  abreviaciones comunes (xq, q, tmb, pq), emojis, signos de apertura.
- SÍ contar: solo errores que CAMBIAN EL SIGNIFICADO o IMPIDEN ENTENDER.
- En la mayoría de chats bien escritos el resultado debe ser 0.
```

**(c)** Pedir el breakdown en el JSON de salida:

```json
{
    "nps_score": ...,
    "response_correct": ...,
    "spelling_errors": ...,
    "empathy_breakdown": {
        "nombre": <bool>,
        "contexto": <bool>,
        "calidez": <bool>,
        "resolucion": <bool>
    },
    "feedback": "...",
    "strengths": "...",
    "improvements": "..."
}
```

**(d)** Persistir el breakdown en `ai_feedback`:

```python
session.ai_feedback = json.dumps({
    'feedback': evaluation.get('feedback', ''),
    'strengths': evaluation.get('strengths', ''),
    'improvements': evaluation.get('improvements', ''),
    'empathy_breakdown': evaluation.get('empathy_breakdown', {})
}, ensure_ascii=False)
```

### 4.5 Cálculo del perfil — fórmulas suavizadas

**Archivo:** `training.py` — función `calculate_vex_profile`.

#### 4.5.1 Agregar promedio de ART

```python
art_values = [s.avg_response_time for s in sessions
              if s.avg_response_time and s.avg_response_time > 0]
avg_art = sum(art_values) / len(art_values) if art_values else 0
```

#### 4.5.2 Agregar tasas de pilares de empatía

```python
import json
empathy_pillars = {'nombre': 0, 'contexto': 0, 'calidez': 0, 'resolucion': 0}
pillar_count = 0
for s in sessions:
    if not s.ai_feedback:
        continue
    try:
        fb = json.loads(s.ai_feedback)
        br = fb.get('empathy_breakdown') or {}
        if br:
            pillar_count += 1
            for k in empathy_pillars:
                if br.get(k):
                    empathy_pillars[k] += 1
    except (json.JSONDecodeError, TypeError):
        pass
empathy_pillar_rate = {
    k: (v / pillar_count) if pillar_count else 0 for k, v in empathy_pillars.items()
}
```

#### 4.5.3 Penalización ortográfica suavizada

```python
# antes: spelling_rate × 10 (saturaba al 10%)
spelling_penalty = min(spelling_rate * 25, 1)   # ahora satura al 4%
```

#### 4.5.4 Fórmulas con piso mínimo

```python
# Comunicación: piso 30 + ortografía 30% + NPS 40%
comm_raw = 30 + (1 - spelling_penalty) * 30 + (avg_nps / 10) * 40

# Empatía: 70% pilares + 30% NPS (fallback al 100% NPS si no hay breakdown)
if pillar_count > 0:
    empathy_pillars_score = (
        empathy_pillar_rate['nombre'] * 15 +
        empathy_pillar_rate['contexto'] * 25 +
        empathy_pillar_rate['calidez'] * 25 +
        empathy_pillar_rate['resolucion'] * 35
    )
    empathy_raw = empathy_pillars_score * 0.7 + (avg_nps * 10) * 0.3
else:
    empathy_raw = avg_nps * 10

# Resolución: piso 25
resolution_raw = 25 + correct_rate * 50 + (avg_nps / 10) * 25

# Velocidad: ART (70%) + WPM (30%)
if avg_art <= 0:
    speed_art = 65       # neutro para sesiones legacy
elif avg_art <= 120:
    speed_art = 100
elif avg_art <= 180:
    speed_art = 100 - ((avg_art - 120) / 60) * 20
elif avg_art <= 300:
    speed_art = 80 - ((avg_art - 180) / 120) * 30
elif avg_art <= 600:
    speed_art = 50 - ((avg_art - 300) / 300) * 30
else:
    speed_art = 20

speed_wpm = min(100, (avg_wpm / 25) * 100) if avg_wpm > 0 else 50
speed_raw = speed_art * 0.7 + speed_wpm * 0.3

# Adaptabilidad: piso 30, variety más permisiva (0.4 del catálogo)
variety = min(1, unique_scenarios / max(total_scenarios * 0.4, 1))
adapt_raw = 30 + improvement_trend * 35 + variety * 35

# Compliance: piso 25
compliance_raw = 25 + correct_rate * 45 + (1 - spelling_penalty) * 30
```

#### 4.5.5 Conversión Sten amigable

```python
def to_sten(raw):
    sten = int(raw / 10) + (1 if (raw % 10) >= 4 else 0)
    return max(1, min(10, sten))
```

Antes era `round(raw / 10)`. El nuevo umbral 4 hace que un raw de 64
suba a Sten 7 en lugar de bajar a 6.

#### 4.5.6 Predictive Index — pesos rebalanceados

```python
pi = (resolution * 0.22 + empathy * 0.25 + comm * 0.18 +
      speed * 0.15 + adapt * 0.10 + compliance * 0.10)
pi_pct = round(pi * 10, 1)
```

| Dimensión       | Antes | Ahora |
|-----------------|-------|-------|
| Empatía         | 20%   | **25%** |
| Resolución      | 25%   | 22%   |
| Comunicación    | 20%   | 18%   |
| Velocidad       | 15%   | 15%   |
| Adaptabilidad   | 10%   | 10%   |
| Compliance      | 10%   | 10%   |

#### 4.5.7 Categorías y recomendaciones suavizadas

```python
# Categorías
if overall >= 8.5 and all(s >= 7 for s in scores):
    category = 'elite'
elif overall >= 6.5 and all(s >= 4 for s in scores):
    category = 'alto'
elif overall >= 4.5:
    category = 'desarrollo'
else:
    category = 'refuerzo'

# Recomendaciones
if pi_pct >= 65:        # antes 70
    rec = 'recomendado'
elif pi_pct >= 45:      # antes 50
    rec = 'observaciones'
else:
    rec = 'no_recomendado'
```

---

## 4-bis. Modos de Scoring (Flexible / Standard / Exigente)

Esta capa permite que cada escenario de entrenamiento se evalúe con
distinta severidad. La selección del modo la hace el creador del
escenario; cada batch hace un snapshot del modo al iniciar.

### 4-bis.1 Modelo de datos

Tres cambios en `models.py`:

```python
# En TrainingScenario:
scoring_mode = db.Column(db.String(20), nullable=True)  # null = legacy

# En TrainingBatch:
scoring_mode = db.Column(db.String(20), nullable=True)  # snapshot al crear

# Nueva tabla:
class ScoringModeOverride(db.Model):
    __tablename__ = 'scoring_mode_overrides'
    id = db.Column(db.Integer, primary_key=True)
    mode = db.Column(db.String(20), unique=True, nullable=False)
    config_json = db.Column(db.Text)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    updated_at = db.Column(db.DateTime, ...)
```

Migración (`migrate_v6.py`): dos `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
y un `CREATE TABLE IF NOT EXISTS scoring_mode_overrides`.

### 4-bis.2 Módulo `scoring_modes.py`

Define los 3 modos como diccionarios `DEFAULT_MODES` (flexible / standard
/ exigente), cada uno con: `pi_weights`, `floors`, `spelling_multiplier`,
`empathy_pillars_weight`, `art_curve`, `thresholds`, `recommendation`,
`ai_hint`, metadata visual (icon, color, label, when_to_use).

Helpers:

```python
def get_mode_config(mode_name):
    """Devuelve dict del modo, prefiriendo override del SuperAdmin si existe.
    Legacy/null -> standard."""

def get_effective_mode(mode_name):
    """Igual que get_mode_config pero devuelve también (name_normalized,
    is_legacy, config)."""

def list_modes():
    """Lista los 3 modos con metadata para selectors UI."""
```

También exporta `PEDAGOGICAL_GUIDE` (qué mide cada parámetro) y
`ADMIN_SUMMARY` (3-5 frases por modo, sin números internos, para mostrar
a admins read-only).

### 4-bis.3 Aplicación en `calculate_vex_profile`

El cálculo del perfil VEX usa el modo del **batch más reciente** del
usuario:

```python
sorted_for_mode = sorted(sessions, key=lambda s: s.created_at, reverse=True)
latest_batch = TrainingBatch.query.get(sorted_for_mode[0].batch_id)
mode_name = latest_batch.scoring_mode if latest_batch else None
_, _, mode_cfg = get_effective_mode(mode_name)
floors = mode_cfg['floors']
art_curve = mode_cfg['art_curve']
pi_weights = mode_cfg['pi_weights']
thresholds = mode_cfg['thresholds']
rec_thresholds = mode_cfg['recommendation']
spell_mult = mode_cfg['spelling_multiplier']
empathy_pillars_w = mode_cfg['empathy_pillars_weight']
```

Después se reemplazan los literales hardcodeados:

| Antes (hardcoded) | Ahora (del modo) |
|---|---|
| `min(spelling_rate * 25, 1)` | `min(spelling_rate * spell_mult, 1)` |
| `30 + ... + (avg_nps/10) * 40` | `floors['communication'] + ... + (avg_nps/10) * 40` |
| `empathy_pillars_score * 0.7 + ...` | `empathy_pillars_score * empathy_pillars_w + ...` |
| `25 + correct_rate * 50 + ...` | `floors['resolution'] + correct_rate * 50 + ...` |
| `if avg_art <= 120: speed_art = 100` | `if avg_art <= art_curve['excellent_max']: speed_art = 100` |
| `pi = resolution * 0.22 + empathy * 0.25 + ...` | `pi = resolution * pi_weights['resolution'] + ...` |
| `if overall >= 8.5 and all(s >= 7 ...)` | `if overall >= thresholds['elite_overall'] and all(s >= thresholds['elite_min_dim'] ...)` |
| `if pi_pct >= 65: rec = 'recomendado'` | `if pi_pct >= rec_thresholds['recomendado']: rec = 'recomendado'` |

### 4-bis.4 Aplicación en el prompt de la IA evaluadora

Antes del prompt principal, agregar:

```python
batch_obj = TrainingBatch.query.get(session.batch_id) if session.batch_id else None
batch_mode = batch_obj.scoring_mode if batch_obj else None
eff_mode_name, _, eff_mode_cfg = get_effective_mode(batch_mode)
mode_ai_hint = eff_mode_cfg.get('ai_hint', '')
mode_label = eff_mode_cfg.get('label', 'Standard')

eval_prompt = f"""...
MODO DE EVALUACIÓN: {mode_label}
{mode_ai_hint}
..."""
```

### 4-bis.5 UI: selector en escenario

En `templates/admin/training_scenarios.html`, agregar bloque "Modo de
Evaluación" antes de la sección de casos. Tres `<label class="mode-card">`
con `<input type=radio name=scoring_mode>`. Standard preseleccionado.
JS hace que clickear la card marque el radio y aplique `.selected`. CSS
da el look de cards con borde de color y un `<details>` mostrando 5
viñetas de "Qué mide este modo".

El `scenario_json_filter` en `app.py` debe incluir el campo
`scoring_mode` para que el modal de edición pueda preseleccionar.

### 4-bis.6 Vista SuperAdmin (editor) y vista admin (read-only)

Una sola plantilla `templates/admin/vex_modos.html` que decide el modo
de los inputs según `is_superadmin`. Cuando es false, todos los `input`
salen con `readonly`.

Dos rutas en `training.py`:
- `GET /admin/vex/modos` (`@coordinador_or_above`) — render de la vista
- `POST /admin/vex/modos/save` (`@superadmin_required`) — valida que los
  pesos PI sumen ~1, persiste en `scoring_mode_overrides`
- `POST /admin/vex/modos/reset/<mode>` (`@superadmin_required`) — borra
  el override

### 4-bis.7 Badges visibles

Donde se muestre un escenario o un batch, agregar el badge del modo. CSS:

```css
.mode-badge { display:inline-block; padding:2px 8px; border-radius:10px;
              font-size:11px; font-weight:700; }
.mode-badge.flexible { background:#e8f5e9; color:#2e7d32; }
.mode-badge.standard { background:#e3f2fd; color:#0277bd; }
.mode-badge.exigente { background:#ffebee; color:#c62828; }
.mode-badge.legacy   { background:#f0f0f0; color:#666; }
```

Tres lugares como mínimo:
- Lista de escenarios admin (columna nueva)
- Página de inicio de entrenamiento (junto a categoría)
- Batch result (header)
- Header de sesión activa (sobre fondo oscuro: usar `rgba` semitransparente)

### 4-bis.8 Nav

Agregar al dropdown "Vex Predictive":

```html
<a href="{{ url_for('training.vex_modos') }}">Modos de Scoring</a>
```

Visible para coordinadores (read-only) y SuperAdmin (editor).

---

## 4-ter. Hard caps universales + transparencia del baremo

Después de los modos, agregamos una capa de seguridad que protege la
integridad de la recomendación independientemente del modo elegido.

### 4-ter.1 Hard caps en `calculate_vex_profile`

Después del cálculo de `category` y `rec` y antes de persistir, evaluamos:

```python
cap_reasons = []

if abandonment_rate > 0.40:
    cap_reasons.append({
        'rule': 'abandonment',
        'detail': f'{int(abandonment_rate*100)}% sesiones abandonadas',
        'effect': 'Categoria max "Desarrollo", recomendacion max "Observaciones"'
    })

if correct_rate < 0.50:
    cap_reasons.append({
        'rule': 'low_correct_rate',
        'detail': f'Solo {int(correct_rate*100)}% respuestas correctas',
        'effect': 'Recomendacion max "Observaciones"'
    })

if avg_nps < 4.0:
    cap_reasons.append({
        'rule': 'low_nps',
        'detail': f'NPS promedio {avg_nps:.1f}',
        'effect': 'Recomendacion max "Observaciones"'
    })

# Aplicar caps acumulativos
if any(c['rule'] == 'abandonment' for c in cap_reasons):
    if category in ('elite', 'alto'):
        category = 'desarrollo'
if cap_reasons:
    if rec == 'recomendado':
        rec = 'observaciones'
```

`cap_reasons` se adjunta al `profile` como atributo volátil
(`profile._cap_reasons`) para que la route lo pase al template sin
necesidad de persistirlo en DB.

### 4-ter.2 Variedad efectiva — fórmula corregida

```python
# Antes (BUG):
variety = min(1, unique_scenarios / max(total_scenarios * 0.4, 1))

# Despues:
variety = min(1, unique_scenarios / max(total_scenarios * 0.5, 3))
```

El divisor `max(total*0.4, 1)` permitía que con 1 solo escenario y
pocos cargados (1-2) el ratio fuera 1.0. Ahora se exige un mínimo
absoluto de 3 escenarios resueltos para alcanzar el 100%.

### 4-ter.3 Pisos del modo Flexible — recalibrados

En `scoring_modes.py`, el modo `flexible` originalmente tenía pisos de
35 en cada dimensión. Eso inflaba demasiado: cualquier sesión que no
fuera auto-fail llegaba a Sten 6+. Bajamos a 25:

```python
'floors': {
    'communication': 25, 'resolution': 25, 'adaptability': 25,
    'compliance': 25, 'empathy': 0, 'speed_no_data': 60
}
```

Sigue siendo más permisivo que Standard (25-30) pero ya no anula
la evaluación.

### 4-ter.4 Transparencia en el perfil VEX

La route `vex_profile()` ahora calcula y pasa al template:

```python
# Distribucion de sesiones por modo
mode_counts = {'flexible': 0, 'standard': 0, 'exigente': 0, 'legacy': 0}
for s in all_sessions:
    batch_mode = batch.scoring_mode if batch else None
    key = batch_mode if batch_mode in MODE_NAMES else 'legacy'
    mode_counts[key] += 1

# Recuperar info volatil del calculate_vex_profile
cap_reasons = getattr(profile, '_cap_reasons', [])
active_mode = getattr(profile, '_active_mode', 'standard')
mode_cfg = getattr(profile, '_mode_cfg', None)
```

El template `vex_profile.html` muestra:

1. **Bloque "Baremo de medición aplicado"** con:
   - Modo activo destacado (con icono y color)
   - Indicador "Legacy (sin modo asignado, usa Standard)" si aplica
2. **Distribución de sesiones por modo**: 4 contadores con badges
   color-coded (🟢 Flexible, 🔵 Standard, 🔴 Exigente, ⚪ Legacy) con
   conteo y porcentaje
3. **Detalle plegable** con los umbrales exactos del modo activo
   (Elite, Alto, Desarrollo, Recomendado, Observaciones)
4. **Bloque rojo de hard caps aplicados** (si hay): muestra cada regla
   disparada con su detalle y efecto

### 4-ter.5 Por qué importa documentar todo esto

El SuperAdmin / Coordinador no tiene que **adivinar** por qué un perfil
salió "Observaciones" en lugar de "Recomendado" cuando el PI calculado
es 65%. La UI le dice exactamente: *"Hard cap: solo 33% de respuestas
correctas → recomendación máx Observaciones"*.

Esto convierte el sistema de evaluación en algo **auditable** y
**defendible**: cualquier decisión derivada del scoring (incorporar a
la operativa o no) puede explicarse con los criterios concretos
disparados.

---

## 5. Documentación

Si tu otro proyecto tiene una página de metodología visible al usuario:

1. Reemplazá las fórmulas viejas por las nuevas (sección 4.5).
2. Agregá una sección de **Empatía con rúbrica jerárquica** con la tabla
   de pesos 15/25/25/35.
3. Reemplazá la sección de **Velocidad** por la basada en ART
   (la versión completa está en `templates/admin/vex_methodology.html`
   de este repo, sección "4. Velocidad — ART").
4. Actualizá las tablas de **Categorías** y **Recomendaciones** con los
   nuevos umbrales.
5. Cambiá la mención del modelo de IA a "GPT-5.4 mini".

Para usuarios técnicos del otro proyecto, copiá `scoring.md` (este repo)
y adaptalo: tiene el detalle completo de fórmulas y la tabla
"Resumen de cambios vs versión anterior".

---

## 6. Orden de aplicación recomendado

Si hacés todo de una pasada, aplicá en este orden para minimizar riesgos:

1. **Migración primero** — `migrate_v5.py` y agregarlo al Dockerfile.
   Que la columna exista antes de que el código intente leerla.
2. **Modelo SQLAlchemy** — agregar `avg_response_time` a `TrainingSession`.
3. **Cálculo en `end_session`** — empezar a poblar la columna.
4. **Prompt de evaluación IA** — agregar rúbrica + ortografía leniente +
   `empathy_breakdown` en el JSON.
5. **Persistir el breakdown** en `ai_feedback`.
6. **`calculate_vex_profile`** — fórmulas nuevas + categorías + recomendaciones.
7. **Cambio de modelo IA** a `gpt-5.4-mini` **+ renombrar `max_tokens` a `max_completion_tokens`** (ambos cambios juntos en el mismo commit, o el deploy queda roto).
8. **UX de chat** (textareas + auto-resize).
9. **UX de admin** (textareas con auto-grow).
10. **Documentación HTML/MD**.

Después de los puntos 1-7, los perfiles existentes se recalcularán
automáticamente la próxima vez que un usuario cierre una sesión. Las
sesiones legacy sin ART ni empathy_breakdown caen al fallback (puntaje
neutro 65 en velocidad-ART, fórmula NPS pura en empatía).

---

## 7. Validación post-deploy

Después de aplicar:

- [ ] La migración se ejecuta sin error en el primer arranque del contenedor.
- [ ] El `psql \d training_sessions` muestra `avg_response_time` con default 0.
- [ ] Crear una sesión nueva y cerrarla; verificar que `avg_response_time` se popule.
- [ ] El JSON de `ai_feedback` incluye `empathy_breakdown` con 4 booleans.
- [ ] El perfil VEX recalcula sin error y la categoría coincide con los nuevos umbrales.
- [ ] El chat de entrenamiento envuelve texto largo verticalmente.
- [ ] El widget de asistente envuelve texto largo verticalmente.
- [ ] En "Crear Escenario", al pegar un texto largo el textarea crece.
- [ ] La página de Metodología muestra el nuevo modelo y las nuevas fórmulas.
- [ ] El asistente VEX AI responde usando GPT-5.4 mini (verificable en logs de OpenAI).
- [ ] No aparecen `[CHAT] OpenAI HTTP 400` en los logs del backend tras enviar un mensaje (si aparece, leer el body — probablemente sea `max_tokens` no renombrado o falta de acceso al modelo).

---

## 8. Inventario de archivos modificados

| Archivo                                       | Cambio                                |
|-----------------------------------------------|---------------------------------------|
| `models.py`                                   | +columna `avg_response_time`          |
| `migrate_v5.py`                               | **Nuevo** — migración SQL             |
| `Dockerfile`                                  | + `migrate_v4.py` y `migrate_v5.py`   |
| `chat.py`                                     | Modelo OpenAI → gpt-5.4-mini          |
| `training.py`                                 | ART, prompt IA, `calculate_vex_profile` |
| `templates/training/session.html`             | input → textarea                      |
| `templates/base.html`                         | input → textarea (asistente)          |
| `static/css/training.css`                     | textarea wrap + auto-resize           |
| `static/css/chat.css`                         | textarea wrap + auto-resize           |
| `static/js/training.js`                       | autoResize + Shift+Enter              |
| `static/js/chat.js`                           | autoResizeInput                       |
| `templates/admin/training_scenarios.html`     | textareas con auto-grow               |
| `templates/admin/vex_methodology.html`        | doc visible actualizada               |
| `scoring.md`                                  | **Nuevo** — referencia técnica        |
| `README.md`                                   | sección scoring + link a scoring.md   |
| `guiademodificacion.md`                       | **Nuevo** — este documento            |

---

## 9. Referencias rápidas

- **Documentación interna del scoring:** `scoring.md` (en este repo)
- **Documentación visible al usuario:** `templates/admin/vex_methodology.html`
- **Modelo OpenAI usado:** `gpt-5.4-mini` ([docs](https://developers.openai.com/api/docs/models/gpt-5.4-mini))
- **Función central del scoring:** `training.py::calculate_vex_profile`
- **Cálculo de ART:** `training.py::end_session`
- **Prompt de evaluación IA:** `training.py::end_session` (variable `eval_prompt`)
