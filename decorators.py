from functools import wraps
from flask import redirect, url_for, flash
from flask_login import login_required, current_user


def superadmin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_superadmin:
            flash('No tienes permisos para acceder a esta seccion.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def coordinador_or_above(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role not in ('superadmin', 'coordinador'):
            flash('No tienes permisos para acceder a esta seccion.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def supervisor_or_above(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role not in ('superadmin', 'coordinador', 'supervisor'):
            flash('No tienes permisos para acceder a esta seccion.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def scoped_query(model_class, user=None):
    """Return query filtered to user's operativa. SuperAdmin sees all."""
    from flask_login import current_user as cu
    u = user or cu
    query = model_class.query
    if u.is_superadmin:
        return query
    if u.operativa_id:
        return query.filter_by(operativa_id=u.operativa_id)
    return query.filter(db.false())


# Import db here to avoid circular imports at module level
from models import db
