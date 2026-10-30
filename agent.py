#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
O'zbekistondagi Energetika, Loyihalash va Muhandislik Vakansiyalari AI Agenti (Render.com 24/7 Ready)
Manbalar:
  1. [hh.uz API] — to'g'ridan-to'g'ri rasmiy JSON API (brauzer headers bilan)
  2. [LinkedIn Guest API] — to'g'ridan-to'g'ri LinkedIn Public Guest API (DuckDuckGo-siz)
Yangilangan Gemini Model:
  - gemini-3.0-flash / gemini-3.8-flash (avtomatik mos modelni tanlash mexanizmi bilan)
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
# 1. SOZLAMALAR, VAQT MINTAQASI (UTC+5) VA REAL BRAUZER HEADERS
# =====================================================================

try:
    TASHKENT_TZ = ZoneInfo("Asia/Tashkent")
except Exception:
    TASHKENT_TZ = timezone(timedelta(hours=5), name="Asia/Tashkent")


def get_tashkent_now() -> datetime:
    """Server qaysi davlatda (UTC) bo'lishidan qat'i nazar, aniq Toshkent vaqtini (UTC+5) qaytaradi."""
    return datetime.now(TASHKENT_TZ)


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

HH_API_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9,uz;q=0.8,en;q=0.7",
}

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

