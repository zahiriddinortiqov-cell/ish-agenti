#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
O'zbekistondagi Energetika, Loyihalash va Muhandislik Vakansiyalari AI Agenti (Render.com 24/7 Ready)
Yangilangan himoya va qoidalar:
1. Gemini API limiti tugaganda (429 Quota Exceeded / RESOURCE_EXHAUSTED) yoki har qanday AI xatoligida
   bot to'xtab qolmaydi (qulab tushmaydi).
2. AI limiti tugaganda foydalanuvchiga "⚠️ AI limiti tugadi, lekin topilgan vakansiyalar:" deb xabar beradi
   va topilgan vakansiyalarni AI tahlilisiz (kompaniya nomi, vakansiya sarlavhasi va havolasi bilan
   oddiy, tushunarli ko'rinishda) Telegramga yuboradi.
3. LinkedIn qismi o'zgarishsiz saqlangan, hh.uz 403 himoyasi, 30 kunlik filtr va 5 tadan batching faol.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

# Render.com loglarida barcha print() xabarlari darhol ko'rinishi uchun buferni o'chiramiz
os.environ["PYTHONUNBUFFERED"] = "1"
try:
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
except Exception:
    pass

import requests
import schedule
import telebot
from dotenv import load_dotenv
from telebot.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from pydantic import BaseModel, Field
from pypdf import PdfReader
from google import genai
from google.genai import types


# =====================================================================
# 1. SOZLAMALAR, VAQT MINTAQASI (UTC+5) VA HEADERS
# =====================================================================

try:
    TASHKENT_TZ = ZoneInfo("Asia/Tashkent")
except Exception:
    TASHKENT_TZ = timezone(timedelta(hours=5), name="Asia/Tashkent")


def get_tashkent_now() -> datetime:
    """Server qaysi davlatda (UTC) bo'lishidan qat'i nazar, aniq Toshkent vaqtini (UTC+5) qaytaradi."""
    return datetime.now(TASHKENT_TZ)


# LinkedIn uchun haqiqiy Chrome brauzeri sarlavhalari (TEGILMADI)
BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "ru-RU,ru;q=0.9,uz-UZ;q=0.8,uz;q=0.7,en-US;q=0.6,en;q=0.5",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

# hh.uz API uchun HTTP 403 ni aylanib o'tuvchi sarlavhalar
HH_HEADER_PROFILES: list[dict[str, str]] = [
    {
        "User-Agent": "EnergyCareerUzBot/2.1 (contact@energycareer.uz)",
        "HH-User-Agent": "EnergyCareerUzBot/2.1 (contact@energycareer.uz)",
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9,uz;q=0.8,en;q=0.7",
    },
    {
        "User-Agent": "ApplicantMobileClient/7.118.0 (android; uzbekistan)",
        "HH-User-Agent": "ApplicantMobileClient/7.118.0 (android; uzbekistan)",
        "Accept": "application/json",
    },
    {
        "User-Agent": "JobSearchMonitorUz/1.4 (hr-monitor@mail.uz)",
        "Accept": "application/json",
    },
]

DEFAULT_PROJECT_DIR = Path(r"D:\IshAgenti")
BASE_DIR = DEFAULT_PROJECT_DIR if DEFAULT_PROJECT_DIR.exists() else Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "natijalar"
STATE_FILE = OUTPUT_DIR / "vacancies_state.json"
SEEN_JOBS_FILE = BASE_DIR / "seen_jobs.txt"

RESUME_FILES: dict[str, Path] = {
    "uz": BASE_DIR / "rezyume_uz.pdf",
    "ru": BASE_DIR / "rezyume_ru.pdf",
    "en": BASE_DIR / "rezyume_en.pdf",
}

# Bir modelning kunlik limiti (20 ta so'rov) tugasa, keyingi modelga o'tadi;
# barchasining limiti tugasa, AI tahlilisiz oddiy matn rejimiga o'tadi!
GEMINI_MODEL = "gemini-3.0-flash"
GEMINI_MODEL_CANDIDATES: list[str] = [
    "gemini-3.0-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-3-flash-preview",
]
ACTIVE_GEMINI_MODEL: str | None = None

SCHEDULE_TIMES_UZT: list[str] = ["09:00", "19:00"]

HIGH_MATCH_THRESHOLD = 80
MEDIUM_MATCH_THRESHOLD = 0

GEMINI_BATCH_SIZE = 5
MAX_VACANCY_AGE_DAYS = 30

MAX_HH_VACANCIES_PER_SCAN = 35
MAX_LINKEDIN_JOBS_PER_SCAN = 15

HH_API_BASE_URL = "https://api.hh.ru/vacancies"
UZBEKISTAN_AREA_ID = "97"

HH_SEARCH_KEYWORDS: list[str] = [
    "Энергетик",
    "Электроэнергетика",
    "Главный энергетик",
    "Электрик",
    "Электромонтажник",
    "Electrical Engineer",
    "Energetik",
    "Elektroenergetika",
    "Инженер-проектировщик",
    "Проектировщик",
    "Промэлектропроект",
    "Электроснабжение",
    "ЭОМ",
    "Электроосвещение",
    "Muhandis-proyektirovshchik",
    "Engineer",
    "Инженер-энергетик",
    "Инженер ПТО",
    "Главный инженер",
    "Технический директор",
    "Технолог",
    "Строительный контроль",
    "Наладчик",
]

TARGET_KEYWORDS: list[str] = [kw.lower() for kw in HH_SEARCH_KEYWORDS] + [
    "инженер",
    "силовое электрооборудование",
    "релейной защиты",
    "рза",
    "кипиа",
    "асутп",
    "подстанц",
    "solar",
    "energy",
]

# 2-MANBA: LinkedIn Public Guest API (TEGILMADI)
LINKEDIN_GUEST_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
LINKEDIN_GUEST_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

LINKEDIN_SEARCH_KEYWORDS: list[str] = [
    "Electrical Engineer",
    "Energy Engineer",
    "Project Engineer",
    "Энергетик",
    "Инженер ПТО",
    "Инженер-проектировщик",
]

# Telegram Reply Keyboard tugmalari matni
BTN_SEARCH_NOW = "🔍 Hozir qidirish"
BTN_STOP_SEARCH = "⏹️ Qidiruvni to‘xtatish"
BTN_RESUME_SCHEDULE = "▶️ Qayta faollashtirish"
BTN_CLEAR_SEEN = "🗑️ Tarixni tozalash"

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_REGEX = re.compile(r"(?:\+?998[\s\-()]*)?(?:\d{2}[\s\-()]*)\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")

STATE_LOCK = threading.Lock()
SEEN_LOCK = threading.Lock()
SCAN_LOCK = threading.Lock()

STOP_SCAN_EVENT = threading.Event()
AUTO_SCHEDULE_ENABLED = True


# =====================================================================
# 2. RENDER.COM / KOYEB VA GITHUB UCHUN FAYLLARNI AVTOMATIK YARATISH
# =====================================================================

def auto_setup() -> None:
    """Render.com, Koyeb va GitHub uchun zarur fayllarni UTF-8 formatda yaratadi."""
    print("\n[0/4] Render.com / Koyeb va GitHub uchun loyiha fayllari tekshirilmoqda (auto_setup)...", flush=True)

    requirements_content = (
        "google-genai\n"
        "pypdf\n"
        "requests\n"
        "pydantic\n"
        "pyTelegramBotAPI\n"
        "python-dotenv\n"
        "schedule\n"
        "pytz\n"
        "tzdata\n"
    )

    runtime_content = "python-3.11.9\n"
    procfile_content = "web: python3 agent.py\n"

    dockerfile_content = (
        "FROM python:3.11-slim\n"
        "WORKDIR /app\n"
        "COPY requirements.txt ./\n"
        "RUN python3 -m pip install --no-cache-dir -r requirements.txt\n"
        "COPY . .\n"
        "ENV PORT=10000\n"
        "ENV PYTHONUNBUFFERED=1\n"
        "EXPOSE 10000\n"
        'CMD ["python3", "agent.py"]\n'
    )

    gitignore_content = (
        ".env\n"
        "__pycache__/\n"
        "*.py[cod]\n"
        "natijalar/\n"
    )

    files_to_create: dict[Path, str] = {
        BASE_DIR / "requirements.txt": requirements_content,
        BASE_DIR / "runtime.txt": runtime_content,
        BASE_DIR / "Procfile": procfile_content,
        BASE_DIR / "Dockerfile": dockerfile_content,
        BASE_DIR / ".gitignore": gitignore_content,
    }

    for file_path, content in files_to_create.items():
        try:
            file_path.write_text(content, encoding="utf-8", newline="\n")
            print(f"  [+] Tayyorlandi: {file_path}", flush=True)
        except Exception as exc:
            print(f"  [!] {file_path.name} faylini yaratishda xatolik: {exc}", flush=True)


# =====================================================================
# 3. RENDER / KOYEB "HEALTH CHECK" VA KEEP-ALIVE SERVERI
# =====================================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        payload = {
            "status": "ok",
            "service": "UzEnergyJobAgent",
            "model": ACTIVE_GEMINI_MODEL or GEMINI_MODEL,
            "tashkent_time": get_tashkent_now().strftime("%Y-%m-%d %H:%M:%S UTC+5"),
            "auto_schedule_enabled": AUTO_SCHEDULE_ENABLED,
        }
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


