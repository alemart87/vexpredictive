"""
Migration script: Originally imported HTML content into PostgreSQL.
Now a no-op since the platform starts with a clean slate.
"""


def migrate():
    print("[MIGRATE] Content migration disabled - platform starts empty.")
    return


if __name__ == '__main__':
    migrate()
