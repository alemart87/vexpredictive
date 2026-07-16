/* Entrenamiento por Voz — WebRTC directo navegador <-> OpenAI Realtime.
   El backend solo acuna tokens efimeros; el audio no pasa por Flask.
   El token se pide AL ATENDER (gesto del usuario: requisito de autoplay de
   los navegadores) y el mismo camino sirve para reconectar tras un corte.
   Namespace propio (voice*) para no chocar con training.js ni chat.js. */
(function () {
    'use strict';

    // ---------- Pagina INDEX: iniciar llamada ----------
    document.querySelectorAll('.voice-start-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var scenarioId = btn.getAttribute('data-scenario-id');
            btn.disabled = true;
            btn.textContent = 'Creando sesion...';
            fetch('/api/voice/session/start/' + scenarioId, { method: 'POST' })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.error) throw new Error(data.error);
                    window.location.href = data.redirect;
                })
                .catch(function (e) {
                    alert(e.message || 'No se pudo iniciar la llamada.');
                    btn.disabled = false;
                    btn.textContent = '🎙 Iniciar llamada';
                });
        });
    });

    // ---------- Pagina SESSION: la llamada ----------
    var root = document.getElementById('voiceCall');
    if (!root) return;

    var SESSION_ID = window.VOICE_SESSION_ID;
    var MAX_SECONDS = window.VOICE_MAX_SECONDS || 600;
    var WARN_SECONDS = MAX_SECONDS - 120;

    var els = {
        status: document.getElementById('voiceStatus'),
        statusText: document.getElementById('voiceStatusText'),
        timer: document.getElementById('voiceTimer'),
        transcript: document.getElementById('voiceTranscript'),
        answerBtn: document.getElementById('voiceAnswerBtn'),
        endBtn: document.getElementById('voiceEndBtn'),
        audio: document.getElementById('voiceRemoteAudio'),
        warn: document.getElementById('voiceWarn')
    };

    var pc = null, dc = null, micStream = null;
    var connecting = false, ended = false;
    var callStart = 0;          // performance.now() de la conexion vigente
    var elapsedBase = 0;        // ms acumulados de conexiones anteriores (reconexion)
    var usage = { input_tokens: 0, output_tokens: 0 };
    var userSpeech = { start: 0, end: 0 };
    var clientSpeech = { start: 0 };
    var timerInt = null, hbInt = null;

    function nowMs() {
        return elapsedBase + (callStart ? Math.round(performance.now() - callStart) : 0);
    }

    function setStatus(kind, text) {
        if (els.status) els.status.className = 'voice-status voice-status-' + kind;
        if (els.statusText) els.statusText.textContent = text;
    }

    function addLine(role, text) {
        if (!els.transcript || !text) return;
        var div = document.createElement('div');
        div.className = 'voice-line voice-line-' + role;
        var who = document.createElement('span');
        who.className = 'voice-line-who';
        who.textContent = role === 'user' ? 'Vos' : 'Cliente';
        var body = document.createElement('span');
        body.textContent = text;
        div.appendChild(who);
        div.appendChild(body);
        els.transcript.appendChild(div);
        els.transcript.scrollTop = els.transcript.scrollHeight;
    }

    function postTurn(role, transcript, startMs, endMs) {
        if (!transcript) return;
        fetch('/api/voice/turn', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: SESSION_ID, role: role, transcript: transcript,
                started_at_ms: Math.max(0, startMs || 0),
                ended_at_ms: Math.max(0, endMs || 0)
            })
        }).catch(function () { /* si falla un turno no cortamos la llamada */ });
    }

    function handleEvent(ev) {
        switch (ev.type) {
            case 'input_audio_buffer.speech_started':
                userSpeech.start = nowMs();
                setStatus('speaking', 'Te esta escuchando...');
                break;
            case 'input_audio_buffer.speech_stopped':
                userSpeech.end = nowMs();
                setStatus('thinking', 'El cliente esta pensando...');
                break;

            case 'conversation.item.input_audio_transcription.completed':
                var t = (ev.transcript || '').trim();
                if (t) {
                    addLine('user', t);
                    postTurn('user', t, userSpeech.start, userSpeech.end || nowMs());
                }
                break;

            case 'output_audio_buffer.started':
                clientSpeech.start = nowMs();
                setStatus('client', 'El cliente esta hablando...');
                break;
            case 'output_audio_buffer.stopped':
            case 'output_audio_buffer.cleared':
                setStatus('listening', 'Tu turno — habla con naturalidad');
                break;

            case 'response.output_audio_transcript.done':
                var ct = (ev.transcript || '').trim();
                if (ct) {
                    addLine('client', ct);
                    postTurn('client', ct, clientSpeech.start || nowMs(), nowMs());
                }
                break;

            case 'response.done':
                if (ev.response && ev.response.usage) {
                    usage.input_tokens += ev.response.usage.input_tokens || 0;
                    usage.output_tokens += ev.response.usage.output_tokens || 0;
                }
                break;

            case 'error':
                console.error('[VOICE] API error', ev);
                break;
        }
    }

    function startTimer() {
        if (timerInt) return;
        timerInt = setInterval(function () {
            var s = Math.floor(nowMs() / 1000);
            if (els.timer) {
                var mm = String(Math.floor(s / 60)).padStart(2, '0');
                var ss = String(s % 60).padStart(2, '0');
                els.timer.textContent = mm + ':' + ss;
            }
            if (s >= WARN_SECONDS && els.warn) els.warn.style.display = 'block';
            if (s >= MAX_SECONDS) endCall('timeout');
        }, 1000);
        hbInt = setInterval(function () {
            fetch('/api/voice/heartbeat/' + SESSION_ID, { method: 'POST' }).catch(function () {});
        }, 30000);
    }

    function stopTimers() {
        if (timerInt) { clearInterval(timerInt); timerInt = null; }
        if (hbInt) { clearInterval(hbInt); hbInt = null; }
    }

    function teardownConnection() {
        elapsedBase = nowMs();
        callStart = 0;
        stopTimers();
        try { if (dc) dc.close(); } catch (e) {}
        try { if (pc) pc.close(); } catch (e) {}
        dc = null; pc = null;
        if (micStream) { micStream.getTracks().forEach(function (tr) { tr.stop(); }); micStream = null; }
    }

    function showAnswerButton(label, statusKind, statusText) {
        if (els.answerBtn) {
            els.answerBtn.style.display = 'inline-block';
            els.answerBtn.disabled = false;
            els.answerBtn.textContent = label;
        }
        if (els.endBtn) els.endBtn.style.display = 'none';
        setStatus(statusKind, statusText);
    }

    function showCallUI() {
        if (els.answerBtn) els.answerBtn.style.display = 'none';
        if (els.endBtn) { els.endBtn.style.display = 'inline-block'; els.endBtn.disabled = false; }
    }

    function onConnectionLost() {
        if (ended || connecting) return;
        teardownConnection();
        showAnswerButton('🔁 Reconectar llamada', 'error',
            'Se corto la conexion. Podes reconectar y continuar donde quedaste.');
    }

    function endCall(reason) {
        if (ended) return;
        ended = true;
        teardownConnection();
        setStatus('ended', 'Finalizando y evaluando la llamada...');
        if (els.answerBtn) els.answerBtn.style.display = 'none';
        if (els.endBtn) {
            els.endBtn.style.display = 'inline-block';
            els.endBtn.disabled = true;
            els.endBtn.textContent = 'Evaluando...';
        }
        fetch('/api/voice/end/' + SESSION_ID, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ usage: usage, reason: reason || 'user' })
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                window.location.href = data.redirect || '/voice-training';
            })
            .catch(function () { window.location.href = '/voice-training'; });
    }

    function connect() {
        if (connecting || ended) return;
        connecting = true;
        if (els.answerBtn) els.answerBtn.disabled = true;
        setStatus('connecting', 'Preparando la llamada...');

        var boot = null;
        fetch('/api/voice/session/' + SESSION_ID + '/token', { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.error) throw new Error(data.error);
                boot = data;
                setStatus('connecting', 'Pidiendo acceso al microfono...');
                return navigator.mediaDevices.getUserMedia({
                    audio: { echoCancellation: true, noiseSuppression: true }
                });
            })
            .then(function (stream) {
                micStream = stream;
                setStatus('connecting', 'Conectando la llamada...');

                pc = new RTCPeerConnection();
                pc.addTrack(stream.getTracks()[0], stream);
                pc.ontrack = function (e) { if (els.audio) els.audio.srcObject = e.streams[0]; };
                pc.onconnectionstatechange = function () {
                    if (pc && (pc.connectionState === 'failed' || pc.connectionState === 'disconnected')) {
                        onConnectionLost();
                    }
                };

                dc = pc.createDataChannel('oai-events');
                dc.onmessage = function (e) {
                    try { handleEvent(JSON.parse(e.data)); } catch (err) {}
                };
                dc.onopen = function () {
                    connecting = false;
                    callStart = performance.now();
                    showCallUI();
                    setStatus('client', boot.resumed
                        ? 'Llamada retomada — segui la conversacion'
                        : 'Llamada conectada — el cliente va a hablar');
                    startTimer();
                };

                return pc.createOffer()
                    .then(function (offer) { return pc.setLocalDescription(offer); })
                    .then(function () {
                        return fetch('https://api.openai.com/v1/realtime/calls?model=' + encodeURIComponent(boot.model), {
                            method: 'POST',
                            headers: {
                                'Authorization': 'Bearer ' + boot.client_secret,
                                'Content-Type': 'application/sdp'
                            },
                            body: pc.localDescription.sdp
                        });
                    })
                    .then(function (r) {
                        if (!r.ok) throw new Error('No se pudo establecer la llamada (HTTP ' + r.status + ').');
                        return r.text();
                    })
                    .then(function (answerSdp) {
                        return pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
                    });
            })
            .catch(function (e) {
                connecting = false;
                teardownConnection();
                var msg;
                if (e && (e.name === 'NotAllowedError' || e.name === 'PermissionDeniedError')) {
                    msg = 'Necesitamos acceso al microfono. Habilitalo en el navegador y volve a intentar.';
                } else {
                    msg = (e && e.message) || 'No se pudo conectar la llamada.';
                }
                showAnswerButton('🔁 Reintentar', 'error', msg);
            });
    }

    if (els.answerBtn) els.answerBtn.addEventListener('click', connect);
    if (els.endBtn) {
        els.endBtn.addEventListener('click', function () {
            if (!ended && confirm('¿Finalizar la llamada? Se evaluara tu desempeño.')) endCall('user');
        });
    }
    window.addEventListener('beforeunload', function () { teardownConnection(); });
})();