# GEMINI MODEL SOZLAMASI:
# Terminal shriftida nuqtali nol ('3.0') '3.8' ga o'xshab ko'rinishi sababli
# ikkala variant hamda avtomatik model aniqlash ro'yxati kiritildi:
GEMINI_MODEL = "gemini-3.0-flash"
GEMINI_MODEL_CANDIDATES: list[str] = [
    "gemini-3.0-flash",
    "gemini-3.8-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]
ACTIVE_GEMINI_MODEL: str | None = None

# Avtomatik qidiruv vaqtlari (O'zbekiston vaqti bilan kuniga 2 marta: 09:00 va 19:00)
SCHEDULE_TIMES_UZT: list[str] = ["09:00", "19:00"]

# Foiz cheklovisiz: 0%+ barcha topilgan vakansiyalar yuboriladi
HIGH_MATCH_THRESHOLD = 80
MEDIUM_MATCH_THRESHOLD = 0

MAX_HH_VACANCIES_PER_SCAN = 35
MAX_LINKEDIN_JOBS_PER_SCAN = 15

# 1-MANBA: hh.uz (HeadHunter API) — O'zbekiston hududi kodi: 97
HH_API_BASE_URL = "https://api.hh.ru/vacancies"
UZBEKISTAN_AREA_ID = "97"

HH_SEARCH_KEYWORDS: list[str] = [
    # 1. Elektroenergetika yo'nalishi
    "Энергетик",
    "Электроэнергетика",
    "Главный энергетик",
    "Электрик",
    "Электромонтажник",
    "Electrical Engineer",
    "Energetik",
    "Elektroenergetika",
    # 2. Loyihalash va Muhandislik
    "Инженер-проектировщик",
    "Проектировщик",
    "Промэлектропроект",
    "Электроснабжение",
    "ЭОМ",
    "Электроосвещение",
    "Muhandis-proyektirovshchik",
    "Engineer",
    "Инженер-энергетик",
    # 3. Qurilish va Ishlab chiqarish texnik yo'nalishlari
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

# 2-MANBA: LinkedIn Public Guest API
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
# 3. RENDER / KOYEB "HEALTH CHECK" MINI VEB-SERVERI
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

    server_thread = threading.Thread(target=_serve, daemon=True)
    server_thread.start()


# =====================================================================
# 4. GEMINI STRUCTURED OUTPUT SXEMASI (PYDANTIC)
# =====================================================================

class VacancyEvaluation(BaseModel):
    detected_language: Literal["uz", "ru", "en"] = Field(
        description="Vakansiya matnining asosiy tili: 'uz' (o'zbek), 'ru' (rus) yoki 'en' (ingliz)."
    )
    is_energy_or_project_relevant: bool = Field(
        description="Vakansiya energetika, loyihalash, muhandislik, PTO, qurilish yoki ishlab chiqarishga aloqadorligi."
    )
    match_score: int = Field(
        ge=0,
        le=100,
        description=(
            "Nomzodning vakansiyaga moslik foizi (0–100%). "
            "MUHIM: Ish staji talabi ballni umuman tushirmasin! "
            "Nomzodning oliy ma'lumoti, AutoCAD va MS Office bilimlari uchun maksimal ball berilsin."
        ),
    )
    contact_details: str = Field(
        default="",
        description="Vakansiyada ko'rsatilgan ish beruvchi aloqa ma'lumotlari (email, telefon, Telegram yoki manzil)."
    )
    matching_skills: list[str] = Field(
        description="Nomzodda mavjud bo'lgan (jumladan AutoCAD, elektr sxemalar, MS Office, ta'lim va rezyumedagi) mos ko'nikmalar."
    )
    missing_skills: list[str] = Field(
        description="Yetishmayotgan texnik ko'nikmalar (ESLATMA: ish staji va tajriba yillari bu yerga kiritilmasin!)."
    )
    analysis_summary: str = Field(
        description="Vakansiya haqida va moslik sababi bo'yicha qisqa, aniq tahlil (2-3 gap)."
    )
    cover_letter: str = Field(
        description=(
            "Vakansiya tilida ('uz', 'ru' yoki 'en') yozilgan to'liq va nusxalab yuborishga tayyor Cover Letter "
            "(har qanday foizda ham bo'sh qoldirilmasin!)."
        )
    )


# =====================================================================
# 5. TAKRORIY E'LONLARDAN HIMOYALASH (seen_jobs.txt) VA KALIT SO'Z FILTRI
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


def find_matched_keywords(title: str, description: str, source_kw: str = "") -> list[str]:
    haystack = f"{title} {description}".lower()
    matched: list[str] = []
    if source_kw:
        matched.append(source_kw)
    for kw in TARGET_KEYWORDS:
        if kw in haystack and kw.lower() not in [m.lower() for m in matched]:
            matched.append(kw)
    return matched[:6]


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
# 8. QIDIRUV MANBALARI: [hh.uz API] VA [LinkedIn Guest API]
# =====================================================================

def format_salary(salary_data: dict[str, Any] | None) -> str:
    if not salary_data:
        return "Ko'rsatilmagan (Suhbat asosida)"
    s_from = salary_data.get("from")
    s_to = salary_data.get("to")
    currency = salary_data.get("currency", "")
    if s_from and s_to:
        return f"{s_from:,} – {s_to:,} {currency}"
    if s_from:
        return f"dan {s_from:,} {currency}"
    if s_to:
        return f"gacha {s_to:,} {currency}"
    return "Ko'rsatilmagan"


def fetch_new_hh_uz_vacancies(
    keywords: list[str],
    seen_jobs: set[str],
    max_items: int = MAX_HH_VACANCIES_PER_SCAN,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now_str = get_tashkent_now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now_str} UZT] [hh.uz] {len(keywords)} ta kalit so'z bo'yicha qidirilmoqda...", flush=True)

    session = requests.Session()
    session.headers.update(HH_API_HEADERS)

    batch_seen_ids: set[str] = set()
    new_short_vacancies: list[tuple[dict[str, Any], str]] = []
    skipped_seen_count = 0
    api_errors: list[str] = []

    for kw in keywords:
        if STOP_SCAN_EVENT.is_set():
            break
        params = {
            "text": kw,
            "area": UZBEKISTAN_AREA_ID,
            "per_page": 20,
            "page": 0,
        }
        try:
            resp = session.get(HH_API_BASE_URL, params=params, timeout=15)
            if resp.status_code != 200:
                err_msg = f"HTTP {resp.status_code} ({kw})"
                print(f"  [!] [hh.uz] {err_msg}", flush=True)
                if len(api_errors) < 3:
                    api_errors.append(err_msg)
                continue

            data = resp.json()
            items = data.get("items") or []

            for item in items:
                raw_id = str(item.get("id", "")).strip()
                if not raw_id:
                    continue
                vac_id = f"hh_{raw_id}"
                if vac_id in batch_seen_ids:
                    continue
                batch_seen_ids.add(vac_id)

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
        except Exception as exc:
            err_msg = f"{kw}: {exc}"
            print(f"  [!] [hh.uz] Tarmoq xatoligi ({err_msg})", flush=True)
            if len(api_errors) < 3:
                api_errors.append(str(exc)[:60])
        time.sleep(0.15)

    print(
        f"  [*] [hh.uz] Qidiruv yakunlandi: Jami API dan {len(batch_seen_ids)} ta noyob e'lon olindi -> "
        f"Yangisi: {len(new_short_vacancies)} ta, Avval ko'rilgani (seen_jobs): {skipped_seen_count} ta.",
        flush=True,
    )

    detailed_vacancies: list[dict[str, Any]] = []
    for item, source_kw in new_short_vacancies[:max_items]:
        if STOP_SCAN_EVENT.is_set():
            break
        raw_id = str(item.get("id")).strip()
        vac_id = f"hh_{raw_id}"
        detail_url = f"{HH_API_BASE_URL}/{raw_id}"
        try:
            resp = session.get(detail_url, timeout=15)
            detail = resp.json() if resp.status_code == 200 else item
        except Exception:
            detail = item

        title = detail.get("name", "Noma'lum vakansiya")
        employer = (detail.get("employer") or {}).get("name", "Noma'lum korxona")
        area = (detail.get("area") or {}).get("name", "O'zbekiston")
        experience = (detail.get("experience") or {}).get("name", "Ko'rsatilmagan")
        employment = (detail.get("employment") or {}).get("name", "")
        salary_str = format_salary(detail.get("salary"))
        url = detail.get("alternate_url") or f"https://uzbekistan.hh.uz/vacancy/{raw_id}"

        raw_desc = detail.get("description") or ""
        snippet_req = ((item.get("snippet") or {}).get("requirement") or "")
        snippet_resp = ((item.get("snippet") or {}).get("responsibility") or "")
        full_text = clean_html(raw_desc) or clean_html(f"{snippet_req} {snippet_resp}")

        matched_kws = find_matched_keywords(title, full_text, source_kw=source_kw)
        contact_info = extract_contacts_from_vacancy(detail, full_text)
        key_skills = [s.get("name", "") for s in detail.get("key_skills", []) if s.get("name")]

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
                "skills": key_skills,
                "matched_keywords": matched_kws,
                "description": full_text,
                "contacts": contact_info,
                "url": url,
                "published_at": detail.get("published_at", ""),
            }
        )
        time.sleep(0.15)

    diag = {
        "unique_found": len(batch_seen_ids),
        "new_count": len(detailed_vacancies),
        "skipped_seen": skipped_seen_count,
        "errors": api_errors,
    }
    return detailed_vacancies, diag


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
                "description": description_text,
                "contacts": contacts or "LinkedIn havolasi orqali (Easy Apply / HR)",
                "url": card["url"],
                "published_at": get_tashkent_now().strftime("%Y-%m-%d"),
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
# 9. GEMINI AI TAHLILI (AVTOMATIK MODEL TANLASH + ZAXIRA MEXANIZMI)
# =====================================================================

