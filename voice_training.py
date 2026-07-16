"""
Modulo de entrenamiento por VOZ — blueprint independiente.

Hermano del entrenamiento por chat (training.py): reutiliza los escenarios,
los modos de scoring y la filosofia de evaluacion, pero con tablas propias
(voice_sessions/voice_turns) para no alterar las estadisticas ni el indice
predictivo existentes.

El audio viaja navegador<->OpenAI por WebRTC con un token efimero; Flask solo
acuna el token, persiste transcripciones y evalua al cierre.
"""
import json
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from models import db, User, TrainingScenario, VoiceSession, VoiceTurn
from decorators import coordinador_or_above
from training import parse_cases, get_case, safe_elapsed, can_view_training
from chat import call_openai
from scoring_modes import get_effective_mode
import realtime_client
import voice_scoring

voice_bp = Blueprint('voice', __name__)

HEARTBEAT_STALE_MINUTES = 3
MAX_CALL_SECONDS = realtime_client.MAX_CALL_SECONDS


def _sweep_abandoned(user_id=None):
    """Marca abandoned las sesiones activas sin heartbeat reciente."""
    cutoff = datetime.utcnow() - timedelta(minutes=HEARTBEAT_STALE_MINUTES)
    q = VoiceSession.query.filter(VoiceSession.status == 'active')
    if user_id:
        q = q.filter(VoiceSession.user_id == user_id)
    stale = [s for s in q.all()
             if (s.last_heartbeat or s.started_at) and
                (s.last_heartbeat or s.started_at).replace(tzinfo=None) < cutoff]
    for s in stale:
        s.status = 'abandoned'
        s.ended_at = datetime.utcnow()
        s.duration_seconds = int(safe_elapsed(s.started_at))
    if stale:
        db.session.commit()


def _own_active_session():
    return VoiceSession.query.filter_by(user_id=current_user.id, status='active').first()


# ---------- Vistas de usuario ----------

@voice_bp.route('/voice-training')
@login_required
def index():
    _sweep_abandoned(current_user.id)
    q = TrainingScenario.query.filter_by(is_active=True)
    if not current_user.is_superadmin and current_user.operativa_id:
        q = q.filter_by(operativa_id=current_user.operativa_id)
    scenarios = q.order_by(TrainingScenario.created_at.desc()).all()

    my_sessions = (VoiceSession.query
                   .filter_by(user_id=current_user.id)
                   .order_by(VoiceSession.created_at.desc())
                   .limit(20).all())
    active = _own_active_session()
    return render_template('voice/index.html',
                           scenarios=scenarios,
                           my_sessions=my_sessions,
                           active_session=active,
                           voices={v['id']: v['label'] for v in realtime_client.VOICES})


@voice_bp.route('/voice-training/session/<int:vs_id>')
@login_required
def session_view(vs_id):
    vs = VoiceSession.query.get_or_404(vs_id)
    if vs.user_id != current_user.id:
        flash('No tenes acceso a esa sesion.', 'error')
        return redirect(url_for('voice.index'))
    if vs.status != 'active':
        return redirect(url_for('voice.result_view', vs_id=vs.id))
    return render_template('voice/session.html', vs=vs,
                           scenario=vs.scenario,
                           max_call_seconds=MAX_CALL_SECONDS)


@voice_bp.route('/voice-training/result/<int:vs_id>')
@login_required
def result_view(vs_id):
    vs = VoiceSession.query.get_or_404(vs_id)
    if vs.user_id != current_user.id and not current_user.can_manage_users:
        flash('No tenes acceso a esa sesion.', 'error')
        return redirect(url_for('voice.index'))
    feedback = {}
    try:
        feedback = json.loads(vs.ai_feedback) if vs.ai_feedback else {}
    except (json.JSONDecodeError, TypeError):
        feedback = {'feedback': vs.ai_feedback or ''}
    return render_template('voice/result.html', vs=vs, feedback=feedback,
                           turns=vs.turns)


# ---------- API de sesion ----------

VOICE_DAILY_LIMIT = int(__import__('os').environ.get('VOICE_DAILY_LIMIT', '20'))


