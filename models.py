from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # superadmin, coordinador, supervisor, operador
    operativa_id = db.Column(db.Integer, db.ForeignKey('operativas.id'), nullable=True)
    is_active_user = db.Column(db.Boolean, default=True)
    max_concurrent_training = db.Column(db.Integer, default=1)  # 1-10 simultaneous chats
    profile_photo = db.Column(db.String(500), nullable=True)  # URL to profile photo
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_superadmin(self):
        return self.role == 'superadmin'

    @property
    def is_coordinador(self):
        return self.role == 'coordinador'

    @property
    def can_manage_users(self):
        return self.role in ('superadmin', 'coordinador')

    @property
    def can_manage_content(self):
        return self.role in ('superadmin', 'coordinador')


class Operativa(db.Model):
    __tablename__ = 'operativas'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True, nullable=False)
    logo_url = db.Column(db.String(500))
    primary_color = db.Column(db.String(7))
    secondary_color = db.Column(db.String(7))
    accent_color = db.Column(db.String(7))
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    users = db.relationship('User', backref='operativa', lazy=True,
                            foreign_keys='User.operativa_id')
    creator = db.relationship('User', foreign_keys=[created_by])


class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True, nullable=False)
    description = db.Column(db.Text)
    icon = db.Column(db.String(50))
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    operativa_id = db.Column(db.Integer, db.ForeignKey('operativas.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    contents = db.relationship('Content', backref='category', lazy=True)
    operativa = db.relationship('Operativa', backref='categories', foreign_keys=[operativa_id])


class Content(db.Model):
    __tablename__ = 'contents'

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'))
    title = db.Column(db.String(500), nullable=False)
    slug = db.Column(db.String(500), unique=True, nullable=False)
    html_content = db.Column(db.Text, nullable=False)
    keywords = db.Column(db.Text)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    operativa_id = db.Column(db.Integer, db.ForeignKey('operativas.id'), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    operativa = db.relationship('Operativa', backref='contents', foreign_keys=[operativa_id])


class PageView(db.Model):
    __tablename__ = 'page_views'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    content_id = db.Column(db.Integer, db.ForeignKey('contents.id'))
    page_path = db.Column(db.String(500))
    referrer = db.Column(db.String(500))
    session_id = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref='page_views')
    content = db.relationship('Content', backref='page_views')


class ClickEvent(db.Model):
    __tablename__ = 'click_events'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    content_id = db.Column(db.Integer, db.ForeignKey('contents.id'))
    element_type = db.Column(db.String(50))
    element_text = db.Column(db.String(500))
    page_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref='click_events')


class SearchLog(db.Model):
    __tablename__ = 'search_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    query = db.Column(db.String(500))
    results_count = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref='search_logs')


class ChatConversation(db.Model):
    __tablename__ = 'chat_conversations'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(255), default='Nueva conversacion')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    messages = db.relationship('ChatMessage', backref='conversation', lazy=True,
                               order_by='ChatMessage.created_at')
    user = db.relationship('User', backref='conversations')


