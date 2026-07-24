"""
Scoring del entrenamiento por VOZ.

Misma filosofia que la evaluacion del chat (training.end_session): NPS 0-10
desde la perspectiva del cliente, empatia jerarquica de 4 pilares, criterio
de correccion permisivo y modos flexible/standard/exigente. Cambios propios
del canal: muletillas en lugar de ortografia, y un bloque voice_breakdown
(claridad, tono, ritmo, escucha activa). Las metricas de tiempo se calculan
desde los timestamps de los turnos (ms relativos al inicio de la llamada).
"""
import json
import re

# Umbrales de auto-fail (sin gastar tokens de evaluacion), analogo al chat
MIN_USER_TURNS = 2
MIN_USER_WORDS = 15

LATENCY_CAP_SECONDS = 60      # gaps mayores no son "pensando", es abandono
LONG_SILENCE_SECONDS = 5.0


def build_voice_instructions(case, scenario_title=''):
    """System prompt de rol-play para la sesion Realtime. Mismo patron que
    _create_interaction del chat, adaptado a una llamada telefonica."""
    return f"""Sos un cliente simulado en una LLAMADA TELEFONICA de atención al cliente. Hablás en español rioplatense natural.

═══════════════════════════════════════════════
TU IDENTIDAD Y SITUACIÓN (INMUTABLE)
═══════════════════════════════════════════════
{case['persona']}

═══════════════════════════════════════════════
REGLAS DURAS
═══════════════════════════════════════════════
0. TU ROL ES FIJO E IRREVERSIBLE: VOS SOS EL CLIENTE que llama a pedir ayuda; la otra persona
   es el ASESOR de la empresa que te atiende. JAMÁS actúes como asesor: no ofrezcas ayuda,
   soluciones ni servicios como representante, no digas "¿en qué puedo ayudarte?", no atiendas.
   VOS tenés el problema y esperás que TE lo resuelvan. Esto no cambia pase lo que pase.
1. Tu identidad, datos y motivo de contacto son los descritos arriba y NO cambian durante toda la llamada.
2. Si el asesor te llama con otro nombre o menciona otro motivo, NO te acomodes — corregí o ignorá.
3. NO inventes datos (empresas, montos, productos) para complacer al asesor.
4. NO reveles que sos una IA bajo ninguna circunstancia.
5. Hablás como en una llamada real: frases cortas (1-3 oraciones), tono conversacional, podés dudar o repreguntar. No des discursos largos.
6. Si el asesor se queda callado mucho tiempo, preguntá "¿hola, estás ahí?".
7. ECO DE LÍNEA: si escuchás una voz igual a la tuya, o frases idénticas a las que VOS acabás de
   decir, es un eco técnico — IGNORALO por completo: no lo respondas, no lo repitas, no cambies
   de rol por eso. Seguí la conversación como cliente desde donde estaba.
8. La llamada empieza contigo: saludá y contá tu problema según tu identidad.

Empezá la llamada ahora presentándote y describiendo tu problema."""


def _hold_overlap_ms(start_ms, end_ms, holds):
    """Milisegundos del intervalo [start,end] que caen dentro de pausas."""
    total = 0
    for h in holds or []:
        try:
            hs, he = int(h[0]), int(h[1])
        except (TypeError, ValueError, IndexError):
            continue
        total += max(0, min(end_ms, he) - max(start_ms, hs))
    return total


