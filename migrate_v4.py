"""
Migration v4: Add profile_photo column to users table.
Safe to run multiple times (uses IF NOT EXISTS).
"""
import sys
from app import app
from models import db


def migrate_v4():
    with app.app_context():
        print("[MIGRATE V4] Starting profile photo migration...")

        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'users' AND column_name = 'profile_photo'
                ) THEN
                    ALTER TABLE users ADD COLUMN profile_photo VARCHAR(500);
                    RAISE NOTICE 'Added profile_photo column to users';
                ELSE
                    RAISE NOTICE 'profile_photo column already exists';
                END IF;
            END $$;
        """))

        db.session.commit()
        print("[MIGRATE V4] Done.")


if __name__ == '__main__':
    migrate_v4()
