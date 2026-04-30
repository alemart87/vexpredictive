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
        activeSessionId = sid;
        var i = interactions[sid];
        chatHeader.textContent = 'Chat ' + i.number + (i.status === 'completed' ? ' ✅ Completado' : '');
        // Re-render from state (prevents message mixing)
        renderChat(sid);

        if (i.status === 'completed') {
            document.getElementById('trainInputArea').style.display = 'none';
        } else {
            document.getElementById('trainInputArea').style.display = 'flex';
            chatInput.focus();
        }
        renderSidebar();
    }

    function addMsgToDOM(role, content) {
        var typing = document.getElementById('trainTyping');
        var div = document.createElement('div');
        div.className = 'training-msg ' + role;
        div.textContent = content;
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
        // Re-add all messages from state
        i.messages.forEach(function(m) {
            var div = document.createElement('div');
            div.className = 'training-msg ' + m.role;
            div.textContent = m.content;
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
        var text = chatInput.value.trim();
        if (!text) return;

        chatInput.value = '';
        autoResize();
        chatSend.disabled = true;
        i.messages.push({role: 'user', content: text});
        addMsgToDOM('user', text);
        chatTyping.classList.add('active');
        chatMessages.scrollTop = chatMessages.scrollHeight;

        try {
            var res = await fetch('/api/training/message', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: parseInt(sendingSid), message: text})
            });
            var data = await res.json();
            chatTyping.classList.remove('active');
            if (data.response) {
                i.messages.push({role: 'client', content: data.response});
                // Only add to DOM if this chat is still the active one
                if (activeSessionId == sendingSid) {
                    addMsgToDOM('client', data.response);
                } else {
                    // User switched chats; sidebar preview will update
                    renderSidebar();
                }
            }
        } catch(e) {
            chatTyping.classList.remove('active');
            if (activeSessionId == sendingSid) {
                addMsgToDOM('client', 'Error de conexión.');
            }
        }
        chatSend.disabled = false;
        if (activeSessionId == sendingSid) chatInput.focus();
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
                        messages: [{role: 'client', content: data.first_message}],
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
