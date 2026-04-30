"""
Migration v8: Agrega abandonment_rate al VexProfile.

abandonment_rate (0..1) = sesiones con auto-fail / total. Se persiste
para mostrarlo en el perfil VEX y aplicar el hard cap del scoring.

Idempotente.
"""
from app import app
from models import db


def migrate_v8():
    with app.app_context():
        print("[MIGRATE V8] Adding abandonment_rate to vex_profiles...")

        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'vex_profiles'
                      AND column_name = 'abandonment_rate'
                ) THEN
                    ALTER TABLE vex_profiles
                        ADD COLUMN abandonment_rate DOUBLE PRECISION DEFAULT 0;
                    RAISE NOTICE 'Added abandonment_rate to vex_profiles';
                ELSE
                    RAISE NOTICE 'abandonment_rate already exists';
                END IF;
            END $$;
        """))

        db.session.commit()
        print("[MIGRATE V8] Done.")


if __name__ == '__main__':
    migrate_v8()