@voice_bp.route('/api/voice/session/start/<int:scenario_id>', methods=['POST'])
@login_required
def api_start(scenario_id):
    """Crea la sesion en DB y redirige a la pantalla de llamada. El token
    efimero NO se acuna aca sino en api_token, al momento de atender: asi el
    TTL corto nunca expira esperando y la reconexion usa el mismo camino."""
    import random

    _sweep_abandoned(current_user.id)
    if _own_active_session():
        return jsonify({'error': 'Ya tenes una llamada en curso. Finalizala antes de iniciar otra.'}), 400

    # Tope diario por usuario (control de costos)
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = VoiceSession.query.filter(
        VoiceSession.user_id == current_user.id,
        VoiceSession.created_at >= today_start).count()
    if today_count >= VOICE_DAILY_LIMIT:
        return jsonify({'error': f'Alcanzaste el limite de {VOICE_DAILY_LIMIT} llamadas de entrenamiento por dia.'}), 429

    scenario = TrainingScenario.query.get_or_404(scenario_id)
    if not scenario.is_active:
        return jsonify({'error': 'Ese escenario no esta disponible.'}), 400
    if not current_user.is_superadmin and current_user.operativa_id and \
            scenario.operativa_id != current_user.operativa_id:
        return jsonify({'error': 'Ese escenario no pertenece a tu operativa.'}), 403

    cases = parse_cases(scenario)
    case_idx = random.randrange(len(cases)) if cases else 0

    vs = VoiceSession(
        user_id=current_user.id,
        scenario_id=scenario.id,
        case_index=case_idx,
        scoring_mode=scenario.scoring_mode,
        voice_name=realtime_client.valid_voice(scenario.voice_name or realtime_client.DEFAULT_VOICE),
        status='active',
        started_at=datetime.utcnow(),
        last_heartbeat=datetime.utcnow(),
    )
    db.session.add(vs)
    db.session.commit()

    return jsonify({
        'session_id': vs.id,
        'redirect': url_for('voice.session_view', vs_id=vs.id),
    })


@voice_bp.route('/api/voice/session/<int:vs_id>/token', methods=['POST'])
@login_required
def api_token(vs_id):
    """Acuna un token efimero para atender (o RETOMAR) la llamada. Si la
    sesion ya tiene turnos, la nueva sesion Realtime arranca con el contexto
    de lo conversado para continuar donde quedo (la conexion anterior murio
    con la pestana/recarga)."""
    vs = VoiceSession.query.get_or_404(vs_id)
    if vs.user_id != current_user.id or vs.status != 'active':
        return jsonify({'error': 'Sesion invalida.'}), 400

    case = get_case(vs.scenario, vs.case_index or 0)
    instructions = voice_scoring.build_voice_instructions(case, vs.scenario.title)

    prior_turns = VoiceTurn.query.filter_by(session_id=vs.id).order_by(VoiceTurn.started_at_ms).all()
    if prior_turns:
        resumen = '\n'.join(
            f"{'ASESOR' if t.role == 'user' else 'VOS (cliente)'}: {t.transcript}"
            for t in prior_turns[-12:])
        instructions += f"""

═══════════════════════════════════════════════
LA LLAMADA SE CORTO Y SE RETOMA
═══════════════════════════════════════════════
Esto ya se habló antes del corte:
{resumen}

Retomá la llamada con naturalidad ("¿hola? se había cortado...") SIN volver a
presentarte desde cero ni repetir lo ya resuelto."""

    vs.last_heartbeat = datetime.utcnow()
    secret, err = realtime_client.mint_client_secret(instructions, voice=vs.voice_name)
    if err:
        db.session.commit()
        return jsonify({'error': err}), 502

    vs.openai_session_id = secret.get('session_id')
    db.session.commit()

    return jsonify({
        'client_secret': secret['client_secret'],
        'model': secret['model'],
        'expires_at': secret.get('expires_at'),
        'resumed': bool(prior_turns),
    })


@voice_bp.route('/api/voice/turn', methods=['POST'])
@login_required
def api_turn():
    data = request.get_json(silent=True) or {}
    vs = VoiceSession.query.get_or_404(int(data.get('session_id', 0)))
    if vs.user_id != current_user.id or vs.status != 'active':
        return jsonify({'error': 'Sesion invalida.'}), 400

    role = data.get('role')
    transcript = (data.get('transcript') or '').strip()
    if role not in ('user', 'client') or not transcript:
        return jsonify({'error': 'Datos incompletos.'}), 400

    turn = VoiceTurn(
        session_id=vs.id,
        role=role,
        transcript=transcript[:8000],
        started_at_ms=int(data.get('started_at_ms') or 0),
        ended_at_ms=int(data.get('ended_at_ms') or 0),
        word_count=len(transcript.split()),
    )
    db.session.add(turn)
    vs.last_heartbeat = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'turn_id': turn.id})


@voice_bp.route('/api/voice/heartbeat/<int:vs_id>', methods=['POST'])
@login_required
def api_heartbeat(vs_id):
    vs = VoiceSession.query.get_or_404(vs_id)
    if vs.user_id != current_user.id or vs.status != 'active':
        return jsonify({'error': 'Sesion invalida.'}), 400
    vs.last_heartbeat = datetime.utcnow()
    db.session.commit()
    elapsed = int(safe_elapsed(vs.started_at))
    return jsonify({'ok': True, 'elapsed': elapsed, 'max_seconds': MAX_CALL_SECONDS})


