"""
بوت تحميل ريلز/بوستات انستغرام + نظام اشتراك إجباري بمراجعة يدوية + لوحة إذاعة
--------------------------------------------------------------------------------
المكتبات المطلوبة:
    pip install -r requirements.txt

ملاحظة صادقة ومهمة عن "الستوري":
    تحميل الستوري (Stories) من انستغرام يحتاج تسجيل دخول فعلي لحساب انستغرام
    (جلسة/كوكيز)، لأن الستوريات غير متاحة في صفحات انستغرام العامة مثل البوستات
    والريلز. هذا البوت (مثل النسخة السابقة) يدعم فقط الريلز والبوستات العامة.
    لو حاب أضيف دعم فعلي للستوري، هذا يحتاج منك حساب انستغرام مخصص لتسجيل دخول
    البوت فيه (وفيه مخاطرة حظر/تقييد من انستغرام لأنه يخالف شروط استخدامها),
    فقلي إذا تريد نمشي بهذا الاتجاه أو نكتفي بالريلز/البوستات.

آلية الاشتراك الإجباري:
    1) المستخدم الجديد يضغط /start.
    2) يُطلب منه الاشتراك بقناة اليوتيوب ثم إرسال صورة سكرين شوت تثبت اشتراكه.
    3) الصورة تُرسل تلقائيًا للأدمن مع زرّي "قبول" و"رفض".
    4) بعد القبول فقط يقدر المستخدم يستخدم ميزة التحميل.

لوحة الإذاعة (للأدمن فقط):
    - أمر /broadcast يبدأ محادثة: يرسل الأدمن نص الإذاعة، ثم يختار تثبيت أو
      عدم تثبيت، ثم تُرسل الرسالة لكل المستخدمين (جدد وقدامى) المسجلين بالبوت.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import sys

try:
    from instagrapi import Client as InstagrapiClient
except Exception as _ig_import_error:
    InstagrapiClient = None
    # نطبع السبب الحقيقي فورًا بالـ console (مو بس "غير مثبتة") عشان تشخيص أسرع
    print(f"[تحذير بدء التشغيل] تعذر استيراد instagrapi: {_ig_import_error}", file=sys.stderr)
    print(f"[تحذير بدء التشغيل] البايثون المستخدم فعليًا: {sys.executable}", file=sys.stderr)

# ================== الإعدادات ==================
BOT_TOKEN = "1295172663:AAE17qmCEGxWS4tqobatrOO1VdXNh_9L2_E"
ADMIN_ID = 1427023555
YOUTUBE_CHANNEL_URL = "https://youtube.com/channel/UCGk2JuX9y7Q0la4G22mT32Q?si=EzRQvJ2EJY5-UMBs"

DB_FILE = Path(__file__).parent / "users.json"
ACCOUNT_FILE = Path(__file__).parent / "ig_accounts.json"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    )
}

TMP_DIR = Path(tempfile.gettempdir()) / "ig_bot_videos"
TMP_DIR.mkdir(exist_ok=True)


def download_video_to_disk(url: str) -> str | None:
    """يحمّل الفيديو من الرابط لملف مؤقت بامتداد .mp4 صريح، يرجّع المسار أو None عند الفشل."""
    local_path = TMP_DIR / f"{uuid.uuid4().hex}.mp4"
    try:
        with requests.get(url, headers=HEADERS, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
        # لو الملف صغير جدًا أو فاضي، غالبًا صار خطأ بالتحميل
        if local_path.stat().st_size < 1024:
            local_path.unlink(missing_ok=True)
            return None
        return str(local_path)
    except Exception:
        logger.exception("فشل تحميل الفيديو من الرابط: %s", url)
        if local_path.exists():
            local_path.unlink(missing_ok=True)
        return None


def generate_thumbnail(video_path: str) -> str | None:
    """يستخرج فريم من ثانية 0.5 كصورة مصغّرة للفيديو عبر ffmpeg. يرجّع مسار الصورة أو None."""
    thumb_path = str(Path(video_path).with_suffix(".jpg"))
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-ss", "0.5", "-i", video_path,
                "-frames:v", "1", "-vf", "scale=320:-1", thumb_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        if result.returncode == 0 and Path(thumb_path).exists():
            return thumb_path
    except Exception:
        logger.exception("فشل توليد صورة مصغّرة للفيديو")
    return None


OVERLAY_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
]


def _find_overlay_font() -> str | None:
    for p in OVERLAY_FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def build_video_meta(username: str | None, profile_url: str | None) -> dict:
    """
    يبني نسختين من نفس المعلومات:
    - overlay_text: بدون إيموجي (تُكتب داخل الفيديو نفسه عبر ffmpeg -
      الإيموجي الملوّن لا يشتغل مع drawtext، جُرّب فعليًا وفشل).
    - caption_text: بإيموجي (تنزل بكابشن تيليجرام، يعرضها تيليجرام صح دايمًا).
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    overlay_lines = [now_str]
    caption_lines = [f"🕒 {now_str}"]

    if username:
        overlay_lines.append(f"@{username}")
        caption_lines.append(f"👤 @{username}")
    if profile_url:
        overlay_lines.append(profile_url)
        caption_lines.append(f"🔗 {profile_url}")

    return {"overlay_text": "\n".join(overlay_lines), "caption_text": "\n".join(caption_lines)}