def build_fallback_evaluation(
    vacancy: dict[str, Any],
    selected_lang: Literal["uz", "ru", "en"],
) -> VacancyEvaluation:
    title = vacancy.get("title", "Инженер-энергетик")
    company = vacancy.get("company", "Компания")
    kws = vacancy.get("matched_keywords") or ["Energetika", "AutoCAD", "MS Office"]

    if selected_lang == "uz":
        summary = f"Vakansiya '{', '.join(kws[:4])}' kalit so'zlari bo'yicha topildi. AutoCAD va elektroenergetika bilimlari ushbu lavozimga mos keladi."
        cl = (
            f"Assalomu alaykum, {company} jamoasi!\n\n"
            f"Sizning '{title}' vakansiyangiz bo'yicha murojaat qilmoqdaman. "
            "Energetika va elektrotexnika yo'nalishi bo'yicha oliy ma'lumotga egaman, "
            "AutoCAD dasturida elektr sxemalar va chizmalar bilan ishlash hamda MS Office "
            "dasturlarida texnik hujjatlarni yuritish ko'nikmalarini mukammal o'zlashtirganman.\n\n"
            "Rezyumeimni ilova qilmoqdaman. Suhbatga tayyorman!"
        )
    elif selected_lang == "en":
        summary = f"Matched via keywords: {', '.join(kws[:4])}. Strong alignment with electrical engineering, AutoCAD schematics, and project documentation."
        cl = (
            f"Dear Hiring Team at {company},\n\n"
            f"I am writing to apply for the {title} position. I hold a degree in Electrical & Power Engineering "
            "and possess strong proficiency in AutoCAD (electrical schematics, layouts, power supply design) and MS Office.\n\n"
            "Please find my resume attached for your review. Thank you for your time and consideration."
        )
    else:
        summary = f"Вакансия найдена по ключевым словам: {', '.join(kws[:4])}. Профильное энергетическое образование и отличное владение AutoCAD и MS Office."
        cl = (
            f"Здравствуйте, уважаемая команда {company}!\n\n"
            f"Прошу рассмотреть мою кандидатуру на вакансию «{title}». Имею профильное инженерно-энергетическое образование, "
            "отлично владею AutoCAD (разработка и чтение электрических схем, чертежей ЭОМ, электроснабжение, документация ПТО) и пакетом MS Office.\n\n"
            "Буду рад подробнее обсудить сотрудничество на собеседовании!"
        )

    return VacancyEvaluation(
        detected_language=selected_lang,
        is_energy_or_project_relevant=True,
        match_score=75,
        contact_details=str(vacancy.get("contacts") or ""),
        matching_skills=["AutoCAD", "MS Office", *kws[:3]],
        missing_skills=[],
        analysis_summary=summary,
        cover_letter=cl,
    )


