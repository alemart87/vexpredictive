import os
import re
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from models import db, User, Content, Category, ChatConversation, ChatMessage, Operativa, DocumentReview
from werkzeug.utils import secure_filename
from datetime import datetime, timezone
from decorators import superadmin_required, coordinador_or_above, scoped_query

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# --- Dashboard ---
@admin_bp.route('/dashboard')
@coordinador_or_above
def dashboard():
    return render_template('admin/dashboard.html')


# --- Content Management ---
@admin_bp.route('/contents')
@coordinador_or_above
def content_list():
    contents = scoped_query(Content).order_by(Content.updated_at.desc()).all()
    categories = scoped_query(Category).order_by(Category.sort_order).all()
    return render_template('admin/content_list.html', contents=contents, categories=categories)


@admin_bp.route('/contents/new', methods=['GET', 'POST'])
@coordinador_or_above
def content_new():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        slug = request.form.get('slug', '').strip()
        category_id = request.form.get('category_id')
        html_content = request.form.get('html_content', '')
        keywords = request.form.get('keywords', '')
        description = request.form.get('description', '')

        if not title or not slug:
            flash('Titulo y slug son obligatorios.', 'error')
        elif Content.query.filter_by(slug=slug).first():
            flash('Ya existe un contenido con ese slug.', 'error')
        else:
            content = Content(
                title=title,
                slug=slug,
                category_id=int(category_id) if category_id else None,
                html_content=html_content,
                keywords=keywords,
                description=description,
                operativa_id=current_user.operativa_id,
                created_by=current_user.id,
                updated_by=current_user.id
            )
            db.session.add(content)
            db.session.commit()
            flash('Contenido creado correctamente.', 'success')
            return redirect(url_for('admin.content_list'))

    categories = scoped_query(Category).order_by(Category.sort_order).all()
    return render_template('admin/content_edit.html', content=None, categories=categories)


@admin_bp.route('/contents/<int:content_id>/edit', methods=['GET', 'POST'])
@coordinador_or_above
def content_edit(content_id):
    content = Content.query.get_or_404(content_id)

    if request.method == 'POST':
        content.title = request.form.get('title', '').strip()
        content.slug = request.form.get('slug', '').strip()
        content.category_id = int(request.form.get('category_id')) if request.form.get('category_id') else None
        content.html_content = request.form.get('html_content', '')
        content.keywords = request.form.get('keywords', '')
        content.description = request.form.get('description', '')
        content.updated_by = current_user.id
        content.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        flash('Contenido actualizado correctamente.', 'success')
        return redirect(url_for('admin.content_list'))

    categories = scoped_query(Category).order_by(Category.sort_order).all()
    return render_template('admin/content_edit.html', content=content, categories=categories)


@admin_bp.route('/contents/<int:content_id>/delete', methods=['POST'])
@coordinador_or_above
def content_delete(content_id):
    content = Content.query.get_or_404(content_id)
    db.session.delete(content)
    db.session.commit()
    flash('Contenido eliminado.', 'success')
    return redirect(url_for('admin.content_list'))


# --- Categories ---
@admin_bp.route('/categories')
@coordinador_or_above
def category_list():
    categories = scoped_query(Category).order_by(Category.sort_order).all()
    return render_template('admin/categories.html', categories=categories)


@admin_bp.route('/categories/save', methods=['POST'])
@coordinador_or_above
def category_save():
    cat_id = request.form.get('id')
    name = request.form.get('name', '').strip()
    slug = request.form.get('slug', '').strip()
    description = request.form.get('description', '')
    sort_order = int(request.form.get('sort_order', 0))

    if not name or not slug:
        flash('Nombre y slug son obligatorios.', 'error')
        return redirect(url_for('admin.category_list'))

    if cat_id:
        cat = Category.query.get_or_404(int(cat_id))
        cat.name = name
        cat.slug = slug
        cat.description = description
        cat.sort_order = sort_order
    else:
        cat = Category(name=name, slug=slug, description=description, sort_order=sort_order,
                       operativa_id=current_user.operativa_id)
        db.session.add(cat)

    db.session.commit()
    flash('Categoria guardada.', 'success')
    return redirect(url_for('admin.category_list'))