def burn_overlay(video_path: str, overlay_text: str) -> str | None:
    """يكتب overlay_text داخل الفيديو نفسه (أسفل يسار) عبر ffmpeg. يرجّع مسار فيديو جديد أو None لو فشل."""
    font = _find_overlay_font()
    if not font:
        logger.warning("ما لقيت خط مناسب للكتابة على الفيديو - تم تجاوز هذي الخطوة")
        return None

    txt_path = str(Path(video_path).with_suffix(".overlay.txt"))
    out_path = str(Path(video_path).with_suffix(".burned.mp4"))

    try:
        Path(txt_path).write_text(overlay_text, encoding="utf-8")
        vf = (
            f"drawtext=fontfile={font}:textfile={txt_path}:fontsize=20:fontcolor=white:"
            f"box=1:boxcolor=black@0.5:boxborderw=8:x=10:y=h-th-10:line_spacing=6"
        )
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vf", vf, "-codec:a", "copy", out_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        if result.returncode == 0 and Path(out_path).exists() and Path(out_path).stat().st_size > 1024:
            return out_path
        logger.warning("فشلت كتابة النص على الفيديو: %s", result.stderr.decode(errors="ignore")[-500:])
        return None
    except Exception:
        logger.exception("فشل burn_overlay")
        return None
    finally:
        try:
            os.remove(txt_path)
        except OSError:
            pass


async def send_video_from_url(
    message,
    url: str,
    caption: str | None = None,
    username: str | None = None,
    profile_url: str | None = None,
):
    """
    يحمّل الفيديو محليًا، يكتب عليه الوقت/اليوزر/الرابط، يولّد صورة مصغّرة،
    ثم يرفعه لتيليجرام كملف .mp4 حقيقي (فيديو لا صوت، لا شاشة سوداء).
    لو فشلت خطوة الكتابة على الفيديو (مثلاً ffmpeg غير متوفر بالسيرفر)،
    يكمّل ويرسل الفيديو بدون الكتابة بدل ما يوقف كليًا.
    """
    local_path = download_video_to_disk(url)
    if not local_path:
        await message.reply_text("❌ تعذر تحميل الفيديو من المصدر، حاول مرة ثانية.")
        return

    # تيليجرام يرفض رفع ملفات أكبر من 50 ميجا من البوتات - نتأكد قبل ما نحاول ونضيّع وقت
    file_size_mb = Path(local_path).stat().st_size / (1024 * 1024)
    if file_size_mb > 50:
        await message.reply_text(
            f"❌ حجم الفيديو {file_size_mb:.1f} ميجا، وهذا أكبر من الحد المسموح للبوتات (50 ميجا). "
            "ما أقدر أرسله."
        )
        try:
            os.remove(local_path)
        except OSError:
            pass
        return

    meta = build_video_meta(username, profile_url)
    burned_path = burn_overlay(local_path, meta["overlay_text"])
    final_path = burned_path or local_path

    thumb_path = generate_thumbnail(final_path)

    full_caption = meta["caption_text"]
    if caption:
        full_caption = f"{caption}\n\n{full_caption}"

    try:
        with open(final_path, "rb") as f:
            thumb_file = open(thumb_path, "rb") if thumb_path else None
            try:
                await message.reply_video(
                    video=f,
                    filename="video.mp4",
                    caption=full_caption,
                    supports_streaming=True,
                    thumbnail=thumb_file,
                )
            finally:
                if thumb_file:
                    thumb_file.close()
    except Exception as e:
        logger.exception("فشل رفع الفيديو لتيليجرام")
        await message.reply_text(f"❌ صار خطأ أثناء إرسال الملف: {e}")
    finally:
        for p in (local_path, burned_path, thumb_path):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass


async def send_photo_with_meta(message, url: str, username: str | None = None, profile_url: str | None = None):
    """يرسل صورة مع كابشن فيه الوقت/اليوزر/الرابط بإيموجي (الصور ما تُكتب عليها، بس تركيب الكود نفسه)."""
    meta = build_video_meta(username, profile_url)
    await message.reply_photo(photo=url, caption=meta["caption_text"])


INSTAGRAM_URL_RE = re.compile(r"instagram\.com", re.IGNORECASE)
OG_VIDEO_RE = re.compile(r'<meta property="og:video" content="([^"]+)"', re.IGNORECASE)
OG_IMAGE_RE = re.compile(r'<meta property="og:image" content="([^"]+)"', re.IGNORECASE)
IG_OGTITLE_USERNAME_RE = re.compile(r'\(@([A-Za-z0-9_.]+)\)')

# ================== قاعدة بيانات بسيطة (JSON) ==================

