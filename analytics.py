from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from models import db, PageView, ClickEvent, SearchLog, Content
from datetime import datetime, timezone

analytics_bp = Blueprint('analytics', __name__)


@analytics_bp.route('/api/track/pageview', methods=['POST'])
@login_required
def track_pageview():
    data = request.get_json(silent=True) or {}
    page_path = data.get('page_path', '')
    referrer = data.get('referrer', '')
    session_id = data.get('session_id', '')
    content_id = data.get('content_id')

    pv = PageView(
        user_id=current_user.id,
        content_id=content_id,
        page_path=page_path,
        referrer=referrer,
        session_id=session_id
    )
    db.session.add(pv)
    db.session.commit()
    return jsonify({'ok': True})


@analytics_bp.route('/api/track/click', methods=['POST'])
@login_required
def track_click():
    data = request.get_json(silent=True) or {}
    ce = ClickEvent(
        user_id=current_user.id,
        content_id=data.get('content_id'),
        element_type=data.get('element_type', ''),
        element_text=data.get('element_text', '')[:500] if data.get('element_text') else '',
        page_path=data.get('page_path', '')
    )
    db.session.add(ce)
    db.session.commit()
    return jsonify({'ok': True})


@analytics_bp.route('/api/track/search', methods=['POST'])
@login_required
def track_search():
    data = request.get_json(silent=True) or {}
    sl = SearchLog(
        user_id=current_user.id,
        query=data.get('query', '')[:500],
        results_count=data.get('results_count', 0)
    )
    db.session.add(sl)
    db.session.commit()
    return jsonify({'ok': True})


@analytics_bp.route('/api/analytics/overview')
@login_required
def analytics_overview():
    if current_user.role not in ('superadmin', 'coordinador'):
        return jsonify({'error': 'No autorizado'}), 403

    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')

    pv_query = db.session.query(PageView)
    ce_query = db.session.query(ClickEvent)
    sl_query = db.session.query(SearchLog)

    if date_from:
        dt_from = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        pv_query = pv_query.filter(PageView.created_at >= dt_from)
        ce_query = ce_query.filter(ClickEvent.created_at >= dt_from)
        sl_query = sl_query.filter(SearchLog.created_at >= dt_from)
    if date_to:
        dt_to = datetime.strptime(date_to, '%Y-%m-%d').replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        pv_query = pv_query.filter(PageView.created_at <= dt_to)
        ce_query = ce_query.filter(ClickEvent.created_at <= dt_to)
        sl_query = sl_query.filter(SearchLog.created_at <= dt_to)

    total_views = pv_query.count()
    total_clicks = ce_query.count()
    total_searches = sl_query.count()

    # Top pages
    top_pages = db.session.query(
        PageView.page_path,
        db.func.count(PageView.id).label('views')
    ).group_by(PageView.page_path).order_by(db.text('views DESC')).limit(10).all()

    # Top searches
    top_searches = db.session.query(
        SearchLog.query,
        db.func.count(SearchLog.id).label('count')
    ).group_by(SearchLog.query).order_by(db.text('count DESC')).limit(10).all()

    # Views per day
    from sqlalchemy import func, cast, Date
    views_per_day = db.session.query(
        cast(PageView.created_at, Date).label('date'),
        func.count(PageView.id).label('views')
    )
    if date_from:
        views_per_day = views_per_day.filter(PageView.created_at >= dt_from)
    if date_to:
        views_per_day = views_per_day.filter(PageView.created_at <= dt_to)
    views_per_day = views_per_day.group_by('date').order_by('date').all()

    return jsonify({
        'total_views': total_views,
        'total_clicks': total_clicks,
        'total_searches': total_searches,
        'top_pages': [{'path': p, 'views': v} for p, v in top_pages],
        'top_searches': [{'query': q, 'count': c} for q, c in top_searches],
        'views_per_day': [{'date': str(d), 'views': v} for d, v in views_per_day]
    })


@analytics_bp.route('/api/analytics/users')
@login_required
def analytics_users():
    if current_user.role not in ('superadmin', 'coordinador'):
        return jsonify({'error': 'No autorizado'}), 403

    from models import User
    from sqlalchemy import func

    user_stats = db.session.query(
        User.id, User.name, User.email, User.role,
        func.count(PageView.id).label('views')
    ).outerjoin(PageView, User.id == PageView.user_id
    ).filter(User.role != 'superadmin'
    ).group_by(User.id, User.name, User.email, User.role
    ).order_by(db.text('views DESC')).all()

    return jsonify([{
        'id': u_id, 'name': name, 'email': email, 'role': role, 'views': views
    } for u_id, name, email, role, views in user_stats])