def start_health_check_server() -> None:
    port = int(os.environ.get("PORT", "10000"))

    def _serve() -> None:
        try:
            server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
            print(f"  [+] Health Check veb-serveri 0.0.0.0:{port} portida ishga tushdi.", flush=True)
            server.serve_forever()
        except Exception as exc:
            print(f"  [!] Health Check server xatoligi (port {port}): {exc}", flush=True)

    def _keep_alive_ping() -> None:
        """Render bepul tarifda 15 daqiqadan keyin uxlab qolmasligi uchun har 10 daqiqada ping yuboradi."""
        while True:
            time.sleep(600)
            ext_url = os.environ.get("RENDER_EXTERNAL_URL") or f"http://127.0.0.1:{port}/"
            try:
                requests.get(ext_url, timeout=10)
            except Exception:
                pass

    threading.Thread(target=_serve, daemon=True).start()
    threading.Thread(target=_keep_alive_ping, daemon=True).start()


# =====================================================================
# 4. GEMINI BATCH TAHLIL SXEMALARI (PYDANTIC)
# =====================================================================

class BatchVacancyItemEvaluation(BaseModel):
    vacancy_id: str = Field(description="Vakansiyaning unikal ID raqami.")
    detected_language: Literal["uz", "ru", "en"] = Field(
        description="Vakansiya tili: 'uz', 'ru' yoki 'en'."
    )
    match_score: int = Field(
        ge=0,
        le=100,
        description="Nomzodga moslik foizi (0–100%). Ish staji talabi ballni tushirmasin!",
    )
    contact_details: str = Field(
        default="",
        description="Vakansiyada ko'rsatilgan qisqa aloqa ma'lumoti."
    )
    short_summary: str = Field(
        description="Juda qisqa (maksimal 1-2 gap) asosiy vazifa va moslik sababi."
    )


class BatchVacancyEvaluationList(BaseModel):
    items: list[BatchVacancyItemEvaluation] = Field(
        description="Guruhdagi vakansiyalar uchun qisqa baholash natijalari."
    )


# =====================================================================
# 5. TAKRORIY VA ESKIRGAN E'LONLARDAN HIMOYALASH
# =====================================================================

def load_seen_jobs() -> set[str]:
    with SEEN_LOCK:
        try:
            if not SEEN_JOBS_FILE.exists():
                SEEN_JOBS_FILE.write_text("", encoding="utf-8")
                return set()
            lines = SEEN_JOBS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
            return {line.strip() for line in lines if line.strip()}
        except Exception as exc:
            print(f"[OGOHLANTIRISH] {SEEN_JOBS_FILE.name} faylini o'qishda xatolik: {exc}", flush=True)
            return set()


def clear_seen_jobs() -> None:
    with SEEN_LOCK:
        try:
            SEEN_JOBS_FILE.write_text("", encoding="utf-8")
        except Exception as exc:
            print(f"[OGOHLANTIRISH] {SEEN_JOBS_FILE.name} faylini tozalashda xatolik: {exc}", flush=True)


def mark_job_as_seen(vac_id: str) -> None:
    vac_id = str(vac_id).strip()
    if not vac_id:
        return
    with SEEN_LOCK:
        try:
            with SEEN_JOBS_FILE.open("a", encoding="utf-8") as f:
                f.write(f"{vac_id}\n")
        except Exception as exc:
            print(f"[OGOHLANTIRISH] {SEEN_JOBS_FILE.name} fayliga yozishda xatolik: {exc}", flush=True)


def is_vacancy_fresh(published_at_str: str, max_days: int = MAX_VACANCY_AGE_DAYS) -> bool:
    if not published_at_str:
        return True
    raw = str(published_at_str).strip()
    try:
        date_part = raw[:10]
        pub_date = datetime.strptime(date_part, "%Y-%m-%d").date()
        today_date = get_tashkent_now().date()
        age_days = (today_date - pub_date).days
        return age_days <= max_days
    except Exception:
        return True


def find_matched_keywords(title: str, description: str, source_kw: str = "") -> list[str]:
    haystack = f"{title} {description}".lower()
    matched: list[str] = []
    if source_kw:
        matched.append(source_kw)
    for kw in TARGET_KEYWORDS:
        if kw in haystack and kw.lower() not in [m.lower() for m in matched]:
            matched.append(kw)
    return matched[:5]


# =====================================================================
# 6. KONFIGURATSIYA VA .ENV YUKLASH (python-dotenv)
# =====================================================================

def get_config() -> dict[str, str]:
    env_path = BASE_DIR / ".env"
    load_dotenv(dotenv_path=env_path)

    gemini_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    tg_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    tg_chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    missing: list[str] = []
    if not gemini_key:
        missing.append("GEMINI_API_KEY")
    if not tg_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not tg_chat_id:
        missing.append("TELEGRAM_CHAT_ID")

    if missing:
        print("\n[XATOLIK] Quyidagi muhim kalitlar topilmadi:", ", ".join(missing), flush=True)
        sys.exit(1)

    return {
        "GEMINI_API_KEY": gemini_key,
        "TELEGRAM_BOT_TOKEN": tg_token,
        "TELEGRAM_CHAT_ID": tg_chat_id,
    }


# =====================================================================
# 7. PDF REZYUMELARNI O'QISH VA TILNI ANIQLASH
# =====================================================================

def extract_text_from_pdf(pdf_path: Path) -> str:
    if not pdf_path.exists():
        return ""
    try:
        reader = PdfReader(str(pdf_path))
        pages_text: list[str] = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages_text.append(page_text.strip())
        return "\n\n".join(pages_text).strip()
    except Exception as exc:
        print(f"[OGOHLANTIRISH] {pdf_path.name} faylini o'qishda xatolik: {exc}", flush=True)
        return ""


def load_all_resumes() -> dict[str, str]:
    resumes: dict[str, str] = {}
    print("\n[1/4] PDF rezyumelar tekshirilmoqda...", flush=True)
    for lang, path in RESUME_FILES.items():
        text = extract_text_from_pdf(path)
        if text:
            resumes[lang] = text
            print(f"  [+] {path.name} ({lang.upper()}) muvaffaqiyatli o'qildi ({len(text)} belgi).", flush=True)
        else:
            print(f"  [-] {path.name} ({lang.upper()}) topilmadi yoki bo'sh: {path}", flush=True)

    if not resumes:
        print("  [!] PDF rezyumelar topilmadi, standart Junior Energetik/Muhandis profili ishlatiladi.", flush=True)
        resumes = {
            "uz": "Energetika va elektrotexnika yo'nalishi bo'yicha oliy ma'lumotli muhandis. AutoCAD (elektr chizmalar, sxemalar) va MS Office dasturlarini mukammal biladi.",
            "ru": "Инженер-энергетик / проектировщик с высшим профильным образованием. Отличное владение AutoCAD (однолинейные схемы, чертежи ЭОМ, электроснабжение, ПТО) и MS Office.",
            "en": "Electrical & Energy Engineer with a Bachelor's degree in Power Engineering. Proficient in AutoCAD (electrical schematics, layouts, power supply) and MS Office.",
        }
    return resumes


