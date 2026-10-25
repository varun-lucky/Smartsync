import sqlite3
SCHEMA = """
CREATE TABLE IF NOT EXISTS blobs(hash TEXT PRIMARY KEY, cloud_key TEXT NOT NULL, orig_size INT, stored_size INT, ref_count INT DEFAULT 1, encrypted INT DEFAULT 1);
CREATE TABLE IF NOT EXISTS files(file_id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, current_version_id INT, size INT, mtime REAL, state TEXT DEFAULT 'queued', sensitive INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS versions(version_id INTEGER PRIMARY KEY, file_id INT NOT NULL REFERENCES files(file_id), hash TEXT NOT NULL REFERENCES blobs(hash), size INT, created_at REAL, device_id TEXT);
CREATE TABLE IF NOT EXISTS sync_log(log_id INTEGER PRIMARY KEY, path TEXT, phase TEXT CHECK(phase IN ('intent','done')), ts REAL, details TEXT);
CREATE TABLE IF NOT EXISTS audit_log(event_id INTEGER PRIMARY KEY, actor TEXT, action TEXT, target TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS conflicts(conflict_id INTEGER PRIMARY KEY, path TEXT NOT NULL, local_hash TEXT NOT NULL, cloud_hash TEXT NOT NULL, base_hash TEXT, created_at REAL NOT NULL, resolved_at REAL, resolution TEXT);
CREATE TABLE IF NOT EXISTS blocks(hash TEXT PRIMARY KEY, cloud_key TEXT NOT NULL, size INT NOT NULL, stored_size INT NOT NULL, ref_count INT DEFAULT 1);
CREATE TABLE IF NOT EXISTS version_blocks(version_id INT NOT NULL REFERENCES versions(version_id), ordinal INT NOT NULL, block_hash TEXT NOT NULL REFERENCES blocks(hash), PRIMARY KEY(version_id,ordinal));
CREATE TABLE IF NOT EXISTS cloud_outbox(outbox_id INTEGER PRIMARY KEY, path TEXT NOT NULL, action TEXT NOT NULL, content_hash TEXT NOT NULL, created_at REAL NOT NULL, acknowledged_at REAL);
CREATE INDEX IF NOT EXISTS ix_files_state ON files(state);
CREATE INDEX IF NOT EXISTS ix_files_mtime ON files(mtime);
CREATE INDEX IF NOT EXISTS ix_ver_file ON versions(file_id);
CREATE INDEX IF NOT EXISTS ix_ver_hash ON versions(hash);
"""
def connect(path, ro=False):
    c = sqlite3.connect(f"file:{path}?mode=ro" if ro else path, uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    if not ro: c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA foreign_keys=ON")
    return c
def init(path):
    c = connect(path); c.executescript(SCHEMA); c.commit(); c.close()
