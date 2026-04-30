import os
import re
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import (db, User, TrainingScenario, TrainingBatch, TrainingSession,
                    TrainingMessage, TrainingViewPermission, VexProfile)
from datetime import datetime, timezone
from functools import wraps
from decorators import superadmin_required, coordinador_or_above, scoped_query


def utcnow():
    return datetime.utcnow()


def safe_elapsed(start_dt):
    """Calculate elapsed seconds handling naive/aware datetime mix."""
    now = datetime.utcnow()
    if start_dt and start_dt.tzinfo:
        start_dt = start_dt.replace(tzinfo=None)
    return (now - start_dt).total_seconds() if start_dt else 0
from chat import call_openai
import random

training_bp = Blueprint('training', __name__)


def parse_cases(scenario):
    """Parse cases from scenario. Returns list of {persona, expected_response}."""
    try:
        personas = json.loads(scenario.client_persona)
        responses = json.loads(scenario.expected_response)
        if isinstance(personas, list) and isinstance(responses, list):
            cases = []
            for i, p in enumerate(personas):
                resp = responses[i] if i < len(responses) else {}
                cases.append({
                    'persona': p.get('text', '') if isinstance(p, dict) else str(p),
                    'expected': resp.get('text', '') if isinstance(resp, dict) else str(resp),
                    'label': p.get('label', f'Caso {i+1}') if isinstance(p, dict) else f'Caso {i+1}'
                })
            return cases if cases else [{'persona': scenario.client_persona, 'expected': scenario.expected_response, 'label': 'Caso 1'}]
    except (json.JSONDecodeError, TypeError):
        pass
    # Legacy: single text fields
    return [{'persona': scenario.client_persona, 'expected': scenario.expected_response, 'label': 'Caso 1'}]


def get_case(scenario, index):
    """Get a specific case by index."""
    cases = parse_cases(scenario)
    if index < len(cases):
        return cases[index]
    return cases[0]


def can_view_training(f):
    """SuperAdmin, coordinador, or supervisor with permission."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.is_superadmin or current_user.role == 'coordinador':
            return f(*args, **kwargs)
        perm = TrainingViewPermission.query.filter_by(supervisor_id=current_user.id).first()
        if perm:
            return f(*args, **kwargs)
        flash('No tienes permisos para ver esta seccion.', 'error')
        return redirect(url_for('index'))
    return decorated


# ===== User Routes =====

@training_bp.route('/training')
@login_required
def index():
    q = TrainingScenario.query.filter_by(is_active=True)
    if not current_user.is_superadmin and current_user.operativa_id:
        q = q.filter_by(operativa_id=current_user.operativa_id)
    scenarios = q.order_by(TrainingScenario.created_at.desc()).all()
    my_batches = TrainingBatch.query.filter_by(
        user_id=current_user.id
    ).order_by(TrainingBatch.started_at.desc()).limit(10).all()
    active_batch = TrainingBatch.query.filter_by(
        user_id=current_user.id, status='active'
    ).first()
    # Also check legacy sessions without batch
    my_sessions = TrainingSession.query.filter(
        TrainingSession.user_id == current_user.id,
        TrainingSession.batch_id.is_(None)
    ).order_by(TrainingSession.created_at.desc()).limit(10).all()
    return render_template('training/index.html',
                           scenarios=scenarios, my_batches=my_batches,
                           my_sessions=my_sessions, active_batch=active_batch)


def _create_interaction(batch, scenario, interaction_num):
    """Create a single interaction (TrainingSession) within a batch."""
    # Pick a random case, avoiding repeats within the same batch
    cases = parse_cases(scenario)
    if len(cases) == 1:
        case_idx = 0
    else:
        # Get case indices already used in this batch
        used_indices = [
            s.case_index for s in
            TrainingSession.query.filter_by(batch_id=batch.id).all()
            if s.case_index is not None
        ]
        # Available cases not yet used in this batch
        available = [i for i in range(len(cases)) if i not in used_indices]
        if not available:
            # All cases used, reset pool but exclude the most recent one
            last_used = used_indices[-1] if used_indices else -1
            available = [i for i in range(len(cases)) if i != last_used]
            if not available:
                available = list(range(len(cases)))
        case_idx = random.choice(available)
    case = cases[case_idx]

    session = TrainingSession(
        batch_id=batch.id,
        interaction_number=interaction_num,
        case_index=case_idx,
        scenario_id=scenario.id,
        user_id=batch.user_id,
        status='active',
        started_at=datetime.utcnow()
    )
    db.session.add(session)
    db.session.flush()

    # Generate client message using the specific case's persona
    variation = ""
    if interaction_num > 1:
        variation = f"\nNota: Sos un cliente diferente al anterior. Usá un nombre distinto y variá ligeramente tu tono."

    system_prompt = f"""Eres un cliente simulado. Tu personalidad y situación:

{case['persona']}{variation}

REGLAS:
- Actúa como un cliente REAL en un chat de atención
- NO reveles que eres IA
- Reaccioná naturalmente al asesor
- Respuestas cortas (1-3 oraciones)
- Empezá describiendo tu problema"""

    ai_messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': 'Iniciá la conversación como el cliente.'}
    ]
    response_text, tokens = call_openai(ai_messages)

    msg = TrainingMessage(
        session_id=session.id, role='client',
        content=response_text, word_count=len(response_text.split())
    )
    db.session.add(msg)
    session.tokens_used = tokens
    return session, response_text


@training_bp.route('/api/training/batch/start/<int:scenario_id>', methods=['POST'])
@login_required
def start_batch(scenario_id):
    scenario = TrainingScenario.query.get_or_404(scenario_id)

    # Check no active batch
    active = TrainingBatch.query.filter_by(user_id=current_user.id, status='active').first()
    if active:
        return jsonify({'error': 'Ya tienes una sesión activa', 'batch_id': active.id}), 400

    max_c = current_user.max_concurrent_training or 1

    # Snapshot del modo del escenario al crear el batch (asi cambios futuros
    # en el escenario no afectan evaluaciones ya iniciadas). Si el escenario
    # no tiene modo (legacy), el batch tampoco -> se evaluara con Standard
    # pero se etiquetara como 'legacy' en la UI.
    batch = TrainingBatch(
        user_id=current_user.id,
        scenario_id=scenario.id,
        max_concurrent=max_c,
        status='active',
        started_at=datetime.utcnow(),
        scoring_mode=scenario.scoring_mode
    )
    db.session.add(batch)
    db.session.flush()

    # Create first interaction
    session, first_msg = _create_interaction(batch, scenario, 1)
    db.session.commit()

    return jsonify({
        'batch_id': batch.id,
        'max_concurrent': max_c,
        'scenario_title': scenario.title,
        'interactions': [{
            'session_id': session.id,
            'interaction_number': 1,
            'first_message': first_msg,
            'status': 'active'
        }]
    })


@training_bp.route('/api/training/batch/<int:batch_id>/add', methods=['POST'])
@login_required
def add_interaction(batch_id):
    batch = TrainingBatch.query.filter_by(
        id=batch_id, user_id=current_user.id, status='active'
    ).first()
    if not batch:
        return jsonify({'error': 'Batch no encontrado'}), 404

    current_count = TrainingSession.query.filter_by(batch_id=batch.id).count()
    if current_count >= batch.max_concurrent:
        return jsonify({'error': 'Máximo de interacciones alcanzado'}), 400

    scenario = batch.scenario
    session, first_msg = _create_interaction(batch, scenario, current_count + 1)
    db.session.commit()

    return jsonify({
        'session_id': session.id,
        'interaction_number': current_count + 1,
        'first_message': first_msg,
        'status': 'active'
    })


@training_bp.route('/api/training/batch/<int:batch_id>/status')
@login_required
def batch_status(batch_id):
    batch = TrainingBatch.query.filter_by(
        id=batch_id, user_id=current_user.id
    ).first_or_404()
    sessions = TrainingSession.query.filter_by(batch_id=batch.id).all()
    return jsonify({
        'batch_id': batch.id,
        'status': batch.status,
        'max_concurrent': batch.max_concurrent,
        'interactions': [{
            'session_id': s.id,
            'interaction_number': s.interaction_number,
            'status': s.status,
            'nps': s.nps_score,
            'last_message': s.messages[-1].content[:80] if s.messages else ''
        } for s in sessions],
        'completed': sum(1 for s in sessions if s.status == 'completed'),
        'active': sum(1 for s in sessions if s.status == 'active'),
        'total': len(sessions)
    })


@training_bp.route('/training/batch/<int:batch_id>')
@login_required
def batch_view(batch_id):
    batch = TrainingBatch.query.filter_by(
        id=batch_id, user_id=current_user.id
    ).first_or_404()
    return render_template('training/session.html', batch=batch)


# Keep legacy single-session route working
@training_bp.route('/api/training/start/<int:scenario_id>', methods=['POST'])
@login_required
def start_session(scenario_id):
    return start_batch(scenario_id)


@training_bp.route('/api/training/message', methods=['POST'])
@login_required
def send_message():
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    message = data.get('message', '').strip()

    if not message or not session_id:
        return jsonify({'error': 'Datos incompletos'}), 400

    session = TrainingSession.query.filter_by(
        id=session_id, user_id=current_user.id, status='active'
    ).first()
    if not session:
        return jsonify({'error': 'Sesión no encontrada o finalizada'}), 404

    scenario = session.scenario
    word_count = len(message.split())

    # Save user message
    user_msg = TrainingMessage(
        session_id=session.id,
        role='user',
        content=message,
        word_count=word_count
    )
    db.session.add(user_msg)

    # Update session metrics
    session.total_messages = (session.total_messages or 0) + 1
    session.total_words_user = (session.total_words_user or 0) + word_count
    session.total_chars_user = (session.total_chars_user or 0) + len(message)

    # WPM is recalculated accurately at session end using message timestamps

    # Build conversation for OpenAI
    system_prompt = f"""Eres un cliente simulado. Tu personalidad y situación:

{scenario.client_persona}

REGLAS:
- Actúa como un cliente REAL en un chat de atención
- NO reveles que sos IA
- Reaccioná naturalmente al asesor
- Respuestas cortas (1-3 oraciones) como un cliente real en chat
- Si el asesor te ayuda bien, mostrá satisfacción
- Si no, mostrá frustración realista"""

    ai_messages = [{'role': 'system', 'content': system_prompt}]

    # Add conversation history
    for msg in session.messages:
        role = 'assistant' if msg.role == 'client' else 'user'
        ai_messages.append({'role': role, 'content': msg.content})

    response_text, tokens = call_openai(ai_messages)

    # Save client response
    client_msg = TrainingMessage(
        session_id=session.id,
        role='client',
        content=response_text,
        word_count=len(response_text.split())
    )
    db.session.add(client_msg)
    session.tokens_used = (session.tokens_used or 0) + tokens
    db.session.commit()

    return jsonify({
        'response': response_text,
        'metrics': {
            'messages': session.total_messages,
            'words': session.total_words_user,
            'wpm': session.words_per_minute,
            'elapsed_seconds': int(safe_elapsed(session.started_at))
        }
    })


@training_bp.route('/api/training/end/<int:session_id>', methods=['POST'])
@login_required
def end_session(session_id):
    session = TrainingSession.query.filter_by(
        id=session_id, user_id=current_user.id, status='active'
    ).first()
    if not session:
        return jsonify({'error': 'Sesión no encontrada'}), 404

    scenario = session.scenario
    now = datetime.utcnow()

    # Calculate final metrics
    session.ended_at = now
    session.duration_seconds = int(safe_elapsed(session.started_at))

    # WPM + ART (Average Response Time): tiempo entre cada mensaje del cliente
    # y la respuesta del asesor. ART NO mide la duracion total del chat — solo
    # cuanto tarda el asesor en responder cada vez que el cliente le escribe.
    user_messages = [m for m in session.messages if m.role == 'user']
    response_gaps = []  # segundos entre cliente -> asesor
    if user_messages and session.total_words_user:
        typing_seconds = 0
        prev_client_time = None
        for msg in session.messages:
            if msg.role == 'client':
                prev_client_time = msg.created_at
            elif msg.role == 'user':
                if prev_client_time and msg.created_at:
                    gap = (msg.created_at.replace(tzinfo=None) - prev_client_time.replace(tzinfo=None)).total_seconds()
                    # Cap por mensaje a 600s (10min) para no castigar pausas extremas o idle del cliente
                    capped = max(0, min(gap, 600))
                    response_gaps.append(capped)
                    # WPM usa los mismos gaps cap a 120s para estimar tiempo activo de tipeo
                    typing_seconds += min(capped, 120)
                else:
                    typing_seconds += 10  # primer mensaje sin contexto
        typing_minutes = max(typing_seconds / 60, 0.1)
        session.words_per_minute = round(session.total_words_user / typing_minutes, 1)
    elif session.duration_seconds > 0 and session.total_words_user:
        session.words_per_minute = round(session.total_words_user / (session.duration_seconds / 60), 1)

    # ART: promedio de tiempos de respuesta. Si no hay gaps medibles (ej: el
    # asesor habla primero), ART queda en 0 y no penaliza.
    session.avg_response_time = round(sum(response_gaps) / len(response_gaps), 1) if response_gaps else 0

    # Count user messages
    user_msg_count = len(user_messages)

    # If agent barely interacted, auto-fail without calling OpenAI
    if user_msg_count < 2 or (session.total_words_user or 0) < 8:
        session.nps_score = 1
        session.response_correct = False
        session.spelling_errors = 0
        session.ai_feedback = json.dumps({
            'feedback': f'El asesor envió solo {user_msg_count} mensaje(s) con {session.total_words_user or 0} palabras. No se puede evaluar una interacción sin respuesta sustancial al cliente.',
            'strengths': 'No se identificaron fortalezas debido a la falta de interacción.',
            'improvements': '1. Responder al cliente de forma completa. 2. Seguir el procedimiento indicado para el caso. 3. Dedicar tiempo a cada interacción.'
        }, ensure_ascii=False)
        session.status = 'completed'
        db.session.commit()

        batch_complete = False
        batch_id = session.batch_id
        if batch_id:
            batch = TrainingBatch.query.get(batch_id)
            if batch:
                all_sessions = TrainingSession.query.filter_by(batch_id=batch_id).all()
                all_done = all(s.status == 'completed' for s in all_sessions)
                all_spawned = len(all_sessions) >= batch.max_concurrent
                if all_done and all_spawned:
                    batch.status = 'completed'
                    batch.ended_at = now
                    batch.duration_seconds = int(safe_elapsed(batch.started_at))
                    nps_scores = [s.nps_score for s in all_sessions if s.nps_score is not None]
                    batch.overall_nps = round(sum(nps_scores) / len(nps_scores), 1) if nps_scores else 0
                    correct = sum(1 for s in all_sessions if s.response_correct)
                    batch.overall_correct_rate = round(correct / len(all_sessions) * 100, 1)
                    batch.tokens_used = sum(s.tokens_used or 0 for s in all_sessions)
                    db.session.commit()
                    batch_complete = True
                    calculate_vex_profile(current_user.id)
        else:
            calculate_vex_profile(current_user.id)

        return jsonify({'ok': True, 'session_id': session.id, 'batch_id': batch_id, 'batch_complete': batch_complete})

    # Build full conversation text for evaluation
    conversation_text = ""
    for msg in session.messages:
        label = "ASESOR" if msg.role == 'user' else "CLIENTE"
        conversation_text += f"{label}: {msg.content}\n\n"

    # Get user messages for spelling check
    user_texts = ' '.join(m.content for m in session.messages if m.role == 'user')

    # Determinar modo de scoring del batch (snapshot al crear) y obtener su hint
    # para la IA. Legacy/null -> standard.
    from scoring_modes import get_effective_mode
    batch_obj = TrainingBatch.query.get(session.batch_id) if session.batch_id else None
    batch_mode = batch_obj.scoring_mode if batch_obj else None
    eff_mode_name, _is_legacy, eff_mode_cfg = get_effective_mode(batch_mode)
    mode_ai_hint = eff_mode_cfg.get('ai_hint', '')
    mode_label = eff_mode_cfg.get('label', 'Standard')

    # Evaluate with OpenAI
    eval_prompt = f"""Evalúa la siguiente conversación entre un asesor y un cliente simulado.