def load_db() -> dict:
    if not DB_FILE.exists():
        return {}
    try:
        return json.loads(DB_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_db(db: dict):
    DB_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def get_user(db: dict, user_id: int) -> dict:
    return db.get(str(user_id))


def upsert_user(db: dict, user_id: int, **fields):
    key = str(user_id)
    if key not in db:
        db[key] = {}
    db[key].update(fields)
    save_db(db)


# ================== استخراج ميديا انستغرام ==================

def extract_instagram_media(url: str) -> dict:
    if not INSTAGRAM_URL_RE.search(url):
        return {"error": "الرابط غير صالح، لازم يكون من انستغرام."}

    clean_url = url.split("?")[0].strip()

    # ------ المحاولة الأولى: عبر الحساب المسجّل دخول (أوثق، انستغرام لا يحجبه) ------
    client = get_ig_client()
    if client is not None:
        try:
            pk = client.media_pk_from_url(clean_url)
            info = client.media_info(pk)
            username = info.user.username if info.user else None
            profile_url = f"https://www.instagram.com/{username}/" if username else None

            # كاروسيل (منشور فيه أكثر من صورة/فيديو) -> media_type == 8
            if info.media_type == 8 and getattr(info, "resources", None):
                items = []
                for res in info.resources:
                    if res.video_url:
                        items.append({"type": "video", "url": str(res.video_url)})
                    elif res.thumbnail_url:
                        items.append({"type": "photo", "url": str(res.thumbnail_url)})
                if items:
                    return {"type": "carousel", "items": items, "username": username, "profile_url": profile_url}

            if info.video_url:
                return {"type": "video", "url": str(info.video_url), "username": username, "profile_url": profile_url}
            if info.thumbnail_url:
                return {"type": "photo", "url": str(info.thumbnail_url), "username": username, "profile_url": profile_url}
        except Exception:
            logger.warning("فشلت محاولة جلب الميديا عبر الحساب المسجّل، بجرب الطريقة العامة")

    # ------ محاولة احتياطية: قراءة meta tags من الصفحة العامة (بدون تسجيل دخول) ------
    # ملاحظة: هذي الطريقة ما تكتشف الكاروسيل (منشورات متعددة الصور) لأن meta tags
    # تعرض بس أول عنصر بالمنشور. الكاروسيل الكامل يشتغل فقط عبر الحساب المسجّل دخول.
    try:
        resp = requests.get(clean_url, headers=HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"error": f"ما قدرت أوصل للصفحة: {e}"}

    html = resp.text
    username_match = IG_OGTITLE_USERNAME_RE.search(html)
    username = username_match.group(1) if username_match else None
    profile_url = f"https://www.instagram.com/{username}/" if username else None

    video_match = OG_VIDEO_RE.search(html)
    if video_match:
        return {"type": "video", "url": video_match.group(1), "username": username, "profile_url": profile_url}

    image_match = OG_IMAGE_RE.search(html)
    if image_match:
        return {"type": "photo", "url": image_match.group(1), "username": username, "profile_url": profile_url}

    return {
        "error": "ما قدرت ألقى رابط ميديا بهذا المنشور. ممكن يكون الحساب خاص، "
                 "أو رابط ستوري (غير مدعوم حاليًا)، أو رابط غير صالح. "
                 "جرّب تضيف حساب انستغرام فعّال عبر زر ➕ إضافة حساب انستغرام "
                 "لزيادة نسبة نجاح التحميل.",
        "no_client": client is None,
    }


# ================== حساب انستغرام (لتحميل الستوري) ==================
# ملاحظة مهمة: هذا الحساب يُستخدم فقط لتحميل ستوري الحسابات العامة، أو
# الحسابات الخاصة التي يتابعها هذا الحساب فعليًا وبموافقتها. لن يتم استخدامه
# لتجاوز خصوصية أي حساب خاص لم يوافق على متابعة هذا الحساب.

_ig_client = None       # كاش لعميل انستغرام بعد تسجيل الدخول
_ig_client_username = None  # يوزر الحساب المستخدم حاليًا بالكاش
_last_broken_alert_ts = 0   # آخر وقت تم فيه تنبيه الأدمن (لمنع تكرار التنبيه بإزعاج)

SESSIONS_DIR = Path(__file__).parent / "ig_sessions"
SESSIONS_DIR.mkdir(exist_ok=True)


def load_ig_accounts() -> list:
    if not ACCOUNT_FILE.exists():
        return []
    try:
        data = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_ig_accounts(accounts: list):
    ACCOUNT_FILE.write_text(
        json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def add_ig_account_password(username: str, password: str):
    accounts = load_ig_accounts()
    for acc in accounts:
        if acc.get("type") == "password" and acc.get("username", "").lower() == username.lower():
            acc["password"] = password
            save_ig_accounts(accounts)
            return
    accounts.append({"type": "password", "username": username, "password": password})
    save_ig_accounts(accounts)


def add_ig_account_sessionid(sessionid: str, username: str = ""):
    accounts = load_ig_accounts()
    for acc in accounts:
        if acc.get("type") == "session" and acc.get("sessionid") == sessionid:
            acc["username"] = username or acc.get("username", "")
            save_ig_accounts(accounts)
            return
    accounts.append({"type": "session", "sessionid": sessionid, "username": username})
    save_ig_accounts(accounts)


def remove_ig_account(identifier: str) -> bool:
    """يحذف حساب بالبحث عن اليوزر (لحسابات يوزر/رمز أو حسابات سيشن بعد ما يُعرف يوزرها)."""
    accounts = load_ig_accounts()
    new_accounts = [a for a in accounts if a.get("username", "").lower() != identifier.lower()]
    removed = len(new_accounts) != len(accounts)
    if removed:
        save_ig_accounts(new_accounts)
        session_file = SESSIONS_DIR / f"{identifier.lower()}.json"
        session_file.unlink(missing_ok=True)
        global _ig_client, _ig_client_username
        if _ig_client_username and _ig_client_username.lower() == identifier.lower():
            _ig_client = None
            _ig_client_username = None
    return removed


def try_login_ig(username: str, password: str) -> tuple[bool, str]:
    """يختبر تسجيل الدخول فعليًا لحساب معيّن (يوزر/رمز) ويرجّع (نجح؟, رسالة توضيحية)."""
    if InstagrapiClient is None:
        return False, "مكتبة instagrapi غير مثبتة أو تعذر تحميلها على هذا السيرفر. شيّك رسالة الخطأ التفصيلية بالـ console وقت تشغيل البوت (تظهر بأول تشغيل)."

    session_file = SESSIONS_DIR / f"{username.lower()}.json"
    client = InstagrapiClient()
    try:
        if session_file.exists():
            client.load_settings(session_file)
        client.login(username, password)
        client.dump_settings(session_file)

        global _ig_client, _ig_client_username
        _ig_client = client
        _ig_client_username = username
        return True, "تم تسجيل الدخول والتحقق من الحساب بنجاح ✅"
    except Exception as e:
        session_file.unlink(missing_ok=True)
        return False, f"فشل تسجيل الدخول ❌ (الحساب غير صحيح، أو رمز مرور خاطئ، أو انستغرام طلب تحقق إضافي): {e}"


def try_login_ig_sessionid(sessionid: str) -> tuple[bool, str, str]:
    """يختبر تسجيل الدخول عبر sessionid ويرجّع (نجح؟, رسالة, يوزر الحساب لو انعرف)."""
    if InstagrapiClient is None:
        return False, "مكتبة instagrapi غير مثبتة أو تعذر تحميلها على هذا السيرفر. شيّك رسالة الخطأ التفصيلية بالـ console وقت تشغيل البوت (تظهر بأول تشغيل).", ""

    client = InstagrapiClient()
    try:
        client.login_by_sessionid(sessionid)
        info = client.account_info()
        username = info.username

        session_file = SESSIONS_DIR / f"{username.lower()}.json"
        client.dump_settings(session_file)

        global _ig_client, _ig_client_username
        _ig_client = client
        _ig_client_username = username
        return True, f"تم التحقق من الجلسة بنجاح ✅ (الحساب: @{username})", username
    except Exception as e:
        return False, f"فشل التحقق من الجلسة ❌ (sessionid غير صالح أو منتهي): {e}", ""


def get_ig_client():
    """
    يرجّع عميل انستغرام مسجّل دخول من أول حساب شغّال بقائمة الحسابات المضافة
    (يوزر/رمز أو sessionid). لو حساب فشل، يجرّب اللي بعده تلقائيًا.
    """
    global _ig_client, _ig_client_username
    if _ig_client is not None:
        return _ig_client

    if InstagrapiClient is None:
        logger.error("مكتبة instagrapi غير مثبتة. نفذ: pip install instagrapi")
        return None

    accounts = load_ig_accounts()
    if not accounts:
        return None

    for acc in accounts:
        acc_type = acc.get("type", "password")
        client = InstagrapiClient()
        try:
            if acc_type == "session":
                username = acc.get("username") or ""
                session_file = SESSIONS_DIR / f"{username.lower()}.json" if username else None
                if session_file and session_file.exists():
                    client.load_settings(session_file)
                client.login_by_sessionid(acc["sessionid"])
                if not username:
                    info = client.account_info()
                    username = info.username
                    session_file = SESSIONS_DIR / f"{username.lower()}.json"
                client.dump_settings(session_file)
            else:
                username, password = acc["username"], acc["password"]
                session_file = SESSIONS_DIR / f"{username.lower()}.json"
                if session_file.exists():
                    client.load_settings(session_file)
                client.login(username, password)
                client.dump_settings(session_file)

            _ig_client = client
            _ig_client_username = username
            return _ig_client
        except Exception:
            logger.exception(
                "فشل تسجيل الدخول لحساب انستغرام: %s",
                acc.get("username") or acc.get("sessionid", "")[:10],
            )
            continue

    return None


# ================== مراقبة صحة الجلسات ==================

def check_single_account_session(acc: dict) -> bool:
    """يتأكد إن جلسة حساب معيّن لسا شغّالة (بدون تسجيل دخول جديد إن أمكن)."""
    if InstagrapiClient is None:
        return False

    username = acc.get("username")
    if not username:
        return False

    session_file = SESSIONS_DIR / f"{username.lower()}.json"
    if not session_file.exists():
        return False

    client = InstagrapiClient()
    try:
        client.load_settings(session_file)
        client.account_info()  # نداء خفيف يتأكد إن الجلسة مقبولة عند انستغرام
        return True
    except Exception:
        return False


async def check_all_sessions(context: ContextTypes.DEFAULT_TYPE, send_report: bool = True):
    """
    يفحص كل الحسابات المضافة. يبلغ الأدمن فقط عند تغيّر الحالة (شغّال <-> خربان)
    عشان ما يصير إزعاج بتنبيه مكرر كل فحص وهو نفس الحالة.
    send_report=False يمنع الإرسال التلقائي (يستخدمها زر الفحص اليدوي لعرض تقرير واحد بنفسه).
    """
    accounts = load_ig_accounts()
    if not accounts:
        return []

    messages = []
    changed = False

    for acc in accounts:
        username = acc.get("username") or (acc.get("sessionid", "")[:10] + "…")
        was_healthy = acc.get("healthy", True)
        is_healthy = check_single_account_session(acc)

        if is_healthy != was_healthy:
            changed = True
            acc["healthy"] = is_healthy
            if not is_healthy:
                messages.append(
                    f"❌ الحساب @{username} توقف عن العمل (الجلسة انتهت أو تم حظره).\n"
                    f"روح لـ 📋 عرض/حذف الحسابات واحذفه، وضيف حساب بديل."
                )
            else:
                messages.append(f"✅ الحساب @{username} رجع يشتغل طبيعي.")

    if changed:
        save_ig_accounts(accounts)

    if messages and send_report:
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text="\n\n".join(messages))
        except Exception:
            logger.exception("تعذر إشعار الأدمن بحالة الجلسات")

    return messages


async def sessions_health_job(context: ContextTypes.DEFAULT_TYPE):
    """مهمة دورية (JobQueue) تفحص كل الحسابات وتبلغ الأدمن عند أي تغيّر بالحالة."""
    await check_all_sessions(context)


async def alert_admin_all_accounts_down(context: ContextTypes.DEFAULT_TYPE):
    """
    يُستدعى وقت فشل تحميل حقيقي لمستخدم بسبب توقف كل الحسابات المضافة.
    فيه تهدئة (cooldown) 30 دقيقة عشان ما يصير إزعاج لو صار كذا طلب بنفس الوقت.
    """
    global _last_broken_alert_ts
    now = time.time()
    if now - _last_broken_alert_ts < 1800:  # 30 دقيقة
        return
    _last_broken_alert_ts = now

    accounts = load_ig_accounts()
    if not accounts:
        return
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text="⚠️ كل حسابات انستغرام المضافة فشلت أثناء طلب تحميل حقيقي الآن.\n"
                 "افحص الحسابات من 📋 عرض/حذف الحسابات وضيف حساب شغّال.",
        )
    except Exception:
        logger.exception("تعذر إشعار الأدمن بتوقف كل الحسابات")



