
import sqlite3
import re
from pathlib import Path

DB_PATH = Path(__file__).with_name("behruz_memory.db")


def connect():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
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
                    ON DELETE CASCADE
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT 'legacy',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, content)
            )
        """)

        # Eski bazaga user_id ustunlarini xavfsiz qo'shish.
        conversation_columns = {
            row["name"]
            for row in con.execute(
                "PRAGMA table_info(conversations)"
            ).fetchall()
        }

        if "user_id" not in conversation_columns:
            con.execute("""
                ALTER TABLE conversations
                ADD COLUMN user_id TEXT NOT NULL DEFAULT 'legacy'
            """)

        memory_columns = {
            row["name"]
            for row in con.execute(
                "PRAGMA table_info(memories)"
            ).fetchall()
        }

        if "user_id" not in memory_columns:
            con.execute("""
                ALTER TABLE memories
                ADD COLUMN user_id TEXT NOT NULL DEFAULT 'legacy'
            """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_user
            ON conversations(user_id, id)
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id, id)
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_user
            ON memories(user_id, id)
        """)


def clean_user_id(user_id):
    if not isinstance(user_id, str):
        return "legacy"

    user_id = user_id.strip()

    if not user_id or len(user_id) > 100:
        return "legacy"

    return user_id


def clean_title(text):
    text = re.sub(r"\s+", " ", str(text)).strip()

    if not text:
        return "Yangi suhbat"

    if len(text) > 60:
        text = text[:57].rstrip() + "..."

    return text


def create_conversation(title="Yangi suhbat", user_id="legacy"):
    user_id = clean_user_id(user_id)
    title = clean_title(title)

    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO conversations (title, user_id)
            VALUES (?, ?)
            """,
            (title, user_id)
        )

        return cur.lastrowid


def conversation_belongs_to(conversation_id, user_id):
    user_id = clean_user_id(user_id)

    with connect() as con:
        row = con.execute(
            """
            SELECT id
            FROM conversations
            WHERE id = ? AND user_id = ?
            """,
            (conversation_id, user_id)
        ).fetchone()

    return row is not None


def save_message(
    conversation_id,
    role,
    content,
    user_id="legacy"
):
    user_id = clean_user_id(user_id)

    if role not in ("user", "assistant"):
        raise ValueError("Noto'g'ri xabar turi")

    content = str(content)[:10000]

    with connect() as con:
        conversation = con.execute(
            """
            SELECT id, title
            FROM conversations
            WHERE id = ? AND user_id = ?
            """,
            (conversation_id, user_id)
        ).fetchone()

        if conversation is None:
            raise ValueError("Suhbat topilmadi yoki ruxsat yo'q")

        con.execute(
            """
            INSERT INTO messages (conversation_id, role, content)
            VALUES (?, ?, ?)
            """,
            (conversation_id, role, content)
        )

        # Yangi suhbat nomini birinchi foydalanuvchi xabaridan olamiz.
        if role == "user" and conversation["title"] == "Yangi suhbat":
            title = clean_title(content)

            if title != "Yangi suhbat":
                con.execute(
                    """
                    UPDATE conversations
                    SET title = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (title, conversation_id, user_id)
                )


def get_messages(conversation_id, limit=100, user_id="legacy"):
    user_id = clean_user_id(user_id)
    limit = min(max(int(limit), 1), 1000)

    with connect() as con:
        rows = con.execute(
            """
            SELECT m.role, m.content, m.created_at
            FROM messages AS m
            JOIN conversations AS c
                ON c.id = m.conversation_id
            WHERE c.id = ? AND c.user_id = ?
            ORDER BY m.id DESC
            LIMIT ?
            """,
            (conversation_id, user_id, limit)
        ).fetchall()

    return [dict(row) for row in reversed(rows)]


def list_conversations(limit=50, user_id="legacy"):
    user_id = clean_user_id(user_id)
    limit = min(max(int(limit), 1), 100)

    with connect() as con:
        rows = con.execute(
            """
            SELECT id, title, created_at
            FROM conversations
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit)
        ).fetchall()

    return [dict(row) for row in rows]


def remember(content, user_id="legacy"):
    user_id = clean_user_id(user_id)
    content = str(content).strip()[:500]

    if not content:
        return

    with connect() as con:
        con.execute(
            """
            INSERT OR IGNORE INTO memories (content, user_id)
            VALUES (?, ?)
            """,
            (content, user_id)
        )


def get_memories(user_id="legacy"):
    user_id = clean_user_id(user_id)

    with connect() as con:
        rows = con.execute(
            """
            SELECT id, content, created_at
            FROM memories
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (user_id,)
        ).fetchall()

    return [dict(row) for row in rows]


def forget(memory_id, user_id="legacy"):
    user_id = clean_user_id(user_id)

    with connect() as con:
        con.execute(
            """
            DELETE FROM memories
            WHERE id = ? AND user_id = ?
            """,
            (memory_id, user_id)
        )


def get_recent_context(
    exclude_conversation_id=None,
    limit=12,
    user_id="legacy"
):
    user_id = clean_user_id(user_id)
    limit = min(max(int(limit), 1), 50)

    with connect() as con:
        if exclude_conversation_id is None:
            rows = con.execute(
                """
                SELECT c.title, m.role, m.content, m.created_at
                FROM messages AS m
                JOIN conversations AS c
                    ON c.id = m.conversation_id
                WHERE c.user_id = ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, limit)
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT c.title, m.role, m.content, m.created_at
                FROM messages AS m
                JOIN conversations AS c
                    ON c.id = m.conversation_id
                WHERE c.user_id = ?
                  AND c.id != ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, exclude_conversation_id, limit)
            ).fetchall()

    return [dict(row) for row in reversed(rows)]


def delete_conversation(conversation_id, user_id="legacy"):
    user_id = clean_user_id(user_id)

    with connect() as con:
        cur = con.execute(
            """
            DELETE FROM conversations
            WHERE id = ? AND user_id = ?
            """,
            (conversation_id, user_id)
        )

        return cur.rowcount > 0


init_db()