MODO DE EVALUACIÓN: {mode_label}
{mode_ai_hint}

ESCENARIO: {scenario.title}
DESCRIPCIÓN: {scenario.description or ''}
RESPUESTA ESPERADA DEL ASESOR (referencia): {get_case(scenario, session.case_index or 0)['expected']}

DATOS DEL ASESOR:
- Mensajes enviados: {user_msg_count}
- Palabras totales: {session.total_words_user or 0}
- Duración: {session.duration_seconds} segundos

CONVERSACIÓN:
{conversation_text}

TEXTO DEL ASESOR (para revisar ortografía):
{user_texts}

NPS - ANÁLISIS DE SENTIMIENTO DEL CLIENTE (escala 0-10):
El NPS se determina desde la PERSPECTIVA DEL CLIENTE. Sé generoso con la evaluación:
si el cliente fue atendido con un esfuerzo razonable y obtuvo respuesta a su necesidad, NPS alto.
- NPS 9-10 (Promotor): El cliente se sintió escuchado y atendido con calidez. Su problema fue abordado.
- NPS 7-8 (Promotor leve): Buena experiencia general, bien atendido aunque algún detalle podría mejorar.
- NPS 5-6 (Pasivo): Atendido correctamente pero sin nada destacable. Experiencia neutra.
- NPS 3-4 (Detractor leve): Respuestas frías o genéricas; el cliente sintió que no abordaron su necesidad.
- NPS 0-2 (Detractor): El cliente se sintió ignorado o mal atendido. Solo aplicar en casos claros.

EMPATÍA — RÚBRICA JERÁRQUICA (evaluá EN ORDEN, cada paso vale):
Esta rúbrica define cómo medimos la empatía. Mencionalas en el feedback.
1. NOMBRE: ¿El asesor mencionó el nombre del cliente al menos una vez?
2. CONTEXTO: ¿Demostró comprender el problema del cliente (parafrasear, reconocer la situación)?
3. CALIDEZ: ¿Usó un tono amable, humano, o emojis adecuados (no robótico ni cortante)?
4. RESOLUCIÓN: ¿Se enfocó genuinamente en ayudar al cliente, no en recitar un speech?
Una conversación con los 4 pasos cumplidos es alta empatía. Faltar alguno no es necesariamente
catastrófico — el orden indica prioridad: la resolución y la calidez pesan más que recitar el nombre.

Factores que MEJORAN el NPS:
- Saludo personalizado, uso del nombre del cliente
- Comprensión del problema (parafraseo, reconocimiento)
- Tono cálido y humano
- Respuestas útiles y orientadas a resolver
- Despedida amable

Factores que BAJAN el NPS:
- Respuestas frías, robóticas o tipo speech
- Ignorar lo que el cliente dice
- No ofrecer soluciones concretas
- Monosílabos sistemáticos
- Falta de calidez ante una situación delicada

CRITERIO DE CORRECCIÓN (response_correct):
- true: El asesor cubrió la esencia del procedimiento esperado. NO requiere texto literal; basta con
  abordar la idea principal. Sé permisivo: si la solución es razonable y resuelve el problema, true.
- false: El asesor ignoró el procedimiento o falló en abordar el caso.

ORTOGRAFÍA — REGLAS LENIENTES (importante):
- NO contar como errores: tildes omitidas, mayúsculas iniciales en chat informal, abreviaciones
  comunes (xq, q, tmb, pq, gracias→graxs), uso de emojis, falta de signos de apertura ¿¡, errores
  tipográficos menores que NO afectan la comprensión.
- SÍ contar como error: solo faltas que CAMBIAN EL SIGNIFICADO o IMPIDEN ENTENDER el mensaje
  (palabras mal escritas que cambian el sentido, conjugaciones incorrectas que confunden, frases
  ilegibles). Si la duda es razonable, NO lo contés.
- En la mayoría de chats bien escritos el resultado debe ser 0. Solo subir el conteo cuando
  hay errores que un cliente real notaría con molestia.

