import os
import re
import json
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from models import db, Content, ChatConversation, ChatMessage
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

chat_bp = Blueprint('chat', __name__)

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

SYSTEM_PROMPT = """Eres VEX AI, el asistente virtual inteligente de la plataforma Vex People Predictive.
Eres un experto en los contenidos, entrenamientos y recursos disponibles en la plataforma.

SOBRE LA PLATAFORMA:
Vex People Predictive es una plataforma empresarial de entrenamientos y evaluacion predictiva del talento.
Sus funcionalidades principales son:
- **Contenidos**: Base de conocimiento con articulos organizados por categorias que los usuarios pueden consultar.
- **Entrenamientos**: Simulaciones interactivas donde los usuarios practican atencion al cliente con clientes virtuales generados por IA. Se evalua NPS, ortografia, velocidad y cumplimiento de procedimientos.
- **VEX Profile**: Perfil predictivo del talento basado en 6 dimensiones (comunicacion, empatia, resolucion, velocidad, adaptabilidad, cumplimiento).
- **Buscar**: Buscador de articulos y recursos dentro de la plataforma.
- **Revisiones**: Los supervisores pueden solicitar revisiones de documentos a los coordinadores.

REGLAS ESTRICTAS:
1. Respondes SIEMPRE en espanol profesional
2. Saluda al usuario por su nombre en la primera interaccion
3. SIEMPRE analiza TODO el contexto proporcionado antes de responder. El contexto CONTIENE la informacion que necesitas
4. Cuando el contexto tiene informacion relevante, DEBES usarla y responder con detalle
5. Incluye SIEMPRE el link al articulo: [Titulo](/content/slug)
6. Si hay MULTIPLES articulos relevantes, menciona TODOS con sus links
7. Usa listas con vinetas para pasos y procedimientos
8. Si NO hay articulos en el contexto, responde de forma util usando tu conocimiento sobre la plataforma (funcionalidades, navegacion, como usar entrenamientos, etc.). NUNCA digas simplemente "reformula tu pregunta"
9. NUNCA digas "no tengo informacion" si el contexto tiene articulos relacionados
10. Se proactivo: si el usuario pregunta algo general, explicale las funcionalidades disponibles de la plataforma y como puede aprovecharlas
11. Si la plataforma aun no tiene contenidos cargados en una operativa, explicalo con naturalidad: "Aun no hay articulos cargados en tu operativa, pero puedo ayudarte a entender como funciona la plataforma"
12. Cuando el usuario pide ayuda general, guialo por las secciones principales: Contenidos, Entrenamientos, Buscar, y como navegar la plataforma"""


def strip_html(html):
    """Remove HTML tags and get clean plain text."""
    import html as html_module
    # Remove script and style blocks completely
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove nav, header, footer boilerplate
    text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Replace block elements with newlines
    text = re.sub(r'<(?:br|p|div|h[1-6]|li|tr|dt|dd)[^>]*/?>', '\n', text, flags=re.IGNORECASE)
    # Remove remaining tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode HTML entities
    text = html_module.unescape(text)
    # Clean whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    text = text.strip()
    # More context (3500 chars)
    return text[:3500]


STOP_WORDS = {'de', 'la', 'el', 'en', 'un', 'una', 'los', 'las', 'es', 'que', 'por',
               'se', 'del', 'al', 'con', 'para', 'su', 'como', 'mas', 'ya', 'le', 'lo',
               'me', 'si', 'no', 'mi', 'te', 'tu', 'hay', 'ser', 'son', 'era', 'fue'}


def get_stem_variants(word):
    """Generate simple Spanish stem variants for fuzzy matching."""
    variants = {word}
    # Plural/singular
    if word.endswith('es'):
        variants.add(word[:-2])
        variants.add(word[:-1])
    elif word.endswith('s'):
        variants.add(word[:-1])
    else:
        variants.add(word + 's')
        variants.add(word + 'es')
    # Common suffixes
    if word.endswith('ción'):
        variants.add(word.replace('ción', 'ciones'))
    if word.endswith('ciones'):
        variants.add(word.replace('ciones', 'ción'))
    # ando/endo -> ar/er
    if word.endswith('ando'):
        variants.add(word[:-4] + 'ar')
    if word.endswith('endo'):
        variants.add(word[:-4] + 'er')
    return variants


