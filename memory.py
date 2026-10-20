
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("behruz_memory.db")


def connect():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL DEFAULT 'Yangi suhbat',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(id)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def create_conversation(title="Yangi suhbat"):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO conversations (title) VALUES (?)",
            (title[:100],)
        )
        return cur.lastrowid


def save_message(conversation_id, role, content):
    if role not in ("user", "assistant"):
        raise ValueError("Noto'g'ri xabar turi")
    with connect() as con:
        con.execute(
            "INSERT INTO messages (conversation_id, role, content) "
            "VALUES (?, ?, ?)",
            (conversation_id, role, content[:10000])
        )


def get_messages(conversation_id, limit=100):
    with connect() as con:
        rows = con.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, min(max(limit, 1), 200))
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def list_conversations(limit=50):
    with connect() as con:
        rows = con.execute(
            "SELECT id, title, created_at FROM conversations "
            "ORDER BY id DESC LIMIT ?",
            (min(max(limit, 1), 100),)
        ).fetchall()
    return [dict(row) for row in rows]


def remember(content):
    content = content.strip()[:500]
    if not content:
        return
    with connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO memories (content) VALUES (?)",
            (content,)
        )


def get_memories():
    with connect() as con:
        rows = con.execute(
            "SELECT id, content, created_at FROM memories ORDER BY id DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def forget(memory_id):
    with connect() as con:
        con.execute("DELETE FROM memories WHERE id = ?", (memory_id,))


init_db()
def get_recent_context(exclude_conversation_id=None, limit=12):
    """Boshqa suhbatlardagi so‘nggi xabarlarni topadi."""
    with connect() as con:
        if exclude_conversation_id is None:
            rows = con.execute("""
                SELECT c.title, m.role, m.content, m.created_at
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                ORDER BY m.id DESC
                LIMIT ?
            """, (limit,)).fetchall()
        else:
            rows = con.execute("""
                SELECT c.title, m.role, m.content, m.created_at
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE m.conversation_id != ?
                ORDER BY m.id DESC
                LIMIT ?
            """, (exclude_conversation_id, limit)).fetchall()

    return [dict(row) for row in reversed(rows)]

def delete_conversation(conversation_id):
    """Suhbatni va uning barcha xabarlarini bazadan o‘chiradi."""
    with connect() as con:
        exists = con.execute(
            "SELECT id FROM conversations WHERE id = ?",
            (conversation_id,)
        ).fetchone()

        if exists is None:
            return False

        con.execute(
            "DELETE FROM messages WHERE conversation_id = ?",
            (conversation_id,)
        )
        con.execute(
            "DELETE FROM conversations WHERE id = ?",
            (conversation_id,)
        )

    return True