Respondé EXACTAMENTE en este formato JSON (sin markdown, solo JSON puro):
{{
    "nps_score": <número del 0 al 10>,
    "response_correct": <true o false>,
    "spelling_errors": <número de errores ortográficos que afectan la comprensión>,
    "empathy_breakdown": {{
        "nombre": <true o false: ¿mencionó el nombre del cliente?>,
        "contexto": <true o false: ¿demostró comprender el problema?>,
        "calidez": <true o false: ¿tono amable/humano/emojis adecuados?>,
        "resolucion": <true o false: ¿se enfocó en ayudar más que recitar speech?>
    }},
    "feedback": "<retroalimentación constructiva desde la perspectiva del cliente, mencionando los 4 pilares de empatía>",
    "strengths": "<2-3 fortalezas observadas>",
    "improvements": "<2-3 áreas de mejora concretas>"
}}"""

    eval_messages = [
        {'role': 'system', 'content': 'Eres un analista de experiencia del cliente (CX). Tu rol es evaluar conversaciones de atención poniéndote en el lugar del cliente. Analizás el sentimiento del cliente a lo largo de la conversación: ¿se sintió escuchado? ¿atendido con empatía? ¿le resolvieron su necesidad? El NPS refleja cómo se fue el cliente, no la perfección técnica del asesor. Aplicás a cualquier industria: banca, telefonía, seguros, servicios generales, etc.'},
        {'role': 'user', 'content': eval_prompt}
    ]

    eval_response, eval_tokens = call_openai(eval_messages)
    session.tokens_used = (session.tokens_used or 0) + eval_tokens

    # Parse evaluation
    try:
        # Clean possible markdown wrapping
        clean = eval_response.strip()
        if clean.startswith('```'):
            clean = re.sub(r'^```\w*\n?', '', clean)
            clean = re.sub(r'\n?```$', '', clean)
        evaluation = json.loads(clean)

        session.nps_score = max(0, min(10, int(evaluation.get('nps_score', 5))))
        session.response_correct = evaluation.get('response_correct', False)
        session.spelling_errors = int(evaluation.get('spelling_errors', 0))
        session.ai_feedback = json.dumps({
            'feedback': evaluation.get('feedback', ''),
            'strengths': evaluation.get('strengths', ''),
            'improvements': evaluation.get('improvements', ''),
            'empathy_breakdown': evaluation.get('empathy_breakdown', {})
        }, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[TRAINING] Eval parse error: {e}", flush=True)
        session.nps_score = 5
        session.response_correct = False
        session.spelling_errors = 0
        session.ai_feedback = json.dumps({
            'feedback': eval_response,
            'strengths': '',
            'improvements': ''
        }, ensure_ascii=False)

    session.status = 'completed'
    db.session.commit()

    # Check if this was part of a batch
    batch_complete = False
    batch_id = session.batch_id
    if batch_id:
        batch = TrainingBatch.query.get(batch_id)
        if batch:
            all_sessions = TrainingSession.query.filter_by(batch_id=batch_id).all()
            all_done = all(s.status == 'completed' for s in all_sessions)
            all_spawned = len(all_sessions) >= batch.max_concurrent

            if all_done and all_spawned:
                # Close the batch
                batch.status = 'completed'
                batch.ended_at = datetime.utcnow()
                batch.duration_seconds = int(safe_elapsed(batch.started_at))
                nps_scores = [s.nps_score for s in all_sessions if s.nps_score is not None]
                batch.overall_nps = round(sum(nps_scores) / len(nps_scores), 1) if nps_scores else 0
                correct = sum(1 for s in all_sessions if s.response_correct)
                batch.overall_correct_rate = round(correct / len(all_sessions) * 100, 1)
                batch.tokens_used = sum(s.tokens_used or 0 for s in all_sessions)
                db.session.commit()
                batch_complete = True

                # Auto-update Vex profile
                calculate_vex_profile(current_user.id)
    else:
        # Legacy single session — update vex directly
        calculate_vex_profile(current_user.id)

    return jsonify({
        'ok': True,
        'session_id': session.id,
        'batch_id': batch_id,
        'batch_complete': batch_complete
    })


@training_bp.route('/training/result/<int:session_id>')
@login_required
def result_view(session_id):
    session = TrainingSession.query.filter_by(id=session_id).first_or_404()
    if session.user_id != current_user.id and not current_user.is_superadmin:
        perm = TrainingViewPermission.query.filter_by(supervisor_id=current_user.id).first()
        if not perm:
            flash('No tienes permisos.', 'error')
            return redirect(url_for('index'))
    # If part of batch, show batch result
    if session.batch_id:
        return redirect(url_for('training.batch_result', batch_id=session.batch_id))
    return render_template('training/result.html', session=session)


@training_bp.route('/training/batch/<int:batch_id>/result')
@login_required
def batch_result(batch_id):
    batch = TrainingBatch.query.get_or_404(batch_id)
    if batch.user_id != current_user.id and not current_user.is_superadmin:
        perm = TrainingViewPermission.query.filter_by(supervisor_id=current_user.id).first()
        if not perm:
            flash('No tienes permisos.', 'error')
            return redirect(url_for('index'))
    sessions = TrainingSession.query.filter_by(batch_id=batch_id).order_by(TrainingSession.interaction_number).all()
    return render_template('training/batch_result.html', batch=batch, sessions=sessions)


# ===== Admin Routes =====

@training_bp.route('/admin/training')
@can_view_training
def admin_dashboard():
    return render_template('admin/training_dashboard.html')


@training_bp.route('/admin/training/scenarios')
@coordinador_or_above
def admin_scenarios():
    from scoring_modes import list_modes
    q = TrainingScenario.query
    if not current_user.is_superadmin and current_user.operativa_id:
        q = q.filter_by(operativa_id=current_user.operativa_id)
    scenarios = q.order_by(TrainingScenario.is_active.desc(), TrainingScenario.created_at.desc()).all()
    return render_template('admin/training_scenarios.html',
                           scenarios=scenarios,
                           scoring_modes=list_modes())


@training_bp.route('/admin/training/scenarios/save', methods=['POST'])
@coordinador_or_above
def admin_scenario_save():
    s_id = request.form.get('id')
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    client_persona = request.form.get('client_persona', '').strip()
    expected_response = request.form.get('expected_response', '').strip()
    difficulty = request.form.get('difficulty', 'medio')
    category = request.form.get('category', '').strip()
    scoring_mode = request.form.get('scoring_mode', 'standard')
    if scoring_mode not in ('flexible', 'standard', 'exigente'):
        scoring_mode = 'standard'

    if not title or not client_persona or not expected_response:
        flash('Título, persona del cliente y respuesta esperada son obligatorios.', 'error')
        return redirect(url_for('training.admin_scenarios'))

    if s_id:
        s = TrainingScenario.query.get_or_404(int(s_id))
        s.title = title
        s.description = description
        s.client_persona = client_persona
        s.expected_response = expected_response
        s.difficulty = difficulty
        s.category = category
        s.scoring_mode = scoring_mode
    else:
        s = TrainingScenario(
            title=title, description=description,
            client_persona=client_persona, expected_response=expected_response,
            difficulty=difficulty, category=category,
            scoring_mode=scoring_mode,
            created_by=current_user.id,
            operativa_id=current_user.operativa_id
        )
        db.session.add(s)

    db.session.commit()
    flash('Escenario guardado.', 'success')
    return redirect(url_for('training.admin_scenarios'))


@training_bp.route('/admin/training/scenarios/<int:s_id>/delete', methods=['POST'])
@coordinador_or_above
def admin_scenario_delete(s_id):
    s = TrainingScenario.query.get_or_404(s_id)
    s.is_active = False
    db.session.commit()
    flash('Escenario ocultado.', 'success')
    return redirect(url_for('training.admin_scenarios'))


@training_bp.route('/admin/training/scenarios/<int:s_id>/toggle', methods=['POST'])
@coordinador_or_above
def admin_scenario_toggle(s_id):
    s = TrainingScenario.query.get_or_404(s_id)
    action = request.form.get('action', 'hide')
    s.is_active = (action == 'show')
    db.session.commit()
    flash('Escenario ' + ('visible' if s.is_active else 'ocultado') + '.', 'success')
    return redirect(url_for('training.admin_scenarios'))


@training_bp.route('/admin/api/training/enhance', methods=['POST'])
@coordinador_or_above
def enhance_text():
    """Use AI to improve a scenario instruction text."""
    data = request.get_json(silent=True) or {}
    text = data.get('text', '').strip()
    field_type = data.get('type', 'persona')  # 'persona' or 'response'

    if not text or len(text) < 10:
        return jsonify({'error': 'Texto muy corto para mejorar'}), 400

    if field_type == 'persona':
        prompt = f"""Mejora la siguiente instrucción para un simulador de cliente de IA en un entrenamiento de atención al cliente.
Debe ser más detallada, incluir nombre del cliente (INVENTÁ un nombre realista y variado, NUNCA uses "Juan Pérez"), estado emocional claro, datos específicos ficticios (documento con números aleatorios, número de cuenta/línea ficticio, etc.), y situación concreta.
Usá nombres diversos y creativos. Mantené el español natural.

TEXTO ORIGINAL:
{text}

Devolvé SOLO el texto mejorado, sin explicaciones."""
    else:
        prompt = f"""Mejora la siguiente descripción de respuesta esperada para evaluar a un asesor de atención al cliente.
Debe incluir pasos claros y numerados que el asesor debe seguir, criterios específicos de resolución, y qué información debe verificar.

TEXTO ORIGINAL:
{text}