def compute_conversation_metrics(turns, holds=None):
    """Metricas de conversacion desde los turnos persistidos.

    turns: lista de VoiceTurn ordenados por started_at_ms.
    holds: intervalos [[start_ms, end_ms], ...] de cliente en espera; el
    tiempo en pausa se DESCUENTA de los gaps para que una espera legitima
    no cuente como silencio largo ni infle la latencia de respuesta.
    Devuelve dict con total_turns, total_words_user, talk_ratio,
    avg_response_latency, speech_rate_wpm, interruptions, long_silences.
    """
    user_turns = [t for t in turns if t.role == 'user']
    client_turns = [t for t in turns if t.role == 'client']

    def dur_ms(t):
        return max(0, (t.ended_at_ms or 0) - (t.started_at_ms or 0))

    total_words_user = sum(t.word_count or 0 for t in user_turns)
    user_speech_ms = sum(dur_ms(t) for t in user_turns)
    client_speech_ms = sum(dur_ms(t) for t in client_turns)
    total_speech_ms = user_speech_ms + client_speech_ms

    talk_ratio = (user_speech_ms / total_speech_ms) if total_speech_ms > 0 else 0.0

    # WPM hablado: palabras del asesor / minutos que estuvo hablando
    speech_rate_wpm = 0.0
    if user_speech_ms > 1000 and total_words_user:
        speech_rate_wpm = total_words_user / (user_speech_ms / 60000.0)

    # Latencia de respuesta (analogo del ART) e interrupciones:
    # recorremos turnos consecutivos cliente->asesor
    latencies = []
    interruptions = 0
    long_silences = 0
    ordered = sorted(turns, key=lambda t: t.started_at_ms or 0)
    for prev, curr in zip(ordered, ordered[1:]):
        prev_end = prev.ended_at_ms or 0
        curr_start = curr.started_at_ms or 0
        # El tiempo en espera dentro del gap no es silencio del asesor
        gap_s = (curr_start - prev_end - _hold_overlap_ms(prev_end, curr_start, holds)) / 1000.0
        if prev.role == 'client' and curr.role == 'user':
            if gap_s < -0.3:
                interruptions += 1  # el asesor arranco antes de que el cliente terminara
            elif gap_s <= LATENCY_CAP_SECONDS:
                latencies.append(max(0.0, gap_s))
        if gap_s > LONG_SILENCE_SECONDS:
            long_silences += 1

    avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0

    return {
        'total_turns': len(user_turns),
        'total_words_user': total_words_user,
        'talk_ratio': round(talk_ratio, 3),
        'avg_response_latency': avg_latency,
        'speech_rate_wpm': round(speech_rate_wpm, 1),
        'interruptions': interruptions,
        'long_silences': long_silences,
    }


def is_auto_fail(metrics):
    """Participacion insuficiente: no gastamos tokens de evaluacion."""
    return metrics['total_turns'] < MIN_USER_TURNS or metrics['total_words_user'] < MIN_USER_WORDS


AUTO_FAIL_RESULT = {
    'nps_score': 1,
    'response_correct': False,
    'filler_words': 0,
    'ai_feedback': {
        'feedback': 'La llamada terminó sin una participación mínima del asesor. '
                    'Un cliente real habría cortado sintiéndose ignorado.',
        'strengths': '',
        'improvements': 'Atendé la llamada: saludá, escuchá el problema del cliente y trabajá en resolverlo.',
        'empathy_breakdown': {'nombre': False, 'contexto': False, 'calidez': False, 'resolucion': False},
        'voice_breakdown': {'claridad': False, 'tono': False, 'ritmo': False, 'escucha': False},
    },
}


