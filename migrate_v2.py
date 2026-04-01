"""
Migration v2: Add multi-interaction training support.
Adds: training_batches table, batch_id + interaction_number to training_sessions,
max_concurrent_training to users, vex_profiles table.
Safe to run multiple times.
"""
from app import app, db
from sqlalchemy import text

MIGRATIONS = [
    # User: max_concurrent_training
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS max_concurrent_training INTEGER DEFAULT 1",

    # TrainingBatch table
    """CREATE TABLE IF NOT EXISTS training_batches (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        scenario_id INTEGER NOT NULL REFERENCES training_scenarios(id),
        max_concurrent INTEGER DEFAULT 1,
        status VARCHAR(20) DEFAULT 'active',
        started_at TIMESTAMP DEFAULT NOW(),
        ended_at TIMESTAMP,
        duration_seconds INTEGER DEFAULT 0,
        overall_nps FLOAT,
        overall_correct_rate FLOAT,
        ai_feedback_summary TEXT,
        tokens_used INTEGER DEFAULT 0
    )""",

    # TrainingSession: batch_id + interaction_number
    "ALTER TABLE training_sessions ADD COLUMN IF NOT EXISTS batch_id INTEGER REFERENCES training_batches(id)",
    "ALTER TABLE training_sessions ADD COLUMN IF NOT EXISTS interaction_number INTEGER DEFAULT 1",
    "ALTER TABLE training_sessions ADD COLUMN IF NOT EXISTS case_index INTEGER DEFAULT 0",

    # VexProfile table
    """CREATE TABLE IF NOT EXISTS vex_profiles (
        id SERIAL PRIMARY KEY,
        user_id INTEGER UNIQUE NOT NULL REFERENCES users(id),
        communication_score FLOAT DEFAULT 0,
        empathy_score FLOAT DEFAULT 0,
        resolution_score FLOAT DEFAULT 0,
        speed_score FLOAT DEFAULT 0,
        adaptability_score FLOAT DEFAULT 0,
        compliance_score FLOAT DEFAULT 0,
        overall_score FLOAT DEFAULT 0,
        predictive_index FLOAT DEFAULT 0,
        profile_category VARCHAR(30),
        recommendation VARCHAR(30),
        sessions_analyzed INTEGER DEFAULT 0,
        last_updated TIMESTAMP DEFAULT NOW()
    )""",
]


def run():
    with app.app_context():
        for sql in MIGRATIONS:
            try:
                db.session.execute(text(sql))
                db.session.commit()
                print(f"  OK: {sql[:60]}...")
            except Exception as e:
                db.session.rollback()
                print(f"  SKIP: {str(e)[:80]}")


if __name__ == '__main__':
    print("Running v2 migrations...")
    run()
    print("Done.")
