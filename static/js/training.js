// Multi-Chat Training Session Manager
(function() {
    var batchId = window.BATCH_ID;
    var maxConcurrent = window.MAX_CONCURRENT || 1;
    if (!batchId) return;

    // State
    var interactions = {};  // session_id → {number, status, messages[], words, msgs}
    var activeSessionId = null;
    var startTime = Date.now();
    var addInterval = null;
    var spawnedCount = 0;
    // Estado per-sesion para evitar bleed entre chats al cambiar:
    var drafts = {};    // sid -> texto que el usuario estaba escribiendo
    var inFlight = {};  // sid -> bool: hay fetch de mensaje en curso

    // DOM — use train* IDs to avoid conflict with chat widget
    var chatList = document.getElementById('trainList');
    var chatMessages = document.getElementById('trainChatMessages');
    var chatHeader = document.getElementById('trainHeader');
    var chatInput = document.getElementById('trainInput');
    var chatSend = document.getElementById('trainSend');
    var chatEnd = document.getElementById('trainEnd');
    var chatTyping = document.getElementById('trainTyping');
    var emojiBtn = document.getElementById('emojiBtn');
    var emojiPicker = document.getElementById('emojiPicker');

    // Emoji picker
    var emojis = ['😊','😃','😅','😂','🤔','👍','👋','🙏','💪','⭐','✅','❌','📋','🔍','💳','🏦','📞','📧','🔐','💰','⏳','🎯','❤️','🙂','😢','😡','🤝','👏','🔔','📌'];
    if (emojiPicker) {
        emojiPicker.innerHTML = emojis.map(function(e) { return '<span data-emoji="' + e + '">' + e + '</span>'; }).join('');
        emojiPicker.addEventListener('click', function(ev) {
            if (ev.target.dataset.emoji) {
                chatInput.value += ev.target.dataset.emoji;
                chatInput.focus();
            }
        });
    }
    if (emojiBtn) {
        emojiBtn.addEventListener('click', function() {
            emojiPicker.style.display = emojiPicker.style.display === 'none' ? 'flex' : 'none';
        });
        // Close picker when clicking outside
        document.addEventListener('click', function(e) {
            if (!emojiBtn.contains(e.target) && !emojiPicker.contains(e.target)) {
                emojiPicker.style.display = 'none';
            }
        });
    }

    // Initialize from server data
    (window.BATCH_INTERACTIONS || []).forEach(function(i) {
        interactions[i.session_id] = {
            number: i.interaction_number,
            status: i.status,
            messages: i.messages || [],
            words: 0, msgs: 0
        };
        spawnedCount++;
    });

    // Render sidebar
    function renderSidebar() {
        var ids = Object.keys(interactions).sort(function(a,b) {
            return interactions[a].number - interactions[b].number;
        });
        var completed = 0, active = 0;
        chatList.innerHTML = ids.map(function(sid) {
            var i = interactions[sid];
            var isActive = sid == activeSessionId;
            var statusIcon = i.status === 'completed' ? '✅' : '🟠';
            var lastMsg = i.messages.length ? i.messages[i.messages.length-1].content.substring(0, 40) + '...' : '';
            if (i.status === 'completed') completed++;
            else active++;
            return '<div class="chat-list-item ' + (isActive ? 'active' : '') + ' ' + i.status + '" data-sid="' + sid + '">' +
                '<div class="cli-header"><span class="cli-num">' + statusIcon + ' Chat ' + i.number + '</span></div>' +
                '<div class="cli-preview">' + lastMsg + '</div></div>';
        }).join('');

        // Add pending slots
        for (var p = spawnedCount + 1; p <= maxConcurrent; p++) {
            chatList.innerHTML += '<div class="chat-list-item pending"><div class="cli-header"><span class="cli-num">⏳ Chat ' + p + '</span></div><div class="cli-preview">Esperando ingreso...</div></div>';
        }

        // Click handlers
        chatList.querySelectorAll('.chat-list-item[data-sid]').forEach(function(el) {
            el.addEventListener('click', function() { selectChat(el.dataset.sid); });
        });

        // Stats
        document.getElementById('gResolved').textContent = completed + '/' + maxConcurrent;
        document.getElementById('gActive').textContent = active;
        document.getElementById('sidebarStats').innerHTML =
            '<div>✅ ' + completed + ' resueltas</div>' +
            '<div>🟢 ' + active + ' activas</div>' +
            '<div>⏳ ' + (maxConcurrent - spawnedCount) + ' pendientes</div>';

        // Check if all done
        if (completed === maxConcurrent && spawnedCount === maxConcurrent) {
            clearInterval(addInterval);
            chatHeader.textContent = '¡Todas las interacciones completadas!';
            chatMessages.innerHTML = '<div style="text-align:center;padding:40px;color:#888"><h3>Sesión finalizada</h3><p>Redirigiendo a resultados...</p></div>';
            document.getElementById('trainInputArea').style.display = 'none';
            setTimeout(function() {
                window.location.href = '/training/batch/' + batchId + '/result';
            }, 2000);
        }
    }

    function selectChat(sid) {
        // Guardar draft del chat anterior antes de cambiar
        if (activeSessionId && interactions[activeSessionId]) {
            drafts[activeSessionId] = chatInput.value;
        }
        activeSessionId = sid;
        var i = interactions[sid];
        chatHeader.textContent = 'Chat ' + i.number + (i.status === 'completed' ? ' ✅ Completado' : '');
        // Re-render from state (prevents message mixing)
        renderChat(sid);

        // Restaurar draft del chat al que entramos (no bleed entre chats)
        chatInput.value = drafts[sid] || '';
        autoResize();

        // Sincronizar typing indicator: solo activo si HAY in-flight para
        // ESTE chat. Sin esto, el typing de otro chat aparecia aca.
        if (inFlight[sid]) {
            chatTyping.classList.add('active');
        } else {
            chatTyping.classList.remove('active');
        }

        // Sincronizar boton Send: deshabilitado solo si in-flight de ESTE chat
        chatSend.disabled = !!inFlight[sid];

        if (i.status === 'completed') {
            document.getElementById('trainInputArea').style.display = 'none';
        } else {
            document.getElementById('trainInputArea').style.display = 'flex';
            chatInput.focus();
        }
        renderSidebar();
    }

    function buildMsgNode(role, content, images) {
        var div = document.createElement('div');
        div.className = 'training-msg ' + role;
        if (content) {
            var txt = document.createElement('div');
            txt.className = 'tm-text';
            txt.textContent = content;
            div.appendChild(txt);
        }
        // Imagenes que el cliente "envia"
        (images || []).forEach(function(url) {
            var a = document.createElement('a');
            a.href = url; a.target = '_blank'; a.className = 'tm-img-link';
            var img = document.createElement('img');
            img.src = url; img.className = 'tm-img'; img.alt = 'Imagen del cliente';
            a.appendChild(img);
            div.appendChild(a);
        });
        return div;
    }

    function addMsgToDOM(role, content, images) {
        var typing = document.getElementById('trainTyping');
        var div = buildMsgNode(role, content, images);
        // Insert before typing indicator
        if (typing) chatMessages.insertBefore(div, typing);
        else chatMessages.appendChild(div);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function renderChat(sid) {
        /**Re-render the chat area from the interaction state (source of truth).**/
        var i = interactions[sid];
        if (!i) return;
        // Remove all messages but keep typing indicator
        var typing = document.getElementById('trainTyping');
        chatMessages.innerHTML = '';
        if (typing) chatMessages.appendChild(typing);
        // Re-add all messages from state (incluye imagenes)
        i.messages.forEach(function(m) {
            var div = buildMsgNode(m.role, m.content, m.images);
            if (typing) chatMessages.insertBefore(div, typing);
            else chatMessages.appendChild(div);
        });
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    // Auto-resize textarea as user types (wraps text vertically)
    function autoResize() {
        chatInput.style.height = 'auto';
        chatInput.style.height = Math.min(chatInput.scrollHeight, 140) + 'px';
    }
    chatInput.addEventListener('input', autoResize);

    // Send message
    chatSend.addEventListener('click', sendMsg);
    chatInput.addEventListener('keydown', function(e) {
        // Enter envía; Shift+Enter inserta salto de línea manual (opcional)
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMsg(); }
    });

    async function sendMsg() {
        if (!activeSessionId) return;
        var sendingSid = activeSessionId;  // Capture which chat we're sending from
        var i = interactions[sendingSid];
        if (i.status !== 'active') return;
        if (inFlight[sendingSid]) return;  // ya hay un mensaje en curso en este chat
        var text = chatInput.value.trim();
        if (!text) return;

        chatInput.value = '';
        drafts[sendingSid] = '';   // limpiar draft de ESTE chat
        autoResize();
        inFlight[sendingSid] = true;
        // Boton/typing: solo afectar la UI si el usuario sigue en este chat
        if (activeSessionId == sendingSid) {
            chatSend.disabled = true;
            chatTyping.classList.add('active');
        }
        i.messages.push({role: 'user', content: text, images: []});
        if (activeSessionId == sendingSid) {
            addMsgToDOM('user', text, []);
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }

        try {
            var res = await fetch('/api/training/message', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: parseInt(sendingSid), message: text})
            });
            var data = await res.json();
            if (data.response) {
                var imgs = data.images || [];
                i.messages.push({role: 'client', content: data.response, images: imgs});
                // Solo agregar al DOM si el usuario sigue en este chat
                if (activeSessionId == sendingSid) {
                    addMsgToDOM('client', data.response, imgs);
                }
            }
        } catch(e) {
            if (activeSessionId == sendingSid) {
                addMsgToDOM('client', 'Error de conexión.');
            }
        }
        inFlight[sendingSid] = false;
        // Solo restaurar UI (typing off, boton on, focus) si el usuario sigue aca
        if (activeSessionId == sendingSid) {
            chatTyping.classList.remove('active');
            chatSend.disabled = false;
            chatInput.focus();
        }
        renderSidebar();
    }

    // End individual interaction
    chatEnd.addEventListener('click', async function() {
        if (!activeSessionId || interactions[activeSessionId].status !== 'active') return;
        var closingSid = activeSessionId;  // Capture BEFORE confirm/async
        var closingNum = interactions[closingSid].number;
        if (!confirm('¿Cerrar Chat ' + closingNum + '? Se evaluará individualmente.')) return;

        chatEnd.disabled = true;
        chatEnd.textContent = 'Evaluando Chat ' + closingNum + '...';
        chatSend.disabled = true;

        try {
            var res = await fetch('/api/training/end/' + closingSid, {method: 'POST'});
            var data = await res.json();
            if (data.ok) {
                interactions[closingSid].status = 'completed';
                // Only update UI if user is still viewing the closed chat
                if (activeSessionId == closingSid) {
                    document.getElementById('trainInputArea').style.display = 'none';
                    chatHeader.textContent = 'Chat ' + closingNum + ' ✅ Completado';
                }
                renderSidebar();
                // If batch is fully complete, redirect to results
                if (data.batch_complete) {
                    setTimeout(function() {
                        window.location.href = '/training/batch/' + batchId + '/result';
                    }, 1500);
                }
            }
        } catch(e) { alert('Error al cerrar Chat ' + closingNum); }
        chatEnd.disabled = false;
        chatEnd.textContent = 'Cerrar Interacción';
        chatSend.disabled = false;
    });

    // Timer
    setInterval(function() {
        var s = Math.floor((Date.now() - startTime) / 1000);
        document.getElementById('gTimer').textContent = Math.floor(s/60) + ':' + ('0' + s%60).slice(-2);
    }, 1000);

    // Progressive client spawn
    if (maxConcurrent > 1 && spawnedCount < maxConcurrent) {
        addInterval = setInterval(async function() {
            if (spawnedCount >= maxConcurrent) { clearInterval(addInterval); return; }
            try {
                var res = await fetch('/api/training/batch/' + batchId + '/add', {method: 'POST'});
                var data = await res.json();
                if (data.session_id) {
                    spawnedCount++;
                    interactions[data.session_id] = {
                        number: data.interaction_number,
                        status: 'active',
                        messages: [{role: 'client', content: data.first_message, images: data.first_images || []}],
                        words: 0, msgs: 0
                    };
                    renderSidebar();
                    // Notify user
                    var notif = document.createElement('div');
                    notif.style.cssText = 'position:fixed;top:20px;right:20px;background:#E6332A;color:#fff;padding:12px 20px;border-radius:12px;z-index:9999;animation:msgFadeIn 0.3s';
                    notif.textContent = '🔔 Nuevo cliente #' + data.interaction_number + ' ingresó';
                    document.body.appendChild(notif);
                    setTimeout(function() { notif.remove(); }, 3000);
                }
            } catch(e) {}
        }, 20000); // New client every 20 seconds
    }

    // Init: select first active chat
    var firstActive = Object.keys(interactions).find(function(sid) { return interactions[sid].status === 'active'; });
    if (firstActive) selectChat(firstActive);
    renderSidebar();
})();