@admin_bp.route('/categories/<int:cat_id>/delete', methods=['POST'])
@coordinador_or_above
def category_delete(cat_id):
    cat = Category.query.get_or_404(cat_id)
    Content.query.filter_by(category_id=cat.id).update({'category_id': None})
    db.session.delete(cat)
    db.session.commit()
    flash('Categoria eliminada.', 'success')
    return redirect(url_for('admin.category_list'))


# --- User Management ---
@admin_bp.route('/users')
@coordinador_or_above
def user_list():
    if current_user.is_superadmin:
        users = User.query.order_by(User.created_at.desc()).all()
    else:
        users = User.query.filter_by(operativa_id=current_user.operativa_id).order_by(User.created_at.desc()).all()
    operativas = Operativa.query.filter_by(is_active=True).all() if current_user.is_superadmin else []
    return render_template('admin/users.html', users=users, operativas=operativas)


@admin_bp.route('/users/save', methods=['POST'])
@coordinador_or_above
def user_save():
    user_id = request.form.get('id')
    email = request.form.get('email', '').strip()
    name = request.form.get('name', '').strip()
    role = request.form.get('role', 'operador')
    password = request.form.get('password', '')
    is_active = request.form.get('is_active') == 'on'
    max_concurrent = int(request.form.get('max_concurrent', 1) or 1)
    operativa_id = request.form.get('operativa_id')

    # Validate role based on current user's permissions
    if current_user.is_superadmin:
        valid_roles = ('coordinador', 'supervisor', 'operador')
    else:
        valid_roles = ('supervisor', 'operador')

    if role not in valid_roles:
        flash('Rol no valido.', 'error')
        return redirect(url_for('admin.user_list'))

    if not email or not name:
        flash('Usuario/email y nombre son obligatorios.', 'error')
        return redirect(url_for('admin.user_list'))

    # Determine operativa_id
    if current_user.is_superadmin and operativa_id:
        target_operativa_id = int(operativa_id)
    elif current_user.is_coordinador:
        target_operativa_id = current_user.operativa_id
    else:
        target_operativa_id = None

    if user_id:
        user = User.query.get_or_404(int(user_id))
        if user.is_superadmin:
            flash('No puedes editar al SuperAdmin desde aqui.', 'error')
            return redirect(url_for('admin.user_list'))
        user.email = email
        user.name = name
        user.role = role
        user.is_active_user = is_active
        user.max_concurrent_training = max(1, min(10, max_concurrent))
        if target_operativa_id:
            user.operativa_id = target_operativa_id
        if password:
            user.set_password(password)
    else:
        if not password:
            flash('La contrasena es obligatoria para nuevos usuarios.', 'error')
            return redirect(url_for('admin.user_list'))
        if User.query.filter_by(email=email).first():
            flash('Ya existe un usuario con ese email/usuario.', 'error')
            return redirect(url_for('admin.user_list'))
        user = User(email=email, name=name, role=role, is_active_user=is_active,
                    max_concurrent_training=max(1, min(10, max_concurrent)),
                    operativa_id=target_operativa_id)
        user.set_password(password)
        db.session.add(user)

    db.session.commit()
    flash('Usuario guardado correctamente.', 'success')
    return redirect(url_for('admin.user_list'))


# --- Image Upload ---
@admin_bp.route('/upload-image', methods=['POST'])
@coordinador_or_above
def upload_image():
    if 'image' not in request.files:
        return jsonify({'error': 'No se envio archivo'}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'Archivo sin nombre'}), 400

    allowed = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in allowed:
        return jsonify({'error': 'Tipo de archivo no permitido'}), 400

    filename = secure_filename(file.filename)
    upload_dir = current_app.config['UPLOAD_DIR']
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, filename)
    file.save(filepath)

    return jsonify({'url': '/imagenes/' + filename})


# --- Insights AI ---
@admin_bp.route('/chat')
@coordinador_or_above
def chat_analytics():
    return render_template('admin/chat_analytics.html')