def download_instagram_story(target_username: str) -> dict:
    """
    يحمّل آخر ستوري لحساب انستغرام - فقط إذا كان الحساب عامًا، أو خاصًا
    ويتابعه حساب البوت فعليًا. لا يوجد أي تجاوز لحسابات خاصة غير متابَعة.
    """
    client = get_ig_client()
    if client is None:
        return {
            "error": "لا يوجد حساب انستغرام مفعّل حاليًا لتحميل الستوري. تواصل مع الأدمن.",
            "no_client": True,
        }

    try:
        user_id = client.user_id_from_username(target_username)
        info = client.user_info(user_id)
    except Exception as e:
        return {"error": f"ما قدرت ألقى الحساب @{target_username}: {e}"}

    if info.is_private and not info.friendship_status.following:
        return {
            "error": "هذا حساب خاص وحساب البوت لا يتابعه، فلا يمكن تحميل ستوريه "
                     "(حفاظًا على خصوصية صاحب الحساب)."
        }

    try:
        stories = client.user_stories(user_id)
    except Exception as e:
        return {"error": f"تعذر جلب الستوري: {e}"}

    if not stories:
        return {"error": "لا يوجد ستوري حالي لهذا الحساب."}

    items = []
    for s in stories:
        if s.video_url:
            items.append({"type": "video", "url": str(s.video_url)})
        elif s.thumbnail_url:
            items.append({"type": "photo", "url": str(s.thumbnail_url)})

    if not items:
        return {"error": "ما قدرت أستخرج روابط الستوري."}

    profile_url = f"https://www.instagram.com/{target_username}/"
    return {"items": items, "username": target_username, "profile_url": profile_url}