def detect_language_fast(text: str) -> Literal["uz", "ru", "en"]:
    sample = text.lower()
    uz_markers = [
        "o‘", "o'", "g‘", "g'", "bo'yicha", "uchun", "talablar",
        "vazifalar", "ma'lumot", "tajriba", "korxona", "o'zbekiston", "toshkent",
        "ў", "қ", "ғ", "ҳ", "бўйича", "учун", "талаблар", "маълумот",
    ]
    ru_markers = [
        "требования", "обязанности", "условия", "опыт работы", "образование",
        "инженер", "проект", "энергетик", "знание", "компания", "график",
    ]
    en_markers = [
        "requirements", "responsibilities", "experience", "engineering",
        "project", "management", "skills", "qualifications", "degree", "energy",
    ]

    uz_score = sum(2 for m in uz_markers if m in sample)
    ru_score = sum(2 for m in ru_markers if m in sample)
    en_score = sum(2 for m in en_markers if m in sample)

    cyrillic_count = len(re.findall(r"[а-яёўқғҳ]", sample))
    latin_count = len(re.findall(r"[a-z]", sample))

    if re.search(r"[ўқғҳ]", sample):
        uz_score += 8
    if re.search(r"[og][\u02bb\u2018\u2019'`]", sample):
        uz_score += 8

    if cyrillic_count > latin_count:
        return "uz" if uz_score > ru_score else "ru"
    if uz_score >= en_score and uz_score > 0:
        return "uz"
    return "en"


def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    no_tags = re.sub(r"<[^>]+>", " ", raw_html)
    unescaped = html.unescape(no_tags)
    return re.sub(r"\s+", " ", unescaped).strip()


def extract_contacts_from_vacancy(detail: dict[str, Any], full_text: str) -> str:
    parts: list[str] = []
    contacts = detail.get("contacts") or {}
    if isinstance(contacts, dict):
        c_name = (contacts.get("name") or "").strip()
        c_email = (contacts.get("email") or "").strip()
        if c_name:
            parts.append(c_name)
        if c_email:
            parts.append(f"Email: {c_email}")
        phones = contacts.get("phones") or []
        for p in phones:
            if isinstance(p, dict):
                formatted_phone = p.get("formatted") or (
                    f"+{p.get('country', '')}{p.get('city', '')}{p.get('number', '')}".strip("+")
                )
                if formatted_phone:
                    parts.append(f"Tel: {formatted_phone}")

    for email_match in EMAIL_REGEX.findall(full_text):
        clean_email = email_match.strip(".,;:()[]<>\"'")
        if clean_email and all(clean_email.lower() not in existing.lower() for existing in parts):
            parts.append(f"Email: {clean_email}")

    for phone_match in PHONE_REGEX.findall(full_text):
        clean_phone = phone_match.strip()
        if len(re.sub(r"\D", "", clean_phone)) >= 9 and all(clean_phone not in existing for existing in parts):
            parts.append(f"Tel: {clean_phone}")

    return " | ".join(parts)


# =====================================================================
# 8. QIDIRUV MANBALARI: [hh.uz] (403 FIX) VA [LinkedIn] (TEGILMADI)
# =====================================================================

def format_salary(salary_data: dict[str, Any] | None) -> str:
    if not salary_data:
        return "Suhbat asosida"
    s_from = salary_data.get("from")
    s_to = salary_data.get("to")
    currency = salary_data.get("currency", "")
    if s_from and s_to:
        return f"{s_from:,} – {s_to:,} {currency}"
    if s_from:
        return f"dan {s_from:,} {currency}"
    if s_to:
        return f"gacha {s_to:,} {currency}"
    return "Suhbat asosida"


def _scrape_hh_web_fallback(kw: str, session: requests.Session) -> list[dict[str, Any]]:
    web_url = "https://tashkent.hh.uz/search/vacancy"
    params = {
        "text": kw,
        "area": UZBEKISTAN_AREA_ID,
        "search_period": MAX_VACANCY_AGE_DAYS,
        "items_on_page": 20,
    }
    try:
        resp = session.get(web_url, params=params, headers=BROWSER_HEADERS, timeout=15)
        if resp.status_code != 200:
            return []
        html_body = resp.text
        matches = re.findall(
            r'<a[^>]*href="https://[^"]*hh\.uz/vacancy/(\d+)[^"]*"[^>]*>(.*?)</a>',
            html_body,
            flags=re.DOTALL | re.IGNORECASE,
        )
        items: list[dict[str, Any]] = []
        seen_local: set[str] = set()
        for vid, raw_title in matches:
            title = clean_html(raw_title)
            if not title or vid in seen_local or len(title) < 3:
                continue
            seen_local.add(vid)
            items.append(
                {
                    "id": vid,
                    "name": title,
                    "employer": {"name": "hh.uz Ish beruvchi"},
                    "area": {"name": "O'zbekiston"},
                    "salary": None,
                    "published_at": get_tashkent_now().strftime("%Y-%m-%d"),
                    "alternate_url": f"https://tashkent.hh.uz/vacancy/{vid}",
                    "snippet": {"requirement": title, "responsibility": ""},
                }
            )
        return items
    except Exception:
        return []


def fetch_new_hh_uz_vacancies(
    keywords: list[str],
    seen_jobs: set[str],
    max_items: int = MAX_HH_VACANCIES_PER_SCAN,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now_str = get_tashkent_now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now_str} UZT] [hh.uz] {len(keywords)} ta kalit so'z bo'yicha qidirilmoqda...", flush=True)

    session = requests.Session()
    active_header_idx = 0

    batch_seen_ids: set[str] = set()
    new_short_vacancies: list[tuple[dict[str, Any], str]] = []
    skipped_seen_count = 0
    skipped_old_count = 0
    api_errors: list[str] = []

    for kw in keywords:
        if STOP_SCAN_EVENT.is_set():
            break

        params = {
            "text": kw,
            "area": UZBEKISTAN_AREA_ID,
            "period": MAX_VACANCY_AGE_DAYS,
            "per_page": 20,
            "page": 0,
        }

        items: list[dict[str, Any]] = []
        success = False

        for attempt in range(len(HH_HEADER_PROFILES)):
            idx = (active_header_idx + attempt) % len(HH_HEADER_PROFILES)
            hdrs = HH_HEADER_PROFILES[idx]
            try:
                resp = session.get(HH_API_BASE_URL, params=params, headers=hdrs, timeout=15)
                if resp.status_code == 200:
                    active_header_idx = idx
                    data = resp.json()
                    items = data.get("items") or []
                    success = True
                    break
                elif resp.status_code == 403:
                    continue
                else:
                    if len(api_errors) < 3:
                        api_errors.append(f"HTTP {resp.status_code} ({kw})")
                    break
            except Exception as exc:
                if len(api_errors) < 3:
                    api_errors.append(str(exc)[:50])
                break

        if not success:
            items = _scrape_hh_web_fallback(kw, session)
            if items:
                success = True
            elif len(api_errors) < 3:
                api_errors.append(f"403/Blocked ({kw})")

        for item in items:
            raw_id = str(item.get("id", "")).strip()
            if not raw_id:
                continue
            vac_id = f"hh_{raw_id}"
            if vac_id in batch_seen_ids:
                continue
            batch_seen_ids.add(vac_id)

            pub_at = str(item.get("published_at") or "")
            if not is_vacancy_fresh(pub_at, max_days=MAX_VACANCY_AGE_DAYS):
                skipped_old_count += 1
                continue

            if vac_id in seen_jobs or raw_id in seen_jobs:
                skipped_seen_count += 1
                continue

            title_str = str(item.get("name") or "")
            snip_req = str(((item.get("snippet") or {}).get("requirement")) or "")
            snip_resp = str(((item.get("snippet") or {}).get("responsibility")) or "")
            combined_short = f"{title_str} {snip_req} {snip_resp}".lower()

            if kw.lower() in ("строительный контроль", "технолог"):
                if not any(
                    w in combined_short
                    for w in ["энерг", "электр", "эом", "пто", "проектир", "инженер", "сет", "кабел", "подстанц", "кипиа", "асутп"]
                ):
                    continue

            new_short_vacancies.append((item, kw))

        time.sleep(0.15)

    print(
        f"  [*] [hh.uz] Yakunlandi: Jami {len(batch_seen_ids)} ta -> "
        f"Yangisi: {len(new_short_vacancies)} ta, Avval ko'rilgan: {skipped_seen_count} ta, Eskirgan: {skipped_old_count} ta.",
        flush=True,
    )

    detailed_vacancies: list[dict[str, Any]] = []
    for item, source_kw in new_short_vacancies[:max_items]:
        if STOP_SCAN_EVENT.is_set():
            break
        raw_id = str(item.get("id")).strip()
        vac_id = f"hh_{raw_id}"

        title = item.get("name", "Noma'lum vakansiya")
        employer = (item.get("employer") or {}).get("name", "Noma'lum korxona")
        area = (item.get("area") or {}).get("name", "O'zbekiston")
        experience = (item.get("experience") or {}).get("name", "Ko'rsatilmagan")
        employment = (item.get("employment") or {}).get("name", "")
        salary_str = format_salary(item.get("salary"))
        url = item.get("alternate_url") or f"https://uzbekistan.hh.uz/vacancy/{raw_id}"

        snippet_req = clean_html(str(((item.get("snippet") or {}).get("requirement")) or ""))
        snippet_resp = clean_html(str(((item.get("snippet") or {}).get("responsibility")) or ""))
        short_desc = f"{snippet_req} {snippet_resp}".strip() or title

        matched_kws = find_matched_keywords(title, short_desc, source_kw=source_kw)
        contact_info = extract_contacts_from_vacancy(item, short_desc)

        detailed_vacancies.append(
            {
                "id": vac_id,
                "source": "[hh.uz]",
                "title": title,
                "company": employer,
                "location": area,
                "salary": salary_str,
                "experience": experience,
                "employment": employment,
                "skills": [],
                "matched_keywords": matched_kws,
                "description": short_desc[:800],
                "contacts": contact_info,
                "url": url,
                "published_at": str(item.get("published_at") or "")[:10],
            }
        )

    diag = {
        "unique_found": len(batch_seen_ids),
        "new_count": len(detailed_vacancies),
        "skipped_seen": skipped_seen_count,
        "errors": api_errors,
    }
    return detailed_vacancies, diag