def build_eval_prompt(scenario, case, turns, metrics, mode_label, mode_ai_hint, duration_seconds):
    """Prompt de evaluacion: rubrica del chat adaptada a llamada de voz."""
    transcript = ''
    for t in sorted(turns, key=lambda x: x.started_at_ms or 0):
        label = 'ASESOR' if t.role == 'user' else 'CLIENTE'
        transcript += f"{label}: {t.transcript}\n\n"

    user_text = ' '.join(t.transcript for t in turns if t.role == 'user')

    return f"""Evalúa la siguiente LLAMADA TELEFÓNICA (transcripta) entre un asesor y un cliente simulado.

MODO DE EVALUACIÓN: {mode_label}
{mode_ai_hint}

ESCENARIO: {scenario.title}
DESCRIPCIÓN: {scenario.description or ''}
RESPUESTA ESPERADA DEL ASESOR (referencia): {case['expected']}

DATOS DE LA LLAMADA:
- Turnos del asesor: {metrics['total_turns']}
- Palabras habladas por el asesor: {metrics['total_words_user']}
- Duración: {duration_seconds} segundos
- Latencia media de respuesta del asesor: {metrics['avg_response_latency']} segundos
- Interrupciones al cliente: {metrics['interruptions']}
- Proporción de habla del asesor: {round(metrics['talk_ratio'] * 100)}%
- Veces que puso al cliente en espera: {metrics.get('hold_count', 0)} (total {metrics.get('hold_seconds', 0)} seg)
  (una espera breve y anunciada es práctica normal; esperas largas, repetidas o sin aviso dañan la experiencia)

TRANSCRIPCIÓN DE LA LLAMADA:
{transcript}

HABLA DEL ASESOR (para detectar muletillas):
{user_text}

IMPORTANTE — ES UNA TRANSCRIPCIÓN AUTOMÁTICA DE VOZ: ignorá por completo la ortografía,
tildes y puntuación (las pone el transcriptor, no el asesor). Evaluá lo DICHO, no lo escrito.

NPS - ANÁLISIS DE SENTIMIENTO DEL CLIENTE (escala 0-10):
El NPS se determina desde la PERSPECTIVA DEL CLIENTE. Sé generoso con la evaluación:
si el cliente fue atendido con un esfuerzo razonable y obtuvo respuesta a su necesidad, NPS alto.
- NPS 9-10 (Promotor): El cliente se sintió escuchado y atendido con calidez. Su problema fue abordado.
- NPS 7-8 (Promotor leve): Buena experiencia general, bien atendido aunque algún detalle podría mejorar.
- NPS 5-6 (Pasivo): Atendido correctamente pero sin nada destacable. Experiencia neutra.
- NPS 3-4 (Detractor leve): Respuestas frías o genéricas; el cliente sintió que no abordaron su necesidad.
- NPS 0-2 (Detractor): El cliente se sintió ignorado o mal atendido. Solo aplicar en casos claros.

EMPATÍA — RÚBRICA JERÁRQUICA (evaluá EN ORDEN, cada paso vale):
1. NOMBRE: ¿El asesor mencionó el nombre del cliente al menos una vez?
2. CONTEXTO: ¿Demostró comprender el problema del cliente (parafrasear, reconocer la situación)?
3. CALIDEZ: ¿Usó un tono amable y humano (no robótico ni cortante)?
4. RESOLUCIÓN: ¿Se enfocó genuinamente en ayudar al cliente, no en recitar un speech?
La resolución y la calidez pesan más que recitar el nombre.

CALIDAD DE VOZ — RÚBRICA ESPECÍFICA DEL CANAL (voice_breakdown):
1. CLARIDAD: ¿Se expresó con frases claras y completas, fáciles de seguir por teléfono?
2. TONO: ¿Sonó cordial y profesional (según la transcripción: cortesía, energía, disposición)?
3. RITMO: ¿Mantuvo un ritmo conversacional adecuado (ni monosílabos ni monólogos)?
4. ESCUCHA: ¿Dejó hablar al cliente, retomó lo que dijo, no lo pisó ni lo ignoró?

MULETILLAS (filler_words): contá cuántas veces el asesor usó rellenos como
"este...", "eh", "o sea", "digamos", "¿viste?", "tipo", "nada...". Un uso ocasional
es normal en el habla — contá solo las ocurrencias claras. En un asesor fluido el
número será bajo (0-3).

CRITERIO DE CORRECCIÓN (response_correct):
- true: El asesor cubrió la esencia del procedimiento esperado. NO requiere texto literal; basta con
  abordar la idea principal. Sé permisivo: si la solución es razonable y resuelve el problema, true.
- false: El asesor ignoró el procedimiento o falló en abordar el caso.

Respondé EXACTAMENTE en este formato JSON (sin markdown, solo JSON puro):
{{
    "nps_score": <número del 0 al 10>,
    "response_correct": <true o false>,
    "filler_words": <número de muletillas claras>,
    "empathy_breakdown": {{
        "nombre": <true o false>,
        "contexto": <true o false>,
        "calidez": <true o false>,
        "resolucion": <true o false>
    }},
    "voice_breakdown": {{
        "claridad": <true o false>,
        "tono": <true o false>,
        "ritmo": <true o false>,
        "escucha": <true o false>
    }},
    "feedback": "<retroalimentación constructiva desde la perspectiva del cliente, mencionando empatía y calidad de voz>",
    "strengths": "<2-3 fortalezas observadas>",
    "improvements": "<2-3 áreas de mejora concretas>"
}}"""


