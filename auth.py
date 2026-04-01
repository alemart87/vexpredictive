from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User
from datetime import datetime, timezone

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    print(f"[AUTH] /login method={request.method} url={request.url}")

    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        print(f"[AUTH] Login attempt: email={email}")

        user = User.query.filter_by(email=email).first()
        print(f"[AUTH] User found: {user is not None}")

        if user:
            pw_ok = user.check_password(password)
            print(f"[AUTH] Password check: {pw_ok}, active: {user.is_active_user}, role: {user.role}")
            if pw_ok and user.is_active_user:
                user.last_login = datetime.now(timezone.utc)
                db.session.commit()
                login_user(user, remember=True)
                next_page = request.args.get('next')
                print(f"[AUTH] Login SUCCESS, redirecting to {next_page or '/'}")
                return redirect(next_page or url_for('index'))

        flash('Email o contraseña incorrectos.', 'error')
        print("[AUTH] Login FAILED")

    return render_template('login.html')


@auth_bp.route('/debug/check')
def debug_check():
    """Temporary debug route - remove in production."""
    users = User.query.all()
    return jsonify({
        'users': [{'id': u.id, 'email': u.email, 'role': u.role, 'active': u.is_active_user} for u in users],
        'total': len(users)
    })


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Sesión cerrada correctamente.', 'success')
    return redirect(url_for('auth.login'))