# =====================================================================
# LINKEDIN QISMI — O'ZGARISHSIZ BORICHA QOLDIRILDI!
# =====================================================================

def _parse_linkedin_guest_html(html_text: str, source_kw: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    li_blocks = re.findall(r"<li[^>]*>(.*?)</li>", html_text, flags=re.DOTALL | re.IGNORECASE)

    for block in li_blocks:
        urn_match = re.search(r"urn:li:jobPosting:(\d+)", block)
        href_match = re.search(r'href="([^"]*linkedin\.com/jobs/view/[^"]+)"', block, flags=re.IGNORECASE)

        job_num = ""
        if urn_match:
            job_num = urn_match.group(1)
        elif href_match:
            id_in_url = re.search(r"(\d{8,12})", href_match.group(1))
            if id_in_url:
                job_num = id_in_url.group(1)

        if not job_num and not href_match:
            continue

        time_match = re.search(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})"', block, flags=re.IGNORECASE)
        pub_date_str = time_match.group(1) if time_match else get_tashkent_now().strftime("%Y-%m-%d")
        if not is_vacancy_fresh(pub_date_str, max_days=MAX_VACANCY_AGE_DAYS):
            continue

        raw_url = href_match.group(1).split("?")[0] if href_match else f"https://www.linkedin.com/jobs/view/{job_num}"
        vac_id = f"li_{job_num}" if job_num else f"li_{hashlib.md5(raw_url.encode()).hexdigest()[:10]}"

        title_match = re.search(
            r'<h3[^>]*class="[^"]*base-search-card__title[^"]*"[^>]*>(.*?)</h3>',
            block,
            flags=re.DOTALL | re.IGNORECASE,
        )
        title = clean_html(title_match.group(1)) if title_match else "Engineer (LinkedIn)"

        comp_match = re.search(
            r'<h4[^>]*class="[^"]*base-search-card__subtitle[^"]*"[^>]*>(.*?)</h4>',
            block,
            flags=re.DOTALL | re.IGNORECASE,
        )
        company = clean_html(comp_match.group(1)) if comp_match else "LinkedIn Employer"

        loc_match = re.search(
            r'<span[^>]*class="[^"]*job-search-card__location[^"]*"[^>]*>(.*?)</span>',
            block,
            flags=re.DOTALL | re.IGNORECASE,
        )
        location = clean_html(loc_match.group(1)) if loc_match else "O'zbekiston"

        cards.append(
            {
                "id": vac_id,
                "job_num": job_num,
                "title": title,
                "company": company,
                "location": location,
                "url": raw_url,
                "source_kw": source_kw,
                "published_at": pub_date_str,
            }
        )
    return cards


def search_linkedin_jobs(
    seen_jobs: set[str],
    max_results: int = MAX_LINKEDIN_JOBS_PER_SCAN,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if STOP_SCAN_EVENT.is_set():
        return [], {"unique_found": 0, "new_count": 0, "skipped_seen": 0, "errors": []}

    now_str = get_tashkent_now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now_str} UZT] [LinkedIn Guest API] To'g'ridan-to'g'ri LinkedIn serveridan qidirilmoqda...", flush=True)

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    batch_ids: set[str] = set()
    raw_new_cards: list[dict[str, Any]] = []
    skipped_seen = 0
    li_errors: list[str] = []

    for kw in LINKEDIN_SEARCH_KEYWORDS:
        if STOP_SCAN_EVENT.is_set():
            break
        params = {
            "keywords": kw,
            "location": "Uzbekistan",
            "start": 0,
        }
        try:
            resp = session.get(LINKEDIN_GUEST_SEARCH_URL, params=params, timeout=15)
            if resp.status_code != 200:
                err_msg = f"HTTP {resp.status_code} ({kw})"
                print(f"  [!] [LinkedIn API] {err_msg}", flush=True)
                if len(li_errors) < 2:
                    li_errors.append(err_msg)
                continue

            parsed_cards = _parse_linkedin_guest_html(resp.text, source_kw=kw)
            for card in parsed_cards:
                vac_id = card["id"]
                if vac_id in batch_ids:
                    continue
                batch_ids.add(vac_id)

                if vac_id in seen_jobs:
                    skipped_seen += 1
                    continue

                raw_new_cards.append(card)
        except Exception as exc:
            print(f"  [!] [LinkedIn API] Xatolik ({kw}): {exc}", flush=True)
            if len(li_errors) < 2:
                li_errors.append(str(exc)[:60])
        time.sleep(0.4)

    linkedin_vacancies: list[dict[str, Any]] = []
    for card in raw_new_cards[:max_results]:
        if STOP_SCAN_EVENT.is_set():
            break
        job_num = card.get("job_num", "")
        description_text = f"{card['title']} — {card['company']} ({card['location']})"

        if job_num:
            detail_url = LINKEDIN_GUEST_DETAIL_URL.format(job_id=job_num)
            try:
                d_resp = session.get(detail_url, timeout=12)
                if d_resp.status_code == 200 and d_resp.text:
                    desc_match = re.search(
                        r'<div[^>]*class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
                        d_resp.text,
                        flags=re.DOTALL | re.IGNORECASE,
                    )
                    if desc_match:
                        description_text = clean_html(desc_match.group(1))
            except Exception:
                pass
            time.sleep(0.25)

        contacts = extract_contacts_from_vacancy({}, description_text)
        matched_kws = find_matched_keywords(card["title"], description_text, source_kw=card["source_kw"])

        linkedin_vacancies.append(
            {
                "id": card["id"],
                "source": "[LinkedIn]",
                "title": card["title"],
                "company": card["company"],
                "location": card["location"],
                "salary": "Suhbat asosida (LinkedIn)",
                "experience": "Ko'rsatilmagan",
                "employment": "Full-time",
                "skills": [],
                "matched_keywords": matched_kws,
                "description": description_text[:900],
                "contacts": contacts or "LinkedIn havolasi orqali (Easy Apply / HR)",
                "url": card["url"],
                "published_at": card.get("published_at") or get_tashkent_now().strftime("%Y-%m-%d"),
            }
        )

    print(
        f"  [*] [LinkedIn Guest API] Yakunlandi: Jami {len(batch_ids)} ta e'lon topildi -> "
        f"Yangisi: {len(linkedin_vacancies)} ta, Avval ko'rilgan: {skipped_seen} ta.",
        flush=True,
    )
    diag = {
        "unique_found": len(batch_ids),
        "new_count": len(linkedin_vacancies),
        "skipped_seen": skipped_seen,
        "errors": li_errors,
    }
    return linkedin_vacancies, diag


# =====================================================================
# 9. GEMINI BATCH TAHLILI VA 429 QUOTA EXCEEDED HIMOYASI (TRY-EXCEPT)
# =====================================================================

