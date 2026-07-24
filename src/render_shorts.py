import argparse
import sqlite3
import subprocess
import tempfile
import re
from datetime import datetime, timezone
from pathlib import Path

# Parse command-line options for the render workflow.
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

# Connect to SQLite and ensure the shorts preview table exists.
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

# Fetch ingested clips from the clips table, optionally filtered by topic.
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

# Run an FFmpeg command and raise a clear error if it fails.
def run_ffmpeg(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {result.stderr.strip()}")
    return result

# Normalize one clip by trimming, filtering, and converting video/audio formats.
def normalize_clip(input_file, output_file,
                   start_time=None, end_time=None, duration=None,
                   crop=None, scale=None, video_filter=None):
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
    if video_filter:
        command += ["-vf", video_filter]
    else:
        filters = []
        if crop:
            w, h, x, y = crop
            filters.append(f"crop={w}:{h}:{x}:{y}")
        if scale:
            sw, sh = scale
            filters.append(f"scale={sw}:{sh}")
        if filters:
            command += ["-vf", ",".join(filters)]

    # Normalize formats (audio and video) so concat can join clips reliably.
    command += [
        "-r", "30",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        "-movflags", "+faststart"
    ]

    # Output file
    command.append(str(output_file))

    # Run the command
    return run_ffmpeg(command)

# Write FFmpeg concat-list text for the normalized clip files.
def write_concat_file(clips, concat_file):
    concat_file = Path(concat_file)

    with concat_file.open("w", encoding="utf-8") as f:
        for clip in clips:
            f.write(f"file '{Path(clip)}'\n")

    return concat_file

# Join normalized clips into one output video using FFmpeg concat mode.
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

# Build a timestamped output path for the rendered preview Short.
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

# Render selected clips into one vertical preview Short.
def render_short(clips, output_dir, topic, max_duration):
    scale = (1080, 1920)
    width, height = scale

    if not clips:
        raise RuntimeError("No clips available to render.")
  
    output_file = build_output_path(output_dir, topic)

    duration_per_clip = max_duration / len(clips)

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        normalized_clips = []

        for index, clip in enumerate(clips):
            input_file = Path(clip["file_path"])
            temp_output = temp_dir / f"clip_{index}.mp4"

            normalize_clip(
                input_file,
                temp_output,
                duration=duration_per_clip,
                video_filter=f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
            )

            normalized_clips.append(temp_output)

        concat_file = temp_dir / "concat.txt"
        write_concat_file(normalized_clips, concat_file)
        concat_clips(concat_file, output_file)

    return output_file

# Save a rendered Short preview record into the shorts table.
def insert_short_record(conn, output_path, topic, clip_count, target_duration_seconds):
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    cursor.execute("""
    INSERT INTO shorts (
        output_path,
        topic,
        clip_count,
        target_duration_seconds,
        status,
        created_at,
        updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        str(output_path),
        topic,
        clip_count,
        target_duration_seconds,
        "preview",
        now,
        now
    ))

    conn.commit()
    return cursor.lastrowid

# Split clips into existing files and missing file paths.
def validate_clip_paths(clips):
    valid_clips = []
    missing_clips = []

    for clip in clips:
        path = Path(clip["file_path"])
        if path.exists() and path.is_file():
            valid_clips.append(clip)
        else:
            missing_clips.append(clip)
    
    return valid_clips, missing_clips

# Run the full render workflow from CLI args to saved preview record.
def main():
    args = parse_args()
    conn = connect_database(args.db)
    try:
        clips = fetch_clips(conn, args.topic, args.limit)
        valid_clips, missing_clips = validate_clip_paths(clips)

        if not valid_clips:
            print("There are no valid clips to render.")
            if missing_clips:
                print("Missing clips:")
                for clip in missing_clips:
                    print(f"- {clip['file_path']}")
            return

        output_path = render_short(
            valid_clips,
            args.output,
            args.topic,
            args.max_duration
        )

        short_id = insert_short_record(
            conn,
            output_path,
            args.topic,
            len(valid_clips),
            args.max_duration
        )

        print(f"Rendered Short ID: {short_id}")
        print(f"Output: {output_path}")
        print(f"Used clips: {len(valid_clips)}")
        print(f"Missing clips skipped: {len(missing_clips)}")
        if missing_clips:
            print("Missing clips:")
            for clip in missing_clips:
                print(f"- {clip['file_path']}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