def analyze_vacancy_with_gemini(
    client: genai.Client,
    vacancy: dict[str, Any],
    resumes: dict[str, str],
) -> tuple[VacancyEvaluation, str]:
    global ACTIVE_GEMINI_MODEL

    combined_vac_text = f"{vacancy.get('title', '')}\n{vacancy.get('description', '')}"
    pre_detected_lang = detect_language_fast(combined_vac_text)

    selected_lang: Literal["uz", "ru", "en"] = (
        pre_detected_lang if pre_detected_lang in resumes else next(iter(resumes.keys()))  # type: ignore[assignment]
    )
    resume_text = resumes[selected_lang]
    source_label = vacancy.get("source", "[hh.uz]")
    matched_kws = ", ".join(vacancy.get("matched_keywords") or [])

    prompt = f"""
Siz O'zbekistondagi energetika, loyihalash (ЭОМ, Электроснабжение) va muhandislik bo'yicha HR-ekspert va karyera maslahatchisisiz.

QAT'IY BAHOLASH SHARTLARI:
1. STAJ TALABINI INOBATGA OLMANG:
   - Nomzod yangi bitiruvchi (Junior mutaxassis).
   - Vakansiyada "1–3 yil", "3–6 yil staj kerak" deyilgan bo'lsa ham, bu holat moslik foizini (`match_score`) UMUMAN TUSHIRMASIN!
2. AUTOCAD, ЭОМ, ПТО VA MS OFFICE KO'NIKMALARIGA MAKSIMAL BALL:
   - Nomzod **AutoCAD** (2D/3D chizmalar, bir chiziqli elektr sxemalar, ЭОМ, Электроснабжение, ПТО hujjatlari) va **MS Office** dasturlarini MUKAMMAL BILADI deb hisoblang!
   - Elektroenergetika, Проектировщик, Инженер ПТО, Электрик, Наладчик, Главный энергетик / инженер yo'nalishidagi vakansiyalarga yuqori moslik balli bering.
3. HAR QANDAY BALLDA HAM COVER LETTER YOZING:
   - `match_score` necha foiz chiqishidan qat'i nazar, `cover_letter` maydoniga AYNAN VAKANSIYA TILIDA ('uz', 'ru' yoki 'en') yuborishga tayyor professional Cover Letter yozib bering.

--- VAKANSIYA MA'LUMOTLARI ---
Manba: {source_label}
Lavozim: {vacancy.get('title')}
Korxona: {vacancy.get('company')}
Hudud: {vacancy.get('location')}
Maosh: {vacancy.get('salary')}
Mos kelgan kalit so'zlar: {matched_kws}
Aniqlangan kontaktlar: {vacancy.get('contacts', '')}
Vakansiya tavsifi:
{vacancy.get('description', '')[:6000]}

--- NOMZOD REZYUMESI ({selected_lang.upper()} tilidagi fayldan) ---
Qo'shimcha kafolatlangan ko'nikmalar: AutoCAD (elektr sxemalar, ЭОМ, ПТО, loyihalash — mukammal), MS Office (Excel, Word — mukammal).
Rezyume matni:
{resume_text[:8000]}
""".strip()

    models_to_try = [ACTIVE_GEMINI_MODEL] if ACTIVE_GEMINI_MODEL else list(GEMINI_MODEL_CANDIDATES)
    evaluation: VacancyEvaluation | None = None
    last_error: Exception | None = None

    for model_name in models_to_try:
        if not model_name:
            continue
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=VacancyEvaluation,
                    temperature=0.2,
                ),
            )
            if response.parsed and isinstance(response.parsed, VacancyEvaluation):
                evaluation = response.parsed
            else:
                raw_json = json.loads(response.text or "{}")
                evaluation = VacancyEvaluation(**raw_json)

            if ACTIVE_GEMINI_MODEL != model_name:
                ACTIVE_GEMINI_MODEL = model_name
                print(f"     [+] Faol Gemini modeli tasdiqlandi: {ACTIVE_GEMINI_MODEL}", flush=True)
            break
        except Exception as exc:
            last_error = exc
            err_str = str(exc)
            # Agar model nomi 404 NOT_FOUND bersa, ro'yxatdagi keyingi modelni sinab ko'ramiz
            if "404" in err_str or "NOT_FOUND" in err_str:
                ACTIVE_GEMINI_MODEL = None
                continue
            break

    if evaluation is None:
        print(f"     [!] Gemini API xatoligi ({last_error}) — zaxira tahlil ishlatildi.", flush=True)
        evaluation = build_fallback_evaluation(vacancy, selected_lang)

    api_contacts = str(vacancy.get("contacts") or "").strip()
    if api_contacts and api_contacts not in evaluation.contact_details:
        if evaluation.contact_details:
            evaluation.contact_details = f"{api_contacts} | {evaluation.contact_details}"
        else:
            evaluation.contact_details = api_contacts

    return evaluation, selected_lang