def _call_gemini_with_fallback(
    client: genai.Client,
    prompt: str,
    response_schema: Any = None,
) -> Any:
    """
    Agar joriy model 429 (Quota Exceeded / RESOURCE_EXHAUSTED) yoki 404 bersa,
    avtomatik ravishda zaxira Gemini modellarini sinab ko'radi.
    Barcha modellar limitdan oshgan bo'lsa, xatolik qaytaradi va bot oddiy matn rejimiga o'tadi.
    """
    global ACTIVE_GEMINI_MODEL
    models_to_try: list[str] = []
    if ACTIVE_GEMINI_MODEL:
        models_to_try.append(ACTIVE_GEMINI_MODEL)
    for m in GEMINI_MODEL_CANDIDATES:
        if m not in models_to_try:
            models_to_try.append(m)

    last_error: Exception | None = None

    for model_name in models_to_try:
        try:
            cfg = (
                types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    temperature=0.2,
                )
                if response_schema
                else types.GenerateContentConfig(temperature=0.3)
            )
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=cfg,
            )
            if ACTIVE_GEMINI_MODEL != model_name:
                ACTIVE_GEMINI_MODEL = model_name
                print(f"  [+] Faol Gemini modeli: {ACTIVE_GEMINI_MODEL}", flush=True)
            return resp
        except Exception as exc:
            last_error = exc
            err_str = str(exc)
            # 429 (Quota Exceeded) yoki 404 yoki 503 bo'lsa, keyingi modelga o'tamiz
            if any(code in err_str for code in ("429", "RESOURCE_EXHAUSTED", "Quota", "404", "NOT_FOUND", "503")):
                ACTIVE_GEMINI_MODEL = None
                continue
            break

    raise RuntimeError(f"{last_error}")


def build_no_ai_simple_item(vac: dict[str, Any], resumes: dict[str, str]) -> dict[str, Any]:
    """
    Gemini API ishlamaganda yoki 429 limit tugaganda sun'iy intellekt tahlilisiz
    oddiy ma'lumot strukturasini qaytaradi (`ai_failed = True`).
    """
    combined = f"{vac.get('title', '')} {vac.get('description', '')}"
    lang = detect_language_fast(combined)
    if lang not in resumes:
        lang = next(iter(resumes.keys()))  # type: ignore[assignment]
    return {
        "detected_language": lang,
        "match_score": 0,
        "contact_details": str(vac.get("contacts") or ""),
        "short_summary": "",
        "ai_failed": True,
    }


def analyze_vacancies_batch_with_gemini(
    client: genai.Client,
    batch: list[dict[str, Any]],
    resumes: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], bool]:
    """
    5 ta vakansiyani bitta guruh (batch) qilib Gemini'ga yuboradi.
    Qaytaradi: (results_map, ai_quota_failed)
    Agar Gemini API 429 Quota Exceeded yoki xatolik bersa, bot to'xtab qolmaydi —
    `ai_quota_failed = True` qaytaradi va vakansiyalar AI tahlilisiz oddiy matn ko'rinishida yuboriladi!
    """
    if not batch:
        return {}, False

    vacancies_block: list[str] = []
    for idx, vac in enumerate(batch, start=1):
        vacancies_block.append(
            f"[{idx}] ID: {vac['id']}\n"
            f"Lavozim: {vac.get('title')}\n"
            f"Kompaniya: {vac.get('company')} ({vac.get('location')})\n"
            f"Kalit so'zlar: {', '.join(vac.get('matched_keywords') or [])}\n"
            f"Qisqa matn: {str(vac.get('description') or '')[:500]}\n"
        )

    resume_sample = (resumes.get("ru") or resumes.get("uz") or resumes.get("en") or "")[:1800]

    prompt = f"""
Siz energetika va muhandislik vakansiyalari ekspertisiz. Quyidagi {len(batch)} ta vakansiyani nomzod profili bilan solishtirib, har biri uchun QISQA baho bering.

QAT'IY QOIDALAR:
1. Nomzod Junior (yangi bitiruvchi), ammo oliy muhandislik ma'lumotiga ega, **AutoCAD** (elektr chizmalar, ЭОМ, ПТО) va **MS Office** ni mukammal biladi.
2. Vakansiyadagi ish staji ("1-3 yil", "3-6 yil") talabi ballni (`match_score`) UMUMAN TUSHIRMASIN!
3. `short_summary` JUDA QISQA bo'lsin (maksimal 1-2 gap: asosiy vazifa va nega mosligi).

NOMZOD QISQA PROFILI:
{resume_sample}

VAKANSIYALAR RO'YXATI:
{chr(10).join(vacancies_block)}
""".strip()

    results_map: dict[str, dict[str, Any]] = {}
    ai_failed = False

    try:
        response = _call_gemini_with_fallback(
            client=client,
            prompt=prompt,
            response_schema=BatchVacancyEvaluationList,
        )
        if response.parsed and isinstance(response.parsed, BatchVacancyEvaluationList):
            parsed_list = response.parsed.items
        else:
            raw_data = json.loads(response.text or "{}")
            parsed_list = [BatchVacancyItemEvaluation(**x) for x in raw_data.get("items", [])]

        for item_ev in parsed_list:
            lang = item_ev.detected_language if item_ev.detected_language in resumes else next(iter(resumes.keys()))
            results_map[item_ev.vacancy_id] = {
                "detected_language": lang,
                "match_score": item_ev.match_score,
                "contact_details": item_ev.contact_details,
                "short_summary": item_ev.short_summary,
                "ai_failed": False,
            }
    except Exception as exc:
        ai_failed = True
        print(
            f"  [!] Gemini API limiti tugadi yoki xatolik yuz berdi ({str(exc)[:120]}) -> "
            "Vakansiyalar AI tahlilisiz oddiy matn ko'rinishida yuboriladi!",
            flush=True,
        )

    for vac in batch:
        vid = vac["id"]
        if vid not in results_map:
            results_map[vid] = build_no_ai_simple_item(vac, resumes)
        else:
            api_contacts = str(vac.get("contacts") or "").strip()
            if api_contacts and api_contacts not in results_map[vid]["contact_details"]:
                results_map[vid]["contact_details"] = api_contacts

    return results_map, ai_failed


def generate_cover_letter_on_demand(
    client: genai.Client,
    record: dict[str, Any],
    resumes: dict[str, str],
) -> tuple[str, bool]:
    """
    [ ✍️ Kover-xat yozish ] tugmasi bosilganda ishlaydi.
    Agar Gemini API limiti tugagan bo'lsa ham try-except orqali tayyor shablon Kover-xat qaytaradi.
    Qaytaradi: (cover_letter_text, used_ai)
    """
    vac = record["vacancy"]
    lang = record.get("resume_lang", "ru")
    resume_text = resumes.get(lang) or next(iter(resumes.values()))

    prompt = f"""
Quyidagi vakansiya uchun nomzod nomidan AYNAN '{lang.upper()}' tilida qisqa, professional va yuborishga tayyor Cover Letter (ilova xati) yozib bering.
Nomzod energetika/muhandislik bo'yicha oliy ma'lumotli, AutoCAD (chizmalar, elektr sxemalar, ЭОМ, ПТО) va MS Office dasturlarini mukammal biladi.
Faqat tayyor xat matnini qaytaring (ortiqcha izohsiz).

Vakansiya: {vac.get('title')}
Kompaniya: {vac.get('company')}
Tavsif: {vac.get('description', '')[:2000]}

Nomzod rezyumesi:
{resume_text[:3500]}
""".strip()

    try:
        resp = _call_gemini_with_fallback(client=client, prompt=prompt, response_schema=None)
        text = (resp.text or "").strip()
        if text:
            return text, True
    except Exception as exc:
        print(f"[!] Kover-xatda AI limiti tugagan ({str(exc)[:100]}) — tayyor shablon ishlatildi.", flush=True)

    # AI limiti tugaganda ishlatiladigan tayyor shablon Kover-xat
    title = vac.get("title", "Инженер-энергетик")
    company = vac.get("company", "Компания")
    if lang == "uz":
        return (
            f"Assalomu alaykum, {company} jamoasi!\n\n"
            f"Sizning «{title}» vakansiyangiz bo'yicha murojaat qilmoqdaman. "
            "Energetika va elektrotexnika yo'nalishida oliy ma'lumotga egaman, AutoCAD (elektr sxemalar, chizmalar) "
            "hamda MS Office dasturlarini mukammal bilaman.\n\n"
            f"Rezyumeimni (rezyume_{lang}.pdf) ilova qilmoqdaman. Suhbatga tayyorman!",
            False,
        )
    if lang == "en":
        return (
            f"Dear Hiring Team at {company},\n\n"
            f"I am writing to apply for the {title} role. I hold a degree in Electrical & Power Engineering "
            "and have strong proficiency in AutoCAD (electrical schematics, layouts) and MS Office.\n\n"
            "Thank you for your time and consideration.",
            False,
        )
    return (
        f"Здравствуйте, уважаемая команда {company}!\n\n"
        f"Прошу рассмотреть мою кандидатуру на вакансию «{title}». Имею профильное инженерно-энергетическое образование, "
        "отлично владею AutoCAD (однолинейные схемы, чертежи ЭОМ, электроснабжение, ПТО) и MS Office.\n\n"
        "Буду рад обсудить сотрудничество на собеседовании!",
        False,
    )