Devolvé SOLO el texto mejorado, sin explicaciones."""

    messages = [
        {'role': 'system', 'content': 'Sos un experto en diseño de escenarios de entrenamiento para contact centers y atención al cliente en general.'},
        {'role': 'user', 'content': prompt}
    ]
    result, tokens = call_openai(messages)
    return jsonify({'enhanced': result, 'tokens': tokens})


@training_bp.route('/admin/training/permissions', methods=['POST'])
@coordinador_or_above
def admin_permissions():
    supervisor_id = request.form.get('supervisor_id')
    action = request.form.get('action', 'grant')

    if action == 'revoke':
        TrainingViewPermission.query.filter_by(supervisor_id=int(supervisor_id)).delete()
    else:
        existing = TrainingViewPermission.query.filter_by(supervisor_id=int(supervisor_id)).first()
        if not existing:
            perm = TrainingViewPermission(
                supervisor_id=int(supervisor_id),
                granted_by=current_user.id
            )
            db.session.add(perm)
    db.session.commit()
    flash('Permisos actualizados.', 'success')
    return redirect(url_for('training.admin_dashboard'))


@training_bp.route('/admin/api/training/insights')
@can_view_training
def api_training_insights():
    from sqlalchemy import func, cast, Date
    from datetime import timedelta

    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')

    if not date_from:
        dt_from = datetime(2020, 1, 1, tzinfo=timezone.utc)
    else:
        dt_from = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)

    if not date_to:
        dt_to = datetime.now(timezone.utc)
    else:
        dt_to = datetime.strptime(date_to, '%Y-%m-%d').replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)

    sessions_q = TrainingSession.query.filter(
        TrainingSession.status == 'completed',
        TrainingSession.created_at.between(dt_from, dt_to)
    )
    if not current_user.is_superadmin and current_user.role == 'coordinador' and current_user.operativa_id:
        operativa_user_ids = [u.id for u in User.query.filter_by(operativa_id=current_user.operativa_id).all()]
        sessions_q = sessions_q.filter(TrainingSession.user_id.in_(operativa_user_ids))
    sessions = sessions_q.all()

    total = len(sessions)
    avg_nps = sum(s.nps_score or 0 for s in sessions) / total if total else 0
    correct_rate = sum(1 for s in sessions if s.response_correct) / total * 100 if total else 0
    avg_wpm = sum(s.words_per_minute or 0 for s in sessions) / total if total else 0
    avg_duration = sum(s.duration_seconds or 0 for s in sessions) / total if total else 0

    # NPS per day
    nps_day_q = db.session.query(
        cast(TrainingSession.created_at, Date).label('date'),
        func.avg(TrainingSession.nps_score).label('avg_nps'),
        func.count(TrainingSession.id).label('count')
    ).filter(
        TrainingSession.status == 'completed',
        TrainingSession.created_at.between(dt_from, dt_to)
    )
    if not current_user.is_superadmin and current_user.role == 'coordinador' and current_user.operativa_id:
        nps_day_q = nps_day_q.filter(TrainingSession.user_id.in_(operativa_user_ids))
    nps_day = nps_day_q.group_by('date').order_by('date').all()

    # NPS distribution
    nps_dist = {i: 0 for i in range(11)}
    for s in sessions:
        if s.nps_score is not None:
            nps_dist[s.nps_score] = nps_dist.get(s.nps_score, 0) + 1

    # User rankings
    user_stats = {}
    for s in sessions:
        uid = s.user_id
        if uid not in user_stats:
            user_stats[uid] = {'name': s.user.name, 'role': s.user.role,
                               'sessions': 0, 'total_nps': 0, 'total_wpm': 0, 'correct': 0}
        user_stats[uid]['sessions'] += 1
        user_stats[uid]['total_nps'] += (s.nps_score or 0)
        user_stats[uid]['total_wpm'] += (s.words_per_minute or 0)
        if s.response_correct:
            user_stats[uid]['correct'] += 1

    rankings = []
    for uid, st in user_stats.items():
        rankings.append({
            'name': st['name'], 'role': st['role'],
            'sessions': st['sessions'],
            'avg_nps': round(st['total_nps'] / st['sessions'], 1),
            'avg_wpm': round(st['total_wpm'] / st['sessions'], 1),
            'correct_rate': round(st['correct'] / st['sessions'] * 100)
        })
    rankings.sort(key=lambda x: x['avg_nps'], reverse=True)

    # Scenario stats
    scenario_stats = {}
    for s in sessions:
        sid = s.scenario_id
        if sid not in scenario_stats:
            scenario_stats[sid] = {'title': s.scenario.title, 'difficulty': s.scenario.difficulty,
                                   'sessions': 0, 'total_nps': 0}
        scenario_stats[sid]['sessions'] += 1
        scenario_stats[sid]['total_nps'] += (s.nps_score or 0)

    scenarios_data = []
    for sid, st in scenario_stats.items():
        scenarios_data.append({
            'title': st['title'], 'difficulty': st['difficulty'],
            'sessions': st['sessions'],
            'avg_nps': round(st['total_nps'] / st['sessions'], 1)
        })

    # Recommendations
    recommendations = []
    if rankings:
        worst = min(rankings, key=lambda x: x['avg_nps'])
        if worst['avg_nps'] < 7 and worst['sessions'] >= 2:
            recommendations.append({
                'icon': '🎓', 'priority': 'alta',
                'title': f'{worst["name"]} necesita refuerzo',
                'desc': f'NPS promedio de {worst["avg_nps"]}/10 en {worst["sessions"]} sesiones. Programar capacitación personalizada.'
            })
        best = max(rankings, key=lambda x: x['avg_nps'])
        if best['avg_nps'] >= 9 and best['sessions'] >= 2:
            recommendations.append({
                'icon': '⭐', 'priority': 'info',
                'title': f'{best["name"]} es referente',
                'desc': f'NPS promedio de {best["avg_nps"]}/10. Considerar como mentor para el equipo.'
            })

    low_wpm = [r for r in rankings if r['avg_wpm'] < 10 and r['sessions'] >= 2]
    if low_wpm:
        recommendations.append({
            'icon': '⚡', 'priority': 'media',
            'title': f'{len(low_wpm)} usuario(s) con velocidad baja',
            'desc': 'WPM menor a 10. Practicar velocidad de tipeo y familiarización con procedimientos.'
        })

    if correct_rate < 60 and total >= 3:
        recommendations.append({
            'icon': '📋', 'priority': 'alta',
            'title': f'Tasa de acierto baja: {correct_rate:.0f}%',
            'desc': 'Menos del 60% de respuestas correctas. Revisar los escenarios y reforzar procedimientos.'
        })

    # Permissions
    sup_q = User.query.filter_by(role='supervisor', is_active_user=True)
    if not current_user.is_superadmin and current_user.role == 'coordinador' and current_user.operativa_id:
        sup_q = sup_q.filter_by(operativa_id=current_user.operativa_id)
    supervisors = sup_q.all()
    permissions = TrainingViewPermission.query.all()
    perm_ids = {p.supervisor_id for p in permissions}

    # Recent sessions
    recent_q = TrainingSession.query.filter_by(status='completed')
    if not current_user.is_superadmin and current_user.role == 'coordinador' and current_user.operativa_id:
        recent_q = recent_q.filter(TrainingSession.user_id.in_(operativa_user_ids))
    recent = recent_q.order_by(TrainingSession.ended_at.desc()).limit(20).all()

    return jsonify({
        'stats': {
            'total_sessions': total,
            'avg_nps': round(avg_nps, 1),
            'correct_rate': round(correct_rate, 1),
            'avg_wpm': round(avg_wpm, 1),
            'avg_duration': round(avg_duration)
        },
        'nps_per_day': [{'date': str(d), 'avg_nps': round(float(n), 1), 'count': c} for d, n, c in nps_day],
        'nps_distribution': nps_dist,
        'rankings': rankings,
        'scenarios': scenarios_data,
        'recommendations': recommendations,
        'supervisors': [{'id': s.id, 'name': s.name, 'has_access': s.id in perm_ids} for s in supervisors],
        'recent_sessions': [{
            'id': s.id,
            'user': s.user.name,
            'scenario': s.scenario.title,
            'nps': s.nps_score,
            'wpm': s.words_per_minute,
            'correct': s.response_correct,
            'duration': s.duration_seconds,
            'date': s.ended_at.strftime('%d/%m/%Y %H:%M') if s.ended_at else ''
        } for s in recent]
    })


@training_bp.route('/admin/training/session/<int:session_id>/detail')
@can_view_training
def admin_session_detail(session_id):
    s = TrainingSession.query.get_or_404(session_id)
    feedback = {}
    try:
        feedback = json.loads(s.ai_feedback) if s.ai_feedback else {}
    except json.JSONDecodeError:
        feedback = {'feedback': s.ai_feedback or ''}

    return jsonify({
        'user': s.user.name,
        'scenario': s.scenario.title,
        'nps': s.nps_score,
        'correct': s.response_correct,
        'wpm': s.words_per_minute,
        'duration': s.duration_seconds,
        'spelling_errors': s.spelling_errors,
        'feedback': feedback,
        'messages': [{
            'role': m.role,
            'content': m.content,
            'created_at': m.created_at.strftime('%H:%M:%S') if m.created_at else ''
        } for m in s.messages]
    })


# ===== Live Monitor =====

@training_bp.route('/admin/api/training/live')
@can_view_training
def api_training_live():
    batches_q = TrainingBatch.query.filter_by(status='active')
    if not current_user.is_superadmin and current_user.role == 'coordinador' and current_user.operativa_id:
        op_user_ids = [u.id for u in User.query.filter_by(operativa_id=current_user.operativa_id).all()]
        batches_q = batches_q.filter(TrainingBatch.user_id.in_(op_user_ids))
    batches = batches_q.all()
    result = []
    for b in batches:
        sessions = TrainingSession.query.filter_by(batch_id=b.id).all()
        result.append({
            'batch_id': b.id,
            'user_name': b.user.name if b.user else '-',
            'scenario': b.scenario.title if b.scenario else '-',
            'max_concurrent': b.max_concurrent,
            'interactions_total': len(sessions),
            'interactions_active': sum(1 for s in sessions if s.status == 'active'),
            'interactions_completed': sum(1 for s in sessions if s.status == 'completed'),
            'interactions_pending': b.max_concurrent - len(sessions),
            'elapsed_seconds': int(safe_elapsed(b.started_at))
        })
    return jsonify({'active_batches': result})


# ===== Vex People Skill Predictive =====

def calculate_vex_profile(user_id):
    """Calculate and update VexProfile for a user based on ALL completed sessions.

    El perfil agregado se calcula usando el modo del batch MAS RECIENTE del
    usuario (Flexible/Standard/Exigente). Las dimensiones siguen siendo
    objetivas pero los pisos, la curva ART y los umbrales de categoria/
    recomendacion vienen del modo. Sesiones legacy sin modo -> Standard.
    """
    from scoring_modes import get_effective_mode

    sessions = TrainingSession.query.filter_by(
        user_id=user_id, status='completed'
    ).all()

    if len(sessions) < 2:
        return None  # Minimum 2 sessions required

    # Determinar modo a usar para el perfil: el del batch mas reciente.
    sorted_for_mode = sorted(sessions, key=lambda s: s.created_at or datetime.min, reverse=True)
    latest_session = sorted_for_mode[0]
    latest_batch = TrainingBatch.query.get(latest_session.batch_id) if latest_session.batch_id else None
    latest_mode_name = latest_batch.scoring_mode if latest_batch else None
    _eff_mode_name, _is_legacy, mode_cfg = get_effective_mode(latest_mode_name)
    floors = mode_cfg['floors']
    art_curve = mode_cfg['art_curve']
    pi_weights = mode_cfg['pi_weights']
    thresholds = mode_cfg['thresholds']
    rec_thresholds = mode_cfg['recommendation']
    spell_mult = mode_cfg['spelling_multiplier']
    empathy_pillars_w = mode_cfg['empathy_pillars_weight']

    # --- Raw metric aggregation ---
    total_sessions = len(sessions)
    total_words = sum(s.total_words_user or 0 for s in sessions)
    total_spelling = sum(s.spelling_errors or 0 for s in sessions)
    avg_nps = sum(s.nps_score or 0 for s in sessions) / total_sessions
    avg_wpm = sum(s.words_per_minute or 0 for s in sessions) / total_sessions
    correct_count = sum(1 for s in sessions if s.response_correct)
    correct_rate = correct_count / total_sessions
    # Spelling rate: errores por palabra. Solo contamos errores que afectan
    # comprensión (el prompt de IA ya filtra tildes/abreviaciones).
    spelling_rate = total_spelling / max(total_words, 1)
    total_scenarios = TrainingScenario.query.filter_by(is_active=True).count() or 1

    # Auto-fail / abandono: sesiones donde el asesor practicamente no
    # interactuo. Coincide con el filtro de end_session: <2 mensajes o
    # <8 palabras -> NPS automatico = 1.
    def _is_auto_fail(s):
        return (s.nps_score == 1 and not s.response_correct
                and (s.total_words_user or 0) < 10)
    auto_fail_sessions = [s for s in sessions if _is_auto_fail(s)]
    abandonment_rate = len(auto_fail_sessions) / total_sessions  # 0..1

    # ART agregado. Sesiones auto-fail sin ART real cuentan como respuesta
    # muy lenta (1200s = mas alla del 'lento_max') para reflejar el
    # abandono. Sesiones legacy sin ART y sin auto-fail siguen como neutras.
    art_values = []
    for s in sessions:
        v = s.avg_response_time
        if v and v > 0:
            art_values.append(v)
        elif _is_auto_fail(s):
            # Penalizacion fuerte: equivalente a un ART catastrofico
            art_values.append(1200)
    avg_art = sum(art_values) / len(art_values) if art_values else 0  # segundos

    # Empatia: dividir por TOTAL de sesiones, no por pillar_count.
    # Sesiones sin breakdown (auto-fails o legacy) cuentan como pilares=0.
    # Esto evita que una sola sesion buena infle la empatia cuando las
    # demas fueron abandonos.
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
    # Tasa por pilar SOBRE TOTAL de sesiones (no sobre pillar_count).
    empathy_pillar_rate = {
        k: (v / total_sessions) for k, v in empathy_pillars.items()
    }

    # Variedad efectiva: solo escenarios donde el asesor tuvo exito
    # (response_correct=True OR nps_score>=6). Abandonar 3 escenarios
    # distintos NO suma variedad. Para variedad 100% se requiere AL
    # MENOS 3 escenarios resueltos (o 50% del catalogo, lo que sea mayor).
    successful_scenario_ids = set(
        s.scenario_id for s in sessions
        if s.response_correct or (s.nps_score or 0) >= 6
    )
    unique_scenarios = len(successful_scenario_ids)

    # Improvement trend (NPS slope across sessions ordered by date)
    sorted_sessions = sorted(sessions, key=lambda s: s.created_at or datetime.min)
    nps_values = [s.nps_score or 5 for s in sorted_sessions]
    if len(nps_values) >= 2:
        n = len(nps_values)
        x_mean = (n - 1) / 2
        y_mean = sum(nps_values) / n
        numerator = sum((i - x_mean) * (nps_values[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        slope = numerator / denominator if denominator > 0 else 0
        improvement_trend = max(0, min(1, (slope + 0.5) / 1.0))  # Normalize -0.5..+0.5 to 0..1
    else:
        improvement_trend = 0.5

    # --- Dimension raw scores (0-100) ---
    # Penalizacion ortografica segun modo (multiplicador del modo).
    spelling_penalty = min(spelling_rate * spell_mult, 1)

    # 1. Comunicacion: piso del modo + ortografia + NPS.
    comm_raw = floors['communication'] + (1 - spelling_penalty) * 30 + (avg_nps / 10) * 40

    # 2. Empatia: pilares (Nombre/Contexto/Calidez/Resolucion 15/25/25/35)
    #    mezclados con NPS segun el peso del modo.
    if pillar_count > 0:
        empathy_pillars_score = (
            empathy_pillar_rate['nombre'] * 15 +
            empathy_pillar_rate['contexto'] * 25 +
            empathy_pillar_rate['calidez'] * 25 +
            empathy_pillar_rate['resolucion'] * 35
        )  # 0-100
        empathy_raw = empathy_pillars_score * empathy_pillars_w + (avg_nps * 10) * (1 - empathy_pillars_w)
    else:
        empathy_raw = avg_nps * 10  # fallback legacy

    # 3. Resolucion: piso del modo + correct_rate + NPS.
    resolution_raw = floors['resolution'] + correct_rate * 50 + (avg_nps / 10) * 25

    # 4. Velocidad: curva ART del modo (4 cortes configurables).
    if avg_art <= 0:
        speed_art = art_curve['no_data_score']
    elif avg_art <= art_curve['excellent_max']:
        speed_art = 100
    elif avg_art <= art_curve['healthy_max']:
        span = art_curve['healthy_max'] - art_curve['excellent_max']
        speed_art = 100 - ((avg_art - art_curve['excellent_max']) / max(span, 1)) * 20
    elif avg_art <= art_curve['acceptable_max']:
        span = art_curve['acceptable_max'] - art_curve['healthy_max']
        speed_art = 80 - ((avg_art - art_curve['healthy_max']) / max(span, 1)) * 30
    elif avg_art <= art_curve['slow_max']:
        span = art_curve['slow_max'] - art_curve['acceptable_max']
        speed_art = 50 - ((avg_art - art_curve['acceptable_max']) / max(span, 1)) * 30
    else:
        speed_art = 20

    speed_wpm = min(100, (avg_wpm / 25) * 100) if avg_wpm > 0 else 50
    speed_raw = speed_art * 0.7 + speed_wpm * 0.3

    # 5. Adaptabilidad: piso del modo + tendencia + variedad.
    # Variedad: requiere minimo 3 escenarios resueltos para 100% (o 50%
    # del catalogo, lo que sea mayor). Antes el divisor podia caer a 1
    # cuando habia pocos escenarios y daba variedad maxima con 1 unico
    # exito - bug corregido.
    variety = min(1, unique_scenarios / max(total_scenarios * 0.5, 3))
    adapt_raw = floors['adaptability'] + improvement_trend * 35 + variety * 35

    # 6. Compliance: piso del modo + correct_rate + ortografia.
    compliance_raw = floors['compliance'] + correct_rate * 45 + (1 - spelling_penalty) * 30

    raw_scores = [comm_raw, empathy_raw, resolution_raw, speed_raw, adapt_raw, compliance_raw]

    # --- Convert to Sten scale (1-10) ---
    # Curva más generosa: redondeo hacia arriba para no perder décimas valiosas.
    def to_sten(raw):
        """Convert 0-100 raw score to 1-10 Sten."""
        sten = int(raw / 10) + (1 if (raw % 10) >= 4 else 0)
        return max(1, min(10, sten))

    scores = [to_sten(r) for r in raw_scores]
    comm, empathy, resolution, speed, adapt, compliance = scores

    # --- Overall score (simple average) ---
    overall = round(sum(scores) / 6, 1)

    # --- Predictive Index (pesos del modo) ---
    pi = (
        resolution * pi_weights['resolution'] +
        empathy * pi_weights['empathy'] +
        comm * pi_weights['communication'] +
        speed * pi_weights['speed'] +
        adapt * pi_weights['adaptability'] +
        compliance * pi_weights['compliance']
    )
    pi_pct = round(pi * 10, 1)  # 1-10 -> 10-100%

    # --- Categoria de perfil (umbrales del modo) ---
    if overall >= thresholds['elite_overall'] and all(s >= thresholds['elite_min_dim'] for s in scores):
        category = 'elite'
    elif overall >= thresholds['alto_overall'] and all(s >= thresholds['alto_min_dim'] for s in scores):
        category = 'alto'
    elif overall >= thresholds['desarrollo_overall']:
        category = 'desarrollo'
    else:
        category = 'refuerzo'

    # --- Recomendacion (umbrales del modo) ---
    if pi_pct >= rec_thresholds['recomendado']:
        rec = 'recomendado'
    elif pi_pct >= rec_thresholds['observaciones']:
        rec = 'observaciones'
    else:
        rec = 'no_recomendado'

    # --- HARD CAPS UNIVERSALES (aplican a los 3 modos) ---
    # Reglas de seguridad independientes del modo y los pesos. Detectan
    # casos donde la formula matematica da Recomendado pero la realidad
    # operativa es que el asesor falla la tarea fundamental. Se acumulan
    # en cap_reasons para mostrarlos al usuario en el perfil.
    cap_reasons = []

    # Cap 1: Tasa de abandono > 40% -> muestra no confiable.
    if abandonment_rate > 0.40:
        cap_reasons.append({
            'rule': 'abandonment',
            'detail': f'{int(abandonment_rate*100)}% de sesiones abandonadas (limite 40%)',
            'effect': 'Categoria max "Desarrollo", recomendacion max "Observaciones"'
        })

    # Cap 2: Tasa de aciertos < 50% -> falla la tarea fundamental.
    if correct_rate < 0.50:
        cap_reasons.append({
            'rule': 'low_correct_rate',
            'detail': f'Solo {int(correct_rate*100)}% de respuestas correctas (limite 50%)',
            'effect': 'Recomendacion max "Observaciones"'
        })

    # Cap 3: NPS promedio < 4 -> mayoria de clientes detractores.
    if avg_nps < 4.0:
        cap_reasons.append({
            'rule': 'low_nps',
            'detail': f'NPS promedio {avg_nps:.1f} (limite 4.0). Clientes mayormente detractores.',
            'effect': 'Recomendacion max "Observaciones"'
        })

    # Aplicar caps acumulativos
    if any(c['rule'] == 'abandonment' for c in cap_reasons):
        if category in ('elite', 'alto'):
            category = 'desarrollo'
    if cap_reasons:
        if rec == 'recomendado':
            rec = 'observaciones'

    # --- Save/Update ---
    profile = VexProfile.query.filter_by(user_id=user_id).first()
    if not profile:
        profile = VexProfile(user_id=user_id)
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
    profile.sessions_analyzed = total_sessions
    profile.abandonment_rate = round(abandonment_rate, 3)
    profile.last_updated = datetime.utcnow()
    # Adjuntamos info volatil para el render del perfil. No se persiste
    # en DB - se recalcula con cada llamada a calculate_vex_profile().
    profile._cap_reasons = cap_reasons
    profile._active_mode = _eff_mode_name
    profile._is_legacy_mode = _is_legacy
    profile._mode_cfg = mode_cfg
    db.session.commit()

    return profile


# ===== Vex Routes =====

@training_bp.route('/admin/vex')
@coordinador_or_above
def vex_dashboard():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    q = VexProfile.query.join(User)
    if not current_user.is_superadmin and current_user.role == 'coordinador' and current_user.operativa_id:
        q = q.filter(User.operativa_id == current_user.operativa_id)
    pagination = q.order_by(
        VexProfile.overall_score.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)
    return render_template('admin/vex_dashboard.html', profiles=pagination.items, pagination=pagination)


@training_bp.route('/admin/vex/profile/<int:user_id>')
@coordinador_or_above
def vex_profile(user_id):
    # Recalculate before showing (esto popula profile._cap_reasons,
    # profile._active_mode, profile._mode_cfg en memoria)
    calculate_vex_profile(user_id)
    profile = VexProfile.query.filter_by(user_id=user_id).first_or_404()

    page = request.args.get('page', 1, type=int)
    per_page = 10
    pagination = TrainingSession.query.filter_by(
        user_id=user_id, status='completed'
    ).order_by(TrainingSession.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    # Distribucion de sesiones por modo (recorre los batches del usuario).
    # Para sesiones sin batch o con batch sin scoring_mode -> legacy.
    all_sessions = TrainingSession.query.filter_by(
        user_id=user_id, status='completed'
    ).all()
    mode_counts = {'flexible': 0, 'standard': 0, 'exigente': 0, 'legacy': 0}
    batch_cache = {}
    for s in all_sessions:
        if s.batch_id:
            if s.batch_id not in batch_cache:
                b = TrainingBatch.query.get(s.batch_id)
                batch_cache[s.batch_id] = b.scoring_mode if b else None
            mname = batch_cache[s.batch_id]
        else:
            mname = None
        key = mname if mname in ('flexible', 'standard', 'exigente') else 'legacy'
        mode_counts[key] += 1

    # Recuperar info volatil que dejo calculate_vex_profile
    cap_reasons = getattr(profile, '_cap_reasons', [])
    active_mode = getattr(profile, '_active_mode', 'standard')
    is_legacy_mode = getattr(profile, '_is_legacy_mode', False)
    mode_cfg = getattr(profile, '_mode_cfg', None)
    if mode_cfg is None:
        from scoring_modes import get_mode_config
        mode_cfg = get_mode_config(active_mode)

    return render_template('admin/vex_profile.html',
                           profile=profile,
                           sessions=pagination.items,
                           pagination=pagination,
                           mode_counts=mode_counts,
                           cap_reasons=cap_reasons,
                           active_mode=active_mode,
                           is_legacy_mode=is_legacy_mode,
                           mode_cfg=mode_cfg)


@training_bp.route('/admin/vex/methodology')
@coordinador_or_above
def vex_methodology():
    return render_template('admin/vex_methodology.html')


@training_bp.route('/admin/vex/modos')
@coordinador_or_above
def vex_modos():
    """Vista de los 3 modos de scoring.

    SuperAdmin: editor completo con valores numericos editables.
    Admins/Coordinadores: vista read-only con la guia pedagogica.
    """
    from scoring_modes import (DEFAULT_MODES, MODE_NAMES, PEDAGOGICAL_GUIDE,
                               ADMIN_SUMMARY, get_mode_config)
    is_superadmin = current_user.is_superadmin
    modes_data = []
    for name in MODE_NAMES:
        cfg = get_mode_config(name)
        default = DEFAULT_MODES[name]
        is_overridden = (cfg != default)
        modes_data.append({
            'key': name,
            'config': cfg,
            'is_overridden': is_overridden,
            'summary': ADMIN_SUMMARY[name]
        })
    return render_template('admin/vex_modos.html',
                           modes_data=modes_data,
                           pedagogical_guide=PEDAGOGICAL_GUIDE,
                           is_superadmin=is_superadmin)


@training_bp.route('/admin/vex/modos/save', methods=['POST'])
@superadmin_required
def vex_modos_save():
    """Guarda overrides de un modo. Solo SuperAdmin.

    Todos los valores se ACOTAN al rango seguro antes de persistir. Si el
    usuario manda algo fuera de rango (ej: 150000 en un piso) se clampea
    silenciosamente al maximo permitido. Esto protege el modelo de scoring
    de cualquier input invalido.
    """
    import json as _json
    from models import ScoringModeOverride
    from scoring_modes import DEFAULT_MODES

    mode = request.form.get('mode', '').strip()
    if mode not in DEFAULT_MODES:
        flash('Modo invalido.', 'error')
        return redirect(url_for('training.vex_modos'))

    def _clamp(field, lo, hi, default):
        """Lee un campo del form, lo convierte a float y lo acota a [lo, hi]."""
        raw = request.form.get(field)
        try:
            v = float(raw)
        except (ValueError, TypeError):
            return float(default)
        return max(lo, min(hi, v))

    # --- Pesos PI: cada uno 5%-50%, suma debe ser ~1.00 ---
    weights = {
        'empathy':       _clamp('w_empathy',       0.05, 0.50, 0.25),
        'resolution':    _clamp('w_resolution',    0.05, 0.50, 0.22),
        'communication': _clamp('w_communication', 0.05, 0.50, 0.18),
        'speed':         _clamp('w_speed',         0.05, 0.50, 0.15),
        'adaptability':  _clamp('w_adaptability',  0.05, 0.50, 0.10),
        'compliance':    _clamp('w_compliance',    0.05, 0.50, 0.10),
    }
    total_w = sum(weights.values())
    if abs(total_w - 1.0) > 0.02:
        flash(f'Los pesos del Predictive Index deben sumar 1.00 (ahora suman {total_w:.2f}). Usa el boton "Auto-balancear" o ajusta manualmente.', 'error')
        return redirect(url_for('training.vex_modos'))

    # --- Curva ART: cortes en segundos, cada uno mayor que el anterior ---
    art_excellent  = _clamp('art_excellent',  30,  600, 120)
    art_healthy    = _clamp('art_healthy',    art_excellent + 5, 900, 180)
    art_acceptable = _clamp('art_acceptable', art_healthy + 5,   1200, 300)
    art_slow       = _clamp('art_slow',       art_acceptable + 5, 1800, 600)
    art_no_data    = _clamp('art_no_data',    30, 95, 65)

    # --- Pisos por dimension: 0-60 ---
    floors = {
        'communication': _clamp('floor_communication', 0, 60, 30),
        'resolution':    _clamp('floor_resolution',    0, 60, 25),
        'adaptability':  _clamp('floor_adaptability',  0, 60, 30),
        'compliance':    _clamp('floor_compliance',    0, 60, 25),
        'empathy':       0,
        'speed_no_data': _clamp('floor_speed_no_data', 30, 95, 65)
    }

    # --- Umbrales de categoria: ordenamiento Elite > Alto > Desarrollo ---
    elite_overall      = _clamp('th_elite_overall',      6.0, 10.0, 8.5)
    alto_overall       = _clamp('th_alto_overall',       4.0, elite_overall - 0.3, 6.5)
    desarrollo_overall = _clamp('th_desarrollo_overall', 2.0, alto_overall - 0.3, 4.5)
    elite_min_dim      = _clamp('th_elite_min', 5, 10, 7)
    alto_min_dim       = _clamp('th_alto_min',  3,  9, 4)

    # --- Recomendacion: Recomendado > Observaciones ---
    rec_recomendado   = _clamp('rec_recomendado',   30, 95, 65)
    rec_observaciones = _clamp('rec_observaciones', 10, rec_recomendado - 5, 45)

    # --- Otros ---
    spelling_multiplier    = _clamp('spelling_multiplier',    5, 50, 25)
    empathy_pillars_weight = _clamp('empathy_pillars_weight', 0.0, 1.0, 0.7)

    cfg = {
        'pi_weights': weights,
        'spelling_multiplier': spelling_multiplier,
        'empathy_pillars_weight': empathy_pillars_weight,
        'art_curve': {
            'excellent_max':  art_excellent,
            'healthy_max':    art_healthy,
            'acceptable_max': art_acceptable,
            'slow_max':       art_slow,
            'no_data_score':  art_no_data
        },
        'thresholds': {
            'elite_overall':      elite_overall,
            'elite_min_dim':      elite_min_dim,
            'alto_overall':       alto_overall,
            'alto_min_dim':       alto_min_dim,
            'desarrollo_overall': desarrollo_overall
        },
        'recommendation': {
            'recomendado':   rec_recomendado,
            'observaciones': rec_observaciones
        },
        'floors': floors
    }

    override = ScoringModeOverride.query.filter_by(mode=mode).first()
    if not override:
        override = ScoringModeOverride(mode=mode)
        db.session.add(override)
    override.config_json = _json.dumps(cfg, ensure_ascii=False)
    override.updated_by = current_user.id
    db.session.commit()
    flash(f'Modo "{mode}" actualizado correctamente.', 'success')
    return redirect(url_for('training.vex_modos'))


@training_bp.route('/admin/vex/modos/reset/<mode>', methods=['POST'])
@superadmin_required
def vex_modos_reset(mode):
    """Borra el override de un modo: vuelve al default de fabrica."""
    from models import ScoringModeOverride
    from scoring_modes import DEFAULT_MODES
    if mode not in DEFAULT_MODES:
        flash('Modo invalido.', 'error')
        return redirect(url_for('training.vex_modos'))
    override = ScoringModeOverride.query.filter_by(mode=mode).first()
    if override:
        db.session.delete(override)
        db.session.commit()
    flash(f'Modo "{mode}" restaurado a valores de fabrica.', 'success')
    return redirect(url_for('training.vex_modos'))