@admin_bp.route('/chat/<int:conv_id>/detail')
@coordinador_or_above
def admin_chat_detail(conv_id):
    conv = ChatConversation.query.get_or_404(conv_id)
    return jsonify({
        'title': conv.title,
        'user': conv.user.name if conv.user else 'Desconocido',
        'messages': [{
            'role': m.role,
            'content': m.content,
            'created_at': m.created_at.strftime('%d/%m/%Y %H:%M') if m.created_at else ''
        } for m in conv.messages]
    })


@admin_bp.route('/api/insights')
@coordinador_or_above
def api_insights():
    from sqlalchemy import func, cast, Date
    from collections import Counter
    from datetime import timedelta

    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')

    if not date_from:
        dt_from = datetime.now(timezone.utc) - timedelta(hours=24)
    else:
        dt_from = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)

    if not date_to:
        dt_to = datetime.now(timezone.utc)
    else:
        dt_to = datetime.strptime(date_to, '%Y-%m-%d').replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)

    # Base filtered queries - scope by operativa
    convs_q = ChatConversation.query.filter(ChatConversation.created_at.between(dt_from, dt_to))
    if not current_user.is_superadmin and current_user.operativa_id:
        op_user_ids = [u.id for u in User.query.filter_by(operativa_id=current_user.operativa_id).all()]
        convs_q = convs_q.filter(ChatConversation.user_id.in_(op_user_ids))

    conv_ids = [c.id for c in convs_q.all()]
    msgs_q = ChatMessage.query.filter(ChatMessage.conversation_id.in_(conv_ids)) if conv_ids else ChatMessage.query.filter(False)

    total_convs = len(conv_ids)
    total_msgs = msgs_q.filter_by(role='user').count() if conv_ids else 0
    total_tokens = db.session.query(func.coalesce(func.sum(ChatMessage.tokens_used), 0)).filter(
        ChatMessage.conversation_id.in_(conv_ids)).scalar() if conv_ids else 0
    unique_users = db.session.query(func.count(func.distinct(ChatConversation.user_id))).filter(
        ChatConversation.id.in_(conv_ids)).scalar() if conv_ids else 0

    convs_per_day = db.session.query(
        cast(ChatConversation.created_at, Date).label('date'),
        func.count(ChatConversation.id).label('count')
    ).filter(ChatConversation.id.in_(conv_ids)
    ).group_by('date').order_by('date').all() if conv_ids else []

    top_users = db.session.query(
        User.name, User.role,
        func.count(func.distinct(ChatConversation.id)).label('convs')
    ).join(ChatConversation, User.id == ChatConversation.user_id
    ).filter(ChatConversation.id.in_(conv_ids)
    ).filter(User.role != 'superadmin'
    ).group_by(User.id, User.name, User.role
    ).order_by(func.count(func.distinct(ChatConversation.id)).desc()).limit(10).all() if conv_ids else []

    stop = {'hola', 'como', 'que', 'para', 'por', 'con', 'una', 'uno',
            'los', 'las', 'del', 'sobre', 'quiero', 'necesito', 'saber',
            'puedo', 'hacer', 'tiene', 'esta', 'esto', 'esos', 'esas',
            'favor', 'buenas', 'buenos', 'dias', 'gracias', 'muchas', 'bien', 'muy'}

    user_messages = []
    if conv_ids:
        user_messages = ChatMessage.query.filter(
            ChatMessage.conversation_id.in_(conv_ids),
            ChatMessage.role == 'user'
        ).all()

    all_text = ' '.join(m.content.lower() for m in user_messages)
    words_list = [w for w in re.findall(r'\w+', all_text) if len(w) > 2 and w not in stop]
    word_freq = Counter(words_list).most_common(15)

    bigrams = Counter()
    for i in range(len(words_list) - 1):
        pair = words_list[i] + ' ' + words_list[i + 1]
        bigrams[pair] += 1
    top_bigrams = bigrams.most_common(8)

    top_topics = [{'word': w, 'count': c} for w, c in top_bigrams if c > 1]
    for w, c in word_freq:
        if len(top_topics) >= 10:
            break
        if not any(w in t['word'] for t in top_topics):
            top_topics.append({'word': w, 'count': c})

    categories = scoped_query(Category).filter_by(is_active=True).all()
    cat_coverage = {}
    for cat in categories:
        contents_in_cat = Content.query.filter_by(category_id=cat.id, is_active=True).all()
        cat_kw = set()
        for cont in contents_in_cat:
            for kw in (cont.keywords or '').split(','):
                kw = kw.strip().lower()
                if len(kw) > 2:
                    cat_kw.add(kw)
        mentions = 0
        for msg in user_messages:
            msg_lower = msg.content.lower()
            if any(kw in msg_lower for kw in cat_kw):
                mentions += 1
        cat_coverage[cat.name] = mentions

    recommendations = []
    if top_topics and top_topics[0]['count'] >= 2:
        t = top_topics[0]
        recommendations.append({
            'icon': '&#128293;', 'title': f'Tema mas consultado: "{t["word"]}"',
            'desc': f'Con {t["count"]} menciones. Evaluar si el equipo necesita capacitacion especifica.',
            'priority': 'alta'
        })

    for name_val, role_val, convs in top_users:
        if convs >= 3:
            recommendations.append({
                'icon': '&#127891;', 'title': f'{name_val} ({role_val}) realizo {convs} consultas',
                'desc': 'Alta actividad puede indicar necesidad de capacitacion personalizada.',
                'priority': 'media'
            })
            break

    if cat_coverage:
        max_cov = max(cat_coverage.values()) if cat_coverage.values() else 0
        for cat_name, mentions in cat_coverage.items():
            if mentions == 0 and total_msgs > 3:
                recommendations.append({
                    'icon': '&#9888;', 'title': f'Sin consultas sobre "{cat_name}"',
                    'desc': f'Considerar difusion de contenidos de "{cat_name}".',
                    'priority': 'media'
                })
            elif max_cov > 0 and mentions == max_cov:
                recommendations.append({
                    'icon': '&#128200;', 'title': f'"{cat_name}" es la mas consultada',
                    'desc': f'{mentions} consultas relacionadas. Verificar que el contenido este actualizado.',
                    'priority': 'alta'
                })

    if unique_users and total_convs:
        avg = total_convs / unique_users
        if avg >= 2:
            recommendations.append({
                'icon': '&#9989;', 'title': f'Buen nivel de uso ({avg:.1f} consultas/usuario)',
                'desc': 'El equipo esta adoptando la herramienta activamente.',
                'priority': 'info'
            })
        elif total_convs > 0 and avg < 1.5:
            recommendations.append({
                'icon': '&#128226;', 'title': 'Bajo engagement general',
                'desc': f'Promedio de {avg:.1f} consultas/usuario. Considerar promover mas el uso de VEX AI.',
                'priority': 'media'
            })

    if total_tokens > 20000:
        cost_est = total_tokens * 0.00015 / 1000
        recommendations.append({
            'icon': '&#128176;', 'title': f'Consumo: {total_tokens:,} tokens (~${cost_est:.2f} USD)',
            'desc': 'Monitorear el costo del modelo.',
            'priority': 'info'
        })

    if not recommendations:
        recommendations.append({
            'icon': '&#128202;', 'title': 'Datos insuficientes en este periodo',
            'desc': 'Amplia el rango de fechas para obtener recomendaciones mas precisas.',
            'priority': 'info'
        })

    recent_convs = convs_q.order_by(ChatConversation.updated_at.desc()).limit(30).all()

    return jsonify({
        'stats': {
            'total_conversations': total_convs,
            'total_messages': total_msgs,
            'total_tokens': total_tokens,
            'unique_users': unique_users
        },
        'convs_per_day': [{'date': str(d), 'count': c} for d, c in convs_per_day],
        'top_users': [{'name': n, 'role': r, 'convs': c} for n, r, c in top_users],
        'top_topics': top_topics,
        'frequent_questions': [{'word': w, 'count': c} for w, c in word_freq],
        'category_coverage': cat_coverage,
        'recommendations': recommendations,
        'recent_conversations': [{
            'id': c.id,
            'user': c.user.name if c.user else '-',
            'role': c.user.role if c.user else '-',
            'title': c.title,
            'messages': len(c.messages),
            'tokens': sum(m.tokens_used or 0 for m in c.messages),
            'date': c.created_at.strftime('%d/%m/%Y %H:%M') if c.created_at else ''
        } for c in recent_convs]
    })