# =====================================================================
# 10. HOLATNI SAQLASH (STATE) VA TELEGRAM XABAR FORMATLARI
# =====================================================================

def load_state() -> dict[str, dict[str, Any]]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict[str, dict[str, Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with STATE_LOCK:
        try:
            STATE_FILE.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"[OGOHLANTIRISH] Holat faylini saqlashda xatolik: {exc}", flush=True)


def build_main_reply_keyboard() -> ReplyKeyboardMarkup:
    markup = ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True, row_width=2)
    btn_now = KeyboardButton(BTN_SEARCH_NOW)
    btn_clear = KeyboardButton(BTN_CLEAR_SEEN)
    btn_stop = KeyboardButton(BTN_STOP_SEARCH)
    btn_resume = KeyboardButton(BTN_RESUME_SCHEDULE)
    markup.add(btn_now, btn_clear)
    markup.add(btn_stop, btn_resume)
    return markup


def build_vacancy_inline_keyboard(vac_id: str) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(
            text="✍️ Kover-xat yozish",
            callback_data=f"write_cl|{vac_id}",
        ),
        InlineKeyboardButton(
            text="❌ Kerak emas",
            callback_data=f"skip|{vac_id}",
        ),
    )
    return keyboard


def format_plain_no_ai_vacancy_message(
    record: dict[str, Any],
    cover_letter_text: str | None = None,
    status_note: str | None = None,
) -> str:
    """
    Gemini API limiti tugaganda (429 Quota Exceeded) sun'iy intellekt tahlilisiz,
    shunchaki kompaniya nomi, vakansiya sarlavhasi va havolasini oddiy matn ko'rinishida chiqaradi.
    """
    vac = record["vacancy"]
    lang = record.get("resume_lang", "ru")
    source_tag = vac.get("source", "[hh.uz]")
    resume_filename = f"rezyume_{lang}.pdf"

    msg = (
        f"📌 <b>{html.escape(str(vac.get('title', '')))}</b>  <code>{html.escape(source_tag)}</code>\n"
        f"🏢 <b>Kompaniya:</b> {html.escape(str(vac.get('company', '')))}\n"
        f"📍 <b>Hudud:</b> {html.escape(str(vac.get('location', '')))} | 💰 {html.escape(str(vac.get('salary', '')))}\n"
        f"📄 <b>Rezyume:</b> <code>{resume_filename}</code>\n"
        f"🔗 <b>Havola:</b> {html.escape(str(vac.get('url', '')))}"
    )

    if cover_letter_text:
        msg += (
            f"\n\n✉️ <b>Tayyor Kover-xat ({lang.upper()}):</b>\n"
            f"<pre>{html.escape(cover_letter_text.strip()[:2500])}</pre>"
        )

    if status_note:
        msg += f"\n\n{status_note}"

    return msg[:4050]


def format_short_vacancy_message(
    record: dict[str, Any],
    cover_letter_text: str | None = None,
    status_note: str | None = None,
) -> str:
    """
    Agar AI ishlagan bo'lsa qisqa tahlilli formatda, agar AI limiti tugagan bo'lsa (`ai_failed=True`)
    sun'iy intellekt tahlilisiz oddiy matn ko'rinishida qaytaradi.
    """
    ev = record.get("evaluation") or {}
    if ev.get("ai_failed"):
        return format_plain_no_ai_vacancy_message(
            record,
            cover_letter_text=cover_letter_text,
            status_note=status_note,
        )

    vac = record["vacancy"]
    lang = record["resume_lang"]
    score = int(ev.get("match_score", 75))
    source_tag = vac.get("source", "[hh.uz]")
    resume_filename = f"rezyume_{lang}.pdf"
    contacts = (ev.get("contact_details") or vac.get("contacts") or "").strip() or "Havola orqali"
    short_summary = (ev.get("short_summary") or "").strip()
    pub_date = vac.get("published_at") or "Yangi"

    badge = "🔥 <b>O‘ta mos vakansiya!</b>" if score >= HIGH_MATCH_THRESHOLD else "📌 <b>Yangi vakansiya</b>"

    msg = (
        f"{badge} (<b>{score}%</b>)  <code>{html.escape(source_tag)}</code>\n\n"
        f"🏢 <b>{html.escape(str(vac.get('company', '')))}</b>\n"
        f"💼 <b>{html.escape(str(vac.get('title', '')))}</b>\n"
        f"📍 {html.escape(str(vac.get('location', '')))} | 💰 {html.escape(str(vac.get('salary', '')))} | 📅 {html.escape(str(pub_date))}\n"
        f"☎️ <b>Aloqa:</b> {html.escape(contacts[:120])}\n"
        f"📄 <b>Rezyume:</b> <code>{resume_filename}</code>\n"
        f"🔗 <a href=\"{html.escape(str(vac.get('url', '')))}\">Vakansiyani ochish</a>\n\n"
        f"💡 {html.escape(short_summary[:240])}"
    )

    if cover_letter_text:
        cl_trimmed = cover_letter_text.strip()
        if len(cl_trimmed) > 2500:
            cl_trimmed = cl_trimmed[:2500] + "..."
        msg += (
            f"\n\n✉️ <b>Tayyor Kover-xat ({lang.upper()} — nusxalash uchun ustiga bosing):</b>\n"
            f"<pre>{html.escape(cl_trimmed)}</pre>"
        )

    if status_note:
        msg += f"\n\n{status_note}"

    return msg[:4050]


# =====================================================================
# 11. YAGONA QIDIRUV VA 429 XATOLIKDAN HIMOYALANGAN YUBORISH JARAYONI
# =====================================================================

