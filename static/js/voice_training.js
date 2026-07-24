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
        holdBtn: document.getElementById('voiceHoldBtn'),
        audio: document.getElementById('voiceRemoteAudio'),
        warn: document.getElementById('voiceWarn'),
        silenceWarn: document.getElementById('voiceSilenceWarn')
    };

    // Corte automatico por silencio: si NADIE habla (ni asesor ni cliente)
    // durante SILENCE_CUT_S segundos, la llamada se finaliza sola. El tiempo
    // en espera no cuenta como silencio.
    var SILENCE_WARN_S = 45;
    var SILENCE_CUT_S = 60;
    var lastActivityMs = -1;
    var connected = false;

    function markActivity() { lastActivityMs = nowMs(); }

    // Pausas (cliente en espera): se trackean localmente y se reportan al
    // servidor (incremental al retomar + payload autoritativo en el cierre).
    var holdState = { active: false, startMs: 0, intervals: [] };

    function holdIntervalsForPayload() {
        var list = holdState.intervals.slice();
        if (holdState.active) list.push([holdState.startMs, nowMs()]);
        return list;
    }

    var pc = null, dc = null, micStream = null;
    var connecting = false, ended = false;
    var callStart = 0;          // performance.now() de la conexion vigente
    var elapsedBase = 0;        // ms acumulados de conexiones anteriores (reconexion)
    var usage = { input_tokens: 0, output_tokens: 0 };
    var userSpeech = { start: 0, end: 0 };
    var clientSpeech = { start: 0 };
    var pendingClient = null;   // transcript del cliente esperando el fin del AUDIO
    var clientAudioEndedAt = 0;
    var lastClientText = '';    // ultimo parlamento del cliente (para filtrar eco)
    var micTrack = null;
    var timerInt = null, hbInt = null;

    /* ---- Anti-eco ----
       Sin auriculares, la voz del cliente sale por el parlante, reentra por
       el microfono y el VAD la toma como habla del asesor: la IA "se
       responde sola". Defensas:
       1) constraints de audio (echoCancellation/autoGainControl),
       2) modo parlante (toggle): silencia el mic mientras el cliente habla,
       3) filtro de eco: si el "turno del asesor" es casi identico al ultimo
          parlamento del cliente, se descarta (no ensucia transcript/metricas). */
    var headphonesMode = localStorage.getItem('voice_headphones') !== '0';
    var clientSpeaking = false;   // la IA esta reproduciendo audio ahora

    function updateMicGate() {
        // En espera el mic queda SIEMPRE cerrado. Fuera de espera: en modo
        // parlante cortamos el mic mientras la IA habla (half-duplex, evita
        // eco); con auriculares queda full-duplex (se puede interrumpir).
        if (!micTrack) return;
        if (holdState.active) { micTrack.enabled = false; return; }
        micTrack.enabled = headphonesMode ? true : !clientSpeaking;
    }

    function showEchoNotice(msg) {
        var n = document.getElementById('voiceEchoNotice');
        if (!n) return;
        n.textContent = msg;
        n.style.display = 'block';
    }

    function onEchoDetected() {
        // El eco YA llego a OpenAI (el audio va directo por WebRTC), asi que
        // ademas de limpiar el transcript: 1) cancelamos la auto-respuesta en
        // curso, 2) pasamos AUTOMATICAMENTE a modo parlante para que no se
        // repita, 3) avisamos al asesor.
        try {
            if (dc && dc.readyState === 'open') {
                dc.send(JSON.stringify({ type: 'response.cancel' }));
            }
        } catch (e) {}
        if (headphonesMode) {
            headphonesMode = false;
            localStorage.setItem('voice_headphones', '0');
            var hp = document.getElementById('voiceHeadphones');
            if (hp) hp.checked = false;
            updateMicGate();
            showEchoNotice('🔊 Detectamos eco de parlantes: el cliente se estaba escuchando a si mismo. ' +
                'Activamos el modo parlante automaticamente (tu microfono se silencia mientras el cliente habla). ' +
                'Si usas auriculares, volve a marcar la casilla.');
        }
    }

    function toggleHold() {
        if (ended || !connected) return;
        if (!holdState.active) {
            holdState.active = true;
            holdState.startMs = nowMs();
            if (els.audio) els.audio.muted = true;
            updateMicGate();
            if (els.holdBtn) els.holdBtn.textContent = '▶ Retomar llamada';
            setStatus('hold', 'Cliente en espera');
        } else {
            var endMs = nowMs();
            holdState.intervals.push([holdState.startMs, endMs]);
            holdState.active = false;
            if (els.audio) els.audio.muted = false;
            updateMicGate();
            markActivity();
            if (els.holdBtn) els.holdBtn.textContent = '⏸ Poner en espera';
            setStatus('listening', 'Llamada retomada — segui la conversacion');
            // Registro incremental (el payload del cierre es el autoritativo)
            fetch('/api/voice/hold/' + SESSION_ID, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ started_at_ms: holdState.startMs, ended_at_ms: endMs })
            }).catch(function () {});
        }
    }

    function looksLikeEcho(text) {
        if (!lastClientText) return false;
        var norm = function (s) {
            return s.toLowerCase().replace(/[^a-z0-9áéíóúüñ ]+/gi, ' ').split(/\s+/).filter(Boolean);
        };
        var words = norm(text);
        if (words.length < 3) return false;
        var clientSet = {};
        norm(lastClientText).forEach(function (w) { clientSet[w] = 1; });
        var hits = 0;
        words.forEach(function (w) { if (clientSet[w]) hits++; });
        return (hits / words.length) >= 0.7;
    }

    /* ---- Grabacion de la llamada ----
       Mezclamos el microfono y el audio remoto del cliente en un solo
       stream (Web Audio) y lo grabamos con MediaRecorder. El AudioContext
       vive durante TODA la sesion (sobrevive reconexiones: cada conexion
       nueva vuelve a enchufar sus fuentes al mismo destino). Al finalizar
       se sube el archivo al servidor. Si el navegador no soporta grabar,
       la llamada funciona igual, solo que sin audio guardado. */
    var rec = { ctx: null, dest: null, recorder: null, chunks: [], mime: '', uploaded: false };

    function recEnsure() {
        var AC = window.AudioContext || window.webkitAudioContext;
        if (!AC || !window.MediaRecorder || rec.recorder) return;
        try {
            rec.ctx = new AC();
            rec.dest = rec.ctx.createMediaStreamDestination();
            if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
                rec.mime = 'audio/webm;codecs=opus';
            } else if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported('audio/mp4')) {
                rec.mime = 'audio/mp4';  // Safari
            }
            rec.recorder = rec.mime ? new MediaRecorder(rec.dest.stream, { mimeType: rec.mime })
                                    : new MediaRecorder(rec.dest.stream);
            rec.recorder.ondataavailable = function (e) {
                if (e.data && e.data.size) rec.chunks.push(e.data);
            };
            rec.recorder.start(1000);
        } catch (e) {
            rec.recorder = null;
        }
    }

    function recAttach(stream) {
        if (!rec.ctx || !rec.dest || !stream) return;
        try { rec.ctx.createMediaStreamSource(stream).connect(rec.dest); } catch (e) {}
    }

    function recStopAndUpload() {
        if (!rec.recorder || rec.uploaded) return Promise.resolve();
        rec.uploaded = true;
        return new Promise(function (resolve) {
            var finish = function () {
                var blob = new Blob(rec.chunks, { type: rec.mime || 'audio/webm' });
                if (!blob.size) return resolve();
                var fd = new FormData();
                fd.append('audio', blob, 'llamada.' + (rec.mime.indexOf('mp4') !== -1 ? 'mp4' : 'webm'));
                fetch('/api/voice/recording/' + SESSION_ID, { method: 'POST', body: fd })
                    .catch(function () { /* sin grabacion no se corta el cierre */ })
                    .finally(resolve);
            };
            if (rec.recorder.state === 'inactive') return finish();
            rec.recorder.onstop = finish;
            try { rec.recorder.stop(); } catch (e) { resolve(); }
        });
    }

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

    /* El transcript del cliente llega ANTES de que su audio termine de
       reproducirse (se genera mas rapido de lo que se habla). Si lo
       persistieramos ahi, la duracion del turno quedaria corta y la latencia
       del asesor inflada. Por eso se bufferea y se postea cuando el AUDIO
       termina (output_audio_buffer.stopped/cleared), con dos redes de
       seguridad: si el asesor arranca a hablar, o al finalizar la llamada. */
    function flushClientTurn(endMs) {
        if (!pendingClient) return;
        postTurn('client', pendingClient.text, pendingClient.start, endMs || nowMs());
        pendingClient = null;
    }

    function handleEvent(ev) {
        switch (ev.type) {
            case 'input_audio_buffer.speech_started':
                markActivity();
                flushClientTurn(nowMs());  // barge-in: el cliente quedo interrumpido
                userSpeech.start = nowMs();
                if (!holdState.active) setStatus('speaking', 'Te esta escuchando...');
                break;
            case 'input_audio_buffer.speech_stopped':
                markActivity();
                userSpeech.end = nowMs();
                if (!holdState.active) setStatus('thinking', 'El cliente esta pensando...');
                break;

            case 'conversation.item.input_audio_transcription.completed':
                markActivity();
                var t = (ev.transcript || '').trim();
                if (t) {
                    if (looksLikeEcho(t)) {
                        console.warn('[VOICE] turno descartado por eco:', t);
                        onEchoDetected();
                        break;
                    }
                    addLine('user', t);
                    postTurn('user', t, userSpeech.start, userSpeech.end || nowMs());
                }
                break;

            case 'output_audio_buffer.started':
                markActivity();
                clientSpeech.start = nowMs();
                clientAudioEndedAt = 0;
                clientSpeaking = true;
                updateMicGate();
                if (!holdState.active) setStatus('client', 'El cliente esta hablando...');
                break;
            case 'output_audio_buffer.stopped':
            case 'output_audio_buffer.cleared':
                markActivity();
                clientAudioEndedAt = nowMs();
                clientSpeaking = false;
                updateMicGate();
                flushClientTurn(clientAudioEndedAt);
                if (!holdState.active) setStatus('listening', 'Tu turno — habla con naturalidad');
                break;

            case 'response.output_audio_transcript.done':
                markActivity();
                var ct = (ev.transcript || '').trim();
                if (ct) {
                    addLine('client', ct);
                    lastClientText = ct;
                    pendingClient = { text: ct, start: clientSpeech.start || nowMs() };
                    // Si el audio de esta respuesta ya termino, posteamos ya
                    if (clientAudioEndedAt >= (clientSpeech.start || 0) && clientAudioEndedAt > 0) {
                        flushClientTurn(clientAudioEndedAt);
                    }
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
            if (s >= MAX_SECONDS) { endCall('timeout'); return; }

            if (holdState.active) {
                // El silencio en espera no corta la llamada; mostramos cuanto lleva
                var hs = Math.floor((nowMs() - holdState.startMs) / 1000);
                if (els.statusText) els.statusText.textContent = 'Cliente en espera (' + hs + 's)';
                if (els.silenceWarn) els.silenceWarn.style.display = 'none';
                return;
            }

            // Corte automatico por silencio total
            if (connected && lastActivityMs >= 0) {
                var idle = Math.floor((nowMs() - lastActivityMs) / 1000);
                if (idle >= SILENCE_CUT_S) {
                    endCall('silence');
                } else if (idle >= SILENCE_WARN_S && els.silenceWarn) {
                    els.silenceWarn.textContent = '🔇 Nadie habla hace ' + idle + 's. La llamada se cortara sola a los ' +
                        SILENCE_CUT_S + 's — habla, o pone al cliente en espera.';
                    els.silenceWarn.style.display = 'block';
                } else if (els.silenceWarn) {
                    els.silenceWarn.style.display = 'none';
                }
            }
        }, 1000);
    }

    function stopTimers() {
        if (timerInt) { clearInterval(timerInt); timerInt = null; }
    }

    // Heartbeat desde que la pagina carga (aun sin atender): sin esto, el
    // barrido de abandonadas del servidor mata la sesion mientras el usuario
    // busca los auriculares o espera para reconectar.
    function startHeartbeat() {
        if (hbInt) return;
        hbInt = setInterval(function () {
            fetch('/api/voice/heartbeat/' + SESSION_ID, { method: 'POST' }).catch(function () {});
        }, 30000);
    }

    function stopHeartbeat() {
        if (hbInt) { clearInterval(hbInt); hbInt = null; }
    }

    function teardownConnection() {
        elapsedBase = nowMs();
        callStart = 0;
        connected = false;
        stopTimers();
        try { if (dc) dc.close(); } catch (e) {}
        try { if (pc) pc.close(); } catch (e) {}
        dc = null; pc = null;
        if (micStream) { micStream.getTracks().forEach(function (tr) { tr.stop(); }); micStream = null; }
        micTrack = null;
    }

    function showAnswerButton(label, statusKind, statusText) {
        if (els.answerBtn) {
            els.answerBtn.style.display = 'inline-block';
            els.answerBtn.disabled = false;
            els.answerBtn.textContent = label;
        }
        if (els.endBtn) els.endBtn.style.display = 'none';
        if (els.holdBtn) els.holdBtn.style.display = 'none';
        if (els.silenceWarn) els.silenceWarn.style.display = 'none';
        setStatus(statusKind, statusText);
    }

    function showCallUI() {
        if (els.answerBtn) els.answerBtn.style.display = 'none';
        if (els.endBtn) { els.endBtn.style.display = 'inline-block'; els.endBtn.disabled = false; }
        if (els.holdBtn) els.holdBtn.style.display = 'inline-block';
    }

    function onConnectionLost() {
        if (ended) return;
        // Tambien cubre fallos de ICE DURANTE el connect (post-SDP, pre-datachannel):
        // sin resetear connecting aca, la UI quedaba colgada sin boton.
        connecting = false;
        teardownConnection();
        showAnswerButton('🔁 Reconectar llamada', 'error',
            'Se corto la conexion. Podes reconectar y continuar donde quedaste.');
    }

    function endCall(reason) {
        if (ended) return;
        ended = true;
        flushClientTurn(nowMs());
        var callMs = nowMs();
        var holds = holdIntervalsForPayload();  // incluye una pausa abierta al cortar
        teardownConnection();
        stopHeartbeat();
        setStatus('ended', reason === 'silence'
            ? 'Llamada finalizada por silencio prolongado. Evaluando...'
            : 'Finalizando y evaluando la llamada...');
        if (els.answerBtn) els.answerBtn.style.display = 'none';
        if (els.holdBtn) els.holdBtn.style.display = 'none';
        if (els.silenceWarn) els.silenceWarn.style.display = 'none';
        if (els.endBtn) {
            els.endBtn.style.display = 'inline-block';
            els.endBtn.disabled = true;
            els.endBtn.textContent = 'Evaluando...';
        }
        recStopAndUpload().then(function () {
            return fetch('/api/voice/end/' + SESSION_ID, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ usage: usage, call_ms: callMs, holds: holds, reason: reason || 'user' })
            });
        })
            .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
            .then(function (res) {
                if (!res.ok || res.data.error) {
                    // La evaluacion fallo (p.ej. OpenAI caido): la sesion sigue
                    // activa en el servidor, dejamos reintentar el cierre.
                    ended = false;
                    setStatus('error', res.data.error || 'No se pudo evaluar. Reintenta.');
                    if (els.endBtn) {
                        els.endBtn.disabled = false;
                        els.endBtn.textContent = '🔁 Reintentar finalizar';
                    }
                    return;
                }
                window.location.href = res.data.redirect || '/voice-training';
            })
            .catch(function () {
                ended = false;
                setStatus('error', 'No se pudo finalizar. Verifica tu conexion y reintenta.');
                if (els.endBtn) {
                    els.endBtn.disabled = false;
                    els.endBtn.textContent = '🔁 Reintentar finalizar';
                }
            });
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
                    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
                });
            })
            .then(function (stream) {
                micStream = stream;
                micTrack = stream.getAudioTracks()[0] || null;
                setStatus('connecting', 'Conectando la llamada...');

                recEnsure();
                recAttach(micStream);

                pc = new RTCPeerConnection();
                pc.addTrack(stream.getTracks()[0], stream);
                pc.ontrack = function (e) {
                    if (els.audio) els.audio.srcObject = e.streams[0];
                    recAttach(e.streams[0]);
                };
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
                    connected = true;
                    callStart = performance.now();
                    markActivity();
                    showCallUI();
                    if (holdState.active) {
                        // Reconexion durante una espera: se mantiene el estado
                        updateMicGate();
                        if (els.audio) els.audio.muted = true;
                        setStatus('hold', 'Cliente en espera');
                    } else {
                        setStatus('client', boot.resumed
                            ? 'Llamada retomada — segui la conversacion'
                            : 'Llamada conectada — el cliente va a hablar');
                    }
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
                var msg = (e && e.message) || 'No se pudo conectar la llamada.';
                if (e && (e.name === 'NotAllowedError' || e.name === 'PermissionDeniedError')) {
                    msg = 'Necesitamos acceso al microfono. Habilitalo en el navegador y volve a intentar.';
                } else if (msg.indexOf('Sesion invalida') !== -1) {
                    // La sesion ya no esta activa (expiro o se cerro en otro lado)
                    setStatus('error', 'Esta sesion ya no esta disponible. Volviendo al inicio...');
                    setTimeout(function () { window.location.href = '/voice-training'; }, 2000);
                    return;
                }
                showAnswerButton('🔁 Reintentar', 'error', msg);
            });
    }

    // Toggle auriculares/parlante (persistido). Sin auriculares activamos el
    // modo half-duplex que evita que la IA se escuche a si misma.
    var hpToggle = document.getElementById('voiceHeadphones');
    if (hpToggle) {
        hpToggle.checked = headphonesMode;
        hpToggle.addEventListener('change', function () {
            headphonesMode = hpToggle.checked;
            localStorage.setItem('voice_headphones', headphonesMode ? '1' : '0');
            updateMicGate();
        });
    }

    if (els.answerBtn) els.answerBtn.addEventListener('click', connect);
    if (els.holdBtn) els.holdBtn.addEventListener('click', toggleHold);
    if (els.endBtn) {
        els.endBtn.addEventListener('click', function () {
            if (!ended && confirm('¿Finalizar la llamada? Se evaluara tu desempeño.')) endCall('user');
        });
    }
    window.addEventListener('beforeunload', function () { teardownConnection(); });

    // La sesion queda "viva" para el servidor desde que la pagina abre,
    // aunque el usuario todavia no haya atendido.
    startHeartbeat();
})();
