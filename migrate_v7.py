"""
Migration v7: Soft delete & purge de usuarios.

Agrega:
- users.is_purged       BOOLEAN DEFAULT FALSE  (PII anonimizada)
- users.purged_at       TIMESTAMP NULL
- users.deactivated_at  TIMESTAMP NULL

Idempotente: usa IF NOT EXISTS.
"""
from app import app
from models import db


def migrate_v7():
    with app.app_context():
        print("[MIGRATE V7] Adding soft-delete + purge fields to users...")

        for col, ddl in [
            ('is_purged',      'BOOLEAN DEFAULT FALSE'),
            ('purged_at',      'TIMESTAMP'),
            ('deactivated_at', 'TIMESTAMP'),
        ]:
            db.session.execute(db.text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'users' AND column_name = '{col}'
                    ) THEN
                        ALTER TABLE users ADD COLUMN {col} {ddl};
                        RAISE NOTICE 'Added users.{col}';
                    ELSE
                        RAISE NOTICE 'users.{col} already exists';
                    END IF;
                END $$;
            """))

        db.session.commit()
        print("[MIGRATE V7] Done.")


if __name__ == '__main__':
    migrate_v7()
