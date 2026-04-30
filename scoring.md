# Sistema de Scoring — Vex People Predictive

Documento de referencia del modelo predictivo de talento. Detalla cómo se evalúa cada
sesión de entrenamiento y cómo se agregan los resultados en el perfil VEX del asesor.

> Fuente única de verdad: `training.py` (función `calculate_vex_profile` y endpoint
> `end_session`). Si modificás las fórmulas, actualizá este documento.

---

## 1. Capa 1 — Evaluación por sesión (IA)

Cada sesión cerrada se evalúa con OpenAI (**GPT-5.4 mini**, ID:
`gpt-5.4-mini`). El modelo recibe la conversación completa, el escenario, la
respuesta esperada y un texto consolidado del asesor para revisión ortográfica.
Devuelve un JSON estructurado con las señales que alimentan el perfil agregado.

> El cliente simulado durante la sesión y la evaluación final usan el mismo
> modelo. Si necesitás cambiar el modelo, editá `chat.py:166` (`call_openai`).
> Nota: GPT-5.x usa `max_completion_tokens` (no `max_tokens`); si volvés a un
> modelo de la familia GPT-4 hay que renombrar el parámetro de vuelta.

### 1.1 Auto-fail

Si el asesor envió **<2 mensajes** o escribió **<8 palabras** la sesión recibe
NPS=1 sin llamar al modelo. Evita inflar tokens en interacciones vacías y deja
una marca clara de "no hubo trabajo real".

### 1.2 Salidas del modelo

| Campo                | Tipo           | Descripción |
|----------------------|----------------|-------------|
| `nps_score`          | int 0-10       | Sentimiento del cliente al cerrar el chat |
| `response_correct`   | bool           | ¿Cubrió la esencia del procedimiento? |
| `spelling_errors`    | int            | Errores que **afectan la comprensión** (no tildes ni abreviaciones) |
| `empathy_breakdown`  | objeto         | Cumplimiento de los 4 pilares (ver 1.3) |
| `feedback`           | string         | Retroalimentación al asesor |
| `strengths`          | string         | 2-3 fortalezas |
| `improvements`       | string         | 2-3 áreas de mejora |

### 1.3 Rúbrica de Empatía (jerárquica)

La empatía se evalúa con cuatro pilares ordenados por importancia operativa.
Ningún pilar es eliminatorio, pero los que pesan más al final son los últimos
(la calidad de la atención manda sobre formulismos).

| Orden | Pilar       | Pregunta clave                                                | Peso en empatía |
|-------|-------------|---------------------------------------------------------------|-----------------|
| 1     | Nombre      | ¿Mencionó el nombre del cliente al menos una vez?             | 15%             |
| 2     | Contexto    | ¿Demostró comprender el problema (parafrasear, reconocer)?    | 25%             |
| 3     | Calidez     | ¿Tono amable/humano (o emojis adecuados)?                     | 25%             |
| 4     | Resolución  | ¿Se enfocó en ayudar, no en recitar un speech?                | 35%             |

> Mezcla final de empatía: **70% pilares + 30% NPS**. Sesiones legacy sin
> breakdown caen al cálculo histórico (100% NPS).

### 1.4 Reglas de Ortografía (lenientes)

El modelo recibe instrucciones explícitas para **no penalizar**:

- Tildes omitidas
- Mayúsculas iniciales en chat informal
- Abreviaciones comunes (`xq`, `q`, `tmb`, `pq`, `graxs`)
- Emojis
- Apertura de signos (`¿`, `¡`)
- Errores tipográficos menores que **no afectan la comprensión**

Solo se cuenta como error lo que **cambia el significado** o **impide entender**.
La mayoría de los chats bien escritos deben dar **0 errores**.

---

## 2. Capa 2 — Perfil VEX (agregación)

Se calcula con `calculate_vex_profile(user_id)` y requiere **≥2 sesiones**
completadas. El resultado se persiste en `vex_profiles`.

### 2.1 Métricas base agregadas

