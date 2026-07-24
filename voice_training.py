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
import os
import time
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_, and_
from sqlalchemy.orm import joinedload

from models import db, User, TrainingScenario, VoiceSession, VoiceTurn, VoiceVexProfile
from decorators import supervisor_or_above
from training import parse_cases, get_case, safe_elapsed, can_view_training
from chat import call_openai
from scoring_modes import get_effective_mode
import realtime_client
import voice_scoring

voice_bp = Blueprint('voice', __name__)

HEARTBEAT_STALE_MINUTES = 3
MAX_CALL_SECONDS = realtime_client.MAX_CALL_SECONDS
VOICE_DAILY_LIMIT = int(os.environ.get('VOICE_DAILY_LIMIT', '20'))
RECORDING_RETENTION_DAYS = int(os.environ.get('VOICE_RECORDING_RETENTION_DAYS', '15'))
RECORDING_MAX_BYTES = 15 * 1024 * 1024  # una llamada de 10 min en Opus pesa ~2 MB


def _recordings_dir():
    """Directorio de grabaciones en el disco persistente. Subcarpeta de
    UPLOAD_DIR, pero NUNCA servida por /imagenes/ (app.serve_image la
    bloquea): el audio solo sale por el endpoint autenticado de abajo."""
    base = os.environ.get('VOICE_RECORDINGS_DIR')
    return base or os.path.join(current_app.config['UPLOAD_DIR'], 'voice_recordings')


def _cleanup_expired_recordings():
    """Borra grabaciones con mas de RECORDING_RETENTION_DAYS dias y limpia
    recording_path de esas sesiones. Corre oportunisticamente (sin cron)."""
    d = _recordings_dir()
    if not os.path.isdir(d):
        return
    cutoff_ts = time.time() - RECORDING_RETENTION_DAYS * 86400
    removed = []
    for fn in os.listdir(d):
        p = os.path.join(d, fn)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff_ts:
                os.remove(p)
                removed.append(fn)
        except OSError:
            pass
    if removed:
        VoiceSession.query.filter(VoiceSession.recording_path.in_(removed)) \
            .update({'recording_path': None}, synchronize_session=False)
        db.session.commit()
        print(f'[VOICE] {len(removed)} grabaciones expiradas eliminadas', flush=True)


def _sweep_abandoned(user_id=None):
    """Marca abandoned las sesiones activas sin heartbeat reciente.
    El frontend manda heartbeat desde que la pantalla de llamada carga
    (aun sin atender), asi que solo caen sesiones con pestana cerrada."""
    cutoff = datetime.utcnow() - timedelta(minutes=HEARTBEAT_STALE_MINUTES)
    q = VoiceSession.query.filter(
        VoiceSession.status == 'active',
        or_(VoiceSession.last_heartbeat < cutoff,
            and_(VoiceSession.last_heartbeat.is_(None), VoiceSession.started_at < cutoff)))
    if user_id:
        q = q.filter(VoiceSession.user_id == user_id)
    stale = q.all()
    for s in stale:
        s.status = 'abandoned'
        s.ended_at = datetime.utcnow()
        s.duration_seconds = int(safe_elapsed(s.started_at))
    if stale:
        db.session.commit()


def _own_active_session():
    return VoiceSession.query.filter_by(user_id=current_user.id, status='active').first()


def _feedback_dict(vs):
    """ai_feedback (JSON string) -> dict, con fallback si esta corrupto."""
    try:
        return json.loads(vs.ai_feedback) if vs.ai_feedback else {}
    except (json.JSONDecodeError, TypeError):
        return {'feedback': vs.ai_feedback or ''}


def _can_view_session(vs):
    """Regla unica de acceso a resultados/grabaciones: el dueno, el
    superadmin, o un coordinador de la MISMA operativa."""
    if vs.user_id == current_user.id or current_user.is_superadmin:
        return True
    return (current_user.can_manage_users and vs.user is not None and
            vs.user.operativa_id == current_user.operativa_id)


