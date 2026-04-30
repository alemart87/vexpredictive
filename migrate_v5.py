"""
Migration v5: Add avg_response_time (ART) column to training_sessions.
ART = Average Response Time, tiempo medio en segundos entre el mensaje del
cliente y la respuesta del asesor. Reemplaza la duracion total como senal
de velocidad en el scoring (ver scoring.md).

Safe to run multiple times (uses IF NOT EXISTS).
"""
from app import app
from models import db


def migrate_v5():
    with app.app_context():
        print("[MIGRATE V5] Adding avg_response_time to training_sessions...")

        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'training_sessions'
                      AND column_name = 'avg_response_time'
                ) THEN
                    ALTER TABLE training_sessions
                        ADD COLUMN avg_response_time DOUBLE PRECISION DEFAULT 0;
                    RAISE NOTICE 'Added avg_response_time column';
                ELSE
                    RAISE NOTICE 'avg_response_time already exists';
                END IF;
            END $$;
        """))

        db.session.commit()
        print("[MIGRATE V5] Done.")


if __name__ == '__main__':
    migrate_v5()
