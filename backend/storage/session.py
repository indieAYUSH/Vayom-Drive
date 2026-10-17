import sqlite3
from telethon.sessions.sqlite import SQLiteSession


class RobustSQLiteSession(SQLiteSession):
    """
    Ban-proof SQLite session that ensures WAL mode and a 30s busy timeout,
    eliminating "database is locked" errors during concurrent Telethon updates.
    """
    def _cursor(self):
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.filename,
                timeout=30.0,
                check_same_thread=False
            )
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute("PRAGMA busy_timeout = 30000;")
        return self._conn.cursor()