def _recording_file(vs):
    """Ruta absoluta de la grabacion si existe en disco, o None."""
    if not vs.recording_path:
        return None
    path = os.path.join(_recordings_dir(), vs.recording_path)
    return path if os.path.isfile(path) else None


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
    # Un coordinador solo ve resultados de SU operativa (sin esto se
    # filtrarian transcripts y grabaciones entre tenants)
    if not _can_view_session(vs):
        flash('No tenes acceso a esa sesion.', 'error')
        return redirect(url_for('voice.index'))
    return render_template('voice/result.html', vs=vs, feedback=_feedback_dict(vs),
                           turns=vs.turns,
                           has_recording=_recording_file(vs) is not None,
                           retention_days=RECORDING_RETENTION_DAYS)


# ---------- API de sesion ----------

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
        voice_name=realtime_client.valid_voice(scenario.voice_name),
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

    # Control de jailbreak "por detras": detectamos en ambos sentidos y el
    # navegador re-ancla el personaje via data channel con la correccion.
    resp = {'ok': True}
    if role == 'user' and voice_scoring.detect_jailbreak_attempt(transcript):
        vs.jailbreak_attempts = (vs.jailbreak_attempts or 0) + 1
        resp['jailbreak_detected'] = True
        resp['shield'] = voice_scoring.JAILBREAK_SHIELD
    elif role == 'client' and voice_scoring.detect_role_break(transcript):
        vs.role_breaks = (vs.role_breaks or 0) + 1
        resp['role_break'] = True
        resp['correction'] = voice_scoring.ROLE_BREAK_CORRECTION

    db.session.commit()
    resp['turn_id'] = turn.id
    return jsonify(resp)