def find_relevant_contents(query, limit=4):
    """Find relevant content using full-text search with fuzzy matching."""
    query_lower = query.lower()
    words = [w for w in re.findall(r'\w+', query_lower) if len(w) >= 2 and w not in STOP_WORDS]

    if not words:
        return []

    # Pre-compute stem variants for all query words
    word_variants = {}
    for w in words:
        word_variants[w] = get_stem_variants(w)

    q = Content.query.filter_by(is_active=True)
    if not current_user.is_superadmin and current_user.operativa_id:
        q = q.filter_by(operativa_id=current_user.operativa_id)
    contents = q.all()
    scored = []

    for c in contents:
        score = 0
        keywords = (c.keywords or '').lower()
        title = c.title.lower()
        desc = (c.description or '').lower()
        body_text = strip_html(c.html_content).lower()
        # Combine all searchable text
        all_text = f"{keywords} {title} {desc} {body_text}"

        for word in words:
            # Check all variants of the word
            for variant in word_variants[word]:
                if variant in keywords:
                    score += 5
                    break
            for variant in word_variants[word]:
                if variant in title:
                    score += 4
                    break
            for variant in word_variants[word]:
                if variant in desc:
                    score += 2
                    break
            for variant in word_variants[word]:
                if variant in body_text:
                    score += 1
                    break

        # Bonus: multi-word phrase match
        if len(words) > 1:
            phrase = ' '.join(words)
            if phrase in all_text:
                score += 10
            # Partial phrase (adjacent words)
            for i in range(len(words) - 1):
                pair = words[i] + ' ' + words[i+1]
                if pair in all_text:
                    score += 4

        if score > 0:
            scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:limit]]


def call_openai(messages):
    """Call OpenAI API."""
    if not OPENAI_API_KEY:
        return "Lo siento, el servicio de IA no está configurado. Contacta al administrador.", 0

    payload = json.dumps({
        'model': 'gpt-4o-mini',
        'messages': messages,
        'max_tokens': 1200,
        'temperature': 0.2
    }).encode('utf-8')

    req = Request(
        'https://api.openai.com/v1/chat/completions',
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {OPENAI_API_KEY}'
        }
    )

    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            content = data['choices'][0]['message']['content']
            tokens = data.get('usage', {}).get('total_tokens', 0)
            return content, tokens
    except URLError as e:
        print(f"[CHAT] OpenAI error: {e}", flush=True)
        return "Lo siento, hubo un error al procesar tu consulta. Intenta de nuevo.", 0
    except Exception as e:
        print(f"[CHAT] Unexpected error: {e}", flush=True)
        return "Error inesperado. Por favor intenta de nuevo.", 0


