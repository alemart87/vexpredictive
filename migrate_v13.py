"""
Migration v13: pausas (cliente en espera) en las llamadas de voz.

Agrega a voice_sessions: hold_count, hold_seconds y holds (JSON de
intervalos [[start_ms, end_ms], ...]). Idempotente.
"""
from app import app
from models import db


def migrate_v13():
    with app.app_context():
        print("[MIGRATE V13] Voice holds...")

        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'voice_sessions' AND column_name = 'hold_count'
                ) THEN
                    ALTER TABLE voice_sessions ADD COLUMN hold_count INTEGER DEFAULT 0;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'voice_sessions' AND column_name = 'hold_seconds'
                ) THEN
                    ALTER TABLE voice_sessions ADD COLUMN hold_seconds INTEGER DEFAULT 0;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'voice_sessions' AND column_name = 'holds'
                ) THEN
                    ALTER TABLE voice_sessions ADD COLUMN holds TEXT;
                END IF;
            END $$;
        """))

        db.session.commit()
        print("[MIGRATE V13] Done.")


if __name__ == '__main__':
    migrate_v13()
