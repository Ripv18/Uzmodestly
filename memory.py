
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("behruz_memory.db")


def connect():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL DEFAULT 'Yangi suhbat',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT
            )
        """)

        cols = {
            r["name"] for r in
            con.execute("PRAGMA table_info(conversations)")
        }
        if "user_id" not in cols:
            con.execute(
                "ALTER TABLE conversations ADD COLUMN user_id TEXT"
            )

        con.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


def create_conversation(title="Yangi suhbat", user_id=None):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO conversations (title, user_id) VALUES (?, ?)",
            (title[:100], user_id),
        )
        return cur.lastrowid


def conversation_belongs_to(conversation_id, user_id):
    with connect() as con:
        return con.execute(
            "SELECT 1 FROM conversations WHERE id=? AND user_id=?",
            (conversation_id, user_id),
        ).fetchone() is not None


def save_message(conversation_id, role, content, user_id=None):
    if role not in ("user", "assistant"):
        raise ValueError("Noto'g'ri xabar turi")

    with connect() as con:
        row = con.execute(
            "SELECT user_id, title FROM conversations WHERE id=?",
            (conversation_id,),
        ).fetchone()

        if row is None:
            raise ValueError("Suhbat topilmadi")

        if user_id is not None and row["user_id"] != user_id:
            raise ValueError("Suhbat foydalanuvchiga tegishli emas")

        con.execute(
            "INSERT INTO messages (conversation_id, role, content) "
            "VALUES (?, ?, ?)",
            (conversation_id, role, str(content)[:10000]),
        )

        if role == "user" and row["title"] == "Yangi suhbat":
            title = " ".join(str(content).split())[:60]
            if title:
                con.execute(
                    "UPDATE conversations SET title=? WHERE id=?",
                    (title, conversation_id),
                )


def get_messages(conversation_id, limit=100, user_id=None):
    with connect() as con:
        if user_id is not None and not con.execute(
            "SELECT 1 FROM conversations WHERE id=? AND user_id=?",
            (conversation_id, user_id),
        ).fetchone():
            return []

        rows = con.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
            (conversation_id, min(max(int(limit), 1), 200)),
        ).fetchall()

    return [dict(r) for r in reversed(rows)]


def list_conversations(limit=50, user_id=None):
    with connect() as con:
        if user_id is None:
            rows = con.execute(
                "SELECT id, title, created_at FROM conversations "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, title, created_at FROM conversations "
                "WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()

    return [dict(r) for r in rows]


def remember(content, user_id=None):
    content = str(content or "").strip()[:500]
    if not content or not user_id:
        return False

    with connect() as con:
        old = con.execute(
            "SELECT id FROM memories WHERE user_id=? AND content=?",
            (user_id, content),
        ).fetchone()

        if old:
            return False

        con.execute(
            "INSERT INTO memories (user_id, content) VALUES (?, ?)",
            (user_id, content),
        )
    return True


def get_memories(user_id=None, limit=25):
    if not user_id:
        return []

    with connect() as con:
        rows = con.execute(
            "SELECT id, content, created_at FROM memories "
            "WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()

    return [dict(r) for r in rows]


def forget(memory_id, user_id=None):
    if not user_id:
        return False

    with connect() as con:
        cur = con.execute(
            "DELETE FROM memories WHERE id=? AND user_id=?",
            (memory_id, user_id),
        )
    return cur.rowcount > 0


def forget_matching(content, user_id=None):
    if not user_id or not content:
        return 0

    with connect() as con:
        cur = con.execute(
            "DELETE FROM memories WHERE user_id=? AND content LIKE ?",
            (user_id, "%" + content.strip()[:200] + "%"),
        )
    return cur.rowcount


def get_recent_context(
    exclude_conversation_id=None,
    limit=12,
    user_id=None,
):
    if not user_id:
        return []

    with connect() as con:
        sql = """
            SELECT c.title, m.role, m.content, m.created_at
            FROM messages m
            JOIN conversations c ON c.id=m.conversation_id
            WHERE c.user_id=?
        """
        args = [user_id]

        if exclude_conversation_id is not None:
            sql += " AND c.id != ?"
            args.append(exclude_conversation_id)

        sql += " ORDER BY m.id DESC LIMIT ?"
        args.append(limit)

        rows = con.execute(sql, args).fetchall()

    return [dict(r) for r in reversed(rows)]


def delete_conversation(conversation_id, user_id=None):
    with connect() as con:
        if user_id is None:
            row = con.execute(
                "SELECT id FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT id FROM conversations WHERE id=? AND user_id=?",
                (conversation_id, user_id),
            ).fetchone()

        if row is None:
            return False

        con.execute(
            "DELETE FROM messages WHERE conversation_id=?",
            (conversation_id,),
        )
        con.execute(
            "DELETE FROM conversations WHERE id=?",
            (conversation_id,),
        )
    return True


init_db()
