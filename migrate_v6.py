"""
Migration v6: Modos de Scoring (Flexible / Standard / Exigente).

Agrega:
- training_scenarios.scoring_mode  (string, nullable, null=legacy)
- training_batches.scoring_mode    (string, nullable, snapshot al crear)
- tabla scoring_mode_overrides     (overrides editables por SuperAdmin)

Idempotente: usa IF NOT EXISTS.
"""
from app import app
from models import db


def migrate_v6():
    with app.app_context():
        print("[MIGRATE V6] Adding scoring_mode columns and override table...")

        # 1) Columna scoring_mode en training_scenarios
        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'training_scenarios'
                      AND column_name = 'scoring_mode'
                ) THEN
                    ALTER TABLE training_scenarios
                        ADD COLUMN scoring_mode VARCHAR(20);
                    RAISE NOTICE 'Added scoring_mode to training_scenarios';
                ELSE
                    RAISE NOTICE 'training_scenarios.scoring_mode already exists';
                END IF;
            END $$;
        """))

        # 2) Columna scoring_mode en training_batches
        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'training_batches'
                      AND column_name = 'scoring_mode'
                ) THEN
                    ALTER TABLE training_batches
                        ADD COLUMN scoring_mode VARCHAR(20);
                    RAISE NOTICE 'Added scoring_mode to training_batches';
                ELSE
                    RAISE NOTICE 'training_batches.scoring_mode already exists';
                END IF;
            END $$;
        """))

        # 3) Tabla de overrides
        db.session.execute(db.text("""
            CREATE TABLE IF NOT EXISTS scoring_mode_overrides (
                id SERIAL PRIMARY KEY,
                mode VARCHAR(20) UNIQUE NOT NULL,
                config_json TEXT,
                updated_by INTEGER REFERENCES users(id),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        db.session.commit()
        print("[MIGRATE V6] Done.")


if __name__ == '__main__':
    migrate_v6()
