"""
Migration v11: VEX Profile de Voz (indice predictivo del canal voz).

Crea la tabla voice_vex_profiles, paralela a vex_profiles pero calculada
solo desde voice_sessions. Idempotente.
"""
from app import app
from models import db


def migrate_v11():
    with app.app_context():
        print("[MIGRATE V11] Voice VEX profiles...")

        db.session.execute(db.text("""
            CREATE TABLE IF NOT EXISTS voice_vex_profiles (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
                communication_score FLOAT DEFAULT 0,
                empathy_score FLOAT DEFAULT 0,
                resolution_score FLOAT DEFAULT 0,
                speed_score FLOAT DEFAULT 0,
                adaptability_score FLOAT DEFAULT 0,
                compliance_score FLOAT DEFAULT 0,
                overall_score FLOAT DEFAULT 0,
                predictive_index FLOAT DEFAULT 0,
                profile_category VARCHAR(30),
                recommendation VARCHAR(30),
                sessions_analyzed INTEGER DEFAULT 0,
                abandonment_rate FLOAT DEFAULT 0,
                avg_response_latency FLOAT DEFAULT 0,
                avg_speech_rate FLOAT DEFAULT 0,
                filler_rate FLOAT DEFAULT 0,
                last_updated TIMESTAMP
            );
        """))

        db.session.commit()
        print("[MIGRATE V11] Done.")


if __name__ == '__main__':
    migrate_v11()