# --- Operativas Management (SuperAdmin only) ---
@admin_bp.route('/operativas')
@superadmin_required
def operativa_list():
    operativas = Operativa.query.order_by(Operativa.created_at.desc()).all()
    return render_template('admin/operativas.html', operativas=operativas)


@admin_bp.route('/operativas/save', methods=['POST'])
@superadmin_required
def operativa_save():
    op_id = request.form.get('id')
    name = request.form.get('name', '').strip()
    slug = request.form.get('slug', '').strip()
    description = request.form.get('description', '')
    primary_color = request.form.get('primary_color', '')
    secondary_color = request.form.get('secondary_color', '')
    accent_color = request.form.get('accent_color', '')

    if not name or not slug:
        flash('Nombre y slug son obligatorios.', 'error')
        return redirect(url_for('admin.operativa_list'))

    if op_id:
        op = Operativa.query.get_or_404(int(op_id))
        op.name = name
        op.slug = slug
        op.description = description
        op.primary_color = primary_color or None
        op.secondary_color = secondary_color or None
        op.accent_color = accent_color or None
    else:
        if Operativa.query.filter_by(slug=slug).first():
            flash('Ya existe una operativa con ese slug.', 'error')
            return redirect(url_for('admin.operativa_list'))
        op = Operativa(name=name, slug=slug, description=description,
                       primary_color=primary_color or None,
                       secondary_color=secondary_color or None,
                       accent_color=accent_color or None,
                       created_by=current_user.id)
        db.session.add(op)

    # Handle logo upload
    if 'logo' in request.files and request.files['logo'].filename:
        file = request.files['logo']
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
            filename = secure_filename(f"op_{slug}_logo.{ext}")
            upload_dir = current_app.config['UPLOAD_DIR']
            os.makedirs(upload_dir, exist_ok=True)
            file.save(os.path.join(upload_dir, filename))
            op.logo_url = '/imagenes/' + filename

    db.session.commit()
    flash('Operativa guardada correctamente.', 'success')
    return redirect(url_for('admin.operativa_list'))