class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('chat_conversations.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'user' or 'assistant'
    content = db.Column(db.Text, nullable=False)
    tokens_used = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ===== Training Module =====

class TrainingScenario(db.Model):
    __tablename__ = 'training_scenarios'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    client_persona = db.Column(db.Text, nullable=False)
    expected_response = db.Column(db.Text, nullable=False)
    difficulty = db.Column(db.String(20), default='medio')  # facil, medio, dificil
    category = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)
    operativa_id = db.Column(db.Integer, db.ForeignKey('operativas.id'), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    sessions = db.relationship('TrainingSession', backref='scenario', lazy=True)
    creator = db.relationship('User', foreign_keys=[created_by])
    operativa = db.relationship('Operativa', backref='scenarios', foreign_keys=[operativa_id])


class TrainingBatch(db.Model):
    __tablename__ = 'training_batches'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    scenario_id = db.Column(db.Integer, db.ForeignKey('training_scenarios.id'), nullable=False)
    max_concurrent = db.Column(db.Integer, default=1)
    status = db.Column(db.String(20), default='active')  # active, completed
    started_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    ended_at = db.Column(db.DateTime)
    duration_seconds = db.Column(db.Integer, default=0)
    overall_nps = db.Column(db.Float)
    overall_correct_rate = db.Column(db.Float)
    ai_feedback_summary = db.Column(db.Text)
    tokens_used = db.Column(db.Integer, default=0)

    sessions = db.relationship('TrainingSession', backref='batch', lazy=True)
    user = db.relationship('User', backref='training_batches')
    scenario = db.relationship('TrainingScenario')


class TrainingSession(db.Model):
    __tablename__ = 'training_sessions'

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('training_batches.id'), nullable=True)
    interaction_number = db.Column(db.Integer, default=1)
    case_index = db.Column(db.Integer, default=0)
    scenario_id = db.Column(db.Integer, db.ForeignKey('training_scenarios.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.String(20), default='active')  # active, completed, abandoned
    started_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    ended_at = db.Column(db.DateTime)
    duration_seconds = db.Column(db.Integer, default=0)
    total_messages = db.Column(db.Integer, default=0)
    total_words_user = db.Column(db.Integer, default=0)
    total_chars_user = db.Column(db.Integer, default=0)
    spelling_errors = db.Column(db.Integer, default=0)
    words_per_minute = db.Column(db.Float, default=0)
    nps_score = db.Column(db.Integer)  # 0-10
    ai_feedback = db.Column(db.Text)
    response_correct = db.Column(db.Boolean)
    tokens_used = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    messages = db.relationship('TrainingMessage', backref='session', lazy=True,
                               order_by='TrainingMessage.created_at')
    user = db.relationship('User', backref='training_sessions')


class TrainingMessage(db.Model):
    __tablename__ = 'training_messages'

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('training_sessions.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'user' or 'client'
    content = db.Column(db.Text, nullable=False)
    word_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class TrainingViewPermission(db.Model):
    __tablename__ = 'training_view_permissions'

    id = db.Column(db.Integer, primary_key=True)
    supervisor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    granted_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    supervisor = db.relationship('User', foreign_keys=[supervisor_id])
    granter = db.relationship('User', foreign_keys=[granted_by])


class VexProfile(db.Model):
    __tablename__ = 'vex_profiles'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    communication_score = db.Column(db.Float, default=0)
    empathy_score = db.Column(db.Float, default=0)
    resolution_score = db.Column(db.Float, default=0)
    speed_score = db.Column(db.Float, default=0)
    adaptability_score = db.Column(db.Float, default=0)
    compliance_score = db.Column(db.Float, default=0)
    overall_score = db.Column(db.Float, default=0)
    predictive_index = db.Column(db.Float, default=0)
    profile_category = db.Column(db.String(30))  # elite, alto, desarrollo, refuerzo
    recommendation = db.Column(db.String(30))  # recomendado, observaciones, no_recomendado
    sessions_analyzed = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref=db.backref('vex_profile', uselist=False))


class DocumentReview(db.Model):
    __tablename__ = 'document_reviews'

    id = db.Column(db.Integer, primary_key=True)
    content_id = db.Column(db.Integer, db.ForeignKey('contents.id'), nullable=False)
    requested_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    assigned_to = db.Column(db.Integer, db.ForeignKey('users.id'))
    status = db.Column(db.String(20), default='pending')  # pending, in_review, approved, rejected
    notes = db.Column(db.Text)
    operativa_id = db.Column(db.Integer, db.ForeignKey('operativas.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    resolved_at = db.Column(db.DateTime)

    content = db.relationship('Content', backref='reviews')
    requester = db.relationship('User', foreign_keys=[requested_by])
    reviewer = db.relationship('User', foreign_keys=[assigned_to])
    operativa = db.relationship('Operativa', backref='reviews')
