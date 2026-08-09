"""Create a consistent SQLite backup or run pg_dump for PostgreSQL."""

import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def verify_backup(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("Backup output is empty")
    if path.suffix == ".db":
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as database:
            if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("SQLite backup integrity check failed")
            if not database.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
            ).fetchone():
                raise RuntimeError("SQLite backup contains no tables")


def main() -> Path:
    destination = ROOT / os.getenv("BACKUP_DIR", "backups")
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        pg_dump = shutil.which("pg_dump")
        if not pg_dump:
            raise SystemExit("pg_dump is required to back up PostgreSQL")
        output = destination / f"dwts-{stamp}.sql"
        url = make_url(database_url)
        environment = os.environ.copy()
        if url.password:
            environment["PGPASSWORD"] = url.password
        command = [pg_dump, "--file", str(output)]
        for option, value in {
            "--host": url.host,
            "--port": url.port,
            "--username": url.username,
            "--dbname": url.database,
        }.items():
            if value is not None:
                command.extend([option, str(value)])
        subprocess.run(
            command, check=True, env=environment,
        )
    else:
        source = Path(os.getenv("DATABASE_PATH", "backend/wallet_scores.db"))
        source = source if source.is_absolute() else ROOT / source
        if not source.is_file():
            raise SystemExit(f"SQLite database not found: {source}")
        output = destination / f"dwts-{stamp}.db"
        with sqlite3.connect(source) as current, sqlite3.connect(output) as backup:
            current.backup(backup)
    verify_backup(output)
    print(output)
    return output


if __name__ == "__main__":
    main()