# ================== تيك توك ==================

TIKTOK_URL_RE = re.compile(r"tiktok\.com", re.IGNORECASE)


def extract_tiktok_media(url: str) -> dict:
    """يستخدم خدمة tikwm العامة لاستخراج ميديا تيك توك (فيديو أو صور متعددة) بدون علامة مائية."""
    if not TIKTOK_URL_RE.search(url):
        return {"error": "الرابط غير صالح، لازم يكون من تيك توك."}

    try:
        resp = requests.get(
            "https://www.tikwm.com/api/",
            params={"url": url, "hd": 1},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"error": f"ما قدرت أعالج رابط تيك توك: {e}"}

    payload = data.get("data", {}) or {}
    author = payload.get("author", {}) or {}
    username = author.get("unique_id") or author.get("uniqueId") or None
    profile_url = f"https://www.tiktok.com/@{username}" if username else None

    # منشور صور متعددة (TikTok Photo Mode) -> فيه "images" بدل فيديو وحد
    images = payload.get("images")
    if images:
        items = [{"type": "photo", "url": img} for img in images]
        return {"type": "carousel", "items": items, "username": username, "profile_url": profile_url}

    # نفضّل أعلى جودة متوفرة: hdplay (HD بدون علامة مائية) ثم play كبديل
    play_url = payload.get("hdplay") or payload.get("play")
    if not play_url:
        return {"error": "ما قدرت ألقى رابط الفيديو، تأكد أن الرابط صحيح وعام."}

    if play_url.startswith("/"):
        play_url = "https://www.tikwm.com" + play_url

    return {"type": "video", "url": play_url, "username": username, "profile_url": profile_url}


# ================== لوحات المفاتيح ==================

def build_subscribe_reminder_html() -> str:
    """رسالة تنبيه غير مانعة (soft nudge) - تُعرض بالنص، ما توقف التحميل."""
    return (
        "🚧┇عذراً، عليك الاشتراك في قناة البوت أولاً،\n"
        f'🚧┇القناة: <a href="{YOUTUBE_CHANNEL_URL}">اضغط هنا</a>\n\n'
        "<b>أرسل رابط الفيديو المراد تحميله</b>"
    )


def broadcast_pin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📌 إرسال مع تثبيت", callback_data="bc_pin"),
                InlineKeyboardButton("📨 إرسال بدون تثبيت", callback_data="bc_nopin"),
            ],
            [InlineKeyboardButton("🚫 إلغاء", callback_data="bc_cancel")],
        ]
    )


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 إذاعة", callback_data="admin_broadcast")],
            [InlineKeyboardButton("📌 إلغاء تثبيت آخر إذاعة", callback_data="admin_unpin_last")],
            [InlineKeyboardButton("➕ إضافة حساب (يوزر/رمز)", callback_data="admin_addacc")],
            [InlineKeyboardButton("🔑 إضافة حساب (Session ID)", callback_data="admin_addsession")],
            [InlineKeyboardButton("📋 عرض/حذف الحسابات", callback_data="admin_listacc")],
            [InlineKeyboardButton("🩺 فحص الحسابات الآن", callback_data="admin_checknow")],
            [InlineKeyboardButton("📊 عدد المستخدمين", callback_data="admin_stats")],
        ]
    )


def accounts_list_keyboard(accounts: list) -> InlineKeyboardMarkup:
    rows = []
    for acc in accounts:
        label = acc.get("username") or acc.get("sessionid", "")[:10] + "…"
        type_icon = "🔑" if acc.get("type") == "session" else "🔒"
        rows.append([
            InlineKeyboardButton(
                f"🗑 حذف {type_icon} @{label}", callback_data=f"admin_delacc:{acc.get('username', label)}"
            )
        ])
    rows.append([InlineKeyboardButton("⬅️ رجوع للقائمة", callback_data="admin_menu")])
    return InlineKeyboardMarkup(rows)


# ================== أوامر المستخدم ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db = load_db()
    existing = get_user(db, user.id)

    if existing is None:
        upsert_user(
            db, user.id,
            username=user.username or "",
            first_name=user.first_name or "",
        )

    if user.id == ADMIN_ID:
        await update.message.reply_text(
            "مرحبا بك أيها الأدمن ✅\nاختر من القائمة، أو أرسل رابط ريلز مباشرة للتحميل.",
            reply_markup=admin_menu_keyboard(),
        )
        return

    await update.message.reply_text(
        "مرحبا بك ✅\nأرسل رابط الريلز المراد تحميله."
    )


