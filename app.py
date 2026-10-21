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
def search_internet(query):
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()

    if not api_key:
        return "Internet qidiruvi sozlanmagan."

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 5,
                "include_answer": True
            },
            timeout=15
        )
        response.raise_for_status()
        data = response.json()

        results = []

        if data.get("answer"):
            results.append("QISQA JAVOB: " + data["answer"])

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
MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")

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
        parts = [p.text for p in document.paragraphs if p.text.strip()]

        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))

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
    
@app.route("/logo.png")
def logo():
    return send_from_directory(
        os.path.dirname(os.path.abspath(__file__)),
        "logo.png",
        mimetype="image/png"
    )

@app.route("/google60ac72fbd987c0b6.html")
def google_verify():
    return send_from_directory(".", "google60ac72fbd987c0b6.html")


@app.route("/api/conversations", methods=["GET"])
def conversations():
    user_id = get_user_id()
    if not user_id:
        return jsonify({"error": "Foydalanuvchi ID noto‘g‘ri yoki yo‘q."}), 400

    return jsonify(memory.list_conversations(user_id=user_id))


@app.route("/api/conversations", methods=["POST"])
def new_conversation():
    user_id = get_user_id()
    if not user_id:
        return jsonify({"error": "Foydalanuvchi ID noto‘g‘ri yoki yo‘q."}), 400

    try:
        cid = memory.create_conversation(user_id=user_id)
        return jsonify({"id": cid, "title": "Yangi suhbat"})
    except Exception:
        app.logger.exception("Suhbat yaratishda xato")
        return jsonify({"error": "Suhbat yaratilmadi."}), 500


@app.route("/api/conversations/<int:cid>", methods=["GET"])
def conversation_detail(cid):
    user_id = get_user_id()
    if not user_id:
        return jsonify({"error": "Foydalanuvchi ID noto‘g‘ri yoki yo‘q."}), 400

    if not memory.conversation_belongs_to(cid, user_id):
        return jsonify({"error": "Suhbat topilmadi."}), 404

    return jsonify({
        "id": cid,
        "messages": memory.get_messages(
            cid, limit=1000, user_id=user_id
        ),
    })


@app.route("/api/conversations/<int:cid>", methods=["DELETE"])
def delete_conversation(cid):
    user_id = get_user_id()
    if not user_id:
        return jsonify({"error": "Foydalanuvchi ID noto‘g‘ri yoki yo‘q."}), 400

    try:
        deleted = memory.delete_conversation(cid, user_id=user_id)
        if not deleted:
            return jsonify({"error": "Suhbat topilmadi."}), 404

        return jsonify({
            "success": True,
            "message": "Suhbat o‘chirildi.",
        })
    except Exception:
        app.logger.exception("Suhbatni o‘chirishda xato")
        return jsonify({"error": "Suhbat o‘chirilmadi."}), 500
@app.route("/api/memories", methods=["GET"])
def list_memories():
    user_id = get_user_id()

    if not user_id:
        return jsonify({"error": "Foydalanuvchi ID noto‘g‘ri."}), 400

    try:
        items = memory.get_memories(user_id=user_id, limit=100)
        return jsonify({"memories": items})
    except Exception:
        app.logger.exception("Xotiralarni olishda xato")
        return jsonify({"error": "Xotiralarni olib bo‘lmadi."}), 500


@app.route("/api/memories/<int:memory_id>", methods=["DELETE"])
def delete_memory(memory_id):
    user_id = get_user_id()

    if not user_id:
        return jsonify({"error": "Foydalanuvchi ID noto‘g‘ri."}), 400

    try:
        deleted = memory.forget(memory_id, user_id=user_id)

        if not deleted:
            return jsonify({"error": "Xotira topilmadi."}), 404

        return jsonify({"success": True})
    except Exception:
        app.logger.exception("Xotirani o‘chirishda xato")
        return jsonify({"error": "Xotirani o‘chirib bo‘lmadi."}), 500