@voice_bp.route('/api/voice/hold/<int:vs_id>', methods=['POST'])
@login_required
def api_hold(vs_id):
    """Registra una pausa (cliente en espera) apenas se retoma la llamada.
    Redundante con el payload final de /end (que es autoritativo), pero
    protege el dato si la pestana muere antes de finalizar."""
    vs = VoiceSession.query.get_or_404(vs_id)
    if vs.user_id != current_user.id or vs.status != 'active':
        return jsonify({'error': 'Sesion invalida.'}), 400
    data = request.get_json(silent=True) or {}
    try:
        start_ms = max(0, int(data.get('started_at_ms') or 0))
        end_ms = max(start_ms, int(data.get('ended_at_ms') or 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Datos invalidos.'}), 400

    try:
        intervals = json.loads(vs.holds) if vs.holds else []
    except (json.JSONDecodeError, TypeError):
        intervals = []
    intervals.append([start_ms, end_ms])
    vs.holds = json.dumps(intervals)
    vs.hold_count = len(intervals)
    vs.hold_seconds = int(sum(max(0, h[1] - h[0]) for h in intervals) / 1000)
    vs.last_heartbeat = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'hold_count': vs.hold_count})


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
    turns = VoiceTurn.query.filter_by(session_id=vs.id).order_by(VoiceTurn.started_at_ms).all()

    # Duracion = tiempo de llamada REAL (reloj del cliente o ultimo turno),
    # no el reloj de pared desde que se creo la sesion: el tiempo esperando
    # en "Atender" o entre reconexiones no es llamada ni consume audio.
    wall_seconds = int(safe_elapsed(vs.started_at))
    call_seconds = int((data.get('call_ms') or 0) / 1000)
    if not call_seconds and turns:
        call_seconds = int(max((t.ended_at_ms or 0) for t in turns) / 1000)
    vs.duration_seconds = max(0, min(call_seconds or wall_seconds, wall_seconds, MAX_CALL_SECONDS * 2))

    # Pausas: el payload final del cliente es autoritativo (incluye una
    # pausa abierta al momento de cortar); fallback a lo ya registrado
    # incrementalmente por /api/voice/hold.
    hold_intervals = data.get('holds')
    if isinstance(hold_intervals, list):
        clean = []
        for h in hold_intervals:
            try:
                s, e = max(0, int(h[0])), int(h[1])
                if e > s:
                    clean.append([s, e])
            except (TypeError, ValueError, IndexError):
                continue
        vs.holds = json.dumps(clean) if clean else None
        vs.hold_count = len(clean)
        vs.hold_seconds = int(sum(h[1] - h[0] for h in clean) / 1000)
    try:
        holds_for_metrics = json.loads(vs.holds) if vs.holds else []
    except (json.JSONDecodeError, TypeError):
        holds_for_metrics = []

    metrics = voice_scoring.compute_conversation_metrics(turns, holds=holds_for_metrics)
    metrics['hold_count'] = vs.hold_count or 0
    metrics['hold_seconds'] = vs.hold_seconds or 0
    metrics['jailbreak_attempts'] = vs.jailbreak_attempts or 0
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
        eval_messages = [
            {'role': 'system', 'content': voice_scoring.EVAL_SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt},
        ]
        # Hasta 2 intentos; si la evaluacion no llega (OpenAI caido, JSON roto)
        # NO cerramos la sesion con una nota inventada: queda activa y el
        # frontend puede reintentar el cierre.
        result = None
        for _attempt in range(2):
            raw, eval_tokens = call_openai(eval_messages)
            vs.tokens_used += eval_tokens
            parsed = voice_scoring.parse_eval_response(raw)
            if not parsed.pop('_parse_failed', False):
                result = parsed
                break
        if result is None:
            db.session.rollback()
            return jsonify({'error': 'No se pudo evaluar la llamada en este momento. '
                                     'Reintenta finalizar en unos segundos.'}), 502

    vs.nps_score = result['nps_score']
    vs.response_correct = result['response_correct']
    vs.filler_words = result['filler_words']
    vs.ai_feedback = json.dumps(result['ai_feedback'], ensure_ascii=False)
    vs.status = 'completed'
    db.session.commit()

    # Actualizar el VEX Profile de Voz (nunca rompe el cierre de la llamada)
    try:
        voice_scoring.calculate_voice_vex_profile(vs.user_id)
    except Exception as e:
        print(f'[VOICE] VEX voz calc error: {e}', flush=True)

    return jsonify({'ok': True, 'redirect': url_for('voice.result_view', vs_id=vs.id)})


# ---------- Grabaciones ----------

@voice_bp.route('/api/voice/recording/<int:vs_id>', methods=['POST'])
@login_required
def api_recording_upload(vs_id):
    """Recibe la grabacion mezclada (mic + cliente) que el navegador arma
    con MediaRecorder al finalizar la llamada. Solo el dueno de la sesion."""
    vs = VoiceSession.query.get_or_404(vs_id)
    if vs.user_id != current_user.id:
        return jsonify({'error': 'Sesion invalida.'}), 403
    if vs.status not in ('active', 'completed'):
        return jsonify({'error': 'La sesion ya no admite grabacion.'}), 400

    f = request.files.get('audio')
    if not f:
        return jsonify({'error': 'No se recibio audio.'}), 400

    mimetype = (f.mimetype or '').lower()
    ext = 'mp4' if 'mp4' in mimetype else 'webm'  # Safari graba mp4; el resto webm/opus
    d = _recordings_dir()
    os.makedirs(d, exist_ok=True)
    filename = f'rec_{vs.id}.{ext}'
    path = os.path.join(d, filename)
    f.save(path)

    if os.path.getsize(path) > RECORDING_MAX_BYTES:
        os.remove(path)
        return jsonify({'error': 'Grabacion demasiado grande.'}), 413

    vs.recording_path = filename
    db.session.commit()
    return jsonify({'ok': True})