async def send_media_result(message, result: dict, single_caption: str | None = None):
    """
    يرسل نتيجة extract_* (فيديو مفرد / صورة مفردة / كاروسيل) بالشكل المناسب،
    ويمرّر اسم الحساب ورابط البروفايل لكل فيديو يُرسل.
    """
    username = result.get("username")
    profile_url = result.get("profile_url")

    if result["type"] == "video":
        await send_video_from_url(message, result["url"], caption=single_caption,
                                   username=username, profile_url=profile_url)

    elif result["type"] == "photo":
        meta = build_video_meta(username, profile_url)
        caption = f"{single_caption}\n\n{meta['caption_text']}" if single_caption else meta["caption_text"]
        await message.reply_photo(photo=result["url"], caption=caption)

    elif result["type"] == "carousel":
        for item in result["items"]:
            if item["type"] == "video":
                await send_video_from_url(message, item["url"], username=username, profile_url=profile_url)
            else:
                await send_photo_with_meta(message, item["url"], username=username, profile_url=profile_url)


REMINDER_INTERVAL_SECONDS = 3 * 3600  # كل 3 ساعات


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()

    if not text:
        return

    db = load_db()
    existing = get_user(db, user.id)
    if existing is None:
        upsert_user(db, user.id, username=user.username or "", first_name=user.first_name or "")
        existing = get_user(db, user.id)

    # تنبيه غير مانع بالاشتراك - يظهر مرة كل 3 ساعات فقط، ولا يوقف التحميل إطلاقًا
    if user.id != ADMIN_ID:
        last_reminder_ts = existing.get("last_reminder_ts", 0) if existing else 0
        now_ts = time.time()
        if now_ts - last_reminder_ts >= REMINDER_INTERVAL_SECONDS:
            upsert_user(db, user.id, last_reminder_ts=now_ts)
            await update.message.reply_text(
                build_subscribe_reminder_html(),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

    processing_msg = await update.message.reply_text("⏳ جاري المعالجة...")

    # ------ تيك توك (فيديو أو منشور صور متعددة) ------
    if TIKTOK_URL_RE.search(text):
        result = extract_tiktok_media(text)
        if "error" in result:
            await processing_msg.edit_text(f"❌ {result['error']}")
            return
        try:
            await send_media_result(update.message, result, single_caption="تم التحميل بجودة عالية ✅")
            await processing_msg.delete()
        except Exception as e:
            logger.exception("فشل إرسال ميديا تيك توك")
            await processing_msg.edit_text(f"❌ صار خطأ أثناء إرسال الملف: {e}")
        return

    # ------ ستوري انستغرام: instagram.com/stories/<username>/... ------
    story_match = re.search(r"instagram\.com/stories/([^/?]+)", text, re.IGNORECASE)

    # ------ أو يوزر مجرد (بدون رابط) مثل: john_doe أو @john_doe ------
    plain_username_match = None
    if not story_match and not TIKTOK_URL_RE.search(text) and "instagram.com" not in text.lower():
        candidate = text.lstrip("@").strip()
        if re.fullmatch(r"[A-Za-z0-9._]{1,30}", candidate):
            plain_username_match = candidate

    if story_match or plain_username_match:
        target_username = story_match.group(1) if story_match else plain_username_match
        result = download_instagram_story(target_username)
        if "error" in result:
            if result.get("no_client") and load_ig_accounts():
                # فيه حسابات مضافة لكن كلها فشلت تسجيل الدخول -> نبّه الأدمن
                await alert_admin_all_accounts_down(context)
            await processing_msg.edit_text(f"❌ {result['error']}")
            return
        try:
            username = result.get("username")
            profile_url = result.get("profile_url")
            for item in result["items"]:
                if item["type"] == "video":
                    await send_video_from_url(update.message, item["url"], username=username, profile_url=profile_url)
                else:
                    await send_photo_with_meta(update.message, item["url"], username=username, profile_url=profile_url)
            await processing_msg.delete()
        except Exception as e:
            logger.exception("فشل إرسال الستوري")
            await processing_msg.edit_text(f"❌ صار خطأ أثناء إرسال الملف: {e}")
        return

    # ------ ريلز / بوست انستغرام عادي (فيديو، صورة، أو كاروسيل) ------
    result = extract_instagram_media(text)

    if "error" in result:
        if result.get("no_client") and load_ig_accounts():
            await alert_admin_all_accounts_down(context)
        await processing_msg.edit_text(f"❌ {result['error']}")
        return

    try:
        await send_media_result(update.message, result, single_caption="تم التحميل بجودة عالية ✅")
        await processing_msg.delete()
    except Exception as e:
        logger.exception("فشل إرسال الميديا")
        await processing_msg.edit_text(f"❌ صار خطأ أثناء إرسال الملف: {e}")


# ================== لوحة الإذاعة (أدمن فقط) ==================

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    context.user_data["awaiting_broadcast"] = True
    await update.message.reply_text(
        "أرسل الآن نص/محتوى الإذاعة (نص، صورة مع كابشن، أو فيديو مع كابشن)."
    )


async def broadcast_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يلتقط رسالة الإذاعة من الأدمن بعد /broadcast."""
    if update.effective_user.id != ADMIN_ID:
        return False
    if not context.user_data.get("awaiting_broadcast"):
        return False

    context.user_data["awaiting_broadcast"] = False
    context.user_data["broadcast_message"] = update.message
    await update.message.reply_text(
        "اختر طريقة الإرسال:", reply_markup=broadcast_pin_keyboard()
    )
    return True


async def broadcast_pin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("هذا الزر خاص بالأدمن فقط.", show_alert=True)
        return

    await query.answer()
    choice = query.data  # bc_pin / bc_nopin / bc_cancel

    if choice == "bc_cancel":
        context.user_data.pop("broadcast_message", None)
        await query.edit_message_text("تم إلغاء الإذاعة ❌")
        return

    src_message = context.user_data.get("broadcast_message")
    if not src_message:
        await query.edit_message_text("لا توجد رسالة إذاعة محفوظة، ابدأ من جديد بـ /broadcast")
        return

    db = load_db()
    user_ids = [k for k in db.keys() if int(k) != ADMIN_ID]

    sent, failed = 0, 0
    pinned_map = {}  # chat_id -> message_id (لكل رسالة انثبتت، عشان نقدر نلغيها لاحقًا)

    for uid_str in user_ids:
        uid = int(uid_str)
        try:
            copied = await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=src_message.chat_id,
                message_id=src_message.message_id,
            )
            if choice == "bc_pin":
                try:
                    await context.bot.pin_chat_message(
                        chat_id=uid, message_id=copied.message_id
                    )
                    pinned_map[uid] = copied.message_id
                except Exception:
                    logger.warning("تعذر تثبيت الرسالة عند المستخدم %s", uid)
            sent += 1
        except Exception:
            failed += 1

    context.user_data.pop("broadcast_message", None)

    result_text = f"تمت الإذاعة ✅\nتم الإرسال بنجاح لـ {sent} مستخدم.\nفشل الإرسال لـ {failed} مستخدم."

    if choice == "bc_pin" and pinned_map:
        # نخزّنها بذاكرة البوت (bot_data) - تسمح لاحقًا بإلغاء تثبيت هذي الإذاعة بالذات بزر واحد
        context.bot_data["last_broadcast_pins"] = pinned_map
        result_text += f"\n📌 انثبتت عند {len(pinned_map)} مستخدم (تقدر تلغيها لاحقًا من زر إلغاء تثبيت آخر إذاعة)."

    await query.edit_message_text(result_text)


# ================== إضافة حساب انستغرام (أدمن فقط) ==================

async def addaccount_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    context.user_data["awaiting_ig_account"] = True
    await update.message.reply_text(
        "أرسل بيانات حساب انستغرام بهذا الشكل بالضبط، بسطر واحد:\n\n"
        "username password\n\n"
        "⚠️ هذا الحساب رح يُستخدم فقط لتحميل ستوري الحسابات العامة، أو الحسابات "
        "الخاصة اللي هذا الحساب فعليًا متابعها. راح يُرفض تلقائيًا أي طلب لستوري "
        "حساب خاص غير متابَع.\n"
        "يفضّل تستخدم حساب مخصص لهذا الغرض، مو حسابك الشخصي، لتفادي أي مخاطرة "
        "حظر أو تقييد من انستغرام."
    )


async def addaccount_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_user.id != ADMIN_ID:
        return False
    if not context.user_data.get("awaiting_ig_account"):
        return False

    context.user_data["awaiting_ig_account"] = False
    parts = (update.message.text or "").strip().split(maxsplit=1)

    if len(parts) != 2:
        await update.message.reply_text("الصيغة غلط. أعد المحاولة بـ /addaccount")
        return True

    username, password = parts

    checking_msg = await update.message.reply_text("⏳ جاري التحقق من صحة الحساب...")
    success, msg = try_login_ig(username, password)

    if not success:
        await checking_msg.edit_text(f"{msg}\n\nما تم حفظ الحساب، تأكد من اليوزر والرمز وحاول ثانية.")
    else:
        add_ig_account_password(username, password)
        await checking_msg.edit_text(f"تم حفظ الحساب @{username} ✅\n{msg}")
    return True


async def addsession_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_user.id != ADMIN_ID:
        return False

    text = (update.message.text or "").strip()

    # يدعم الصيغتين: "/s <sessionid>" أو سطر sessionid لحاله بعد /s فاضية
    if text.startswith("/s"):
        sessionid = text[2:].strip()
    elif context.user_data.get("awaiting_ig_session"):
        sessionid = text
    else:
        return False

    context.user_data["awaiting_ig_session"] = False

    if not sessionid:
        await update.message.reply_text("أرسل sessionid بعد الأمر مباشرة، مثال:\n/s 12345%3Aabc...")
        return True

    checking_msg = await update.message.reply_text("⏳ جاري التحقق من الجلسة...")
    success, msg, username = try_login_ig_sessionid(sessionid)

    if not success:
        await checking_msg.edit_text(msg)
    else:
        add_ig_account_sessionid(sessionid, username)
        await checking_msg.edit_text(f"تم حفظ الحساب @{username} عبر sessionid ✅\n{msg}")
    return True


# ================== قائمة الأدمن (الأزرار) ==================

async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("هذا الزر خاص بالأدمن فقط.", show_alert=True)
        return

    await query.answer()
    action = query.data

    if action == "admin_menu":
        await query.edit_message_text(
            "لوحة تحكم الأدمن 👇", reply_markup=admin_menu_keyboard()
        )
        return

    if action == "admin_broadcast":
        context.user_data["awaiting_broadcast"] = True
        await query.edit_message_text(
            "أرسل الآن نص/محتوى الإذاعة (نص، صورة مع كابشن، أو فيديو مع كابشن)."
        )
        return

    if action == "admin_unpin_last":
        pins = context.bot_data.get("last_broadcast_pins")
        if not pins:
            await query.edit_message_text(
                "ما فيه إذاعة مثبّتة حاليًا لإلغائها.",
                reply_markup=admin_menu_keyboard(),
            )
            return

        unpinned, failed = 0, 0
        for uid, mid in pins.items():
            try:
                await context.bot.unpin_chat_message(chat_id=uid, message_id=mid)
                unpinned += 1
            except Exception:
                failed += 1

        context.bot_data["last_broadcast_pins"] = {}
        await query.edit_message_text(
            f"تم إلغاء التثبيت ✅\nنجح عند {unpinned} مستخدم.\nفشل عند {failed} مستخدم "
            f"(غالبًا لأنهم حظروا البوت أو حذفوا المحادثة).",
            reply_markup=admin_menu_keyboard(),
        )
        return

    if action == "admin_addacc":
        context.user_data["awaiting_ig_account"] = True
        await query.edit_message_text(
            "أرسل بيانات حساب انستغرام بهذا الشكل بالضبط، بسطر واحد:\n\n"
            "username password\n\n"
            "⚠️ هذا الحساب يُستخدم فقط لتحميل ستوري الحسابات العامة، أو الحسابات "
            "الخاصة اللي هذا الحساب فعليًا متابعها."
        )
        return

    if action == "admin_addsession":
        context.user_data["awaiting_ig_session"] = True
        await query.edit_message_text(
            "أرسل sessionid حق حساب انستغرام (تقدر تسحبه من كوكيز المتصفح بعد تسجيل الدخول للحساب)، "
            "أرسله كنص عادي بسطر واحد.\n\n"
            "⚠️ نفس القاعدة: هذا الحساب يُستخدم فقط لتحميل ستوري الحسابات العامة، أو "
            "الحسابات الخاصة اللي هذا الحساب فعليًا متابعها."
        )
        return

    if action == "admin_listacc":
        accounts = load_ig_accounts()
        if not accounts:
            await query.edit_message_text(
                "لا يوجد أي حساب انستغرام مضاف حاليًا.",
                reply_markup=admin_menu_keyboard(),
            )
            return
        lines = []
        for a in accounts:
            icon = "🔑 session" if a.get("type") == "session" else "🔒 يوزر/رمز"
            lines.append(f"• @{a.get('username', '؟')} ({icon})")
        names = "\n".join(lines)
        await query.edit_message_text(
            f"الحسابات المضافة ({len(accounts)}):\n{names}\n\nاضغط للحذف:",
            reply_markup=accounts_list_keyboard(accounts),
        )
        return

    if action.startswith("admin_delacc:"):
        username = action.split(":", 1)[1]
        removed = remove_ig_account(username)
        accounts = load_ig_accounts()
        msg = f"تم حذف @{username} ✅" if removed else f"ما لقيت @{username}"
        if accounts:
            await query.edit_message_text(
                f"{msg}\n\nالحسابات المتبقية ({len(accounts)}):",
                reply_markup=accounts_list_keyboard(accounts),
            )
        else:
            await query.edit_message_text(
                f"{msg}\n\nما بقي أي حساب مضاف.",
                reply_markup=admin_menu_keyboard(),
            )
        return

    if action == "admin_checknow":
        accounts = load_ig_accounts()
        if not accounts:
            await query.edit_message_text(
                "لا يوجد أي حساب مضاف حاليًا للفحص.",
                reply_markup=admin_menu_keyboard(),
            )
            return
        await query.edit_message_text("⏳ جاري فحص كل الحسابات...")
        messages = await check_all_sessions(context, send_report=False)
        if not messages:
            report = "كل الحسابات بنفس حالتها السابقة (ما فيه تغيّر) ✅"
        else:
            report = "\n\n".join(messages)
        await context.bot.send_message(chat_id=ADMIN_ID, text=report, reply_markup=admin_menu_keyboard())
        return

    if action == "admin_stats":
        db = load_db()
        total = sum(1 for k in db.keys() if int(k) != ADMIN_ID)
        await query.edit_message_text(
            f"📊 إجمالي المستخدمين المسجّلين بالبوت: {total}",
            reply_markup=admin_menu_keyboard(),
        )
        return


# ================== موزّع مركزي لكل الرسائل ==================
# يقرر قبل أي شي: هل هذه رسالة إذاعة أو بيانات حساب من الأدمن؟ ثم يوجّه
# صور/نصوص المستخدمين للمعالج المناسب.

async def any_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # 1) الأدمن يرسل بيانات حساب انستغرام (يوزر/رمز) بعد /addaccount
    if user.id == ADMIN_ID and context.user_data.get("awaiting_ig_account"):
        await addaccount_receive(update, context)
        return

    # 1ب) الأدمن يرسل sessionid بعد الضغط على زر إضافة بالسيشن (بدون أمر /s)
    if user.id == ADMIN_ID and context.user_data.get("awaiting_ig_session"):
        await addsession_receive(update, context)
        return

    # 2) الأدمن ينتظر إرسال محتوى إذاعة (نص/صورة/فيديو) بعد /broadcast
    if user.id == ADMIN_ID and context.user_data.get("awaiting_broadcast"):
        await broadcast_receive(update, context)
        return

    # 3) نص عادي = رابط (انستغرام/تيك توك) أو رسالة عادية
    if update.message and update.message.text:
        await handle_text(update, context)


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast_start))
    app.add_handler(CommandHandler("addaccount", addaccount_start))
    app.add_handler(CommandHandler("s", addsession_receive))  # /s <sessionid>

    app.add_handler(CallbackQueryHandler(broadcast_pin_callback, pattern=r"^bc_"))
    app.add_handler(CallbackQueryHandler(admin_menu_callback, pattern=r"^admin_"))

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, any_message_router))

    # فحص دوري لصحة كل حسابات انستغرام المضافة كل 6 ساعات (أول فحص بعد دقيقة من التشغيل)
    if app.job_queue is not None:
        app.job_queue.run_repeating(sessions_health_job, interval=6 * 3600, first=60)
    else:
        logger.warning(
            "JobQueue غير متاح - ثبّت الحزمة بـ: pip install \"python-telegram-bot[job-queue]\" "
            "عشان يشتغل الفحص الدوري التلقائي للجلسات."
        )

    logger.info("البوت شغال...")
    app.run_polling()


if __name__ == "__main__":
    main()