EVAL_SYSTEM_PROMPT = (
    'Eres un analista de experiencia del cliente (CX) especializado en atención telefónica. '
    'Evalúas llamadas transcriptas poniéndote en el lugar del cliente: ¿se sintió escuchado? '
    '¿atendido con empatía? ¿le resolvieron su necesidad? El NPS refleja cómo se fue el cliente, '
    'no la perfección técnica del asesor. Trabajás sobre transcripciones automáticas, por lo que '
    'nunca penalizás ortografía ni puntuación. Aplicás a cualquier industria.'
)


def parse_eval_response(raw):
    """Parsea el JSON de la evaluacion (mismo saneo robusto que el chat).
    Devuelve dict normalizado; si falla, un fallback neutro con el texto crudo."""
    try:
        clean = raw.strip()
        if clean.startswith('```'):
            clean = re.sub(r'^```\w*\n?', '', clean)
            clean = re.sub(r'\n?```$', '', clean)
        data = json.loads(clean)
        return {
            'nps_score': max(0, min(10, int(data.get('nps_score', 5)))),
            'response_correct': bool(data.get('response_correct', False)),
            'filler_words': max(0, int(data.get('filler_words', 0) or 0)),
            'ai_feedback': {
                'feedback': data.get('feedback', ''),
                'strengths': data.get('strengths', ''),
                'improvements': data.get('improvements', ''),
                'empathy_breakdown': data.get('empathy_breakdown', {}),
                'voice_breakdown': data.get('voice_breakdown', {}),
            },
        }
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        print(f'[VOICE] Eval parse error: {e}', flush=True)
        # _parse_failed le indica al caller que NO persista esto como nota real
        return {
            '_parse_failed': True,
            'nps_score': 5,
            'response_correct': False,
            'filler_words': 0,
            'ai_feedback': {'feedback': raw, 'strengths': '', 'improvements': '',
                            'empathy_breakdown': {}, 'voice_breakdown': {}},
        }


# ============================================================
#  VEX Profile de Voz — indice predictivo del canal voz
# ============================================================
# Misma metodologia que calculate_vex_profile (chat): 6 dimensiones raw
# 0-100 -> Sten 1-10, pesos/pisos/umbrales del modo activo y hard caps
# universales. Adaptaciones del canal: muletillas en lugar de ortografia,
# latencia de respuesta hablada en lugar del ART del chat (otra escala),
# ritmo de habla en lugar de WPM de tipeo, y los pilares de calidad de voz
# (claridad/tono/ritmo/escucha) alimentando comunicacion y compliance.

MIN_SESSIONS_FOR_PROFILE = 2

# Latencia hablada (seg) -> puntaje. En una llamada, responder en 2s es
# natural; mas de 8s se siente como silencio incomodo. Escala propia del
# canal (el art_curve del chat esta en minutos de tipeo, no aplica).
def _latency_score(lat, no_data_score):
    if lat is None or lat <= 0:
        return no_data_score
    if lat <= 2:
        return 100.0
    if lat <= 4:
        return 100.0 - (lat - 2) / 2 * 20     # 100 -> 80
    if lat <= 8:
        return 80.0 - (lat - 4) / 4 * 30      # 80 -> 50
    if lat <= 15:
        return 50.0 - (lat - 8) / 7 * 30      # 50 -> 20
    return 10.0


def _speech_band_score(wpm):
    """Ritmo conversacional en espanol: 110-160 wpm es natural."""
    if not wpm:
        return 60.0
    if 110 <= wpm <= 160:
        return 100.0
    if 90 <= wpm < 110 or 160 < wpm <= 185:
        return 70.0
    return 40.0