# =====================================================================
# 10. HOLATNI SAQLASH (STATE) VA TELEGRAM KLAVIATURA / XABAR FORMATLARI
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


def format_high_match_message(record: dict[str, Any]) -> str:
    vac = record["vacancy"]
    ev = record["evaluation"]
    lang = record["resume_lang"]
    source_tag = vac.get("source", "[hh.uz]")
    resume_filename = f"rezyume_{lang}.pdf"
    kws = ", ".join(vac.get("matched_keywords") or []) or "Energetika / Muhandislik"
    contacts = (ev.get("contact_details") or "").strip() or (
        "E'londa ochiq ko'rsatilmagan — to'g'ridan-to'g'ri havola orqali yuboring"
    )
    cover_letter = (ev.get("cover_letter") or "").strip()

    header_block = (
        f"🔥 <b>O‘ta mos vakansiya! ({ev.get('match_score')}%)</b>  <code>{html.escape(source_tag)}</code>\n\n"
        f"🏢 <b>Kompaniya:</b> {html.escape(str(vac.get('company', '')))}\n"
        f"💼 <b>Vakansiya:</b> {html.escape(str(vac.get('title', '')))}\n"
        f"🔑 <b>Kalit so‘zlar:</b> <code>{html.escape(kws)}</code>\n"
        f"📍 <b>Hudud:</b> {html.escape(str(vac.get('location', '')))} | "
        f"💰 <b>Maosh:</b> {html.escape(str(vac.get('salary', '')))}\n"
        f"🔗 <b>Havola:</b> <a href=\"{html.escape(str(vac.get('url', '')))}\">{html.escape(str(vac.get('url', '')))}</a>\n"
        f"☎️ <b>Ish beruvchi kontakti:</b> {html.escape(contacts)}\n"
        f"📄 <b>Mos rezyume fayli:</b> <code>{resume_filename}</code>\n\n"
        f"🧠 <b>Tahlil:</b>\n{html.escape(str(ev.get('analysis_summary', '')))}\n\n"
        f"✉️ <b>Tayyor Kover-xat ({lang.upper()} — nusxalash uchun ustiga bosing):</b>\n"
    )

    max_cl_len = 4000 - len(header_block)
    if len(cover_letter) > max_cl_len:
        cover_letter = cover_letter[: max(200, max_cl_len - 20)] + "..."

    return f"{header_block}<pre>{html.escape(cover_letter)}</pre>"


def format_medium_match_message(record: dict[str, Any], status_note: str | None = None) -> str:
    vac = record["vacancy"]
    ev = record["evaluation"]
    lang = record["resume_lang"]
    source_tag = vac.get("source", "[hh.uz]")
    resume_filename = f"rezyume_{lang}.pdf"
    kws = ", ".join(vac.get("matched_keywords") or []) or "Energetika / Muhandislik"
    contacts = (ev.get("contact_details") or "").strip() or "Havola orqali"

    msg = (
        f"📌 <b>Topilgan vakansiya ({ev.get('match_score')}%)</b>  <code>{html.escape(source_tag)}</code>\n\n"
        f"🏢 <b>Kompaniya:</b> {html.escape(str(vac.get('company', '')))}\n"
        f"💼 <b>Vakansiya:</b> {html.escape(str(vac.get('title', '')))}\n"
        f"🔑 <b>Kalit so‘zlar:</b> <code>{html.escape(kws)}</code>\n"
        f"📍 <b>Hudud:</b> {html.escape(str(vac.get('location', '')))} | "
        f"💰 <b>Maosh:</b> {html.escape(str(vac.get('salary', '')))}\n"
        f"🔗 <b>Havola:</b> <a href=\"{html.escape(str(vac.get('url', '')))}\">{html.escape(str(vac.get('url', '')))}</a>\n"
        f"☎️ <b>Aloqa:</b> {html.escape(contacts)}\n"
        f"📄 <b>Tavsiya etilgan rezyume:</b> <code>{resume_filename}</code>\n\n"
        f"🧠 <b>Tahlil:</b>\n{html.escape(str(ev.get('analysis_summary', '')))}"
    )
    if status_note:
        msg += f"\n\n{status_note}"
    return msg[:4050]


