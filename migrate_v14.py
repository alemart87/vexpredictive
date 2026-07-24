"""
Migration v14: control de jailbreak en llamadas de voz.

Agrega a voice_sessions: jailbreak_attempts (intentos del asesor de
romper el ejercicio) y role_breaks (rupturas de personaje del cliente IA
detectadas y corregidas). Idempotente.
"""
from app import app
from models import db


def migrate_v14():
    with app.app_context():
        print("[MIGRATE V14] Voice jailbreak control...")

        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'voice_sessions' AND column_name = 'jailbreak_attempts'
                ) THEN
                    ALTER TABLE voice_sessions ADD COLUMN jailbreak_attempts INTEGER DEFAULT 0;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'voice_sessions' AND column_name = 'role_breaks'
                ) THEN
                    ALTER TABLE voice_sessions ADD COLUMN role_breaks INTEGER DEFAULT 0;
                END IF;
            END $$;
        """))

        db.session.commit()
        print("[MIGRATE V14] Done.")


if __name__ == '__main__':
    migrate_v14()
