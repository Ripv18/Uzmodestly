import os
import io
import json
import time
import base64
import logging
import uuid
from collections import defaultdict, deque

import requests
from flask import Flask, send_from_directory, request, jsonify, Response, stream_with_context
from werkzeug.utils import secure_filename

import memory

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    from docx import Document
except ImportError:
    Document = None


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.json.ensure_ascii = False

logging.basicConfig(level=logging.INFO)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")


def search_internet(query):
    """Tavily orqali internetdan qidiradi."""
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()

    if not api_key:
        return "Internet qidiruvi sozlanmagan: TAVILY_API_KEY yo‘q."

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 5,
                "include_answer": True,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        results = []

        if data.get("answer"):
            results.append("QISQA JAVOB: " + str(data["answer"]))

        for item in data.get("results", []):
            title = item.get("title", "")
            url = item.get("url", "")
            content = item.get("content", "")

            results.append(
                f"Manba: {title}\nURL: {url}\nMa'lumot: {content}"
            )

        return "\n\n".join(results) or "Qidiruv natijasi topilmadi."

    except Exception:
        app.logger.exception("Internet qidiruvida xato")
        return "Internet qidiruvi vaqtincha ishlamadi."


IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
DOCUMENT_EXTENSIONS = {"pdf", "docx"}
requests_by_ip = defaultdict(deque)


def get_user_id():
    """Brauzer yuborgan UUID ni tekshiradi."""
    raw_id = request.headers.get("X-User-ID", "").strip()

    try:
        return str(uuid.UUID(raw_id))
    except (ValueError, AttributeError, TypeError):
        return None


def repair_text(text):
    """Buzilgan o‘zbekcha belgilarni tuzatadi."""
    if not isinstance(text, str):
        return text

    replacements = {
        "â\u0080\u0098": "‘",
        "â\u0080\u0099": "’",
        "â\x80\x98": "‘",
        "â\x80\x99": "’",
        "â": "‘",
        "â": "’",
        "â€“": "–",
        "â€”": "—",
        "â€¦": "…",
        "Ã¢â‚¬Ëœ": "‘",
        "Ã¢â‚¬â„¢": "’",
        "Ã¢â‚¬â€œ": "–",
        "Ã¢â‚¬Â¦": "…",
    }

    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)

    return text


def allowed_file(filename):
    if "." not in filename:
        return False

    ext = filename.rsplit(".", 1)[1].lower()
    return ext in IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS


def extract_document(filename, raw_bytes):
    """PDF yoki DOCX hujjatidan matn ajratadi."""
    ext = filename.rsplit(".", 1)[1].lower()

    if ext == "pdf":
        if PdfReader is None:
            raise ValueError("PDF uchun pypdf kutubxonasi kerak.")

        reader = PdfReader(io.BytesIO(raw_bytes))

        if reader.is_encrypted:
            raise ValueError("Parolli PDF hozircha qo‘llanmaydi.")

        parts = []
        for page in reader.pages[:50]:
            parts.append(page.extract_text() or "")

        text = "\n".join(parts).strip()

        if not text:
            raise ValueError(
                "PDF ichidan matn topilmadi. "
                "Skanerlangan hujjatni rasm sifatida yuborib ko‘r."
            )

        return text[:40000]

    if ext == "docx":
        if Document is None:
            raise ValueError("Word fayli uchun python-docx kerak.")

        document = Document(io.BytesIO(raw_bytes))
        parts = [
            p.text for p in document.paragraphs
            if p.text.strip()
        ]

        for table in document.tables:
            for row in table.rows:
                parts.append(
                    " | ".join(cell.text for cell in row.cells)
                )

        text = "\n".join(parts).strip()

        if not text:
            raise ValueError("Word faylidan matn topilmadi.")

        return text[:40000]

    raise ValueError("Bu hujjat turi qo‘llab-quvvatlanmaydi.")


def read_attachment(file):
    """Rasm yoki hujjatni AI uchun tayyorlaydi."""
    filename = secure_filename(file.filename or "")

    if not filename or not allowed_file(filename):
        raise ValueError(
            "Faqat PNG, JPG, JPEG, WEBP, PDF yoki DOCX yubor."
        )

    raw_bytes = file.read()

    if not raw_bytes:
        raise ValueError("Tanlangan fayl bo‘sh.")

    if len(raw_bytes) > 10 * 1024 * 1024:
        raise ValueError("Fayl hajmi 10 MB dan oshmasligi kerak.")

    ext = filename.rsplit(".", 1)[1].lower()

    if ext in IMAGE_EXTENSIONS:
        if Image is None or ImageOps is None:
            raise ValueError("Rasm uchun Pillow kutubxonasi kerak.")

        try:
            with Image.open(io.BytesIO(raw_bytes)) as image:
                image.verify()

            with Image.open(io.BytesIO(raw_bytes)) as image:
                image = ImageOps.exif_transpose(image)
                image = image.convert("RGB")
                image.thumbnail((3072, 3072))

                buffer = io.BytesIO()
                image.save(
                    buffer,
                    format="JPEG",
                    quality=92,
                    optimize=True,
                )

            encoded = base64.b64encode(
                buffer.getvalue()
            ).decode("ascii")

        except Exception as exc:
            raise ValueError("Rasmni o‘qib bo‘lmadi.") from exc

        return {
            "name": filename,
            "type": "image",
            "data_url": "data:image/jpeg;base64," + encoded,
        }

    return {
        "name": filename,
        "type": "document",
        "text": extract_document(filename, raw_bytes),
    }


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify({
        "error": "Fayl juda katta. Kichikroq fayl yubor."
    }), 413


