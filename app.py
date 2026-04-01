import os
from flask import Flask, render_template, redirect, url_for, request, jsonify, flash, send_from_directory
from flask_login import LoginManager, login_required, current_user
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from models import db, User, Content, Category, PageView, DocumentReview
from datetime import datetime, timezone

load_dotenv()

# Persistent disk path (Render) or local fallback
UPLOAD_DIR = os.environ.get('UPLOAD_DIR', os.path.join(os.path.dirname(__file__), 'static', 'imagenes'))

app = Flask(__name__, static_folder='static', template_folder='templates')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', '').replace('postgres://', 'postgresql://')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.config['UPLOAD_DIR'] = UPLOAD_DIR

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Debes iniciar sesion para acceder.'
login_manager.login_message_category = 'warning'


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def init_superadmin():
    """Create or update superadmin from environment variables."""
    email = os.environ.get('SUPERADMIN_EMAIL')
    password = os.environ.get('SUPERADMIN_PASSWORD')
    print(f"[INIT] SUPERADMIN_EMAIL={'SET' if email else 'MISSING'}, SUPERADMIN_PASSWORD={'SET' if password else 'MISSING'}", flush=True)
    if not email or not password:
        print("[INIT] Skipping superadmin creation - missing env vars")
        return
    try:
        user = User.query.filter_by(email=email).first()
        if user:
            user.role = 'superadmin'
            user.set_password(password)
            user.name = 'Super Admin'
            user.is_active_user = True
            print(f"[INIT] Updated existing superadmin: {email}")
        else:
            user = User(
                email=email,
                name='Super Admin',
                role='superadmin',
                is_active_user=True
            )
            user.set_password(password)
            db.session.add(user)
            print(f"[INIT] Created new superadmin: {email}")
        db.session.commit()
        print("[INIT] Superadmin ready")
    except Exception as e:
        print(f"[INIT] Error creating superadmin: {e}")
        db.session.rollback()


# Register blueprints
from admin import admin_bp
from analytics import analytics_bp
from chat import chat_bp
from training import training_bp

app.register_blueprint(admin_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(training_bp)


# ===== Auth routes =====
from flask_login import login_user, logout_user
import json as json_module


@app.template_filter('count_cases')
def count_cases_filter(text):
    try:
        data = json_module.loads(text)
        if isinstance(data, list):
            return len(data)
    except (json_module.JSONDecodeError, TypeError):
        pass
    return 1


@app.template_filter('scenario_json')
def scenario_json_filter(scenario):
    from training import parse_cases
    cases = parse_cases(scenario)
    return json_module.dumps({
        'title': scenario.title,
        'description': scenario.description or '',
        'difficulty': scenario.difficulty,
        'category': scenario.category or '',
        'cases': cases
    }, ensure_ascii=False)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password) and user.is_active_user:
                user.last_login = datetime.now(timezone.utc)
                db.session.commit()
                login_user(user, remember=True)
                return redirect(url_for('index'))

        flash('Usuario o contrasena incorrectos.', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Sesion cerrada correctamente.', 'success')
    return redirect(url_for('login'))


@app.route('/debug/check')
def debug_check():
    users = User.query.all()
    return jsonify({
        'users': [{'id': u.id, 'email': u.email, 'role': u.role, 'active': u.is_active_user} for u in users],
        'total': len(users)
    })


@app.route('/imagenes/<path:filename>')
def serve_image(filename):
    return send_from_directory(app.config['UPLOAD_DIR'], filename)


@app.context_processor
def inject_nav_categories():
    """Make categories available to all templates for navigation, scoped by operativa."""
    if current_user.is_authenticated:
        q = Category.query.filter_by(is_active=True)
        if not current_user.is_superadmin and current_user.operativa_id:
            q = q.filter_by(operativa_id=current_user.operativa_id)
        cats = q.order_by(Category.sort_order).all()
        return {'nav_categories': cats}
    return {'nav_categories': []}


@app.context_processor
def inject_operativa_branding():
    """Inject operativa custom branding for color overrides."""
    if current_user.is_authenticated and hasattr(current_user, 'operativa') and current_user.operativa:
        op = current_user.operativa
        return {
            'op_name': op.name,
            'op_logo': op.logo_url,
            'op_primary': op.primary_color,
            'op_secondary': op.secondary_color,
        }
    return {}


@app.route('/')
@login_required
def index():
    q_cat = Category.query.filter_by(is_active=True)
    q_content = Content.query.filter_by(is_active=True)
    if not current_user.is_superadmin and current_user.operativa_id:
        q_cat = q_cat.filter_by(operativa_id=current_user.operativa_id)
        q_content = q_content.filter_by(operativa_id=current_user.operativa_id)
    categories = q_cat.order_by(Category.sort_order).all()
    featured = q_content.order_by(Content.updated_at.desc()).limit(6).all()
    return render_template('index.html', categories=categories, featured=featured)


@app.route('/content/<slug>')
@login_required
def view_content(slug):
    content = Content.query.filter_by(slug=slug, is_active=True).first_or_404()
    return render_template('viewer.html', content=content)


@app.route('/category/<slug>')
@login_required
def view_category(slug):
    category = Category.query.filter_by(slug=slug, is_active=True).first_or_404()
    contents = Content.query.filter_by(category_id=category.id, is_active=True).all()
    return render_template('category.html', category=category, contents=contents)


@app.route('/api/search')
@login_required
def api_search():
    q = request.args.get('q', '').strip().lower()
    if not q:
        return jsonify([])
    query = Content.query.filter_by(is_active=True)
    if not current_user.is_superadmin and current_user.operativa_id:
        query = query.filter_by(operativa_id=current_user.operativa_id)
    contents = query.all()
    results = []
    for c in contents:
        keywords = (c.keywords or '').lower()
        title = c.title.lower()
        if q in keywords or q in title:
            results.append({
                'id': c.id,
                'title': c.title,
                'description': c.description or '',
                'slug': c.slug,
                'category': c.category.name if c.category else ''
            })
    return jsonify(results)


# --- Document Review request (for supervisors) ---
@app.route('/api/reviews/request', methods=['POST'])
@login_required
def request_review():
    content_id = request.json.get('content_id')
    notes = request.json.get('notes', '')
    if not content_id:
        return jsonify({'error': 'content_id requerido'}), 400
    review = DocumentReview(
        content_id=content_id,
        requested_by=current_user.id,
        notes=notes,
        operativa_id=current_user.operativa_id
    )
    db.session.add(review)
    db.session.commit()
    return jsonify({'ok': True, 'id': review.id})


with app.app_context():
    db.create_all()
    init_superadmin()


if __name__ == '__main__':
    app.run(debug=True, port=5000)
