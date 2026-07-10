import argparse
import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}  # video types ingest.py accepts

#scans the input folder and finds videos with correct format
#validates each video and extract video metadata
def parse_args():
    parser = argparse.ArgumentParser(
        description="Ingest raw video clips into the HEPHAESTUS clip database."
    )

    parser.add_argument(
        "--input",
        default="inputs/raw_clips",
        type=Path,
        help="Folder containing raw video clips."
    )  # lets the user choose where raw clips are scanned from

    parser.add_argument(
        "--db",
        default="data/clips.db",
        type=Path,
        help="SQLite database path."
    )

    parser.add_argument(
        "--topic",
        default=None,
        help="Topic label for the ingested clips"
        ) #expects a topic

    return parser.parse_args()

def find_video_files(input_dir: Path):
    input_dir.mkdir(parents=True, exist_ok=True)  # create the input folder if it does not exist yet
    videos = []  # stores all supported video files found in the input folder

    for path in sorted(input_dir.iterdir()):  # scan files in a stable order for predictable ingest runs
        if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:  # only accept supported video files
            videos.append(path)  # keep valid candidate videos for processing

    return videos  # return the list of video files to ingest

#creates an id of hexadecimal numbers to avoid duplicates
def file_hash(path: Path):
    algo = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            algo.update(chunk)
    return algo.hexdigest()

#used as a json translator of the string from ffprobe then converts it into python data to be usable
def parse_ffprobe_output(ffprobe_output):
    data = json.loads(ffprobe_output)
    return data

#creating the database and creating the columns where we will save the data
def clips_database(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clips(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path TEXT NOT NULL,
        file_name TEXT NOT NULL,
        file_hash TEXT NOT NULL UNIQUE,
        duration_seconds REAL NOT NULL,
        width INTEGER,
        height INTEGER,
        fps REAL,
        format_name TEXT,
        codec_name TEXT,
        topic TEXT,
        source_url TEXT,
        source_platform TEXT DEFAULT 'manual',
        creator_name TEXT,
        permission_status TEXT DEFAULT 'unknown',
        risk_level TEXT DEFAULT 'unknown',
        status TEXT DEFAULT 'ingested',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)

    conn.commit()   # save schema changes
    return conn

#inserting clip and adding timestamps
def insert_clip(conn, file_path, file_name, file_hash, duration_seconds, width, height, fps, format_name, codec_name, topic):
    cursor = conn.cursor()

    #Get current UTC time with timezone awareness
    now = datetime.now(timezone.utc).isoformat()

    try:
        cursor.execute("""
        INSERT INTO clips (
            file_path, file_name, file_hash, duration_seconds, width, height, fps, format_name, codec_name, topic,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (file_path, file_name, file_hash, duration_seconds, width, height, fps, format_name, codec_name, topic, now, now))

        conn.commit()
        return "inserted"  # tell the caller the clip was saved successfully
    except sqlite3.IntegrityError:
        print(f"Duplicate detected: file_hash {file_hash} already exists.")
        return "duplicate"  # tell the caller this clip was already in the database

# Extract the fields HEPHAESTUS needs from ffprobe metadata.
def extract_video_metadata(metadata):
    video_stream = None
    for stream in metadata.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    if not video_stream:
        raise RuntimeError("No video stream found in file.")

    duration = float(metadata.get("format", {}).get("duration", 0))
    width = video_stream.get("width")
    height = video_stream.get("height")
    fps_str = video_stream.get("r_frame_rate", "0/1")
    num, denom = fps_str.split("/")
    fps = float(num) / float(denom) if denom != "0" else 0.0
    format_name = metadata.get("format", {}).get("format_name")
    codec_name = video_stream.get("codec_name")

    return {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "format_name": format_name,
        "codec_name": codec_name
    }

# Run ffprobe and return video metadata as Python data.
def probe_video(path: Path):
    result = subprocess.run (
        [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path)
        ],
        capture_output=True,  # capture stdout/stderr
        text=True        
    )
    if (result.returncode != 0):
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    return parse_ffprobe_output(result.stdout)

def ingest_file(conn, path: Path, topic):
    clip_hash = file_hash(path)  # create a stable fingerprint for duplicate detection
    raw_metadata = probe_video(path)  # ask ffprobe for raw video metadata
    metadata = extract_video_metadata(raw_metadata)  # keep only the fields HEPHAESTUS stores

    return insert_clip(  # save the validated clip record into SQLite
        conn,
        str(path),
        path.name,
        clip_hash,
        metadata["duration_seconds"],
        metadata["width"],
        metadata["height"],
        metadata["fps"],
        metadata["format_name"],
        metadata["codec_name"],
        topic
    )

def main():
    args = parse_args()  # read terminal options such as --input, --db, and --topic
    conn = clips_database(args.db)  # create/connect to the SQLite clip database
    videos = find_video_files(args.input)  # find supported raw clips to ingest

    inserted = 0  # count newly inserted clips
    duplicates = 0  # count clips skipped because their hash already exists
    failed = []  # store files that failed and why

    for video_path in videos:  # process each candidate video one by one
        try:
            status = ingest_file(conn, video_path, args.topic)  # hash, probe, extract, and insert the clip
            if status == "inserted":  # update success counter
                inserted += 1
            elif status == "duplicate":  # update duplicate counter
                duplicates += 1
        except Exception as error:
            failed.append((video_path.name, str(error)))  # keep failure details for the final report

    conn.close()  # close the database connection after ingest finishes

    print(f"Found {len(videos)} video files")  # report how many supported files were discovered
    print(f"Inserted {inserted} new clips")  # report new rows saved to SQLite
    print(f"Skipped {duplicates} duplicates")  # report duplicate files skipped by hash
    print(f"Failed {len(failed)} files")  # report files that could not be processed

    if failed:  # print specific failure reasons only when there are failures
        print("Failed files:")
        for file_name, reason in failed:
            print(f"- {file_name}: {reason}")  # show the exact file and error for debugging

if __name__ == "__main__":
    main()  # run the ingest workflow when this file is executed directly
