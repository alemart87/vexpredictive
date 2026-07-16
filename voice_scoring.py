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
1. Tu identidad, datos y motivo de contacto son los descritos arriba y NO cambian durante toda la llamada.
2. Si el asesor te llama con otro nombre o menciona otro motivo, NO te acomodes — corregí o ignorá.
3. NO inventes datos (empresas, montos, productos) para complacer al asesor.
4. NO reveles que sos una IA bajo ninguna circunstancia.
5. Hablás como en una llamada real: frases cortas, tono conversacional, podés dudar o repreguntar. No des discursos largos.
6. Si el asesor se queda callado mucho tiempo, preguntá "¿hola, estás ahí?".
7. La llamada empieza contigo: saludá y contá tu problema según tu identidad.

Empezá la llamada ahora presentándote y describiendo tu problema."""


def compute_conversation_metrics(turns):
    """Metricas de conversacion desde los turnos persistidos.

    turns: lista de VoiceTurn ordenados por started_at_ms.
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
        gap_s = ((curr.started_at_ms or 0) - (prev.ended_at_ms or 0)) / 1000.0
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
        return {
            'nps_score': 5,
            'response_correct': False,
            'filler_words': 0,
            'ai_feedback': {'feedback': raw, 'strengths': '', 'improvements': '',
                            'empathy_breakdown': {}, 'voice_breakdown': {}},
        }
