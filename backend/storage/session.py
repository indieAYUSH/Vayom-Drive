import os
import sqlite3
from telethon.sessions.sqlite import SQLiteSession


class RobustSQLiteSession(SQLiteSession):
    """
    Ban-proof SQLite session that ensures WAL mode and a 30s busy timeout,
    eliminating "database is locked" errors during concurrent Telethon updates.
    """
    def _harden_permissions(self):
        """Telethon session files hold account credentials; keep them owner-only."""
        for suffix in ("", "-wal", "-shm"):
            candidate = f"{self.filename}{suffix}"
            if os.path.exists(candidate):
                try:
                    os.chmod(candidate, 0o600)
                except OSError:
                    pass

    def _cursor(self):
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.filename,
                timeout=30.0,
                check_same_thread=False
            )
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute("PRAGMA busy_timeout = 30000;")
            self._harden_permissions()
        return self._conn.cursor()