| Métrica            | Cálculo |
|--------------------|---------|
| `avg_nps`          | Promedio de `nps_score` por sesión |
| `correct_rate`     | Sesiones con `response_correct=true` / total |
| `spelling_rate`    | `Σ spelling_errors` / `Σ total_words_user` |
| `avg_wpm`          | Promedio de `words_per_minute` |
| `avg_art`          | Promedio del **ART** (sólo sesiones con ART > 0) |
| `improvement_trend`| Pendiente lineal del NPS por fecha, normalizada a 0–1 |
| `variety`          | `unique_scenarios` / `max(total_scenarios×0.4, 1)`, cap 1 |
| `empathy_pillar_rate` | Tasa de cumplimiento por pilar (Nombre/Contexto/Calidez/Resolución) |

### 2.2 Penalización ortográfica suavizada

```
spelling_penalty = min(spelling_rate × 25, 1)
```

Antes era ×10 (saturaba al 10% de errores). Ahora satura al **4%** —
1 error cada 25 palabras es ya el máximo de penalización. En la práctica con
las reglas lenientes del modelo el `spelling_rate` será cercano a 0.

### 2.3 Dimensiones (escala raw 0-100)

Cada dimensión tiene **piso mínimo** para que las primeras sesiones no aplasten
el perfil cuando una métrica puntual sale baja.

#### Comunicación

```
comm_raw = 30 + (1 - spelling_penalty) × 30 + (avg_nps / 10) × 40
```

NPS pesa más que ortografía (40 vs 30). Piso 30.

#### Empatía

```
si pillar_count > 0:
    pillars = nombre×15 + contexto×25 + calidez×25 + resolucion×35
    empathy_raw = pillars × 0.7 + (avg_nps × 10) × 0.3
si no:
    empathy_raw = avg_nps × 10   # legacy
```

#### Resolución

```
resolution_raw = 25 + correct_rate × 50 + (avg_nps / 10) × 25
```

Piso 25. Correct rate sigue siendo el factor dominante (50%) pero ya no es
dictador único.

#### Velocidad — basada en ART (Average Response Time)

**ART** = tiempo medio en segundos entre el mensaje del cliente y la respuesta
del asesor. **No** mide la duración total del chat ni penaliza al asesor por
la lentitud del cliente.

```
avg_art ≤ 120s        → speed_art = 100   (excelente)
120s < avg_art ≤ 180s → 100 → 80          (saludable)
180s < avg_art ≤ 300s → 80 → 50           (aceptable)
300s < avg_art ≤ 600s → 50 → 20           (lento)
avg_art > 600s        → 20                (muy lento, cap)
avg_art = 0           → 65                (sin datos, neutro)
```

Meta operativa para asesor con **5 chats simultáneos**: **120-180s** de ART.

```
speed_wpm = min(100, (avg_wpm / 25) × 100)   # 25 WPM = 100%
speed_raw = speed_art × 0.7 + speed_wpm × 0.3
```

ART pesa 70% (capacidad de respuesta), WPM 30% (velocidad de tipeo).

#### Adaptabilidad

```
adapt_raw = 30 + improvement_trend × 35 + variety × 35
```

Piso 30. Premia mejorar con el tiempo y rotar entre escenarios.

#### Compliance

```
compliance_raw = 25 + correct_rate × 45 + (1 - spelling_penalty) × 30
```

### 2.4 Conversión a escala Sten (1-10)

```python
def to_sten(raw):
    sten = int(raw / 10) + (1 if (raw % 10) >= 4 else 0)
    return clamp(sten, 1, 10)
```

Redondeo "amigable" (umbral 4 en lugar de 5) para que un raw de 64 no caiga a
6, sino que suba a 7.

### 2.5 Predictive Index (compuesto ponderado)

| Dimensión      | Peso  |
|----------------|-------|
| Empatía        | 25%   |
| Resolución     | 22%   |
| Comunicación   | 18%   |
| Velocidad      | 15%   |
| Adaptabilidad  | 10%   |
| Compliance     | 10%   |

```
PI (1-10)  = empatía×0.25 + resolución×0.22 + comunicación×0.18
           + velocidad×0.15 + adaptabilidad×0.10 + compliance×0.10
PI (%)     = PI × 10
```

**Cambio clave vs versión anterior:** empatía sube de 20% a 25% por la
nueva rúbrica de 4 pilares. Resolución baja de 25% a 22%. Comunicación de 20% a 18%.

