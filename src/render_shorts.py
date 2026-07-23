import argparse
import sqlite3
import subprocess
import tempfile
import os
import re
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


# High-level editor
def normalize_clip(input_file, output_file,
                   start_time=None, end_time=None, duration=None,
                   crop=None, scale=None):
    command = ["ffmpeg", "-y"]

    # Trim
    if start_time is not None:
        command += ["-ss", str(start_time)]
    command += ["-i", str(input_file)]
    if duration is not None:
        command += ["-t", str(duration)]
    if end_time is not None:
        command += ["-to", str(end_time)]

    # Filters
    filters = []
    if crop:
        w, h, x, y = crop
        filters.append(f"crop={w}:{h}:{x}:{y}")
    if scale:
        sw, sh = scale
        filters.append(f"scale={sw}:{sh}")
    if filters:
        command += ["-vf", ",".join(filters)]

    # Normalize formats (audio and video)
    command += ["-c:v", "libx264", "-c:a", "aac"]

    # Output file
    command.append(str(output_file))

    # Run the command
    return run_ffmpeg(command)


def write_concat_file(clips, concat_file):
    concat_file = Path(concat_file)

    with concat_file.open("w", encoding="utf-8") as f:
        for clip in clips:
            f.write(f"file '{Path(clip)}'\n")

    return concat_file


def concat_clips(concat_file, output_file):
    concat_file = Path(concat_file)
    output_file = Path(output_file)

    output_file.parent.mkdir(parents=True, exist_ok=True)

    command = ["ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy",
                str(output_file)]
    
    run_ffmpeg(command)

    return output_file

def build_output_path(output_dir, topic):
    output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    utc_time = datetime.now(timezone.utc)
    timestamp = utc_time.strftime("%Y%m%d_%H%M%S")

    if topic:
        safe_name = re.sub(r'[<>:"/\\|?*\s]', "_", topic)
        safe_name = re.sub(r'_+', "_", safe_name)
        safe_name = safe_name.strip("_")
        safe_name = f"{safe_name}_{timestamp}.mp4"
    else:
        safe_name = f"short_{timestamp}.mp4"

    return output_dir / safe_name