@admin_bp.route('/operativas/<int:op_id>/toggle', methods=['POST'])
@superadmin_required
def operativa_toggle(op_id):
    op = Operativa.query.get_or_404(op_id)
    op.is_active = not op.is_active
    db.session.commit()
    flash(f'Operativa {"activada" if op.is_active else "desactivada"}.', 'success')
    return redirect(url_for('admin.operativa_list'))


# --- Document Reviews ---
@admin_bp.route('/reviews')
@coordinador_or_above
def review_list():
    if current_user.is_superadmin:
        reviews = DocumentReview.query.order_by(DocumentReview.created_at.desc()).all()
    else:
        reviews = DocumentReview.query.filter_by(
            operativa_id=current_user.operativa_id
        ).order_by(DocumentReview.created_at.desc()).all()
    return render_template('admin/reviews.html', reviews=reviews)


@admin_bp.route('/reviews/<int:review_id>/resolve', methods=['POST'])
@coordinador_or_above
def review_resolve(review_id):
    review = DocumentReview.query.get_or_404(review_id)
    action = request.form.get('action', 'approved')
    notes = request.form.get('notes', '')

    review.status = action
    review.notes = notes
    review.assigned_to = current_user.id
    review.resolved_at = datetime.now(timezone.utc)
    db.session.commit()

    flash(f'Revision {action}.', 'success')
    return redirect(url_for('admin.review_list'))


# --- System Guide (SuperAdmin only) ---
@admin_bp.route('/guide')
@superadmin_required
def system_guide():
    return render_template('admin/system_guide.html')