def run_single_scan(
    bot: telebot.TeleBot,
    config: dict[str, str],
    client: genai.Client,
    resumes: dict[str, str],
    notify_start_and_finish: bool = True,
    trigger_label: str = "Jadval bo'yicha",
) -> None:
    if not SCAN_LOCK.acquire(blocking=False):
        try:
            bot.send_message(
                chat_id=config["TELEGRAM_CHAT_ID"],
                text="⏳ <b>Qidiruv allaqachon bajarilmoqda...</b> Iltimos, yakunlanishini kuting.",
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(),
            )
        except Exception:
            pass
        return

    STOP_SCAN_EVENT.clear()
    chat_id = config["TELEGRAM_CHAT_ID"]

    try:
        seen_jobs = load_seen_jobs()
        if notify_start_and_finish:
            try:
                bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🔍 <b>Qidiruv boshlandi...</b> ({html.escape(trigger_label)})\n"
                        f"🕒 Toshkent vaqti: <code>{get_tashkent_now().strftime('%H:%M:%S')}</code>\n"
                        "⏳ <code>[hh.uz]</code> va <code>[LinkedIn]</code> dan oxirgi 30 kunlik yangi vakansiyalar olinmoqda..."
                    ),
                    parse_mode="HTML",
                    reply_markup=build_main_reply_keyboard(),
                )
            except Exception as exc:
                print(f"[!] Boshlanish xabarini yuborishda xatolik: {exc}", flush=True)

        state = load_state()

        # 1-yo'nalish: hh.uz
        try:
            hh_vacancies, hh_diag = fetch_new_hh_uz_vacancies(
                HH_SEARCH_KEYWORDS,
                seen_jobs=seen_jobs,
                max_items=MAX_HH_VACANCIES_PER_SCAN,
            )
        except Exception as hh_exc:
            print(f"[!] hh.uz qidiruv xatoligi: {hh_exc}", flush=True)
            hh_vacancies, hh_diag = [], {"unique_found": 0, "new_count": 0, "skipped_seen": 0, "errors": [str(hh_exc)]}

        # 2-yo'nalish: LinkedIn (TEGILMADI)
        try:
            linkedin_vacancies, li_diag = search_linkedin_jobs(
                seen_jobs=seen_jobs,
                max_results=MAX_LINKEDIN_JOBS_PER_SCAN,
            )
        except Exception as li_exc:
            print(f"[!] LinkedIn qidiruv xatoligi: {li_exc}", flush=True)
            linkedin_vacancies, li_diag = [], {"unique_found": 0, "new_count": 0, "skipped_seen": 0, "errors": [str(li_exc)]}

        all_new_vacancies = hh_vacancies + linkedin_vacancies

        if STOP_SCAN_EVENT.is_set():
            try:
                bot.send_message(
                    chat_id=chat_id,
                    text="⏹️ <b>Qidiruv foydalanuvchi tomonidan to‘xtatildi.</b>",
                    parse_mode="HTML",
                    reply_markup=build_main_reply_keyboard(),
                )
            except Exception:
                pass
            return

        diag_text = (
            f"📊 <b>Qidiruv natijasi:</b>\n"
            f"• <code>[hh.uz]</code>: Yangi <b>{hh_diag['new_count']}</b> ta "
            f"(Jami: {hh_diag['unique_found']} | Ko‘rilgan: {hh_diag['skipped_seen']})\n"
            f"• <code>[LinkedIn]</code>: Yangi <b>{li_diag['new_count']}</b> ta "
            f"(Jami: {li_diag['unique_found']} | Ko‘rilgan: {li_diag['skipped_seen']})"
        )

        if not all_new_vacancies:
            print("  [*] Yangi (ko'rilmagan) vakansiyalar topilmadi.", flush=True)
            if notify_start_and_finish:
                hint = ""
                if hh_diag["skipped_seen"] > 0 or li_diag["skipped_seen"] > 0:
                    hint = f"\n\n💡 <i>Avvalgi vakansiyalarni qayta olish uchun <b>[ {BTN_CLEAR_SEEN} ]</b> ni bosing.</i>"
                try:
                    bot.send_message(
                        chat_id=chat_id,
                        text=f"✅ <b>Qidiruv yakunlandi.</b>\n\n{diag_text}{hint}",
                        parse_mode="HTML",
                        reply_markup=build_main_reply_keyboard(),
                    )
                except Exception:
                    pass
            return

        sent_count = 0
        ai_quota_exhausted = False
        quota_notice_sent = False

        # Vakansiyalarni 5 tadan guruhlab tahlil qilamiz;
        # Agar Gemini 429 Quota Exceeded bersa, darhol "AI limiti tugadi, lekin topilgan vakansiyalar:"
        # deb xabar chiqarib, qolgan barcha vakansiyalarni AI tahlilisiz oddiy matnda yuboramiz!
        for start_idx in range(0, len(all_new_vacancies), GEMINI_BATCH_SIZE):
            if STOP_SCAN_EVENT.is_set():
                break

            batch = all_new_vacancies[start_idx : start_idx + GEMINI_BATCH_SIZE]

            if not ai_quota_exhausted:
                batch_evals, batch_failed = analyze_vacancies_batch_with_gemini(client, batch, resumes)
                if batch_failed:
                    ai_quota_exhausted = True
            else:
                batch_evals = {vac["id"]: build_no_ai_simple_item(vac, resumes) for vac in batch}

            if ai_quota_exhausted and not quota_notice_sent:
                quota_notice_sent = True
                try:
                    bot.send_message(
                        chat_id=chat_id,
                        text="⚠️ <b>AI limiti tugadi, lekin topilgan vakansiyalar:</b>",
                        parse_mode="HTML",
                        reply_markup=build_main_reply_keyboard(),
                    )
                except Exception:
                    pass

            for vac in batch:
                if STOP_SCAN_EVENT.is_set():
                    break

                vac_id = str(vac["id"]).strip()
                if vac_id in seen_jobs:
                    continue

                ev_data = batch_evals.get(vac_id) or build_no_ai_simple_item(vac, resumes)
                resume_lang = ev_data.get("detected_language", "ru")

                record: dict[str, Any] = {
                    "vacancy": vac,
                    "resume_lang": resume_lang,
                    "evaluation": ev_data,
                    "status": "notified",
                    "updated_at": get_tashkent_now().isoformat(timespec="seconds"),
                }

                tg_text = format_short_vacancy_message(record)
                keyboard = build_vacancy_inline_keyboard(vac_id)

                try:
                    sent_msg = bot.send_message(
                        chat_id=chat_id,
                        text=tg_text,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                        disable_web_page_preview=True,
                    )
                    record["telegram_message_id"] = sent_msg.message_id
                    state[vac_id] = record
                    save_state(state)
                    mark_job_as_seen(vac_id)
                    seen_jobs.add(vac_id)
                    sent_count += 1
                except Exception as tg_exc:
                    print(f"     [!] Telegramga yuborishda xatolik ({vac_id}): {tg_exc}", flush=True)

                time.sleep(0.35)

        if notify_start_and_finish:
            stopped_note = " (To‘xtatildi ⏹️)" if STOP_SCAN_EVENT.is_set() else ""
            mode_note = " (AI tahlilisiz oddiy rejimda)" if ai_quota_exhausted else ""
            try:
                bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"✅ <b>Qidiruv yakunlandi!{stopped_note}</b>\n"
                        f"Jami yuborildi: <b>{sent_count}</b> ta yangi vakansiya{mode_note}."
                    ),
                    parse_mode="HTML",
                    reply_markup=build_main_reply_keyboard(),
                )
            except Exception:
                pass

    except Exception as unexpected_exc:
        print(f"\n[!] Qidiruv jarayonida kutilmagan xatolik: {unexpected_exc}", flush=True)
        try:
            bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Qidiruv jarayonida xatolik yuz berdi, lekin bot ishlashda davom etmoqda: {html.escape(str(unexpected_exc)[:200])}",
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(),
            )
        except Exception:
            pass
    finally:
        SCAN_LOCK.release()


# =====================================================================
# 12. O'ZBEKISTON VAQTI BILAN (UTC+5: 09:00 va 19:00) REJALASHTIRUVCHI
# =====================================================================

def setup_daily_schedule(
    bot: telebot.TeleBot,
    config: dict[str, str],
    client: genai.Client,
    resumes: dict[str, str],
) -> None:
    def scheduled_job(slot_time: str) -> None:
        if not AUTO_SCHEDULE_ENABLED:
            return
        threading.Thread(
            target=run_single_scan,
            args=(bot, config, client, resumes, True, f"Avtomatik jadval ({slot_time} UZT)"),
            daemon=True,
        ).start()

    schedule.clear()
    for t_str in SCHEDULE_TIMES_UZT:
        try:
            schedule.every().day.at(t_str, "Asia/Tashkent").do(scheduled_job, slot_time=t_str)
        except Exception:
            uz_h, uz_m = map(int, t_str.split(":"))
            now_uz = get_tashkent_now()
            target_uz = now_uz.replace(hour=uz_h, minute=uz_m, second=0, microsecond=0)
            target_local = target_uz.astimezone()
            local_time_str = target_local.strftime("%H:%M")
            schedule.every().day.at(local_time_str).do(scheduled_job, slot_time=t_str)


def schedule_runner_loop() -> None:
    while True:
        try:
            schedule.run_pending()
        except Exception as exc:
            print(f"[!] Jadval tekshiruvchisida xatolik: {exc}", flush=True)
        time.sleep(1)


# =====================================================================
# 13. TELEGRAM BOT BOSHQARUVI (BARCHA TUGMALAR TRY-EXCEPT HIMOYASIDA)
# =====================================================================

