import argparse
import sqlite3
import subprocess
import tempfile
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

def parse_args():
    parser = argparse.ArgumentParser(
        description="Render ingested clips into a preview Short."
    )

    parser.add_argument(
        "--db",
        default="data/clips.db",
        type=Path,
        help="Path containing the database."
    )

    parser.add_argument(
        "--output",
        default="outputs/previews",
        type=Path,
        help="Folder where rendered preview Shorts are saved."
    )

    parser.add_argument(
        "--topic",
        default=None,
        help="Only render clips matching this topic."
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="limit of how many clips to use per short video"
    )

    parser.add_argument(
        "--max-duration",
        type=float,
        default=60,
        help="The maximum length of each short video."
    )

    return parser.parse_args()

#saving records for rendered shorts previews
def connect_database(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS shorts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        output_path TEXT NOT NULL,
        topic TEXT,
        clip_count INTEGER NOT NULL,
        target_duration_seconds REAL,
        status TEXT DEFAULT 'preview',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    conn.commit()
    return conn

#fetching clips from the database that is needed
def fetch_clips(conn, topic, limit):
    cursor = conn.cursor()

    if topic is None:
        # No topic filter → fetch all ingested clips
        cursor.execute("""
        SELECT id, file_path, duration_seconds, width, height, fps, topic
        FROM clips
        WHERE status = 'ingested'
        LIMIT ?
        """, (limit,))
    else:
        # Filter by topic if provided
        cursor.execute("""
        SELECT id, file_path, duration_seconds, width, height, fps, topic
        FROM clips
        WHERE status = 'ingested'
        AND topic = ?
        LIMIT ?
        """, (topic, limit))

    rows = cursor.fetchall()

    clips = []
    for row in rows:
        clips.append({
            "id": row[0],
            "file_path": row[1],
            "duration_seconds": row[2],
            "width": row[3],
            "height": row[4],
            "fps": row[5],
            "topic": row[6]
        })

    return clips

#handles running the ffmpeg (generic runner)
def run_ffmpeg(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {result.stderr.strip()}")
    return result

def render_short():
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, "tmp.txt")

        with open(filepath,"w") as f:
            f.write("sample")

        with open(filepath, "r") as r:
            print(r.read())