def format_cover_letter_view_message(record: dict[str, Any]) -> str:
    vac = record["vacancy"]
    ev = record["evaluation"]
    lang = record["resume_lang"]
    source_tag = vac.get("source", "[hh.uz]")
    resume_filename = f"rezyume_{lang}.pdf"
    contacts = (ev.get("contact_details") or "").strip() or "Havola orqali"
    cover_letter = (ev.get("cover_letter") or "").strip()

    header = (
        f"📄 <b>Tayyor Kover-xat ({ev.get('match_score')}% moslik)</b>  <code>{html.escape(source_tag)}</code>\n\n"
        f"🏢 <b>Kompaniya:</b> {html.escape(str(vac.get('company', '')))}\n"
        f"💼 <b>Vakansiya:</b> {html.escape(str(vac.get('title', '')))}\n"
        f"🔗 <b>Havola:</b> <a href=\"{html.escape(str(vac.get('url', '')))}\">{html.escape(str(vac.get('url', '')))}</a>\n"
        f"☎️ <b>Ish beruvchi kontakti:</b> {html.escape(contacts)}\n"
        f"📄 <b>Mos rezyume fayli:</b> <code>{resume_filename}</code>\n\n"
        f"✉️ <b>Kover-xat ({lang.upper()} — nusxalash uchun ustiga bosing):</b>\n"
    )
    max_cl_len = 4000 - len(header)
    if len(cover_letter) > max_cl_len:
        cover_letter = cover_letter[: max(200, max_cl_len - 20)] + "..."

    return f"{header}<pre>{html.escape(cover_letter)}</pre>"