@voice_bp.route('/api/voice/end/<int:vs_id>', methods=['POST'])
@login_required
def api_end(vs_id):
    vs = VoiceSession.query.get_or_404(vs_id)
    if vs.user_id != current_user.id:
        return jsonify({'error': 'Sesion invalida.'}), 403
    if vs.status != 'active':
        return jsonify({'ok': True, 'redirect': url_for('voice.result_view', vs_id=vs.id)})

    data = request.get_json(silent=True) or {}

    vs.ended_at = datetime.utcnow()
    vs.duration_seconds = min(int(safe_elapsed(vs.started_at)), MAX_CALL_SECONDS * 2)

    turns = VoiceTurn.query.filter_by(session_id=vs.id).order_by(VoiceTurn.started_at_ms).all()
    metrics = voice_scoring.compute_conversation_metrics(turns)
    for field in ('total_turns', 'total_words_user', 'talk_ratio', 'avg_response_latency',
                  'speech_rate_wpm', 'interruptions', 'long_silences'):
        setattr(vs, field, metrics[field])

    # Tokens de audio: el frontend acumula usage de los eventos response.done;
    # si no llego nada, estimamos por duracion (~10 tokens/seg de audio).
    usage = data.get('usage') or {}
    input_tokens = int(usage.get('input_tokens') or 0)
    output_tokens = int(usage.get('output_tokens') or 0)
    if not input_tokens and not output_tokens:
        client_speech_ms = sum(max(0, (t.ended_at_ms or 0) - (t.started_at_ms or 0))
                               for t in turns if t.role == 'client')
        input_tokens = vs.duration_seconds * 10
        output_tokens = int(client_speech_ms / 1000 * 10)
    vs.tokens_used = input_tokens + output_tokens
    vs.estimated_cost_usd = realtime_client.estimate_cost_usd(input_tokens, output_tokens)

    if voice_scoring.is_auto_fail(metrics):
        result = voice_scoring.AUTO_FAIL_RESULT
    else:
        eff_name, _legacy, eff_cfg = get_effective_mode(vs.scoring_mode)
        case = get_case(vs.scenario, vs.case_index or 0)
        prompt = voice_scoring.build_eval_prompt(
            vs.scenario, case, turns, metrics,
            eff_cfg.get('label', 'Standard'), eff_cfg.get('ai_hint', ''),
            vs.duration_seconds)
        raw, eval_tokens = call_openai([
            {'role': 'system', 'content': voice_scoring.EVAL_SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt},
        ])
        vs.tokens_used += eval_tokens
        result = voice_scoring.parse_eval_response(raw)

    vs.nps_score = result['nps_score']
    vs.response_correct = result['response_correct']
    vs.filler_words = result['filler_words']
    vs.ai_feedback = json.dumps(result['ai_feedback'], ensure_ascii=False)
    vs.status = 'completed'
    db.session.commit()

    return jsonify({'ok': True, 'redirect': url_for('voice.result_view', vs_id=vs.id)})


# ---------- Admin ----------

@voice_bp.route('/admin/voice')
@can_view_training
def admin_dashboard():
    _sweep_abandoned()
    q = VoiceSession.query
    if not current_user.is_superadmin and current_user.operativa_id:
        op_user_ids = [u.id for u in User.query.filter_by(operativa_id=current_user.operativa_id).all()]
        q = q.filter(VoiceSession.user_id.in_(op_user_ids))

    sessions = q.order_by(VoiceSession.created_at.desc()).limit(50).all()
    completed = [s for s in sessions if s.status == 'completed']

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else 0

    stats = {
        'total': len(sessions),
        'completed': len(completed),
        'avg_nps': avg([s.nps_score for s in completed]),
        'correct_rate': round(100 * sum(1 for s in completed if s.response_correct) / len(completed)) if completed else 0,
        'avg_latency': avg([s.avg_response_latency for s in completed]),
        'avg_duration': avg([s.duration_seconds for s in completed]),
        'total_cost': round(sum(s.estimated_cost_usd or 0 for s in sessions), 4),
    }
    return render_template('admin/voice_dashboard.html', sessions=sessions, stats=stats)


@voice_bp.route('/admin/voice/session/<int:vs_id>/detail')
@can_view_training
def admin_session_detail(vs_id):
    vs = VoiceSession.query.get_or_404(vs_id)
    if not current_user.is_superadmin and current_user.operativa_id:
        if not vs.user or vs.user.operativa_id != current_user.operativa_id:
            return jsonify({'error': 'Sin permiso sobre esta sesion.'}), 403
    feedback = {}
    try:
        feedback = json.loads(vs.ai_feedback) if vs.ai_feedback else {}
    except (json.JSONDecodeError, TypeError):
        feedback = {'feedback': vs.ai_feedback or ''}
    return jsonify({
        'id': vs.id,
        'user': vs.user.name if vs.user else '-',
        'scenario': vs.scenario.title if vs.scenario else '-',
        'status': vs.status,
        'voice': vs.voice_name,
        'duration_seconds': vs.duration_seconds,
        'nps_score': vs.nps_score,
        'response_correct': vs.response_correct,
        'filler_words': vs.filler_words,
        'metrics': {
            'total_turns': vs.total_turns,
            'talk_ratio': vs.talk_ratio,
            'avg_response_latency': vs.avg_response_latency,
            'speech_rate_wpm': vs.speech_rate_wpm,
            'interruptions': vs.interruptions,
            'long_silences': vs.long_silences,
        },
        'feedback': feedback,
        'estimated_cost_usd': vs.estimated_cost_usd,
        'turns': [{'role': t.role, 'transcript': t.transcript,
                   'started_at_ms': t.started_at_ms} for t in vs.turns],
    })