def calculate_voice_vex_profile(user_id):
    """Calcula/actualiza el VoiceVexProfile del usuario. Devuelve el perfil
    o None si no hay muestra suficiente (< 2 llamadas completadas)."""
    from models import db, VoiceSession, VoiceVexProfile
    from scoring_modes import get_effective_mode
    from datetime import datetime

    sessions = (VoiceSession.query
                .filter(VoiceSession.user_id == user_id,
                        VoiceSession.status.in_(('completed', 'abandoned')))
                .order_by(VoiceSession.created_at.asc()).all())
    completed = [s for s in sessions if s.status == 'completed']
    if len(completed) < MIN_SESSIONS_FOR_PROFILE:
        return None

    def _is_autofail(s):
        return (s.total_turns or 0) < MIN_USER_TURNS or (s.total_words_user or 0) < MIN_USER_WORDS

    total = len(sessions)
    failed = sum(1 for s in sessions if s.status == 'abandoned') + \
        sum(1 for s in completed if _is_autofail(s))
    abandonment_rate = failed / total if total else 0.0

    # Modo activo = el de la sesion mas reciente (snapshot), igual que chat
    mode_name, is_legacy, mode_cfg = get_effective_mode(sessions[-1].scoring_mode)
    pi_weights = mode_cfg['pi_weights']
    floors = mode_cfg['floors']
    thresholds = mode_cfg['thresholds']
    rec_thresholds = mode_cfg['recommendation']
    pillars_w = mode_cfg.get('empathy_pillars_weight', 0.7)

    # --- Agregados ---
    avg_nps = sum(s.nps_score or 0 for s in completed) / len(completed)
    correct_rate = sum(1 for s in completed if s.response_correct) / len(completed)

    total_words = sum(s.total_words_user or 0 for s in completed)
    total_fillers = sum(s.filler_words or 0 for s in completed)
    filler_per_100 = (total_fillers / total_words * 100) if total_words else 0.0
    # Saturacion: hablar con algunas muletillas es normal; toleramos ~3x lo
    # que el chat tolera de ortografia con el mismo multiplier del modo.
    filler_penalty = min(1.0, (total_fillers / total_words if total_words else 0)
                         * mode_cfg.get('spelling_multiplier', 25) / 3.0)

    lats = [s.avg_response_latency for s in completed if (s.avg_response_latency or 0) > 0]
    avg_latency = sum(lats) / len(lats) if lats else None
    rates = [s.speech_rate_wpm for s in completed if (s.speech_rate_wpm or 0) > 0]
    avg_speech = sum(rates) / len(rates) if rates else 0.0

    # Pilares (sobre el total de completadas: los auto-fail arrastran False)
    def _pillar_rates(key, names):
        counts = {n: 0 for n in names}
        for s in completed:
            fb = {}
            try:
                fb = json.loads(s.ai_feedback) if s.ai_feedback else {}
            except (json.JSONDecodeError, TypeError):
                pass
            block = fb.get(key) or {}
            for n in names:
                if block.get(n):
                    counts[n] += 1
        return {n: counts[n] / len(completed) for n in names}

    emp = _pillar_rates('empathy_breakdown', ('nombre', 'contexto', 'calidez', 'resolucion'))
    vb = _pillar_rates('voice_breakdown', ('claridad', 'tono', 'ritmo', 'escucha'))

    # Tendencia de NPS (regresion lineal simple sobre el orden cronologico)
    n = len(completed)
    xs = list(range(n))
    ys = [s.nps_score or 0 for s in completed]
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    slope = (sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / var_x) if var_x else 0.0
    trend = max(0.0, min(1.0, 0.5 + slope / 2.0))

    distinct_ok = len({s.scenario_id for s in completed if s.response_correct})
    variety = min(1.0, distinct_ok / 4.0)

    # --- Dimensiones raw 0-100 ---
    pillars_score = (emp['nombre'] * 0.15 + emp['contexto'] * 0.25 +
                     emp['calidez'] * 0.25 + emp['resolucion'] * 0.35)

    comm_raw = floors['communication'] + (1 - filler_penalty) * 15 + \
        vb['claridad'] * 15 + (avg_nps / 10) * 40
    empathy_raw = pillars_score * 100 * pillars_w + avg_nps * 10 * (1 - pillars_w)
    resolution_raw = floors['resolution'] + correct_rate * 50 + (avg_nps / 10) * 25
    speed_raw = _latency_score(avg_latency, floors.get('speed_no_data', 60)) * 0.7 + \
        _speech_band_score(avg_speech) * 0.3
    adapt_raw = floors['adaptability'] + trend * 35 + variety * 35
    compliance_raw = floors['compliance'] + correct_rate * 45 + \
        (1 - filler_penalty) * 15 + vb['escucha'] * 15

    raw_scores = [comm_raw, empathy_raw, resolution_raw, speed_raw, adapt_raw, compliance_raw]

    def to_sten(raw):
        sten = int(raw / 10) + (1 if (raw % 10) >= 4 else 0)
        return max(1, min(10, sten))

    scores = [to_sten(r) for r in raw_scores]
    comm, empathy, resolution, speed, adapt, compliance = scores
    overall = round(sum(scores) / 6, 1)

    pi = (resolution * pi_weights['resolution'] + empathy * pi_weights['empathy'] +
          comm * pi_weights['communication'] + speed * pi_weights['speed'] +
          adapt * pi_weights['adaptability'] + compliance * pi_weights['compliance'])
    pi_pct = round(pi * 10, 1)

    if overall >= thresholds['elite_overall'] and all(s >= thresholds['elite_min_dim'] for s in scores):
        category = 'elite'
    elif overall >= thresholds['alto_overall'] and all(s >= thresholds['alto_min_dim'] for s in scores):
        category = 'alto'
    elif overall >= thresholds['desarrollo_overall']:
        category = 'desarrollo'
    else:
        category = 'refuerzo'

    if pi_pct >= rec_thresholds['recomendado']:
        rec = 'recomendado'
    elif pi_pct >= rec_thresholds['observaciones']:
        rec = 'observaciones'
    else:
        rec = 'no_recomendado'

    # Hard caps universales (mismas 3 reglas que el chat)
    cap_reasons = []
    if abandonment_rate > 0.40:
        cap_reasons.append({
            'rule': 'abandonment',
            'detail': f'{int(abandonment_rate*100)}% de llamadas abandonadas o vacias (limite 40%)',
            'effect': 'Categoria max "Desarrollo", recomendacion max "Observaciones"'})
    if correct_rate < 0.50:
        cap_reasons.append({
            'rule': 'low_correct_rate',
            'detail': f'Solo {int(correct_rate*100)}% de resoluciones correctas (limite 50%)',
            'effect': 'Recomendacion max "Observaciones"'})
    if avg_nps < 4.0:
        cap_reasons.append({
            'rule': 'low_nps',
            'detail': f'NPS promedio {avg_nps:.1f} (limite 4.0). Clientes mayormente detractores.',
            'effect': 'Recomendacion max "Observaciones"'})
    if cap_reasons:
        if category in ('elite', 'alto'):
            category = 'desarrollo'
        if rec == 'recomendado':
            rec = 'observaciones'

    profile = VoiceVexProfile.query.filter_by(user_id=user_id).first()
    if not profile:
        profile = VoiceVexProfile(user_id=user_id)
        db.session.add(profile)

    profile.communication_score = comm
    profile.empathy_score = empathy
    profile.resolution_score = resolution
    profile.speed_score = speed
    profile.adaptability_score = adapt
    profile.compliance_score = compliance
    profile.overall_score = overall
    profile.predictive_index = pi_pct
    profile.profile_category = category
    profile.recommendation = rec
    profile.sessions_analyzed = total
    profile.abandonment_rate = round(abandonment_rate, 3)
    profile.avg_response_latency = round(avg_latency, 2) if avg_latency else 0.0
    profile.avg_speech_rate = round(avg_speech, 1)
    profile.filler_rate = round(filler_per_100, 2)
    profile.last_updated = datetime.utcnow()
    # Volatiles para el render (no persisten), igual que el perfil de chat
    profile._cap_reasons = cap_reasons
    profile._active_mode = mode_name
    profile._is_legacy_mode = is_legacy
    profile._mode_cfg = mode_cfg
    profile._voice_pillars = vb
    profile._empathy_pillars = emp
    db.session.commit()
    return profile
