"""
Migration v12: grabacion de audio de las llamadas de voz.

Agrega voice_sessions.recording_path (archivo en el disco persistente,
con retencion limitada — la limpieza corre en la app). Idempotente.
"""
from app import app
from models import db


def migrate_v12():
    with app.app_context():
        print("[MIGRATE V12] Voice recordings...")

        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'voice_sessions'
                      AND column_name = 'recording_path'
                ) THEN
                    ALTER TABLE voice_sessions ADD COLUMN recording_path VARCHAR(500);
                    RAISE NOTICE 'Added voice_sessions.recording_path';
                ELSE
                    RAISE NOTICE 'voice_sessions.recording_path already exists';
                END IF;
            END $$;
        """))

        db.session.commit()
        print("[MIGRATE V12] Done.")


if __name__ == '__main__':
    migrate_v12()
