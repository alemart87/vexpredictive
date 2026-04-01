"""
Migration v3: Multi-tenant support.
Creates operativas table, adds operativa_id columns, migrates roles, creates document_reviews.
Safe to run multiple times (uses IF NOT EXISTS / IF EXISTS).
"""
import sys
from app import app
from models import db


def migrate_v3():
    with app.app_context():
        print("[MIGRATE V3] Starting multi-tenant migration...")

        # 1. Create operativas table
        db.session.execute(db.text("""
            CREATE TABLE IF NOT EXISTS operativas (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                slug VARCHAR(255) UNIQUE NOT NULL,
                logo_url VARCHAR(500),
                primary_color VARCHAR(7),
                secondary_color VARCHAR(7),
                accent_color VARCHAR(7),
                description TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_by INTEGER REFERENCES users(id),
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """))
        print("[MIGRATE V3] operativas table ensured.")

        # 2. Add operativa_id to users
        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'users' AND column_name = 'operativa_id'
                ) THEN
                    ALTER TABLE users ADD COLUMN operativa_id INTEGER REFERENCES operativas(id);
                END IF;
            END $$
        """))
        print("[MIGRATE V3] users.operativa_id ensured.")

        # 3. Migrate role: asesor -> operador
        result = db.session.execute(db.text(
            "UPDATE users SET role = 'operador' WHERE role = 'asesor'"
        ))
        if result.rowcount > 0:
            print(f"[MIGRATE V3] Migrated {result.rowcount} users from 'asesor' to 'operador'.")

        # 4. Add operativa_id to categories
        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'categories' AND column_name = 'operativa_id'
                ) THEN
                    ALTER TABLE categories ADD COLUMN operativa_id INTEGER REFERENCES operativas(id);
                END IF;
            END $$
        """))
        print("[MIGRATE V3] categories.operativa_id ensured.")

        # 5. Add operativa_id to contents
        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'contents' AND column_name = 'operativa_id'
                ) THEN
                    ALTER TABLE contents ADD COLUMN operativa_id INTEGER REFERENCES operativas(id);
                END IF;
            END $$
        """))
        print("[MIGRATE V3] contents.operativa_id ensured.")

        # 6. Add operativa_id to training_scenarios
        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'training_scenarios' AND column_name = 'operativa_id'
                ) THEN
                    ALTER TABLE training_scenarios ADD COLUMN operativa_id INTEGER REFERENCES operativas(id);
                END IF;
            END $$
        """))
        print("[MIGRATE V3] training_scenarios.operativa_id ensured.")

        # 7. Create document_reviews table
        db.session.execute(db.text("""
            CREATE TABLE IF NOT EXISTS document_reviews (
                id SERIAL PRIMARY KEY,
                content_id INTEGER NOT NULL REFERENCES contents(id),
                requested_by INTEGER NOT NULL REFERENCES users(id),
                assigned_to INTEGER REFERENCES users(id),
                status VARCHAR(20) DEFAULT 'pending',
                notes TEXT,
                operativa_id INTEGER REFERENCES operativas(id),
                created_at TIMESTAMP DEFAULT NOW(),
                resolved_at TIMESTAMP
            )
        """))
        print("[MIGRATE V3] document_reviews table ensured.")

        db.session.commit()
        print("[MIGRATE V3] Multi-tenant migration complete.")


if __name__ == '__main__':
    migrate_v3()
