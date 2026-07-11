import argparse
import sqlite3
import subprocess
import tempfile
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

