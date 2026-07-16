/* Entrenamiento por Voz — WebRTC directo navegador <-> OpenAI Realtime.
   El backend solo acuna el token efimero; el audio no pasa por Flask.
   Namespace propio (voice*) para no chocar con training.js ni chat.js. */
(function () {
    'use strict';

    // ---------- Pagina INDEX: iniciar llamada ----------
    document.querySelectorAll('.voice-start-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var scenarioId = btn.getAttribute('data-scenario-id');
            btn.disabled = true;
            btn.textContent = 'Conectando...';
            fetch('/api/voice/session/start/' + scenarioId, { method: 'POST' })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.error) throw new Error(data.error);
                    sessionStorage.setItem('voice_boot_' + data.session_id, JSON.stringify({
                        secret: data.client_secret, model: data.model
                    }));
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
        endBtn: document.getElementById('voiceEndBtn'),
        audio: document.getElementById('voiceRemoteAudio'),
        warn: document.getElementById('voiceWarn')
    };

    var pc = null, dc = null, micStream = null;
    var callStart = 0;              // performance.now() al conectar
    var ended = false;
    var usage = { input_tokens: 0, output_tokens: 0 };
    var userSpeech = { start: 0, end: 0 };   // ms relativos, del VAD
    var clientSpeech = { start: 0 };         // inicio del audio del cliente
    var timerInt = null, hbInt = null;

    function nowMs() { return callStart ? Math.round(performance.now() - callStart) : 0; }

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
            // --- VAD del microfono del asesor ---
            case 'input_audio_buffer.speech_started':
                userSpeech.start = nowMs();
                setStatus('speaking', 'Te esta escuchando...');
                break;
            case 'input_audio_buffer.speech_stopped':
                userSpeech.end = nowMs();
                setStatus('thinking', 'El cliente esta pensando...');
                break;

            // --- Transcripcion de lo que dijo el asesor ---
            case 'conversation.item.input_audio_transcription.completed':
                var t = (ev.transcript || '').trim();
                if (t) {
                    addLine('user', t);
                    postTurn('user', t, userSpeech.start, userSpeech.end || nowMs());
                }
                break;

            // --- Audio del cliente (IA) ---
            case 'output_audio_buffer.started':
                clientSpeech.start = nowMs();
                setStatus('client', 'El cliente esta hablando...');
                break;
            case 'output_audio_buffer.stopped':
            case 'output_audio_buffer.cleared':
                setStatus('listening', 'Tu turno — hablá con naturalidad');
                break;

            // --- Transcripcion de lo que dijo el cliente ---
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
        timerInt = setInterval(function () {
            var s = Math.floor(nowMs() / 1000);
            if (els.timer) {
                var mm = String(Math.floor(s / 60)).padStart(2, '0');
                var ss = String(s % 60).padStart(2, '0');
                els.timer.textContent = mm + ':' + ss;
            }
            if (s >= WARN_SECONDS && els.warn) els.warn.style.display = 'block';
            if (s >= MAX_SECONDS) endCall('Tiempo maximo alcanzado');
        }, 1000);
        hbInt = setInterval(function () {
            fetch('/api/voice/heartbeat/' + SESSION_ID, { method: 'POST' }).catch(function () {});
        }, 30000);
    }

    function cleanup() {
        if (timerInt) clearInterval(timerInt);
        if (hbInt) clearInterval(hbInt);
        try { if (dc) dc.close(); } catch (e) {}
        try { if (pc) pc.close(); } catch (e) {}
        if (micStream) micStream.getTracks().forEach(function (tr) { tr.stop(); });
    }

    function endCall(reason) {
        if (ended) return;
        ended = true;
        cleanup();
        setStatus('ended', 'Finalizando y evaluando la llamada...');
        if (els.endBtn) { els.endBtn.disabled = true; els.endBtn.textContent = 'Evaluando...'; }
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

    function fatal(msg) {
        setStatus('error', msg);
        if (els.endBtn) els.endBtn.textContent = 'Volver';
        ended = true;
        cleanup();
        if (els.endBtn) els.endBtn.onclick = function () {
            fetch('/api/voice/end/' + SESSION_ID, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ usage: usage, reason: 'error' })
            }).finally(function () { window.location.href = '/voice-training'; });
        };
    }

    function connect() {
        var bootRaw = sessionStorage.getItem('voice_boot_' + SESSION_ID);
        if (!bootRaw) {
            fatal('La sesion de conexion expiro (¿recargaste la pagina?). Finalizá y volvé a iniciar la llamada.');
            return;
        }
        var boot = JSON.parse(bootRaw);
        sessionStorage.removeItem('voice_boot_' + SESSION_ID);

        setStatus('connecting', 'Pidiendo acceso al microfono...');
        navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
            .then(function (stream) {
                micStream = stream;
                setStatus('connecting', 'Conectando la llamada...');

                pc = new RTCPeerConnection();
                pc.addTrack(stream.getTracks()[0], stream);
                pc.ontrack = function (e) { if (els.audio) els.audio.srcObject = e.streams[0]; };
                pc.onconnectionstatechange = function () {
                    if (!ended && (pc.connectionState === 'failed' || pc.connectionState === 'disconnected')) {
                        endCall('connection_lost');
                    }
                };

                dc = pc.createDataChannel('oai-events');
                dc.onmessage = function (e) {
                    try { handleEvent(JSON.parse(e.data)); } catch (err) {}
                };
                dc.onopen = function () {
                    callStart = performance.now();
                    setStatus('client', 'Llamada conectada — el cliente va a hablar');
                    startTimer();
                };

                return pc.createOffer()
                    .then(function (offer) { return pc.setLocalDescription(offer); })
                    .then(function () {
                        return fetch('https://api.openai.com/v1/realtime/calls?model=' + encodeURIComponent(boot.model), {
                            method: 'POST',
                            headers: {
                                'Authorization': 'Bearer ' + boot.secret,
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
                if (e && (e.name === 'NotAllowedError' || e.name === 'PermissionDeniedError')) {
                    fatal('Necesitamos acceso al microfono para entrenar por voz. Habilitalo en el navegador y volve a intentar.');
                } else {
                    fatal((e && e.message) || 'No se pudo conectar la llamada.');
                }
            });
    }

    if (els.endBtn) {
        els.endBtn.addEventListener('click', function () {
            if (!ended && confirm('¿Finalizar la llamada? Se evaluara tu desempeño.')) endCall('user');
        });
    }
    window.addEventListener('beforeunload', function () { cleanup(); });

    connect();
})();
