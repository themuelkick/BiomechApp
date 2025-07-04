import sqlite3

DB_PATH = "pitcher_biomech.db"

def migrate_schema():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Step 1: Check if old schema exists and already has video_source
    c.execute("PRAGMA table_info(sessions)")
    columns = [row[1] for row in c.fetchall()]
    if "video_source" in columns:
        print("✅ Migration not needed — 'video_source' already exists.")
        return

    print("🚧 Starting migration...")

    # Step 2: Rename old table
    c.execute("ALTER TABLE sessions RENAME TO sessions_old")

    # Step 3: Create new sessions table
    c.execute("""
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER,
            date TEXT,
            session_name TEXT,
            video_source TEXT,
            kinovea_csv TEXT,
            notes TEXT,
            FOREIGN KEY(player_id) REFERENCES players(id)
        )
    """)

    # Step 4: Copy data (map youtube_link to video_source)
    c.execute("""
        INSERT INTO sessions (id, player_id, date, session_name, video_source, kinovea_csv, notes)
        SELECT id, player_id, date, session_name, youtube_link, kinovea_csv, notes FROM sessions_old
    """)

    # Step 5: Drop the old table
    c.execute("DROP TABLE sessions_old")

    conn.commit()
    conn.close()
    print("✅ Migration complete. 'youtube_link' replaced with 'video_source'.")

if __name__ == "__main__":
    migrate_schema()