@chat_bp.route('/api/chat/send', methods=['POST'])
@login_required
def chat_send():
    data = request.get_json(silent=True) or {}
    message = data.get('message', '').strip()
    conversation_id = data.get('conversation_id')

    if not message:
        return jsonify({'error': 'Mensaje vacío'}), 400

    # Get or create conversation
    if conversation_id:
        conv = ChatConversation.query.filter_by(
            id=conversation_id, user_id=current_user.id
        ).first()
        if not conv:
            return jsonify({'error': 'Conversación no encontrada'}), 404
    else:
        conv = ChatConversation(
            user_id=current_user.id,
            title=message[:100]
        )
        db.session.add(conv)
        db.session.flush()

    # Save user message
    user_msg = ChatMessage(
        conversation_id=conv.id,
        role='user',
        content=message
    )
    db.session.add(user_msg)

    # Find relevant content (up to 5 articles)
    relevant = find_relevant_contents(message, limit=5)
    context_parts = []
    ref_links = []
    for i, c in enumerate(relevant, 1):
        plain = strip_html(c.html_content)
        cat_name = c.category.name if c.category else 'General'
        context_parts.append(
            f"[ARTÍCULO {i}]\n"
            f"Título: {c.title}\n"
            f"Link: [Ver artículo completo](/content/{c.slug})\n"
            f"Categoría: {cat_name}\n"
            f"Contenido:\n{plain}"
        )
        ref_links.append({'title': c.title, 'slug': c.slug})

    if context_parts:
        separator = "\n\n" + "=" * 40 + "\n\n"
        context_text = (
            f"Se encontraron {len(context_parts)} artículos relevantes. "
            f"DEBES usar esta información para responder:\n\n"
            + separator.join(context_parts)
        )
    else:
        context_text = "No se encontraron artículos en la base de conocimiento para esta consulta. Responde de forma útil usando tu conocimiento sobre las funcionalidades de la plataforma (Contenidos, Entrenamientos, VEX Profile, Búsqueda). Si la operativa aún no tiene contenidos cargados, explícalo con naturalidad y orienta al usuario."

    # Build messages for OpenAI
    user_info = f"El usuario se llama {current_user.name} y tiene el rol de {current_user.role}."
    ai_messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'system', 'content': f"USUARIO: {user_info}"},
        {'role': 'user', 'content': f"[CONTEXTO INTERNO - BASE DE CONOCIMIENTO]\n\n{context_text}"},
        {'role': 'assistant', 'content': 'Entendido, tengo el contexto de la base de conocimiento. Estoy listo para responder.'}
    ]

    # Add recent conversation history (last 20 messages)
    recent = ChatMessage.query.filter_by(
        conversation_id=conv.id
    ).order_by(ChatMessage.created_at.desc()).limit(20).all()
    recent.reverse()
    for msg in recent[:-1]:  # Exclude the message we just added
        ai_messages.append({'role': msg.role, 'content': msg.content})

    ai_messages.append({'role': 'user', 'content': message})

    # Call OpenAI
    response_text, tokens = call_openai(ai_messages)

    # Save assistant message
    assistant_msg = ChatMessage(
        conversation_id=conv.id,
        role='assistant',
        content=response_text,
        tokens_used=tokens
    )
    db.session.add(assistant_msg)
    conv.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({
        'conversation_id': conv.id,
        'message': response_text,
        'references': ref_links,
        'tokens_used': tokens
    })


@chat_bp.route('/api/chat/conversations')
@login_required
def chat_conversations():
    convs = ChatConversation.query.filter_by(
        user_id=current_user.id
    ).order_by(ChatConversation.updated_at.desc()).limit(20).all()

    return jsonify([{
        'id': c.id,
        'title': c.title,
        'updated_at': c.updated_at.isoformat() if c.updated_at else '',
        'message_count': len(c.messages)
    } for c in convs])


@chat_bp.route('/api/chat/conversations/<int:conv_id>')
@login_required
def chat_conversation_messages(conv_id):
    conv = ChatConversation.query.filter_by(
        id=conv_id, user_id=current_user.id
    ).first_or_404()

    return jsonify({
        'id': conv.id,
        'title': conv.title,
        'messages': [{
            'role': m.role,
            'content': m.content,
            'created_at': m.created_at.isoformat() if m.created_at else ''
        } for m in conv.messages]
    })


