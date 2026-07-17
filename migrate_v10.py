"""
Migration v10: Modulo de entrenamiento por VOZ (Realtime API).

Agrega:
- training_scenarios.voice_name  VARCHAR(30) NULL  (voz del cliente simulado,
  la elige quien configura el escenario; null = default del sistema)
- Tablas voice_sessions y voice_turns (las crea db.create_all() al arrancar,
  pero las creamos aca tambien para que la migracion sea autosuficiente).

Idempotente: usa IF NOT EXISTS.
"""
from app import app
from models import db


def migrate_v10():
    with app.app_context():
        print("[MIGRATE V10] Voice training module...")

        # Columna voice_name en escenarios
        db.session.execute(db.text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'training_scenarios'
                      AND column_name = 'voice_name'
                ) THEN
                    ALTER TABLE training_scenarios ADD COLUMN voice_name VARCHAR(30);
                    RAISE NOTICE 'Added training_scenarios.voice_name';
                ELSE
                    RAISE NOTICE 'training_scenarios.voice_name already exists';
                END IF;
            END $$;
        """))

        # Tablas nuevas (idempotente via IF NOT EXISTS)
        db.session.execute(db.text("""
            CREATE TABLE IF NOT EXISTS voice_sessions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                scenario_id INTEGER NOT NULL REFERENCES training_scenarios(id),
                case_index INTEGER DEFAULT 0,
                scoring_mode VARCHAR(20),
                voice_name VARCHAR(30),
                status VARCHAR(20) DEFAULT 'active',
                started_at TIMESTAMP,
                ended_at TIMESTAMP,
                duration_seconds INTEGER DEFAULT 0,
                openai_session_id VARCHAR(100),
                last_heartbeat TIMESTAMP,
                total_turns INTEGER DEFAULT 0,
                total_words_user INTEGER DEFAULT 0,
                talk_ratio FLOAT DEFAULT 0,
                avg_response_latency FLOAT DEFAULT 0,
                speech_rate_wpm FLOAT DEFAULT 0,
                interruptions INTEGER DEFAULT 0,
                long_silences INTEGER DEFAULT 0,
                nps_score INTEGER,
                response_correct BOOLEAN,
                filler_words INTEGER DEFAULT 0,
                ai_feedback TEXT,
                tokens_used INTEGER DEFAULT 0,
                estimated_cost_usd FLOAT DEFAULT 0,
                created_at TIMESTAMP
            );
        """))

        db.session.execute(db.text("""
            CREATE TABLE IF NOT EXISTS voice_turns (
                id SERIAL PRIMARY KEY,
                session_id INTEGER NOT NULL REFERENCES voice_sessions(id),
                role VARCHAR(20) NOT NULL,
                transcript TEXT NOT NULL,
                started_at_ms BIGINT DEFAULT 0,
                ended_at_ms BIGINT DEFAULT 0,
                word_count INTEGER DEFAULT 0,
                created_at TIMESTAMP
            );
        """))

        db.session.execute(db.text("""
            CREATE INDEX IF NOT EXISTS idx_voice_sessions_user ON voice_sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_voice_turns_session ON voice_turns(session_id);
        """))

        db.session.commit()
        print("[MIGRATE V10] Done.")


if __name__ == '__main__':
    migrate_v10()
