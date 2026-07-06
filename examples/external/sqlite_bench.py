import os
import sqlite3
import sys
import tempfile
import time

journal_mode = sys.argv[1] if len(sys.argv) > 1 else "DELETE"
synchronous = sys.argv[2] if len(sys.argv) > 2 else "NORMAL"

db_fd, db_path = tempfile.mkstemp(suffix=".db")
os.close(db_fd)

try:
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA journal_mode={journal_mode};")
    conn.execute(f"PRAGMA synchronous={synchronous};")
    conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, data TEXT);")

    start = time.time()
    inserts = 0
    # Execution for exactly 1 second
    while time.time() - start < 1.0:
        conn.execute("INSERT INTO test (data) VALUES ('benchmark');")
        conn.commit()
        inserts += 1

    elapsed = time.time() - start
    print(f"throughput: {inserts / elapsed:.2f}")
    conn.close()
finally:
    if os.path.exists(db_path):
        os.remove(db_path)