@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/google60ac72fbd987c0b6.html")
def google_verify():
    return send_from_directory(".", "google60ac72fbd987c0b6.html")


@app.route("/api/conversations", methods=["GET"])
def conversations():
    user_id = get_user_id()

    if not user_id:
        return jsonify({
            "error": "Foydalanuvchi ID noto‘g‘ri yoki yo‘q."
        }), 400

    return jsonify(memory.list_conversations(user_id=user_id))


@app.route("/api/conversations", methods=["POST"])
def new_conversation():
    user_id = get_user_id()

    if not user_id:
        return jsonify({
            "error": "Foydalanuvchi ID noto‘g‘ri yoki yo‘q."
        }), 400

    try:
        cid = memory.create_conversation(user_id=user_id)

        return jsonify({
            "id": cid,
            "title": "Yangi suhbat",
        })

    except Exception:
        app.logger.exception("Suhbat yaratishda xato")
        return jsonify({"error": "Suhbat yaratilmadi."}), 500


@app.route("/api/conversations/<int:cid>", methods=["GET"])
def conversation_detail(cid):
    user_id = get_user_id()

    if not user_id:
        return jsonify({
            "error": "Foydalanuvchi ID noto‘g‘ri yoki yo‘q."
        }), 400

    if not memory.conversation_belongs_to(cid, user_id):
        return jsonify({"error": "Suhbat topilmadi."}), 404

    return jsonify({
        "id": cid,
        "messages": memory.get_messages(
            cid,
            limit=1000,
            user_id=user_id,
        ),
    })


@app.route("/api/conversations/<int:cid>", methods=["DELETE"])
def delete_conversation(cid):
    user_id = get_user_id()

    if not user_id:
        return jsonify({
            "error": "Foydalanuvchi ID noto‘g‘ri yoki yo‘q."
        }), 400

    try:
        deleted = memory.delete_conversation(
            cid,
            user_id=user_id,
        )

        if not deleted:
            return jsonify({"error": "Suhbat topilmadi."}), 404

        return jsonify({
            "success": True,
            "message": "Suhbat o‘chirildi.",
        })

    except Exception:
        app.logger.exception("Suhbatni o‘chirishda xato")
        return jsonify({"error": "Suhbat o‘chirilmadi."}), 500
def get_system_prompt():
    return (
        "Sen Uzmodestly AI yordamchisisan. "
        "Foydalanuvchiga o'zbek tilida aniq, foydali va tushunarli javob ber. "
        "Agar bilmasang, bilmasligingni ayt. "
        "Faktlar, sanalar yoki manbalarni to'qib chiqarmagin."
    )


def call_groq(messages, model=None):
    api_key = os.environ.get("GROQ_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("GROQ_API_KEY Render sozlamalarida yo'q.")

    selected_model = model or MODEL

    response = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": selected_model,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": 4096,
        },
        timeout=90,
    )

    if not response.ok:
        app.logger.error(
            "Groq API xatosi: %s %s",
            response.status_code,
            response.text[:1000],
        )
        response.raise_for_status()

    data = response.json()
    choices = data.get("choices", [])

    if not choices:
        raise RuntimeError("AI bo'sh javob qaytardi.")

    message = choices[0].get("message", {})
    content = message.get("content", "")

    if isinstance(content, str):
        return repair_text(content).strip()

    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        return repair_text("\n".join(text_parts)).strip()

    return "AI javob tayyorlay olmadi."


def build_search_context(results):
    return (
        "Quyida internet qidiruvi natijalari keltirilgan.\n"
        "Javobda faqat ushbu natijalar tasdiqlaydigan faktlardan foydalan.\n"
        "Manbada ko'rsatilmagan sana yoki faktni taxmin qilma.\n"
        "Agar natijalar yetarli bo'lmasa, buni ochiq ayt.\n"
        "Javob oxirida foydalanilgan manbalarning URL manzillarini ko'rsat.\n\n"
        "QIDIRUV NATIJALARI:\n"
        + results[:10000]
    )