@app.route("/api/chat", methods=["POST"])
def chat():
    user_id = get_user_id()
    user_name = request.headers.get("X-User-Name", "").strip()[:50]

    if not user_id:
        return jsonify({"error": "Foydalanuvchi ID noto‘g‘ri yoki yo‘q."}), 400

    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "GROQ_API_KEY sozlanmagan."}), 503

    question = (request.form.get("message") or "").strip()[:3000]
    uploaded_file = request.files.get("file")
    has_file = bool(uploaded_file and uploaded_file.filename)

    if not question and not has_file:
        return jsonify({"error": "Xabar yoz yoki fayl yubor."}), 400

    raw_cid = request.form.get("conversation_id")
    try:
        cid = int(raw_cid) if raw_cid else None
    except (TypeError, ValueError):
        return jsonify({"error": "Suhbat ID noto‘g‘ri."}), 400

    attachment = None
    if has_file:
        try:
            attachment = read_attachment(uploaded_file)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    if cid is None:
        try:
            cid = memory.create_conversation(user_id=user_id)
        except Exception:
            app.logger.exception("Suhbat yaratilmadi")
            return jsonify({"error": "Suhbat yaratilmadi."}), 500

    if not memory.conversation_belongs_to(cid, user_id):
        return jsonify({"error": "Suhbat topilmadi."}), 404

    ip = request.remote_addr or "unknown"
    now = time.time()
    recent = requests_by_ip[ip]

    while recent and now - recent[0] > 60:
        recent.popleft()
    if len(recent) >= 10:
        return jsonify({
            "error": "Limit tugadi. Bir daqiqadan keyin urin."
        }), 429
    # Foydalanuvchining xotira buyruqlari
    memory_action = ""

    if not attachment and question:
        lowered_question = question.casefold().strip()

        if lowered_question.startswith("eslab qol:"):
            memory_text = question.split(":", 1)[1].strip()

            if memory_text:
                saved = memory.remember(
                    memory_text,
                    user_id=user_id,
                )

                memory_action = (
                    "Ma'lumot xotiraga saqlandi."
                    if saved
                    else "Bu ma'lumot xotirada allaqachon bor."
                )
            else:
                memory_action = (
                    "Foydalanuvchidan eslab qolish uchun "
                    "ma'lumot yozishini so'ra."
                )

        elif lowered_question.startswith("eslab qol "):
            memory_text = question[len("eslab qol "):].strip()

            if memory_text:
                saved = memory.remember(
                    memory_text,
                    user_id=user_id,
                )

                memory_action = (
                    "Ma'lumot xotiraga saqlandi."
                    if saved
                    else "Bu ma'lumot xotirada allaqachon bor."
                )

        elif lowered_question.startswith("unut:"):
            memory_text = question.split(":", 1)[1].strip()

            removed = (
                memory.forget_matching(
                    memory_text,
                    user_id=user_id,
                )
                if memory_text
                else 0
            )

            memory_action = (
                "So'ralgan xotira o'chirildi."
                if removed
                else "O'chiriladigan xotira topilmadi."
            )
    system_prompt = """
SENING SHAXSING VA JAVOB BERISH QOIDALARI:

1. Sening noming Uzmodestly AI.
2. "Isming nima?" deyilsa, "Men Uzmodestly AI man" deb javob ber.
3. "Seni kim yaratgan?" yoki "Xo'jayining kim?" deyilsa,
   "Meni Behruz Tojimurodov yaratgan" deb javob ber.
4. O'zingni ChatGPT deb tanishtirma.
5. O'zbek tilida tabiiy, sodda va tushunarli javob ber.
6. Oddiy va xavfsiz savollarga asossiz ravishda
   "I can't help with that" deb javob berma.
7. Javob bera olmasang, sababini o'zbek tilida tushuntir.
8. Matematik masalalarni tekshirib, bosqichma-bosqich yech.
9. Rasm yuborilsa, undagi ko'rinadigan narsalar,
   yozuvlar va masalalarni diqqat bilan tahlil qil.
10. Rasm xira yoki yozuvlar o'qilmasa, buni ochiq ayt.
11. Bilmagan narsangni to'qib chiqarma.
12. Oddiy javoblarda ortiqcha Markdown bezaklari va LaTeX ishlatma.
13. Kod so'ralsa, to'liq va ishlaydigan kod ber.
"""

    if user_name:
        system_prompt += (
            "\n\nFOYDALANUVCHI HAQIDA:"
            f"\nFoydalanuvchining ismi: {user_name}."
            "\nAgar foydalanuvchi o‘z ismini so‘rasa, aynan shu ismni ayt."
            "\nIsmni boshqa odamlar bilan aralashtirma."
        )
    if memory_action:
        system_prompt += (
            "\n\nXOTIRA AMALI: " + memory_action
            + "\nFoydalanuvchiga natijani qisqa va rost ayt."
        )

    try:
        saved_memories = memory.get_memories(
            user_id=user_id,
            limit=25,
        )

        if saved_memories:
            memory_text = "\n".join(
                "- " + item["content"]
                for item in saved_memories
            )

            system_prompt += (
                "\n\nFOYDALANUVCHINING SHAXSIY XOTIRASI:"
                "\nQuyidagi ma'lumotlardan kerak bo'lganda foydalan."
                "\nFoydalanuvchi nimalarni eslayotganingni so'rasa, "
                "shu ma'lumotlar asosida javob ber."
                "\n" + memory_text[:5000]
            )

    except Exception:
        app.logger.exception("Shaxsiy xotirani o'qishda xato")
   
    safe_messages = [{"role": "system", "content": system_prompt}]

    try:
        saved_memories = memory.get_memories(
            user_id=user_id, limit=25
        )

        if saved_memories:
            facts = "\n".join(
                "- " + item["content"]
                for item in saved_memories
            )
            safe_messages.append({
                "role": "system",
                "content": (
                    "FOYDALANUVCHINING SHAXSIY XOTIRASI. "
                    "Faqat shu foydalanuvchiga tegishli. "
                    "Savolga aloqador bo'lsa foydalan:\n"
                    + facts[:5000]
                ),
            })

        old_context = memory.get_recent_context(
            exclude_conversation_id=cid,
            limit=12,
            user_id=user_id,
        )

        if old_context:
            context_text = "\n".join(
                f"[{item['title']}] {item['role']}: {item['content']}"
                for item in old_context
            )
            safe_messages.append({
                "role": "system",
                "content": (
                    "Shu foydalanuvchining oldingi suhbatlari. "
                    "Faqat kerakli bo'lsa foydalan:\n"
                    + context_text[:6000]
                ),
            })

        for item in memory.get_messages(
            cid, limit=30, user_id=user_id
        ):
            if item["role"] in ("user", "assistant"):
                safe_messages.append({
                    "role": item["role"],
                    "content": item["content"][:3000],
                })

    except Exception:
        app.logger.exception("Xotirani o'qishda xato")
        return jsonify({
            "error": "Suhbat xotirasini o'qib bo'lmadi."
        }), 500

    saved_question = question or "Yuborilgan faylni tahlil qil."
    user_content = saved_question

    if attachment:
        saved_question = f"[Fayl: {attachment['name']}]\n{saved_question}"

        if attachment["type"] == "document":
            user_content += (
                f"\n\nFayl: {attachment['name']}\n"
                "Fayldan ajratilgan matn:\n"
                + attachment["text"]
            )
        else:
            user_content = [
                {
                    "type": "text",
                    "text": (
                        user_content
                        + "\n\nRASM TAHLILI QOIDALARI:"
                        + "\n1. Rasm matnini diqqat bilan o‘qi."
                        + "\n2. Sonlar, ishoralar, kasrlar va formulalarni tekshir."
                        + "\n3. Masala shartini o‘zgartirma."
                        + "\n4. Noaniq belgilarni taxmin qilma."
                        + "\n5. Masalani bosqichma-bosqich yech."
                        + "\n6. Javobni qayta tekshir."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": attachment["data_url"]},
                },
            ]

    try:
        memory.save_message(
            cid, "user", saved_question, user_id=user_id
        )
        safe_messages.append({
            "role": "user",
            "content": user_content,
        })
    except Exception:
        app.logger.exception("Xabarni saqlashda xato")
        return jsonify({"error": "Xabar saqlanmadi."}), 500

    recent.append(now)
        # Faqat "qidir:" buyrug'i bilan internetdan izlash
   ==> Downloading cache...
==> Cloning from https://github.com/Ripv18/Uzmodestly
==> Checking out commit d6a59affcc86f5a8a50dd08875511fb3445d4445 in branch main
==> Downloaded and extracted 16 MiB cache in 5s.
==> Using Python version 3.14.8 (default)
==> Docs on specifying a Python version: https://render.com/docs/python-version
==> Installing Python version 3.14.8...
==> Using Poetry version 2.1.3 (default)
==> Docs on specifying a Poetry version: https://render.com/docs/poetry-version
==> Running build command 'pip install -r requirements.txt'...
Collecting flask (from -r requirements.txt (line 2))
  Using cached flask-3.1.3-py3-none-any.whl.metadata (3.2 kB)
Collecting requests (from -r requirements.txt (line 3))
  Using cached requests-2.34.2-py3-none-any.whl.metadata (4.8 kB)
Collecting gunicorn (from -r requirements.txt (line 4))
  Using cached gunicorn-26.2.0-py3-none-any.whl.metadata (5.5 kB)
Collecting pillow (from -r requirements.txt (line 5))
  Using cached pillow-13.0.0-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl.metadata (9.1 kB)
Collecting pypdf (from -r requirements.txt (line 6))
  Using cached pypdf-6.21.1-py3-none-any.whl.metadata (7.6 kB)
Collecting python-docx (from -r requirements.txt (line 7))
  Using cached python_docx-1.2.0-py3-none-any.whl.metadata (2.0 kB)
Collecting blinker>=1.9.0 (from flask->-r requirements.txt (line 2))
  Using cached blinker-1.9.0-py3-none-any.whl.metadata (1.6 kB)
Collecting click>=8.1.3 (from flask->-r requirements.txt (line 2))
  Using cached click-8.5.0-py3-none-any.whl.metadata (2.6 kB)
Collecting itsdangerous>=2.2.0 (from flask->-r requirements.txt (line 2))
  Using cached itsdangerous-2.2.0-py3-none-any.whl.metadata (1.9 kB)
Collecting jinja2>=3.1.2 (from flask->-r requirements.txt (line 2))
  Using cached jinja2-3.1.6-py3-none-any.whl.metadata (2.9 kB)
Collecting markupsafe>=2.1.1 (from flask->-r requirements.txt (line 2))
  Using cached markupsafe-3.0.4-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (2.7 kB)
Collecting werkzeug>=3.1.0 (from flask->-r requirements.txt (line 2))
  Using cached werkzeug-3.1.9-py3-none-any.whl.metadata (4.1 kB)
Collecting charset_normalizer<4,>=2 (from requests->-r requirements.txt (line 3))
  Using cached charset_normalizer-3.5.2-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (46 kB)
Collecting idna<4,>=2.5 (from requests->-r requirements.txt (line 3))
  Using cached idna-3.20-py3-none-any.whl.metadata (7.2 kB)
Collecting urllib3<3,>=1.26 (from requests->-r requirements.txt (line 3))
  Using cached urllib3-2.8.0-py3-none-any.whl.metadata (7.4 kB)
Collecting certifi>=2023.5.7 (from requests->-r requirements.txt (line 3))
  Using cached certifi-2026.10.19-py3-none-any.whl.metadata (2.5 kB)
Collecting lxml>=3.1.0 (from python-docx->-r requirements.txt (line 7))
  Using cached lxml-6.1.3-cp314-cp314-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl.metadata (3.3 kB)
Collecting typing_extensions>=4.9.0 (from python-docx->-r requirements.txt (line 7))
  Using cached typing_extensions-4.16.0-py3-none-any.whl.metadata (3.3 kB)
Using cached flask-3.1.3-py3-none-any.whl (103 kB)
Using cached requests-2.34.2-py3-none-any.whl (73 kB)
Using cached charset_normalizer-3.5.2-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (255 kB)
Using cached idna-3.20-py3-none-any.whl (69 kB)
Using cached urllib3-2.8.0-py3-none-any.whl (135 kB)
Using cached gunicorn-26.2.0-py3-none-any.whl (228 kB)
Using cached pillow-13.0.0-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl (7.1 MB)
Using cached pypdf-6.21.1-py3-none-any.whl (405 kB)
Using cached python_docx-1.2.0-py3-none-any.whl (252 kB)
Using cached blinker-1.9.0-py3-none-any.whl (8.5 kB)
Using cached certifi-2026.10.19-py3-none-any.whl (136 kB)
Using cached click-8.5.0-py3-none-any.whl (125 kB)
Using cached itsdangerous-2.2.0-py3-none-any.whl (16 kB)
Using cached jinja2-3.1.6-py3-none-any.whl (134 kB)
Using cached lxml-6.1.3-cp314-cp314-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl (5.2 MB)
Using cached markupsafe-3.0.4-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (23 kB)
Using cached typing_extensions-4.16.0-py3-none-any.whl (45 kB)
Using cached werkzeug-3.1.9-py3-none-any.whl (228 kB)
Installing collected packages: urllib3, typing_extensions, pypdf, pillow, markupsafe, lxml, itsdangerous, idna, gunicorn, click, charset_normalizer, certifi, blinker, werkzeug, requests, python-docx, jinja2, flask

Successfully installed blinker-1.9.0 certifi-2026.10.19 charset_normalizer-3.5.2 click-8.5.0 flask-3.1.3 gunicorn-26.2.0 idna-3.20 itsdangerous-2.2.0 jinja2-3.1.6 lxml-6.1.3 markupsafe-3.0.4 pillow-13.0.0 pypdf-6.21.1 python-docx-1.2.0 requests-2.34.2 typing_extensions-4.16.0 urllib3-2.8.0 werkzeug-3.1.9

[notice] A new release of pip is available: 26.2.1 -> 26.2.2
[notice] To update, run: pip install --upgrade pip
==> Uploading build...
==> Uploaded in 2.4s. Compression took 1.5s
==> Build successful 🎉
==> Deploying...
==> Setting WEB_CONCURRENCY=1 by default, based on available CPUs in the instance
==> Running 'gunicorn app:app'
Traceback (most recent call last):
  File "/opt/render/project/src/.venv/bin/gunicorn", line 6, in <module>
    sys.exit(run())
             ~~~^^
  File "/opt/render/project/src/.venv/lib/python3.14/site-packages/gunicorn/app/wsgiapp.py", line 66, in run
    WSGIApplication("%(prog)s [OPTIONS] [APP_MODULE]", prog=prog).run()
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  File "/opt/render/project/src/.venv/lib/python3.14/site-packages/gunicorn/app/base.py", line 235, in run
    super().run()
    ~~~~~~~~~~~^^
  File "/opt/render/project/src/.venv/lib/python3.14/site-packages/gunicorn/app/base.py", line 71, in run
    Arbiter(self).run()
    ~~~~~~~^^^^^^
  File "/opt/render/project/src/.venv/lib/python3.14/site-packages/gunicorn/arbiter.py", line 63, in __init__
    self.setup(app)
    ~~~~~~~~~~^^^^^
  File "/opt/render/project/src/.venv/lib/python3.14/site-packages/gunicorn/arbiter.py", line 164, in setup
    self.app.wsgi()
    ~~~~~~~~~~~~~^^
  File "/opt/render/project/src/.venv/lib/python3.14/site-packages/gunicorn/app/base.py", line 66, in wsgi
    self.callable = self.load()
                    ~~~~~~~~~^^
  File "/opt/render/project/src/.venv/lib/python3.14/site-packages/gunicorn/app/wsgiapp.py", line 57, in load
    return self.load_wsgiapp()
           ~~~~~~~~~~~~~~~~~^^
  File "/opt/render/project/src/.venv/lib/python3.14/site-packages/gunicorn/app/wsgiapp.py", line 47, in load_wsgiapp
    return util.import_app(self.app_uri)
           ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^
  File "/opt/render/project/src/.venv/lib/python3.14/site-packages/gunicorn/util.py", line 420, in import_app
    mod = importlib.import_module(module)
  File "/opt/render/project/python/Python-3.14.8/lib/python3.14/importlib/__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1406, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap_external>", line 897, in get_code
  File "<frozen importlib._bootstrap_external>", line 827, in source_to_code
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "/opt/render/project/src/app.py", line 620
    if lowered.startswith("qidir:"):
                                    ^
IndentationError: unindent does not match any outer indentation level
==> Exited with status 1
==> Common ways to troubleshoot your deploy: https://render.com/docs/troubleshooting-deploys
        safe_messages.append({
            "role": "system",
            "content": (
                "INTERNET QIDIRUV NATIJALARI. "
                "Natijalardan foydalanib, o'zbek tilida javob ber. "
                "Faktlarni to'qima va javob oxirida manba URLlarini ko'rsat.\n\n"
                + web_results[:10000]
            ),
        })
    selected_model = (
        VISION_MODEL
        if attachment and attachment["type"] == "image"
        else MODEL
    )

    def generate():
        reply_parts = []

        try:
            with requests.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": selected_model,
                    "messages": safe_messages[-31:],
                    "temperature": 0.4,
                    "max_tokens": 1200,
                    "stream": True,
                },
                stream=True,
                timeout=(10, 120),
            ) as response:

                if not response.ok:
                    app.logger.error(
                        "Groq API HTTP %s: %s",
                        response.status_code,
                        response.text[:500],
                    )
                    yield (
                        "AI API xatosi. Model nomi, limit yoki "
                        "API kalit sozlamalarini tekshir."
                    )
                    return

                for line in response.iter_lines(decode_unicode=False):
                    if not line:
                        continue

                    try:
                        line = line.decode("utf-8", errors="replace")
                    except AttributeError:
                        pass

                    if not line.startswith("data:"):
                        continue

                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break

                    try:
                        obj = json.loads(payload)
                        part = obj["choices"][0]["delta"].get("content")
                        if isinstance(part, str) and part:
                            reply_parts.append(part)
                    except (
                        ValueError, KeyError, IndexError, TypeError
                    ):
                        continue

            reply = repair_text("".join(reply_parts)).strip()
            if not reply:
                yield "Javob olinmadi. Qayta urinib ko‘r."
                return

            memory.save_message(
                cid, "assistant", reply, user_id=user_id
            )
            yield reply

        except requests.RequestException:
            app.logger.exception("Groq API ulanish xatosi")
            yield "AI serveriga ulanib bo‘lmadi. Keyinroq urin."
        except Exception:
            app.logger.exception("Javob tayyorlashda xato")
            yield "Javob tayyorlashda xato yuz berdi."

    return Response(
        stream_with_context(generate()),
        content_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
