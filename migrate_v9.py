"""
Migration v9: Imagenes en mensajes de entrenamiento + imagenes por caso.

Agrega:
- training_messages.images  TEXT NULL  (JSON array de URLs que el cliente
  simulado "envio" en ese mensaje)

Las imagenes por caso se guardan dentro del JSON de scenario.client_persona
(campo "images" por caso), no requieren columna nueva.

Idempotente: usa IF NOT EXISTS.
"""
from app import app
from models import db


def migrate_v9():
    with app.app_context():
        print("[MIGRATE V9] Adding images column to training_messages...")

        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'training_messages'
                      AND column_name = 'images'
                ) THEN
                    ALTER TABLE training_messages ADD COLUMN images TEXT;
                    RAISE NOTICE 'Added training_messages.images';
                ELSE
                    RAISE NOTICE 'training_messages.images already exists';
                END IF;
            END $$;
        """))

        db.session.commit()
        print("[MIGRATE V9] Done.")


if __name__ == '__main__':
    migrate_v9()