### 2.6 Categoría del perfil

Umbrales **más alcanzables** que la versión inicial.

| Categoría      | Condición |
|----------------|-----------|
| **Elite**      | Overall ≥ 8.5 **y** todas las dimensiones ≥ 7 |
| **Alto**       | Overall ≥ 6.5 **y** todas las dimensiones ≥ 4 |
| **Desarrollo** | Overall ≥ 4.5 |
| **Refuerzo**   | Overall < 4.5 |

### 2.7 Recomendación

| Recomendación      | Predictive Index |
|--------------------|------------------|
| **Recomendado**    | ≥ 65%            |
| **Observaciones**  | 45 – 65%         |
| **No Recomendado** | < 45%            |

Antes era 70%/50%. Bajamos 5 puntos para alinear con la curva más generosa.

---

## 3. ART — Average Response Time

### 3.1 Cómo se calcula por sesión

En `end_session` se recorren los mensajes y, por cada respuesta del asesor,
se mide el segundo entre el último mensaje del cliente y la réplica:

```python
gap = (msg_asesor.created_at - prev_cliente.created_at).total_seconds()
gap = max(0, min(gap, 600))   # cap 600s para evitar idle extremo
response_gaps.append(gap)

session.avg_response_time = mean(response_gaps)
```

### 3.2 Qué NO mide

- **No** mide la duración total del chat.
- **No** castiga al asesor por la lentitud del cliente.
- **No** se acumula tiempo cuando el asesor habla primero o cuando hay
  varias respuestas seguidas del cliente (sólo cuenta el último gap).

### 3.3 Por qué el cap de 600s

Si un cliente desaparece 20 minutos y vuelve, no es justo que ese gap
arruine el ART del asesor. 600s (10 min) es el máximo razonable que
podemos atribuir a "el asesor no respondió a tiempo".

### 3.4 Migración

Sesiones existentes tienen `avg_response_time = 0` (default DB) y reciben
puntaje neutro de **65** en velocidad-ART. Las nuevas sesiones empiezan a
poblar el campo automáticamente desde el primer cierre.

---

## 4. Resumen de cambios vs versión anterior

| Área                          | Antes                              | Ahora |
|-------------------------------|------------------------------------|-------|
| Penalización ortográfica      | ×10 (10% errores → 0)              | ×25 (4% errores → 0) |
| Reglas de ortografía          | "errores claros"                   | Solo si afectan comprensión |
| Empatía                       | 100% NPS                           | 70% pilares + 30% NPS |
| Pilares de empatía            | —                                  | Nombre/Contexto/Calidez/Resolución (15/25/25/35) |
| Velocidad                     | WPM + duración total               | ART + WPM (70/30) |
| Pisos mínimos por dimensión   | 0                                  | 25–30 según dimensión |
| Conversión Sten               | round(raw/10)                      | int(raw/10) + (1 si resto ≥ 4) |
| Peso empatía en PI            | 20%                                | 25% |
| Categoría Elite               | todas ≥ 8                          | Overall ≥ 8.5 **y** todas ≥ 7 |
| Categoría Alto                | overall ≥ 7 y todas ≥ 5            | Overall ≥ 6.5 y todas ≥ 4 |
| Recomendado                   | PI ≥ 70%                           | PI ≥ 65% |
| Observaciones                 | 50–70%                             | 45–65% |

---

## 5. Tabla de campos persistidos

### `training_sessions`

| Campo                | Tipo     | Origen |
|----------------------|----------|--------|
| `nps_score`          | int      | IA |
| `response_correct`   | bool     | IA |
| `spelling_errors`    | int      | IA (lenient) |
| `words_per_minute`   | float    | calculado al cerrar |
| `avg_response_time`  | float    | calculado al cerrar (**nuevo**) |
| `ai_feedback`        | json     | IA (incluye `empathy_breakdown` desde esta versión) |

### `vex_profiles`

Sin cambios de esquema. Los valores persistidos siguen las nuevas fórmulas.

---

## 6. Migraciones

- `migrate_v5.py` — añade `avg_response_time` a `training_sessions`
  (idempotente, usa `IF NOT EXISTS`).
- Se ejecuta automáticamente en el arranque del contenedor (Dockerfile).