@voice_bp.route('/api/voice/recording/<int:vs_id>')
@login_required
def api_recording_get(vs_id):
    """Sirve el audio con la MISMA regla de acceso que el resultado.
    Las grabaciones nunca se sirven por /imagenes/ (ruta publica)."""
    vs = VoiceSession.query.get_or_404(vs_id)
    if not _can_view_session(vs):
        return jsonify({'error': 'Sin permiso sobre esta grabacion.'}), 403
    path = _recording_file(vs)
    if not path:
        return jsonify({'error': 'La grabacion no existe o ya expiro.'}), 404
    mimetype = 'audio/mp4' if path.endswith('.mp4') else 'audio/webm'
    # conditional=True habilita Range requests (necesario para adelantar/atrasar)
    return send_file(path, mimetype=mimetype, conditional=True)


# ---------- Admin ----------

@voice_bp.route('/admin/voice')
@can_view_training
def admin_dashboard():
    _sweep_abandoned()
    _cleanup_expired_recordings()
    # joinedload evita el N+1 del template (s.user.name / s.scenario.title)
    q = (VoiceSession.query
         .options(joinedload(VoiceSession.user), joinedload(VoiceSession.scenario)))
    if not current_user.is_superadmin and current_user.operativa_id:
        q = q.join(User, VoiceSession.user_id == User.id) \
             .filter(User.operativa_id == current_user.operativa_id)

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
    feedback = _feedback_dict(vs)
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
            'hold_count': vs.hold_count or 0,
            'hold_seconds': vs.hold_seconds or 0,
            'jailbreak_attempts': vs.jailbreak_attempts or 0,
            'role_breaks': vs.role_breaks or 0,
        },
        'feedback': feedback,
        'estimated_cost_usd': vs.estimated_cost_usd,
        'has_recording': _recording_file(vs) is not None,
        'turns': [{'role': t.role, 'transcript': t.transcript,
                   'started_at_ms': t.started_at_ms} for t in vs.turns],
    })


# ---------- VEX Profile de Voz ----------

@voice_bp.route('/admin/vex-voz')
@supervisor_or_above
def vex_voice_dashboard():
    q = (VoiceVexProfile.query
         .join(User, VoiceVexProfile.user_id == User.id)
         .options(joinedload(VoiceVexProfile.user)))
    if not current_user.is_superadmin and current_user.operativa_id:
        q = q.filter(User.operativa_id == current_user.operativa_id)
    profiles = q.order_by(VoiceVexProfile.overall_score.desc()).all()
    return render_template('admin/vex_voice_dashboard.html', profiles=profiles)


@voice_bp.route('/admin/vex-voz/profile/<int:user_id>')
@supervisor_or_above
def vex_voice_profile(user_id):
    target = User.query.get_or_404(user_id)
    if not current_user.is_superadmin and current_user.operativa_id and \
            target.operativa_id != current_user.operativa_id:
        flash('No tenes permiso sobre ese usuario.', 'error')
        return redirect(url_for('voice.vex_voice_dashboard'))

    # Recalculo al vuelo (mismo criterio que el perfil VEX de chat): asi el
    # detalle siempre refleja las ultimas llamadas y trae los volatiles
    # (_cap_reasons, _active_mode, pilares) para el render.
    profile = voice_scoring.calculate_voice_vex_profile(user_id)
    sessions = (VoiceSession.query.filter_by(user_id=user_id)
                .options(joinedload(VoiceSession.scenario))
                .order_by(VoiceSession.created_at.desc()).limit(20).all())
    return render_template('admin/vex_voice_profile.html',
                           target=target, profile=profile, sessions=sessions,
                           min_sessions=voice_scoring.MIN_SESSIONS_FOR_PROFILE)


@voice_bp.route('/admin/voz/guia')
@supervisor_or_above
def voice_guide():
    """Guia: como configurar casos de voz y como se mide. Documentacion
    estatica para coordinadores y supervisores."""
    return render_template('admin/voice_guide.html',
                           voices=realtime_client.VOICES,
                           max_call_minutes=MAX_CALL_SECONDS // 60,
                           daily_limit=VOICE_DAILY_LIMIT,
                           recording_days=RECORDING_RETENTION_DAYS)