# =====================================================================
# 11. YAGONA QIDIRUV VA TAHLIL JARAYONI (DIAGNOSTIKA BILAN)
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
                        f"🤖 Model: <code>{ACTIVE_GEMINI_MODEL or GEMINI_MODEL}</code>\n"
                        f"📂 Tarixda ko‘rilganlar (seen_jobs): <code>{len(seen_jobs)}</code> ta\n"
                        "⏳ Manbalar (<code>[hh.uz API]</code> va <code>[LinkedIn Guest API]</code>) tekshirilmoqda..."
                    ),
                    parse_mode="HTML",
                    reply_markup=build_main_reply_keyboard(),
                )
            except Exception as exc:
                print(f"[!] Boshlanish xabarini yuborishda xatolik: {exc}", flush=True)

        state = load_state()

        hh_vacancies, hh_diag = fetch_new_hh_uz_vacancies(
            HH_SEARCH_KEYWORDS,
            seen_jobs=seen_jobs,
            max_items=MAX_HH_VACANCIES_PER_SCAN,
        )

        linkedin_vacancies, li_diag = search_linkedin_jobs(
            seen_jobs=seen_jobs,
            max_results=MAX_LINKEDIN_JOBS_PER_SCAN,
        )

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
            f"📊 <b>Qidiruv diagnostikasi:</b>\n"
            f"• <code>[hh.uz API]</code>: Jami topildi <b>{hh_diag['unique_found']}</b> ta "
            f"(Yangisi: <b>{hh_diag['new_count']}</b> ta | Avval ko‘rilgan: <b>{hh_diag['skipped_seen']}</b> ta)\n"
            f"• <code>[LinkedIn API]</code>: Jami topildi <b>{li_diag['unique_found']}</b> ta "
            f"(Yangisi: <b>{li_diag['new_count']}</b> ta | Avval ko‘rilgan: <b>{li_diag['skipped_seen']}</b> ta)"
        )
        if hh_diag["errors"]:
            diag_text += f"\n⚠️ <code>[hh.uz izoh]</code>: {html.escape(', '.join(hh_diag['errors']))}"
        if li_diag["errors"]:
            diag_text += f"\n⚠️ <code>[LinkedIn izoh]</code>: {html.escape(', '.join(li_diag['errors']))}"

        if not all_new_vacancies:
            print("  [*] Yangi (ko'rilmagan) vakansiyalar topilmadi.", flush=True)
            if notify_start_and_finish:
                hint = ""
                if hh_diag["skipped_seen"] > 0 or li_diag["skipped_seen"] > 0:
                    hint = (
                        "\n\n💡 <i>Izoh: Topilgan vakansiyalar avval ko‘rilganlar (seen_jobs.txt) "
                        "ro‘yxatida bor ekan. Ularni qaytadan olish uchun pastdagi "
                        f"<b>[ {BTN_CLEAR_SEEN} ]</b> tugmasini bosing!</i>"
                    )
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

        try:
            bot.send_message(
                chat_id=chat_id,
                text=f"{diag_text}\n\n🚀 <b>{len(all_new_vacancies)} ta yangi vakansiya tahlil qilinib yuborilmoqda...</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass

        sent_high_count = 0
        sent_other_count = 0

        for idx, vac in enumerate(all_new_vacancies, start=1):
            if STOP_SCAN_EVENT.is_set():
                break

            vac_id = str(vac.get("id", f"custom_{idx}")).strip()
            source_tag = vac.get("source", "[hh.uz]")
            if vac_id in seen_jobs:
                continue

            print(
                f"\n  -> ({idx}/{len(all_new_vacancies)}) {source_tag} [ID: {vac_id}] "
                f"{vac.get('title')} — {vac.get('company')}",
                flush=True,
            )

            evaluation, resume_lang = analyze_vacancy_with_gemini(client, vac, resumes)
            score = evaluation.match_score

            record: dict[str, Any] = {
                "vacancy": vac,
                "resume_lang": resume_lang,
                "evaluation": evaluation.model_dump(),
                "status": "analyzed",
                "updated_at": get_tashkent_now().isoformat(timespec="seconds"),
            }

            if score >= HIGH_MATCH_THRESHOLD:
                tg_text = format_high_match_message(record)
                try:
                    sent_msg = bot.send_message(
                        chat_id=chat_id,
                        text=tg_text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    record["status"] = "notified_high_match"
                    record["telegram_message_id"] = sent_msg.message_id
                    state[vac_id] = record
                    save_state(state)
                    mark_job_as_seen(vac_id)
                    seen_jobs.add(vac_id)
                    sent_high_count += 1
                except Exception as tg_exc:
                    print(f"     [!] Telegramga yuborishda xatolik: {tg_exc}", flush=True)

                time.sleep(1.0)
                continue

            keyboard = InlineKeyboardMarkup(row_width=2)
            keyboard.add(
                InlineKeyboardButton(
                    text="📄 Kover-xatni ko‘rish",
                    callback_data=f"view|{vac_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Kerak emas",
                    callback_data=f"skip|{vac_id}",
                ),
            )

            tg_text = format_medium_match_message(record)
            try:
                sent_msg = bot.send_message(
                    chat_id=chat_id,
                    text=tg_text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
                record["status"] = "notified_match"
                record["telegram_message_id"] = sent_msg.message_id
                state[vac_id] = record
                save_state(state)
                mark_job_as_seen(vac_id)
                seen_jobs.add(vac_id)
                sent_other_count += 1
            except Exception as tg_exc:
                print(f"     [!] Telegramga yuborishda xatolik: {tg_exc}", flush=True)

            time.sleep(1.0)

        if notify_start_and_finish:
            stopped_note = " (Foydalanuvchi tomonidan to‘xtatildi ⏹️)" if STOP_SCAN_EVENT.is_set() else ""
            summary_msg = (
                f"✅ <b>Qidiruv yakunlandi!{stopped_note}</b>\n\n"
                f"• <b>Yuborilgan yangi vakansiyalar:</b> {sent_high_count + sent_other_count} ta\n"
                f"• 🔥 <b>80%+ (O‘ta mos):</b> {sent_high_count} ta\n"
                f"• 📌 <b>0%–79% (Topilganlar):</b> {sent_other_count} ta"
            )
            try:
                bot.send_message(
                    chat_id=chat_id,
                    text=summary_msg,
                    parse_mode="HTML",
                    reply_markup=build_main_reply_keyboard(),
                )
            except Exception as exc:
                print(f"[!] Yakuniy hisobotni yuborishda xatolik: {exc}", flush=True)

    except Exception as unexpected_exc:
        print(f"\n[!] Qidiruv jarayonida kutilmagan xatolik: {unexpected_exc}", flush=True)
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
# 13. TELEGRAM BOT BOSHQARUVI (REPLY KEYBOARD + INLINE BUTTONS)
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
                f"• <b>Toshkent vaqti:</b> <code>{get_tashkent_now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
                f"• <b>Gemini Model:</b> <code>{ACTIVE_GEMINI_MODEL or GEMINI_MODEL}</code>\n"
                f"• <b>Manbalar:</b> <code>[hh.uz API]</code> + <code>[LinkedIn Guest API]</code>\n"
                f"• <b>Avtomatik jadval:</b> {sched_status}\n"
                f"• <b>Tarix bazasi (seen_jobs.txt):</b> {seen_count} ta vakansiya ko‘rilgan.\n\n"
                "👇 <b>Pastdagi tugmalar orqali boshqarishingiz mumkin:</b>\n"
                f"• <code>{BTN_SEARCH_NOW}</code> — Hoziroq qidirish.\n"
                f"• <code>{BTN_CLEAR_SEEN}</code> — Tarixni tozalab, barcha e'lonlarni qaytadan olish.\n"
                f"• <code>{BTN_STOP_SEARCH}</code> — Qidiruvni to‘xtatish.\n"
                f"• <code>{BTN_RESUME_SCHEDULE}</code> — Jadvalni qayta yoqish."
            )
            bot.send_message(
                chat_id=message.chat.id,
                text=help_text,
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(),
            )
        except Exception as exc:
            print(f"[!] /start javobida xatolik: {exc}", flush=True)

    @bot.message_handler(func=lambda m: (m.text or "").strip() in (BTN_SEARCH_NOW, "/scan"))
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

    @bot.message_handler(func=lambda m: (m.text or "").strip() in (BTN_CLEAR_SEEN, "/clear"))
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
                    f"Endi <b>[ {BTN_SEARCH_NOW} ]</b> tugmasini bossangiz, barcha mavjud vakansiyalar yangidan yuklanadi."
                ),
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(),
            )
        except Exception as exc:
            print(f"[!] Tarixni tozalashda xatolik: {exc}", flush=True)

    @bot.message_handler(func=lambda m: (m.text or "").strip() == BTN_STOP_SEARCH)
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

    @bot.message_handler(func=lambda m: (m.text or "").strip() == BTN_RESUME_SCHEDULE)
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

            if action == "view":
                record["status"] = "cover_letter_viewed"
                record["updated_at"] = get_tashkent_now().isoformat(timespec="seconds")
                state[vac_id] = record
                save_state(state)

                full_view_text = format_cover_letter_view_message(record)
                keep_or_skip_kb = InlineKeyboardMarkup(row_width=1)
                keep_or_skip_kb.add(
                    InlineKeyboardButton(
                        text="❌ Kerak emas",
                        callback_data=f"skip|{vac_id}",
                    )
                )
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=full_view_text,
                    parse_mode="HTML",
                    reply_markup=keep_or_skip_kb,
                    disable_web_page_preview=True,
                )
                bot.answer_callback_query(call.id, "Kover-xat ochildi 📄")
                return

            if action == "skip":
                record["status"] = "dismissed_by_user"
                record["updated_at"] = get_tashkent_now().isoformat(timespec="seconds")
                state[vac_id] = record
                save_state(state)

                dismissed_text = format_medium_match_message(
                    record,
                    status_note="❌ <b>Holat:</b> Kerak emas deb belgilandi",
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
    print(" O'ZBEKISTON ENERGETIKA VA MUHANDISLIK VAKANSIYALARI AI AGENTI (GEMINI 3 FLASH)", flush=True)
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
                "✅ <b>AI Agent yangilandi (Gemini 3 Flash)!</b>\n\n"
                f"• <b>Toshkent vaqti:</b> <code>{get_tashkent_now().strftime('%H:%M:%S (UTC+5)')}</code>\n"
                f"• <b>Gemini Model:</b> <code>{GEMINI_MODEL}</code> / <code>gemini-3.8-flash</code>\n"
                "• <b>Manbalar:</b> <code>[hh.uz API]</code> + <code>[LinkedIn Guest API]</code>\n"
                "• Hoziroq tekshirish uchun <b>[ 🔍 Hozir qidirish ]</b> tugmasini bosing."
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