@chat_bp.route('/api/chat/my-stats')
@login_required
def chat_my_stats():
    """User's own chat stats with real training recommendations."""
    from sqlalchemy import func
    from collections import Counter
    from models import Category, Content

    total_convs = ChatConversation.query.filter_by(user_id=current_user.id).count()
    total_msgs = ChatMessage.query.join(ChatConversation).filter(
        ChatConversation.user_id == current_user.id,
        ChatMessage.role == 'user'
    ).count()

    # Get ALL user messages to analyze topics
    user_messages = ChatMessage.query.join(ChatConversation).filter(
        ChatConversation.user_id == current_user.id,
        ChatMessage.role == 'user'
    ).all()

    all_text = ' '.join(m.content.lower() for m in user_messages)
    stop = {'hola', 'como', 'cómo', 'que', 'qué', 'para', 'por', 'con', 'una', 'uno',
            'los', 'las', 'del', 'información', 'sobre', 'quiero', 'necesito', 'saber',
            'puedo', 'hacer', 'tiene', 'esta', 'esto', 'favor', 'buenas', 'buenos',
            'dias', 'gracias', 'muchas', 'bien', 'muy'}
    words_list = [w for w in re.findall(r'\w+', all_text) if len(w) > 2 and w not in stop]

    # Bigrams for topic detection
    bigrams = Counter()
    for i in range(len(words_list) - 1):
        bigrams[words_list[i] + ' ' + words_list[i + 1]] += 1
    word_freq = Counter(words_list)

    # Build top topics combining bigrams + words
    top_topics = []
    for phrase, count in bigrams.most_common(5):
        if count >= 1:
            top_topics.append(phrase)
    for word, count in word_freq.most_common(10):
        if len(top_topics) >= 5:
            break
        if not any(word in t for t in top_topics):
            top_topics.append(word)

    # Analyze category coverage: which categories' content the user asks about
    categories = Category.query.filter_by(is_active=True).all()
    consulted_cats = set()
    not_consulted_cats = []

    for cat in categories:
        contents_in_cat = Content.query.filter_by(category_id=cat.id, is_active=True).all()
        cat_kw = set()
        for cont in contents_in_cat:
            for kw in (cont.keywords or '').split(','):
                kw = kw.strip().lower()
                if len(kw) > 2:
                    cat_kw.add(kw)

        # Check if user messages touch this category
        found = False
        for msg in user_messages:
            if any(kw in msg.content.lower() for kw in cat_kw):
                consulted_cats.add(cat.name)
                found = True
                break
        if not found:
            not_consulted_cats.append(cat)

    # Build REAL recommendations
    suggestions = []

    # 1. Most consulted topic
    if top_topics:
        suggestions.append({
            'icon': '🔍',
            'text': f'Tu tema más consultado es "{top_topics[0]}". Revisá el contenido disponible en la plataforma para profundizar.'
        })

    # 2. Repeated questions → training need
    if total_msgs > 5 and top_topics:
        repeated = [t for t in top_topics[:3] if word_freq.get(t.split()[0], 0) >= 3]
        if repeated:
            suggestions.append({
                'icon': '🎯',
                'text': f'Consultás frecuentemente sobre "{repeated[0]}". Esto puede indicar una oportunidad de capacitación en este tema.'
            })

    # 3. Categories not explored
    for cat in not_consulted_cats[:2]:
        # Count contents in this category
        content_count = Content.query.filter_by(category_id=cat.id, is_active=True).count()
        if content_count > 0:
            suggestions.append({
                'icon': '📚',
                'text': f'Aún no consultaste sobre "{cat.name}" ({content_count} artículos disponibles). Explorá esta sección para ampliar tu conocimiento.'
            })

    # 4. Categories explored
    if consulted_cats:
        cats_str = ', '.join(list(consulted_cats)[:3])
        suggestions.append({
            'icon': '✅',
            'text': f'Estás consultando activamente sobre: {cats_str}. Buen trabajo manteniéndote informado.'
        })

    # 5. Usage level
    if total_convs == 0:
        suggestions.append({
            'icon': '💬',
            'text': 'Aún no tenés consultas. Probá preguntarme sobre PIN, tarjetas, transferencias o cualquier procedimiento.'
        })
    elif total_convs >= 10:
        suggestions.append({
            'icon': '⭐',
            'text': f'Llevas {total_convs} conversaciones. Sos un usuario activo de VEX AI.'
        })

    if not suggestions:
        suggestions.append({
            'icon': '💡',
            'text': 'Empezá a consultar sobre los temas de la plataforma para recibir recomendaciones personalizadas.'
        })

    return jsonify({
        'total_conversations': total_convs,
        'total_messages': total_msgs,
        'top_topics': top_topics[:5],
        'suggestions': suggestions
    })


@chat_bp.route('/api/chat/conversations/<int:conv_id>', methods=['DELETE'])
@login_required
def chat_conversation_delete(conv_id):
    conv = ChatConversation.query.filter_by(
        id=conv_id, user_id=current_user.id
    ).first_or_404()

    ChatMessage.query.filter_by(conversation_id=conv.id).delete()
    db.session.delete(conv)
    db.session.commit()
    return jsonify({'ok': True})