def register_bot_handlers(
    bot: telebot.TeleBot,
    config: dict[str, str],
    client: genai.Client,
    resumes: dict[str, str],
) -> None:

    @bot.message_handler(commands=["start", "help"])
    def handle_start(message: Message) -> None:
        try:
            seen_count = len(load_seen_jobs())
            sched_status = "Faol ✅ (09:00 va 19:00 UZT)" if AUTO_SCHEDULE_ENABLED else "Vaqtincha to‘xtatilgan ⏸️"
            help_text = (
                "🤖 <b>Energetika va Muhandislik Vakansiyalari AI Agenti</b>\n\n"
                f"• <b>Manbalar:</b> <code>[hh.uz]</code> + <code>[LinkedIn]</code>\n"
                f"• <b>AI Himoyasi:</b> Gemini limiti tugasa ham (429), vakansiyalar oddiy matn ko‘rinishida to‘xtovsiz keladi ✅\n"
                f"• <b>Jadval:</b> {sched_status} | <b>Tarix:</b> {seen_count} ta"
            )
            bot.send_message(
                chat_id=message.chat.id,
                text=help_text,
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(),
            )
        except Exception as exc:
            print(f"[!] /start javobida xatolik: {exc}", flush=True)

    @bot.message_handler(func=lambda m: "Hozir qidirish" in (m.text or "") or (m.text or "").strip() == "/scan")
    def handle_search_now(message: Message) -> None:
        try:
            if str(message.chat.id) != str(config["TELEGRAM_CHAT_ID"]):
                return
            threading.Thread(
                target=run_single_scan,
                args=(bot, config, client, resumes, True, "Qo‘lda: 🔍 Hozir qidirish"),
                daemon=True,
            ).start()
        except Exception as exc:
            print(f"[!] Hozir qidirish tugmasida xatolik: {exc}", flush=True)

    @bot.message_handler(func=lambda m: "Tarixni tozalash" in (m.text or "") or (m.text or "").strip() == "/clear")
    def handle_clear_seen(message: Message) -> None:
        try:
            if str(message.chat.id) != str(config["TELEGRAM_CHAT_ID"]):
                return
            old_count = len(load_seen_jobs())
            clear_seen_jobs()
            bot.send_message(
                chat_id=message.chat.id,
                text=(
                    f"🗑️ <b>Tarix tozalandi!</b> ({old_count} ta yozuv o‘chirildi).\n"
                    f"Endi <b>[ {BTN_SEARCH_NOW} ]</b> ni bossangiz, oxirgi 30 kunlik vakansiyalar yangidan keladi."
                ),
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(),
            )
        except Exception as exc:
            print(f"[!] Tarixni tozalashda xatolik: {exc}", flush=True)

    @bot.message_handler(func=lambda m: "Qidiruvni to" in (m.text or ""))
    def handle_stop_search(message: Message) -> None:
        global AUTO_SCHEDULE_ENABLED
        try:
            if str(message.chat.id) != str(config["TELEGRAM_CHAT_ID"]):
                return
            STOP_SCAN_EVENT.set()
            AUTO_SCHEDULE_ENABLED = False
            bot.send_message(
                chat_id=message.chat.id,
                text=(
                    "⏹️ <b>Qidiruv to‘xtatildi va avto-jadval pauza qilindi!</b>\n"
                    f"Qayta yoqish uchun <code>{BTN_RESUME_SCHEDULE}</code> tugmasini bosing."
                ),
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(),
            )
        except Exception as exc:
            print(f"[!] Qidiruvni to'xtatish tugmasida xatolik: {exc}", flush=True)

    @bot.message_handler(func=lambda m: "Qayta faollashtirish" in (m.text or ""))
    def handle_resume_schedule(message: Message) -> None:
        global AUTO_SCHEDULE_ENABLED
        try:
            if str(message.chat.id) != str(config["TELEGRAM_CHAT_ID"]):
                return
            STOP_SCAN_EVENT.clear()
            AUTO_SCHEDULE_ENABLED = True
            bot.send_message(
                chat_id=message.chat.id,
                text="▶️ <b>Avtomatik rejim (09:00 va 19:00 UZT) qayta faollashtirildi!</b>",
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(),
            )
        except Exception as exc:
            print(f"[!] Qayta faollashtirish tugmasida xatolik: {exc}", flush=True)

    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback(call: CallbackQuery) -> None:
        try:
            data = call.data or ""
            if "|" not in data:
                bot.answer_callback_query(call.id, "Noma'lum buyruq.")
                return

            action, vac_id = data.split("|", 1)
            state = load_state()
            record = state.get(vac_id)

            if not record:
                bot.answer_callback_query(call.id, "Vakansiya ma'lumotlari topilmadi.", show_alert=True)
                return

            if action in ("write_cl", "view"):
                bot.answer_callback_query(call.id, "✍️ Kover-xat tayyorlanmoqda...")

                cover_letter = record.get("generated_cover_letter")
                used_ai = True
                if not cover_letter:
                    cover_letter, used_ai = generate_cover_letter_on_demand(client, record, resumes)
                    record["generated_cover_letter"] = cover_letter
                    record["status"] = "cover_letter_generated"
                    record["updated_at"] = get_tashkent_now().isoformat(timespec="seconds")
                    state[vac_id] = record
                    save_state(state)

                note = None if used_ai else "ℹ️ <i>AI limiti tugagani sababli tayyor shablon kover-xat qo‘yildi.</i>"
                updated_text = format_short_vacancy_message(
                    record,
                    cover_letter_text=cover_letter,
                    status_note=note,
                )
                skip_only_kb = InlineKeyboardMarkup(row_width=1)
                skip_only_kb.add(
                    InlineKeyboardButton(
                        text="❌ Kerak emas",
                        callback_data=f"skip|{vac_id}",
                    )
                )
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=updated_text,
                    parse_mode="HTML",
                    reply_markup=skip_only_kb,
                    disable_web_page_preview=True,
                )
                return

            if action == "skip":
                record["status"] = "dismissed_by_user"
                record["updated_at"] = get_tashkent_now().isoformat(timespec="seconds")
                state[vac_id] = record
                save_state(state)

                dismissed_text = format_short_vacancy_message(
                    record,
                    status_note="❌ <b>Kerak emas deb belgilandi</b>",
                )
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=dismissed_text,
                    parse_mode="HTML",
                    reply_markup=None,
                    disable_web_page_preview=True,
                )
                bot.answer_callback_query(call.id, "O'tkazib yuborildi ❌")
        except Exception as exc:
            print(f"[!] Tugmani qayta ishlashda xatolik: {exc}", flush=True)


# =====================================================================
# 14. ASOSIY ISHGA TUSHIRISH FUNKSIYASI (MAIN)
# =====================================================================

def main() -> None:
    print("=" * 88, flush=True)
    print(" O'ZBEKISTON ENERGETIKA VA MUHANDISLIK VAKANSIYALARI AI AGENTI (429 QUOTA SAFE)", flush=True)
    print(f" Ishchi papka   : {BASE_DIR}", flush=True)
    print(f" Tarix fayli    : {SEEN_JOBS_FILE}", flush=True)
    print(f" Toshkent vaqti : {get_tashkent_now().strftime('%Y-%m-%d %H:%M:%S (UTC+5)')}", flush=True)
    print("=" * 88, flush=True)

    auto_setup()
    start_health_check_server()

    config = get_config()
    resumes = load_all_resumes()
    seen_initial = load_seen_jobs()
    print(f"  [+] seen_jobs.txt tekshirildi: bazada {len(seen_initial)} ta ko'rilgan vakansiya mavjud.", flush=True)

    client = genai.Client(api_key=config["GEMINI_API_KEY"])
    bot = telebot.TeleBot(config["TELEGRAM_BOT_TOKEN"])

    register_bot_handlers(bot, config, client, resumes)
    setup_daily_schedule(bot, config, client, resumes)

    sched_thread = threading.Thread(target=schedule_runner_loop, daemon=True)
    sched_thread.start()

    try:
        bot.send_message(
            chat_id=config["TELEGRAM_CHAT_ID"],
            text=(
                "✅ <b>AI Agent yangilandi (429 Quota Exceeded himoyasi bilan)!</b>\n\n"
                "• Agar Gemini API kunlik limiti tugasa ham, bot to‘xtab qolmaydi.\n"
                "• Limit tugaganda <i>«⚠️ AI limiti tugadi, lekin topilgan vakansiyalar:»</i> deb xabar berib, "
                "barcha vakansiyalarni oddiy matn ko‘rinishida (nomi, kompaniya va havolasi bilan) yuboraveradi."
            ),
            parse_mode="HTML",
            reply_markup=build_main_reply_keyboard(),
        )
    except Exception as exc:
        print(f"  [!] Boshlang'ich Telegram xabarini yuborishda xatolik: {exc}", flush=True)

    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=25)
        except KeyboardInterrupt:
            break
        except Exception as poll_exc:
            print(f"\n[!] Telegram aloqasida uzilish ({poll_exc}). 10 soniyadan so'ng qayta ulanadi...", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()