def generate_answer(question, history=None, attachment=None):
    messages = [
        {
            "role": "system",
            "content": get_system_prompt(),
        }
    ]

    if history:
        messages.extend(history[-20:])

    if attachment and attachment.get("type") == "image":
        user_content = [
            {
                "type": "text",
                "text": question or "Ushbu rasmni tahlil qilib ber.",
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": attachment["data_url"],
                },
            },
        ]

        messages.append({
            "role": "user",
            "content": user_content,
        })

        return call_groq(messages, VISION_MODEL)

    if attachment and attachment.get("type") == "document":
        question = (
            (question + "\n\n") if question else ""
        ) + (
            "Yuborilgan hujjat: "
            + attachment["name"]
            + "\n\nHUJJAT MATNI:\n"
            + attachment["text"][:30000]
        )

    lowered = question.casefold().strip()

    if lowered.startswith("qidir:"):
        query = question[6:].strip()

        if query:
            results = search_internet(query)
            question = (
                "Foydalanuvchi so'rovi: "
                + query
                + "\n\n"
                + build_search_context(results)
            )

    messages.append({
        "role": "user",
        "content": question,
    })

    return call_groq(messages)


@app.route("/api/chat", methods=["POST"])
def chat():
    user_id = get_user_id()

    if not user_id:
        return jsonify({
            "error": "Foydalanuvchi ID noto‘g‘ri yoki yo‘q."
        }), 400

    user_name = request.headers.get("X-User-Name", "").strip()[:50]
    question = (request.form.get("message") or "").strip()[:3000]
    uploaded_file = request.files.get("file")
    attachment = None

    if not question and not (
        uploaded_file and uploaded_file.filename
    ):
        return jsonify({
            "error": "Xabar yoz yoki fayl yubor."
        }), 400

    if uploaded_file and uploaded_file.filename:
        try:
            attachment = read_attachment(uploaded_file)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            app.logger.exception("Faylni o‘qishda xato")
            return jsonify({
                "error": "Faylni o‘qib bo‘lmadi."
            }), 400

    raw_cid = request.form.get("conversation_id")

    try:
        cid = int(raw_cid) if raw_cid else None
    except (TypeError, ValueError):
        cid = None

    if cid is None:
        try:
            cid = memory.create_conversation(user_id=user_id)
        except Exception:
            app.logger.exception("Suhbat yaratishda xato")
            return jsonify({
                "error": "Yangi suhbat yaratilmadi."
            }), 500

    if not memory.conversation_belongs_to(cid, user_id):
        return jsonify({"error": "Suhbat topilmadi."}), 404

    try:
        history_items = memory.get_messages(
            cid,
            limit=30,
            user_id=user_id,
        )

        history = []

        for item in history_items:
            role = item.get("role", "")
            content = item.get("content", "")

            if role in ("user", "assistant") and isinstance(content, str):
                history.append({
                    "role": role,
                    "content": content[:6000],
                })

        answer = generate_answer(
            question=question,
            history=history,
            attachment=attachment,
        )

        if not answer:
            answer = "Kechirasiz, javob yaratilmadi."

              if question:
            memory.save_message(
                cid,
                "user",
                question,
                user_id=user_id,
            )

        elif attachment:
            memory.save_message(
                cid,
                "user",
                "Fayl yuborildi: " + attachment["name"],
                user_id=user_id,
            )

        memory.save_message(
            cid,
            "assistant",
            answer,
            user_id=user_id,
        )

        return jsonify({
            "answer": answer,
            "response": answer,
            "conversation_id": cid,
        })

    except Exception:
        app.logger.exception("Chat API xatosi")
        return jsonify({
            "error": "Javob yaratishda xatolik. Loglarni tekshiring."
        }), 500


@app.route("/api/memories", methods=["GET"])
def list_memories():
    user_id = get_user_id()

    if not user_id:
        return jsonify({
            "error": "Foydalanuvchi ID noto‘g‘ri."
        }), 400

    try:
        items = memory.get_memories(
            user_id=user_id,
            limit=100,
        )

        return jsonify({"memories": items})

    except Exception:
        app.logger.exception("Xotiralarni olishda xato")
        return jsonify({
            "error": "Xotiralarni olib bo‘lmadi."
        }), 500


@app.route("/api/memories", methods=["DELETE"])
def clear_memories():
    user_id = get_user_id()

    if not user_id:
        return jsonify({
            "error": "Foydalanuvchi ID noto‘g‘ri."
        }), 400

    try:
        memory.clear_memories(user_id=user_id)

        return jsonify({
            "success": True,
            "message": "Xotiralar tozalandi.",
        })

    except Exception:
        app.logger.exception("Xotiralarni tozalashda xato")
        return jsonify({
            "error": "Xotiralarni tozalab bo‘lmadi."
        }), 500
        @app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "app": "Uzmodestly AI",
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
