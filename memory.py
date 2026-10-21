import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("behruz_memory.db")


def connect():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _columns(con, table):
    return {
        row[1]
        for row in con.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    }


def init_db():
    with connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL DEFAULT 'Yangi suhbat',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT
            )
        """)

        if "user_id" not in _columns(con, "conversations"):
            con.execute(
                "ALTER TABLE conversations ADD COLUMN user_id TEXT"
            )

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

        # Eski umumiy xotira jadvalini foydalanuvchilar bo‘yicha ajratish.
        con.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        memory_columns = _columns(con, "memories")

        if "user_id" not in memory_columns:
            con.execute(
                "ALTER TABLE memories RENAME TO memories_legacy"
            )

            con.execute("""
                CREATE TABLE memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            old_columns = _columns(con, "memories_legacy")

            if {"id", "content", "created_at"}.issubset(old_columns):
                con.execute("""
                    INSERT INTO memories
                        (id, user_id, content, created_at)
                    SELECT id, 'legacy', content, created_at
                    FROM memories_legacy
                """)

            con.execute("DROP TABLE memories_legacy")

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


def create_conversation(title="Yangi suhbat", user_id=None):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO conversations (title, user_id) VALUES (?, ?)",
            ((title or "Yangi suhbat")[:100], user_id),
        )
        return cur.lastrowid


def conversation_belongs_to(conversation_id, user_id):
    if not user_id:
        return False

    with connect() as con:
        row = con.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()

    return row is not None


def save_message(conversation_id, role, content, user_id=None):
    if role not in ("user", "assistant"):
        raise ValueError("Noto‘g‘ri xabar turi")

    content = str(content or "")[:10000]

    with connect() as con:
        conversation = con.execute(
            "SELECT title, user_id FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()

        if conversation is None:
            raise ValueError("Suhbat topilmadi")

        if (
            user_id is not None
            and conversation["user_id"] != user_id
        ):
            raise ValueError(
                "Bu suhbat ushbu foydalanuvchiga tegishli emas"
            )

        con.execute(
            "INSERT INTO messages "
            "(conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, content),
        )

        if (
            role == "user"
            and (conversation["title"] or "Yangi suhbat")
            == "Yangi suhbat"
        ):
            title = " ".join(content.split())[:60].strip()

            if title:
                con.execute(
                    "UPDATE conversations SET title = ? WHERE id = ?",
                    (title, conversation_id),
                )def get_messages(conversation_id, limit=100, user_id=None):
    with connect() as con:
        if user_id is not None:
            owner = con.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            ).fetchone()

            if owner is None:
                return []

        rows = con.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, min(max(int(limit), 1), 1000)),
        ).fetchall()

    return [dict(row) for row in reversed(rows)]


def list_conversations(limit=50, user_id=None):
    with connect() as con:
        if user_id is None:
            rows = con.execute(
                "SELECT id, title, created_at FROM conversations "
                "ORDER BY id DESC LIMIT ?",
                (min(max(int(limit), 1), 100),),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, title, created_at FROM conversations "
                "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, min(max(int(limit), 1), 100)),
            ).fetchall()

    return [dict(row) for row in rows]


def remember(content, user_id=None):
    content = str(content or "").strip()[:500]

    if not content or not user_id:
        return False

    with connect() as con:
        existing = con.execute(
            "SELECT id FROM memories "
            "WHERE user_id = ? AND lower(content) = lower(?)",
            (user_id, content),
        ).fetchone()

        if existing:
            return False

        con.execute(
            "INSERT INTO memories (user_id, content) VALUES (?, ?)",
            (user_id, content),
        )

    return True


def get_memories(user_id=None, limit=30):
    if not user_id:
        return []

    with connect() as con:
        rows = con.execute(
            "SELECT id, content, created_at FROM memories "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, min(max(int(limit), 1), 100)),
        ).fetchall()

    return [dict(row) for row in rows]


def forget(memory_id, user_id=None):
    if not user_id:
        return False

    with connect() as con:
        cur = con.execute(
            "DELETE FROM memories WHERE id = ? AND user_id = ?",
            (memory_id, user_id),
        )

    return cur.rowcount > 0


def forget_matching(content, user_id=None):
    """Faqat shu foydalanuvchining mos xotirasini o‘chiradi."""
    content = str(content or "").strip()[:500]

    if not content or not user_id:
        return 0

    with connect() as con:
        cur = con.execute(
            "DELETE FROM memories "
            "WHERE user_id = ? AND lower(content) LIKE lower(?)",
            (user_id, f"%{content}%"),
        )

    return cur.rowcountdef get_recent_context(
    exclude_conversation_id=None,
    limit=12,
    user_id=None,
):
    if not user_id:
        return []

    limit = min(max(int(limit), 1), 50)

    with connect() as con:
        if exclude_conversation_id is None:
            rows = con.execute("""
                SELECT c.title, m.role, m.content, m.created_at
                FROM messages m
                JOIN conversations c
                    ON c.id = m.conversation_id
                WHERE c.user_id = ?
                ORDER BY m.id DESC
                LIMIT ?
            """, (user_id, limit)).fetchall()
        else:
            rows = con.execute("""
                SELECT c.title, m.role, m.content, m.created_at
                FROM messages m
                JOIN conversations c
                    ON c.id = m.conversation_id
                WHERE c.user_id = ?
                  AND m.conversation_id != ?
                ORDER BY m.id DESC
                LIMIT ?
            """, (
                user_id,
                exclude_conversation_id,
                limit,
            )).fetchall()

    return [dict(row) for row in reversed(rows)]


def delete_conversation(conversation_id, user_id=None):
    with connect() as con:
        if user_id is None:
            exists = con.execute(
                "SELECT id FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        else:
            exists = con.execute(
                "SELECT id FROM conversations "
                "WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            ).fetchone()

        if exists is None:
            return False

        con.execute(
            "DELETE FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )

        con.execute(
            "DELETE FROM conversations WHERE id = ?",
            (conversation_id,),
        )

    return True


init_db()
