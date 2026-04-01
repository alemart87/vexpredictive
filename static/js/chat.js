// VEX AI Chat Widget
(function() {
    let currentConvId = null;
    let isOpen = false;
    let convsVisible = false;
    let activeTab = 'chat';

    const panel = document.getElementById('chatPanel');
    const mascot = document.getElementById('chatMascot');
    const messagesEl = document.getElementById('chatMessages');
    const inputEl = document.getElementById('chatInput');
    const sendBtn = document.getElementById('chatSend');
    const typingEl = document.getElementById('chatTyping');
    const convsEl = document.getElementById('chatConvs');
    const newChatBtn = document.getElementById('chatNew');
    const historyBtn = document.getElementById('chatHistory');
    const closeBtn = document.getElementById('chatClose');

    if (!mascot) return;

    var userName = (window.VEX_USER && window.VEX_USER.name) ? window.VEX_USER.name.split(' ')[0] : '';

    // Toggle chat
    mascot.addEventListener('click', function() {
        isOpen = true;
        panel.classList.add('open');
        mascot.classList.add('chat-open');
        if (!currentConvId) showWelcome();
        inputEl.focus();
    });

    closeBtn.addEventListener('click', function() {
        isOpen = false;
        panel.classList.remove('open');
        mascot.classList.remove('chat-open');
    });

    newChatBtn.addEventListener('click', function() {
        currentConvId = null;
        convsEl.classList.remove('open');
        convsVisible = false;
        switchTab('chat');
        showWelcome();
    });

    historyBtn.addEventListener('click', function() {
        convsVisible = !convsVisible;
        if (convsVisible) {
            loadConversations();
            convsEl.classList.add('open');
        } else {
            convsEl.classList.remove('open');
        }
    });

    // Tabs
    document.querySelectorAll('.chat-tab').forEach(function(tab) {
        tab.addEventListener('click', function() {
            switchTab(tab.dataset.tab);
        });
    });

    function switchTab(tabName) {
        activeTab = tabName;
        document.querySelectorAll('.chat-tab').forEach(function(t) {
            t.classList.toggle('active', t.dataset.tab === tabName);
        });
        document.querySelectorAll('.chat-tab-content').forEach(function(c) {
            c.classList.toggle('active', c.dataset.tab === tabName);
        });
        if (tabName === 'stats') loadMyStats();
    }

    // Send
    sendBtn.addEventListener('click', sendMessage);
    inputEl.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    function showWelcome() {
        var greeting = userName ? 'Hola ' + userName + '!' : 'Hola!';
        messagesEl.innerHTML =
            '<div class="chat-welcome">' +
            '<h4>' + greeting + '</h4>' +
            '<p>Soy <strong>VEX AI</strong>, tu asistente virtual inteligente.</p>' +
            '<div class="chat-welcome-topics">' +
            '<div class="chat-welcome-topic" data-q="¿Qué contenidos hay disponibles?"><span class="chat-welcome-topic-icon">&#128218;</span>Contenidos</div>' +
            '<div class="chat-welcome-topic" data-q="¿Cómo accedo a los entrenamientos?"><span class="chat-welcome-topic-icon">&#127919;</span>Entrenamientos</div>' +
            '<div class="chat-welcome-topic" data-q="¿Cómo puedo buscar información?"><span class="chat-welcome-topic-icon">&#128269;</span>Buscar</div>' +
            '<div class="chat-welcome-topic" data-q="Necesito ayuda con la plataforma"><span class="chat-welcome-topic-icon">&#128161;</span>Ayuda</div>' +
            '</div>' +
            '</div>';

        // Quick topic click handlers
        messagesEl.querySelectorAll('.chat-welcome-topic').forEach(function(el) {
            el.addEventListener('click', function() {
                inputEl.value = el.dataset.q;
                sendMessage();
            });
        });
    }

    function addMessage(role, content) {
        var welcome = messagesEl.querySelector('.chat-welcome');
        if (welcome) welcome.remove();

        var div = document.createElement('div');
        div.className = 'chat-msg ' + role;

        var html = content
            .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\n/g, '<br>');
        div.innerHTML = html;

        messagesEl.appendChild(div);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    async function sendMessage() {
        var text = inputEl.value.trim();
        if (!text) return;

        // Switch to chat tab if not there
        if (activeTab !== 'chat') switchTab('chat');

        inputEl.value = '';
        sendBtn.disabled = true;
        addMessage('user', text);

        typingEl.classList.add('active');
        messagesEl.scrollTop = messagesEl.scrollHeight;

        try {
            var res = await fetch('/api/chat/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: text,
                    conversation_id: currentConvId
                })
            });
            var data = await res.json();
            typingEl.classList.remove('active');

            if (data.error) {
                addMessage('assistant', 'Error: ' + data.error);
            } else {
                currentConvId = data.conversation_id;
                addMessage('assistant', data.message);
            }
        } catch (err) {
            typingEl.classList.remove('active');
            addMessage('assistant', 'Error de conexión. Intenta de nuevo.');
        }

        sendBtn.disabled = false;
        inputEl.focus();
    }

    async function loadConversations() {
        try {
            var res = await fetch('/api/chat/conversations');
            var convs = await res.json();

            if (convs.length === 0) {
                convsEl.innerHTML = '<div style="padding:16px;text-align:center;color:#888;font-size:13px">Sin conversaciones previas</div>';
                return;
            }

            convsEl.innerHTML = convs.map(function(c) {
                return '<div class="chat-conv-item" data-id="' + c.id + '">' +
                    '<span class="conv-title">' + escapeHtml(c.title) + '</span>' +
                    '<button class="conv-delete" data-id="' + c.id + '" title="Eliminar">&times;</button>' +
                    '</div>';
            }).join('');

            convsEl.querySelectorAll('.chat-conv-item').forEach(function(el) {
                el.addEventListener('click', function(e) {
                    if (e.target.classList.contains('conv-delete')) return;
                    loadConversation(parseInt(el.dataset.id));
                });
            });

            convsEl.querySelectorAll('.conv-delete').forEach(function(btn) {
                btn.addEventListener('click', async function(e) {
                    e.stopPropagation();
                    if (!confirm('¿Eliminar esta conversación?')) return;
                    await fetch('/api/chat/conversations/' + btn.dataset.id, { method: 'DELETE' });
                    if (currentConvId == btn.dataset.id) {
                        currentConvId = null;
                        showWelcome();
                    }
                    loadConversations();
                });
            });
        } catch (err) {
            convsEl.innerHTML = '<div style="padding:16px;color:#888">Error al cargar</div>';
        }
    }

    async function loadConversation(convId) {
        try {
            var res = await fetch('/api/chat/conversations/' + convId);
            var data = await res.json();
            currentConvId = convId;
            messagesEl.innerHTML = '';
            convsEl.classList.remove('open');
            convsVisible = false;
            data.messages.forEach(function(m) { addMessage(m.role, m.content); });
        } catch (err) {
            addMessage('assistant', 'Error al cargar la conversación.');
        }
    }

    async function loadMyStats() {
        var statsEl = document.getElementById('chatStatsContent');
        if (!statsEl) return;

        try {
            var res = await fetch('/api/chat/my-stats');
            var data = await res.json();

            var statsHtml = '<h4>Tu Actividad</h4>' +
                '<div style="display:flex;gap:10px;margin-bottom:14px">' +
                '<div class="chat-stat-card" style="flex:1;text-align:center"><div class="stat-number">' + data.total_conversations + '</div><h5>Consultas</h5></div>' +
                '<div class="chat-stat-card" style="flex:1;text-align:center"><div class="stat-number">' + data.total_messages + '</div><h5>Mensajes</h5></div>' +
                '</div>';

            // Top topics
            if (data.top_topics.length > 0) {
                statsHtml += '<div class="chat-stat-card"><h5>Tus temas frecuentes</h5>' +
                    '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px">' +
                    data.top_topics.map(function(t) {
                        return '<span style="background:#fff4e5;color:#E6332A;padding:4px 10px;border-radius:12px;font-size:12px;font-weight:600">' + t + '</span>';
                    }).join('') + '</div></div>';
            }

            // Recommendations
            statsHtml += '<h4 style="margin-top:16px">Recomendaciones para Vos</h4>';
            if (data.suggestions.length > 0) {
                statsHtml += data.suggestions.map(function(s) {
                    return '<div class="chat-tip">' +
                        '<span class="chat-tip-icon">' + s.icon + '</span>' +
                        '<span>' + s.text + '</span></div>';
                }).join('');
            }

            statsEl.innerHTML = statsHtml;
        } catch (err) {
            statsEl.innerHTML = '<p style="padding:20px;color:#888;text-align:center">Error al cargar estadísticas</p>';
        }
    }

    function escapeHtml(text) {
        var d = document.createElement('div');
        d.textContent = text;
        return d.innerHTML;
    }
})();
