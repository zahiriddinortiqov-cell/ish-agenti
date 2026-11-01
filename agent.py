#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ko'p Foydalanuvchili (Multi-User & Non-Blocking) 3-Tilli (UZ / RU / EN)
AI Ish Qidirish Telegram Boti + Ixcham Yig'iladigan (is_persistent=False) Menyu va Admin Paneli
(Render.com 24/7 Ready)

Yangilangan imkoniyatlar:
1. Yig'iladigan klaviatura (is_persistent=False):
   - Telegram kiritish maydonidagi klaviatura belgisi orqali menyuni istalgan payt yashirish yoki ochish mumkin.
2. Ixcham Admin menyusi:
   - Pastki klaviaturada ortiqcha 4 ta admin tugmasi olib tashlanib, faqat bitta `👑 Admin Panel` tugmasi qoldirildi
     (bosilganda barcha admin funksiyalari chiroyli inline tugmalar ko'rinishida ochiladi).
3. Admin rezyumesini GitHub papkasidan (BASE_DIR) avtomatik ulash va `ADMIN_ID` xavfsizlik sozlamasi saqlangan.
"""

from __future__ import annotations

import hashlib
import html
import io
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
# 1. SOZLAMALAR, XAVFSIZ ADMIN_ID VA VAQT MINTAQASI (UTC+5)
# =====================================================================

try:
    TASHKENT_TZ = ZoneInfo("Asia/Tashkent")
except Exception:
    TASHKENT_TZ = timezone(timedelta(hours=5), name="Asia/Tashkent")


def get_tashkent_now() -> datetime:
    """Server qaysi davlatda (UTC) bo'lishidan qat'i nazar, aniq Toshkent vaqtini (UTC+5) qaytaradi."""
    return datetime.now(TASHKENT_TZ)


def get_today_str() -> str:
    return get_tashkent_now().strftime("%Y-%m-%d")


DEFAULT_PROJECT_DIR = Path(r"D:\IshAgenti")
BASE_DIR = DEFAULT_PROJECT_DIR if DEFAULT_PROJECT_DIR.exists() else Path(__file__).resolve().parent
USERS_DIR = BASE_DIR / "users"

load_dotenv(dotenv_path=BASE_DIR / ".env")

# Xavfsizlik talabi: ADMIN_ID faqat Environment Variable (.env yoki Render) orqali olinadi
ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0").strip() or "0")

# Oddiy foydalanuvchilar uchun kunlik qidiruv limiti (Admin uchun cheksiz)
DAILY_SEARCH_LIMIT: int = 3

# Loyiha papkasidagi (GitHub) Admin rezyume fayllari nomlari
ADMIN_DEFAULT_PDF_FILES: list[str] = [
    "rezyume_uz.pdf",
    "rezyume_ru.pdf",
    "rezyume_en.pdf",
    "rezyume.pdf",
]

# Admin uchun pastki menyuda faqat bitta ixcham tugma
BTN_ADMIN_PANEL = "👑 Admin Panel"


# =====================================================================
# 2. LOKALIZATSIYA LUG'ATI (TRANSLATIONS: UZ, RU, EN)
# =====================================================================

TRANSLATIONS: dict[str, dict[str, str]] = {
    "uz": {
        "btn_search_now": "🔍 Hozir qidirish",
        "btn_upload_resume": "📄 Rezyume yuklash (PDF)",
        "btn_my_keywords": "🔑 Kalit so‘zlarim",
        "btn_clear_seen": "🗑️ Tarixni tozalash",
        "btn_stop_search": "⏹️ Qidiruvni to‘xtatish",
        "btn_resume_schedule": "▶️ Qayta faollashtirish",
        "btn_change_lang": "🌐 Tilni o‘zgartirish",
        "btn_write_cl": "✍️ Kover-xat yozish",
        "btn_skip": "❌ Kerak emas",
        "btn_kw_confirm": "🔍 Shular bo‘yicha qidirish",
        "btn_kw_add": "➕ Boshqa kalit so‘z qo‘shish",
        "choose_lang_prompt": (
            "🇺🇿 <b>Iltimos, muloqot tilini tanlang:</b>\n"
            "🇷🇺 <b>Пожалуйста, выберите язык интерфейса:</b>\n"
            "🇬🇧 <b>Please select your preferred language:</b>"
        ),
        "lang_saved": "✅ <b>O‘zbek tili tanlandi!</b>",
        "welcome_no_resume": (
            "👋 Assalomu alaykum, <b>{name}</b>! Shaxsiy AI Ish Qidirish Botiga xush kelibsiz.\n\n"
            "📄 <b>Iltimos, rezyumengizni PDF formatida yuboring.</b>\n\n"
            "Rezyumengizni faqat <code>.pdf</code> formatida yuklaganingizdan so‘ng, "
            "Gemini sun'iy intellekti uni o‘qib chiqib, sizga mos ish qidirish kalit so‘zlarini avtomatik aniqlab beradi "
            "va <code>hh.uz</code> hamda <code>LinkedIn</code> dan mos vakansiyalarni topib keladi.\n"
            "📊 <b>Kunlik qidiruv limiti:</b> {limit_str}"
        ),
        "welcome_with_resume": (
            "👋 Assalomu alaykum, <b>{name}</b>!\n\n"
            "📄 <b>Yuklangan rezyumengiz:</b> <code>{filename}</code>\n"
            "🔑 <b>Faol kalit so‘zlaringiz:</b> <code>{keywords}</code>\n"
            "📊 <b>Kunlik qidiruv limiti:</b> {limit_str}\n\n"
            "• Vakansiyalarni qidirish uchun <b>[ 🔍 Hozir qidirish ]</b> tugmasini bosing.\n"
            "• Yangi rezyume yuklash uchun <b>PDF (.pdf)</b> faylingizni shu chatga yuboring."
        ),
        "limit_unlimited": "Cheksiz (Admin 👑)",
        "limit_daily": "Kuniga {limit} marta",
        "banned_msg": "🚫 <b>Kechirasiz, sizning profilingiz admin tomonidan bloklangan.</b>",
        "no_admin_access": "Kechirasiz, sizda bu bo'limga kirish huquqi yo'q",
        "upload_pdf_only": (
            "⚠️ <b>Iltimos, rezyumengizni faqat PDF (.pdf) formatida yuboring!</b>\n"
            "Bu botga faylni o‘qish va sizga mos vakansiyalarni aniq tahlil qilish imkonini beradi."
        ),
        "upload_pdf_prompt": (
            "📄 <b>Iltimos, rezyumengizni PDF formatida yuboring.</b>\n\n"
            "Pastdagi 📎 (skrepka) tugmasini bosib, kompyuter yoki telefoningizdan "
            "<code>.pdf</code> formatdagi rezyume faylingizni yuboring."
        ),
        "need_resume_first": "📄 <b>Iltimos, avval rezyumengizni PDF formatida yuboring!</b>",
        "pdf_received_wait": "⏳ <b>Rezyumengiz qabul qilindi!</b>\nMatn o‘qilmoqda va sizga mos kalit so‘zlar aniqlanmoqda...",
        "pdf_empty_error": (
            "⚠️ <b>Ushbu PDF fayldan matn o‘qib bo‘lmadi (skanerlangan rasm bo‘lishi mumkin).</b>\n"
            "Iltimos, matni nusxalanadigan (text-based) PDF rezyume yuboring."
        ),
        "pdf_analyzed_keywords": (
            "✅ <b>Rezyumengiz muvaffaqiyatli o‘qildi!</b> ({filename})\n"
            "👨‍💼 <b>Yo‘nalish:</b> {profession}\n\n"
            "🔑 <b>Sizning rezyumengiz asosida quyidagi kalit so‘zlar aniqlandi:</b>\n"
            "{keywords_list}\n\n"
            "❓ <b>Shular bo‘yicha qidiraylikmi yoki yana boshqa kalit so‘z qo‘shasizmi?</b>\n"
            "<i>(Quyidagi tugmani bosing yoki qo‘shimcha kalit so‘zlaringizni vergul bilan yozib yuboring)</i>"
        ),
        "my_keywords_msg": (
            "🔑 <b>Sizning joriy qidiruv kalit so‘zlaringiz:</b>\n{keywords_list}\n\n"
            "❓ Shular bo‘yicha qidiraylikmi yoki yana boshqa kalit so‘z qo‘shasizmi?\n"
            "<i>(Yangi kalit so‘z qo‘shish uchun ularni vergul bilan yozib yuboring)</i>"
        ),
        "enter_extra_keywords": (
            "✍️ <b>Qo‘shmoqchi bo‘lgan yangi kalit so‘zlaringizni vergul bilan ajratib yozib yuboring:</b>\n"
            "<i>Masalan: Инженер ПТО, ЭОМ, Проектировщик, Electrical Engineer</i>"
        ),
        "keywords_added": (
            "✅ <b>Yangi kalit so‘zlar qo‘shildi!</b>\n"
            "🔑 <b>Yangilangan ro‘yxat:</b> <code>{keywords}</code>\n\n"
            "🚀 Ushbu kalit so‘zlar asosida vakansiyalarni qidirish boshlanmoqda..."
        ),
        "scan_already_running": "⏳ <b>Siz uchun qidiruv allaqachon bajarilmoqda...</b> Iltimos, yakunlanishini kuting.",
        "quota_exhausted_user": (
            "⚠️ <b>Kunlik qidiruv limitingiz tugadi ({limit}/{limit})!</b>\n\n"
            "Gemini API bepul limitini tejash maqsadida oddiy foydalanuvchilarga kuniga "
            "maksimal <b>{limit} marta</b> qidiruv beriladi.\n"
            "🕒 Ertaga limit avtomatik yangilanadi yoki qo‘shimcha limit olish uchun adminga murojaat qiling."
        ),
        "quota_status_regular": "Bugungi limitdan qoldi: {remaining} ta ({used}/{limit})",
        "quota_status_bonus": "Bonus limit ishlatildi (Qolgan bonus: {bonus} ta)",
        "scan_started": (
            "🔍 <b>Qidiruv boshlandi...</b> ({trigger})\n"
            "📊 <b>Limit holati:</b> {quota_info}\n"
            "🔑 <b>Kalit so‘zlaringiz:</b> <code>{keywords}</code>\n"
            "⏳ <code>[hh.uz]</code> va <code>[LinkedIn]</code> tekshirilmoqda..."
        ),
        "scan_stopped": "⏹️ <b>Qidiruv to‘xtatildi va avto-jadval pauza qilindi!</b>",
        "schedule_resumed": "▶️ <b>Avtomatik rejim (09:00 va 19:00 UZT) siz uchun qayta faollashtirildi!</b>",
        "history_cleared": (
            "🗑️ <b>Sizning qidiruv tarixingiz tozalandi!</b> ({count} ta yozuv o‘chirildi).\n"
            "Endi <b>[ 🔍 Hozir qidirish ]</b> ni bossangiz, barcha yangi vakansiyalar qaytadan keladi."
        ),
        "scan_no_new_results": (
            "✅ <b>Qidiruv yakunlandi.</b>\n\n"
            "📊 <b>Qidiruv natijasi:</b>\n"
            "• <code>[hh.uz]</code>: Yangi <b>{hh_new}</b> ta (Jami: {hh_total} | Ko‘rilgan: {hh_seen})\n"
            "• <code>[LinkedIn]</code>: Yangi <b>{li_new}</b> ta (Jami: {li_total} | Ko‘rilgan: {li_seen}){hint}"
        ),
        "scan_hint_clear": "\n\n💡 <i>Avvalgi vakansiyalarni qayta olish uchun <b>[ 🗑️ Tarixni tozalash ]</b> ni bosing.</i>",
        "ai_limit_fallback_notice": "⚠️ <b>AI limiti tugadi, lekin topilgan vakansiyalar:</b>",
        "scan_finished": "✅ <b>Qidiruv yakunlandi!{stopped_note}</b>\nJami yuborildi: <b>{count}</b> ta yangi vakansiya{mode_note}.",
        "card_high_badge": "🔥 <b>O‘ta mos vakansiya!</b>",
        "card_new_badge": "📌 <b>Yangi vakansiya</b>",
        "card_contacts": "Aloqa",
        "card_open_link": "Vakansiyani ochish",
        "card_company": "Kompaniya",
        "card_location": "Hudud",
        "card_link": "Havola",
        "card_cl_header": "✉️ <b>Tayyor Kover-xat ({lang} — nusxalash uchun ustiga bosing):</b>",
        "card_cl_template_note": "ℹ️ <i>AI limiti tugagani sababli tayyor shablon kover-xat qo‘yildi.</i>",
        "card_dismissed": "❌ <b>Kerak emas deb belgilandi</b>",
        "cl_generating_toast": "✍️ Kover-xat tayyorlanmoqda...",
        "skip_toast": "O'tkazib yuborildi ❌",
        "admin_direct_msg_header": "📩 <b>Admindan xabar:</b>",
        "admin_broadcast_header": "📢 <b>Admin e'loni:</b>",
        "admin_bonus_gift": "🎁 Admin sizga <b>+{count} ta</b> qo‘shimcha qidiruv limiti taqdim etdi!",
        "admin_unbanned_notice": "✅ Sizning profilingiz admin tomonidan blokdan chiqarildi!",
    },
    "ru": {
        "btn_search_now": "🔍 Искать сейчас",
        "btn_upload_resume": "📄 Загрузить резюме (PDF)",
        "btn_my_keywords": "🔑 Мои ключевые слова",
        "btn_clear_seen": "🗑️ Очистить историю",
        "btn_stop_search": "⏹️ Остановить поиск",
        "btn_resume_schedule": "▶️ Возобновить авто-поиск",
        "btn_change_lang": "🌐 Сменить язык",
        "btn_write_cl": "✍️ Написать сопроводительное",
        "btn_skip": "❌ Не интересует",
        "btn_kw_confirm": "🔍 Искать по этим словам",
        "btn_kw_add": "➕ Добавить ключевые слова",
        "choose_lang_prompt": (
            "🇺🇿 <b>Iltimos, muloqot tilini tanlang:</b>\n"
            "🇷🇺 <b>Пожалуйста, выберите язык интерфейса:</b>\n"
            "🇬🇧 <b>Please select your preferred language:</b>"
        ),
        "lang_saved": "✅ <b>Выбран русский язык!</b>",
        "welcome_no_resume": (
            "👋 Здравствуйте, <b>{name}</b>! Добро пожаловать в персональный AI-бот для поиска работы.\n\n"
            "📄 <b>Пожалуйста, отправьте ваше резюме в формате PDF.</b>\n\n"
            "После загрузки файла <code>.pdf</code> искусственный интеллект Gemini проанализирует его, "
            "автоматически подберет подходящие ключевые слова и найдет свежие вакансии с <code>hh.uz</code> и <code>LinkedIn</code>.\n"
            "📊 <b>Дневной лимит поиска:</b> {limit_str}"
        ),
        "welcome_with_resume": (
            "👋 Здравствуйте, <b>{name}</b>!\n\n"
            "📄 <b>Загруженное резюме:</b> <code>{filename}</code>\n"
            "🔑 <b>Активные ключевые слова:</b> <code>{keywords}</code>\n"
            "📊 <b>Дневной лимит поиска:</b> {limit_str}\n\n"
            "• Нажмите <b>[ 🔍 Искать сейчас ]</b> для запуска поиска вакансий.\n"
            "• Чтобы обновить резюме, просто отправьте новый файл <b>PDF (.pdf)</b> в этот чат."
        ),
        "limit_unlimited": "Безлимитно (Админ 👑)",
        "limit_daily": "{limit} раза в день",
        "banned_msg": "🚫 <b>Извините, ваш профиль заблокирован администратором.</b>",
        "no_admin_access": "Kechirasiz, sizda bu bo'limga kirish huquqi yo'q",
        "upload_pdf_only": (
            "⚠️ <b>Пожалуйста, отправьте резюме только в формате PDF (.pdf)!</b>\n"
            "Это необходимо для точного чтения текста и анализа подходящих вакансий."
        ),
        "upload_pdf_prompt": (
            "📄 <b>Пожалуйста, отправьте ваше резюме в формате PDF.</b>\n\n"
            "Нажмите на значок 📎 (скрепка) ниже и выберите ваш файл резюме в формате <code>.pdf</code>."
        ),
        "need_resume_first": "📄 <b>Пожалуйста, сначала отправьте ваше резюме в формате PDF!</b>",
        "pdf_received_wait": "⏳ <b>Резюме получено!</b>\nЧитаем текст и подбираем подходящие ключевые слова...",
        "pdf_empty_error": (
            "⚠️ <b>Не удалось прочитать текст из этого PDF-файла (возможно, это скан-копия).</b>\n"
            "Пожалуйста, отправьте текстовый PDF-файл резюме."
        ),
        "pdf_analyzed_keywords": (
            "✅ <b>Ваше резюме успешно прочитано!</b> ({filename})\n"
            "👨‍💼 <b>Направление:</b> {profession}\n\n"
            "🔑 <b>На основе вашего резюме определены следующие ключевые слова:</b>\n"
            "{keywords_list}\n\n"
            "❓ <b>Искать по этим словам или хотите добавить еще ключевые слова?</b>\n"
            "<i>(Нажмите кнопку ниже или напишите дополнительные ключевые слова через запятую)</i>"
        ),
        "my_keywords_msg": (
            "🔑 <b>Ваши текущие ключевые слова для поиска:</b>\n{keywords_list}\n\n"
            "❓ Искать по этим словам или хотите добавить новые?\n"
            "<i>(Чтобы добавить новые слова, просто напишите их через запятую)</i>"
        ),
        "enter_extra_keywords": (
            "✍️ <b>Напишите дополнительные ключевые слова через запятую:</b>\n"
            "<i>Например: Инженер ПТО, ЭОМ, Проектировщик, Electrical Engineer</i>"
        ),
        "keywords_added": (
            "✅ <b>Новые ключевые слова добавлены!</b>\n"
            "🔑 <b>Обновленный список:</b> <code>{keywords}</code>\n\n"
            "🚀 Запускаем поиск вакансий по обновленным ключевым словам..."
        ),
        "scan_already_running": "⏳ <b>Поиск для вас уже выполняется...</b> Пожалуйста, дождитесь завершения.",
        "quota_exhausted_user": (
            "⚠️ <b>Ваш дневной лимит поиска исчерпан ({limit}/{limit})!</b>\n\n"
            "В целях экономии бесплатного лимита Gemini API обычным пользователям доступно "
            "максимум <b>{limit} поиска в день</b>.\n"
            "🕒 Завтра лимит обновится автоматически, либо обратитесь к администратору за дополнительным лимитом."
        ),
        "quota_status_regular": "Осталось на сегодня: {remaining} ({used}/{limit})",
        "quota_status_bonus": "Использован бонусный поиск (Осталось бонусов: {bonus})",
        "scan_started": (
            "🔍 <b>Поиск начался...</b> ({trigger})\n"
            "📊 <b>Статус лимита:</b> {quota_info}\n"
            "🔑 <b>Ключевые слова:</b> <code>{keywords}</code>\n"
            "⏳ Проверяем свежие вакансии на <code>[hh.uz]</code> и <code>[LinkedIn]</code>..."
        ),
        "scan_stopped": "⏹️ <b>Поиск остановлен, авто-расписание поставлено на паузу!</b>",
        "schedule_resumed": "▶️ <b>Автоматический режим (09:00 и 19:00 UZT) снова активирован для вас!</b>",
        "history_cleared": (
            "🗑️ <b>Ваша история поиска очищена!</b> (Удалено записей: {count}).\n"
            "Теперь при нажатии <b>[ 🔍 Искать сейчас ]</b> все свежие вакансии придут заново."
        ),
        "scan_no_new_results": (
            "✅ <b>Поиск завершен.</b>\n\n"
            "📊 <b>Результаты поиска:</b>\n"
            "• <code>[hh.uz]</code>: Новых <b>{hh_new}</b> (Всего: {hh_total} | Просмотрено: {hh_seen})\n"
            "• <code>[LinkedIn]</code>: Новых <b>{li_new}</b> (Всего: {li_total} | Просмотрено: {li_seen}){hint}"
        ),
        "scan_hint_clear": "\n\n💡 <i>Чтобы получить ранее просмотренные вакансии заново, нажмите <b>[ 🗑️ Очистить историю ]</b>.</i>",
        "ai_limit_fallback_notice": "⚠️ <b>Лимит ИИ исчерпан, но вот найденные вакансии:</b>",
        "scan_finished": "✅ <b>Поиск завершен!{stopped_note}</b>\nВсего отправлено новых вакансий: <b>{count}</b>{mode_note}.",
        "card_high_badge": "🔥 <b>Отличное совпадение!</b>",
        "card_new_badge": "📌 <b>Новая вакансия</b>",
        "card_contacts": "Контакты",
        "card_open_link": "Открыть вакансию",
        "card_company": "Компания",
        "card_location": "Локация",
        "card_link": "Ссылка",
        "card_cl_header": "✉️ <b>Готовое сопроводительное письмо ({lang} — нажмите для копирования):</b>",
        "card_cl_template_note": "ℹ️ <i>Лимит ИИ временно исчерпан, использован готовый шаблон письма.</i>",
        "card_dismissed": "❌ <b>Отмечено как неактуальное</b>",
        "cl_generating_toast": "✍️ Пишем сопроводительное письмо...",
        "skip_toast": "Пропущено ❌",
        "admin_direct_msg_header": "📩 <b>Сообщение от администратора:</b>",
        "admin_broadcast_header": "📢 <b>Объявление администратора:</b>",
        "admin_bonus_gift": "🎁 Администратор начислил вам <b>+{count}</b> дополнительных поисков!",
        "admin_unbanned_notice": "✅ Ваш профиль разблокирован администратором!",
    },
    "en": {
        "btn_search_now": "🔍 Search now",
        "btn_upload_resume": "📄 Upload Resume (PDF)",
        "btn_my_keywords": "🔑 My Keywords",
        "btn_clear_seen": "🗑️ Clear History",
        "btn_stop_search": "⏹️ Stop Search",
        "btn_resume_schedule": "▶️ Resume Auto-Search",
        "btn_change_lang": "🌐 Change language",
        "btn_write_cl": "✍️ Write Cover Letter",
        "btn_skip": "❌ Not interested",
        "btn_kw_confirm": "🔍 Search with these keywords",
        "btn_kw_add": "➕ Add more keywords",
        "choose_lang_prompt": (
            "🇺🇿 <b>Iltimos, muloqot tilini tanlang:</b>\n"
            "🇷🇺 <b>Пожалуйста, выберите язык интерфейса:</b>\n"
            "🇬🇧 <b>Please select your preferred language:</b>"
        ),
        "lang_saved": "✅ <b>English language selected!</b>",
        "welcome_no_resume": (
            "👋 Hello, <b>{name}</b>! Welcome to the Personal AI Job Search Bot.\n\n"
            "📄 <b>Please send your resume in PDF format.</b>\n\n"
            "Once you upload your <code>.pdf</code> resume, Gemini AI will read it, "
            "automatically suggest matching job search keywords, and find fresh vacancies on <code>hh.uz</code> and <code>LinkedIn</code>.\n"
            "📊 <b>Daily search limit:</b> {limit_str}"
        ),
        "welcome_with_resume": (
            "👋 Hello, <b>{name}</b>!\n\n"
            "📄 <b>Uploaded resume:</b> <code>{filename}</code>\n"
            "🔑 <b>Active keywords:</b> <code>{keywords}</code>\n"
            "📊 <b>Daily search limit:</b> {limit_str}\n\n"
            "• Tap <b>[ 🔍 Search now ]</b> to find matching vacancies.\n"
            "• To update your resume, simply send a new <b>PDF (.pdf)</b> file to this chat."
        ),
        "limit_unlimited": "Unlimited (Admin 👑)",
        "limit_daily": "{limit} times per day",
        "banned_msg": "🚫 <b>Sorry, your profile has been blocked by the administrator.</b>",
        "no_admin_access": "Kechirasiz, sizda bu bo'limga kirish huquqi yo'q",
        "upload_pdf_only": (
            "⚠️ <b>Please send your resume in PDF (.pdf) format only!</b>\n"
            "This allows the bot to accurately read and analyze your qualifications."
        ),
        "upload_pdf_prompt": (
            "📄 <b>Please send your resume in PDF format.</b>\n\n"
            "Tap the 📎 (attachment) icon below and select your <code>.pdf</code> resume file."
        ),
        "need_resume_first": "📄 <b>Please upload your resume in PDF format first!</b>",
        "pdf_received_wait": "⏳ <b>Resume received!</b>\nExtracting text and predicting optimal job search keywords...",
        "pdf_empty_error": (
            "⚠️ <b>Could not extract text from this PDF file (it may be a scanned image).</b>\n"
            "Please send a text-based PDF resume."
        ),
        "pdf_analyzed_keywords": (
            "✅ <b>Your resume has been analyzed!</b> ({filename})\n"
            "👨‍💼 <b>Field / Role:</b> {profession}\n\n"
            "🔑 <b>Based on your resume, the following keywords were identified:</b>\n"
            "{keywords_list}\n\n"
            "❓ <b>Shall we search using these keywords, or would you like to add more?</b>\n"
            "<i>(Tap a button below or type additional keywords separated by commas)</i>"
        ),
        "my_keywords_msg": (
            "🔑 <b>Your current job search keywords:</b>\n{keywords_list}\n\n"
            "❓ Search with these keywords or add new ones?\n"
            "<i>(To add new keywords, simply type them separated by commas)</i>"
        ),
        "enter_extra_keywords": (
            "✍️ <b>Type the additional keywords you want to add, separated by commas:</b>\n"
            "<i>Example: Electrical Engineer, Project Engineer, AutoCAD, Solar</i>"
        ),
        "keywords_added": (
            "✅ <b>New keywords added!</b>\n"
            "🔑 <b>Updated list:</b> <code>{keywords}</code>\n\n"
            "🚀 Starting vacancy search with your updated keywords..."
        ),
        "scan_already_running": "⏳ <b>A search is already in progress for you...</b> Please wait for it to finish.",
        "quota_exhausted_user": (
            "⚠️ <b>Your daily search limit has been reached ({limit}/{limit})!</b>\n\n"
            "To preserve free Gemini API quota, regular users can run up to <b>{limit} searches per day</b>.\n"
            "🕒 Your limit resets automatically tomorrow, or contact the admin for bonus searches."
        ),
        "quota_status_regular": "Remaining today: {remaining} ({used}/{limit})",
        "quota_status_bonus": "Bonus search used (Remaining bonus: {bonus})",
        "scan_started": (
            "🔍 <b>Search started...</b> ({trigger})\n"
            "📊 <b>Quota status:</b> {quota_info}\n"
            "🔑 <b>Your keywords:</b> <code>{keywords}</code>\n"
            "⏳ Checking recent vacancies on <code>[hh.uz]</code> and <code>[LinkedIn]</code>..."
        ),
        "scan_stopped": "⏹️ <b>Search stopped and auto-schedule paused!</b>",
        "schedule_resumed": "▶️ <b>Automatic schedule (09:00 and 19:00 UZT) resumed for you!</b>",
        "history_cleared": (
            "🗑️ <b>Your search history has been cleared!</b> ({count} entries removed).\n"
            "Tap <b>[ 🔍 Search now ]</b> to receive all recent vacancies again."
        ),
        "scan_no_new_results": (
            "✅ <b>Search completed.</b>\n\n"
            "📊 <b>Search diagnostics:</b>\n"
            "• <code>[hh.uz]</code>: New <b>{hh_new}</b> (Total: {hh_total} | Seen: {hh_seen})\n"
            "• <code>[LinkedIn]</code>: New <b>{li_new}</b> (Total: {li_total} | Seen: {li_seen}){hint}"
        ),
        "scan_hint_clear": "\n\n💡 <i>To re-fetch previously seen vacancies, tap <b>[ 🗑️ Clear History ]</b>.</i>",
        "ai_limit_fallback_notice": "⚠️ <b>AI quota reached, but here are the found vacancies:</b>",
        "scan_finished": "✅ <b>Search completed!{stopped_note}</b>\nTotal new vacancies sent: <b>{count}</b>{mode_note}.",
        "card_high_badge": "🔥 <b>High Match Vacancy!</b>",
        "card_new_badge": "📌 <b>New Vacancy</b>",
        "card_contacts": "Contacts",
        "card_open_link": "Open Vacancy",
        "card_company": "Company",
        "card_location": "Location",
        "card_link": "Link",
        "card_cl_header": "✉️ <b>Ready Cover Letter ({lang} — tap to copy):</b>",
        "card_cl_template_note": "ℹ️ <i>AI quota temporarily reached; a ready template cover letter was provided.</i>",
        "card_dismissed": "❌ <b>Marked as not interested</b>",
        "cl_generating_toast": "✍️ Generating Cover Letter...",
        "skip_toast": "Skipped ❌",
        "admin_direct_msg_header": "📩 <b>Message from Admin:</b>",
        "admin_broadcast_header": "📢 <b>Admin Announcement:</b>",
        "admin_bonus_gift": "🎁 Admin has granted you <b>+{count}</b> bonus searches!",
        "admin_unbanned_notice": "✅ Your profile has been unblocked by the administrator!",
    },
}


def normalize_lang(lang: str | None) -> Literal["uz", "ru", "en"]:
    if lang in ("uz", "ru", "en"):
        return lang  # type: ignore[return-value]
    return "uz"


def tr(lang: str | None, key: str, **kwargs: Any) -> str:
    code = normalize_lang(lang)
    template = TRANSLATIONS.get(code, TRANSLATIONS["uz"]).get(key) or TRANSLATIONS["uz"].get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template


ALL_SEARCH_NOW_BTNS = {TRANSLATIONS[l]["btn_search_now"] for l in TRANSLATIONS} | {"/scan"}
ALL_UPLOAD_RESUME_BTNS = {TRANSLATIONS[l]["btn_upload_resume"] for l in TRANSLATIONS}
ALL_MY_KEYWORDS_BTNS = {TRANSLATIONS[l]["btn_my_keywords"] for l in TRANSLATIONS}
ALL_CLEAR_SEEN_BTNS = {TRANSLATIONS[l]["btn_clear_seen"] for l in TRANSLATIONS} | {"/clear"}
ALL_STOP_SEARCH_BTNS = {TRANSLATIONS[l]["btn_stop_search"] for l in TRANSLATIONS}
ALL_RESUME_SCHED_BTNS = {TRANSLATIONS[l]["btn_resume_schedule"] for l in TRANSLATIONS}
ALL_CHANGE_LANG_BTNS = {TRANSLATIONS[l]["btn_change_lang"] for l in TRANSLATIONS} | {"/language", "/lang"}


# =====================================================================
# 3. TARMOQ VA QIDIRUV KONSTANTALARI
# =====================================================================

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
GEMINI_BATCH_SIZE = 5
MAX_VACANCY_AGE_DAYS = 30

MAX_HH_VACANCIES_PER_SCAN = 30
MAX_LINKEDIN_JOBS_PER_SCAN = 15

HH_API_BASE_URL = "https://api.hh.ru/vacancies"
UZBEKISTAN_AREA_ID = "97"

DEFAULT_FALLBACK_KEYWORDS: list[str] = [
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

# 2-MANBA: LinkedIn Public Guest API (TEGILMADI)
LINKEDIN_GUEST_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
LINKEDIN_GUEST_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_REGEX = re.compile(r"(?:\+?998[\s\-()]*)?(?:\d{2}[\s\-()]*)\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")

USER_LOCKS_GUARD = threading.Lock()
USER_DATA_LOCKS: dict[str, threading.Lock] = {}
USER_SCAN_LOCKS: dict[str, threading.Lock] = {}
USER_STOP_EVENTS: dict[str, threading.Event] = {}

ADMIN_PENDING_ACTION: dict[int, str | None] = {}


def is_admin(user_id: int | str | None) -> bool:
    if user_id is None or ADMIN_ID == 0:
        return False
    try:
        return int(user_id) == ADMIN_ID
    except Exception:
        return False


def get_user_data_lock(user_id: str | int) -> threading.Lock:
    uid = str(user_id)
    with USER_LOCKS_GUARD:
        if uid not in USER_DATA_LOCKS:
            USER_DATA_LOCKS[uid] = threading.Lock()
        return USER_DATA_LOCKS[uid]


def get_user_scan_lock(user_id: str | int) -> threading.Lock:
    uid = str(user_id)
    with USER_LOCKS_GUARD:
        if uid not in USER_SCAN_LOCKS:
            USER_SCAN_LOCKS[uid] = threading.Lock()
        return USER_SCAN_LOCKS[uid]


def get_user_stop_event(user_id: str | int) -> threading.Event:
    uid = str(user_id)
    with USER_LOCKS_GUARD:
        if uid not in USER_STOP_EVENTS:
            USER_STOP_EVENTS[uid] = threading.Event()
        return USER_STOP_EVENTS[uid]


# =====================================================================
# 4. RENDER.COM / KOYEB VA GITHUB UCHUN FAYLLARNI AVTOMATIK YARATISH
# =====================================================================

def auto_setup() -> None:
    print("\n[0/4] Render.com / Koyeb va GitHub uchun loyiha fayllari tekshirilmoqda (auto_setup)...", flush=True)
    USERS_DIR.mkdir(parents=True, exist_ok=True)

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
        "users/\n"
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
# 5. RENDER / KOYEB "HEALTH CHECK" VA KEEP-ALIVE SERVERI
# =====================================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        payload = {
            "status": "ok",
            "service": "MultiUserJobAgentMultilingual",
            "model": ACTIVE_GEMINI_MODEL or GEMINI_MODEL,
            "tashkent_time": get_tashkent_now().strftime("%Y-%m-%d %H:%M:%S UTC+5"),
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
# 6. GEMINI SXEMALARI (KALIT SO'Z ANIQLASH + BATCH TAHLIL)
# =====================================================================

class ResumeKeywordsExtraction(BaseModel):
    detected_language: Literal["uz", "ru", "en"] = Field(
        description="Rezyume yozilgan asosiy til: 'uz', 'ru' yoki 'en'."
    )
    candidate_profession: str = Field(
        description="Nomzodning asosiy kasbi yoki yo'nalishi (qisqa 2-5 so'z)."
    )
    suggested_keywords: list[str] = Field(
        description=(
            "Ushbu nomzodga O'zbekistonda (hh.uz va LinkedIn da) ish topish uchun eng mos "
            "8 tadan 12 tagacha aniq kalit so'zlar va lavozim nomlari (rus, ingliz va o'zbek tillarida)."
        )
    )


class BatchVacancyItemEvaluation(BaseModel):
    vacancy_id: str = Field(description="Vakansiyaning unikal ID raqami.")
    detected_language: Literal["uz", "ru", "en"] = Field(
        description="Vakansiya tili: 'uz', 'ru' yoki 'en'."
    )
    match_score: int = Field(
        ge=0,
        le=100,
        description="Nomzod rezyumesiga moslik foizi (0–100%). Ish staji talabi ballni tushirmasin!",
    )
    contact_details: str = Field(
        default="",
        description="Vakansiyada ko'rsatilgan qisqa aloqa ma'lumoti."
    )
    short_summary: str = Field(
        description="Juda qisqa (maksimal 1-2 gap) asosiy vazifa va nomzodga moslik sababi."
    )


class BatchVacancyEvaluationList(BaseModel):
    items: list[BatchVacancyItemEvaluation] = Field(
        description="Guruhdagi vakansiyalar uchun qisqa baholash natijalari."
    )


# =====================================================================
# 7. FOYDALANUVCHI PROFILI VA ADMIN REZYUMESINI AVTOMATIK ULASH
# =====================================================================

def get_user_dir(user_id: str | int) -> Path:
    d = USERS_DIR / str(user_id).strip()
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_user_profile(user_id: str | int) -> dict[str, Any]:
    uid = str(user_id).strip()
    user_dir = get_user_dir(uid)
    profile_path = user_dir / "profile.json"
    lock = get_user_data_lock(uid)

    default_profile: dict[str, Any] = {
        "user_id": uid,
        "full_name": "",
        "username": "",
        "ui_lang": "",
        "is_banned": False,
        "has_resume": False,
        "resume_filename": "",
        "resume_lang": "uz",
        "resume_text": "",
        "profession": "",
        "keywords": [],
        "awaiting_extra_keywords": False,
        "auto_schedule_enabled": True,
        "daily_search_date": get_today_str(),
        "daily_search_count": 0,
        "bonus_searches": 0,
        "total_searches": 0,
        "created_at": get_tashkent_now().isoformat(timespec="seconds"),
        "updated_at": get_tashkent_now().isoformat(timespec="seconds"),
    }

    with lock:
        if profile_path.exists():
            try:
                loaded = json.loads(profile_path.read_text(encoding="utf-8"))
                for k, v in default_profile.items():
                    loaded.setdefault(k, v)
                return loaded
            except Exception:
                pass
        return default_profile


def save_user_profile(user_id: str | int, profile: dict[str, Any]) -> None:
    uid = str(user_id).strip()
    user_dir = get_user_dir(uid)
    profile_path = user_dir / "profile.json"
    profile["updated_at"] = get_tashkent_now().isoformat(timespec="seconds")
    lock = get_user_data_lock(uid)

    with lock:
        try:
            profile_path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"[OGOHLANTIRISH] User {uid} profilini saqlashda xatolik: {exc}", flush=True)


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages_text: list[str] = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages_text.append(page_text.strip())
        return "\n\n".join(pages_text).strip()
    except Exception as exc:
        print(f"[OGOHLANTIRISH] PDF matnini o'qishda xatolik: {exc}", flush=True)
        return ""


def auto_bind_admin_resume_from_base_dir(client: genai.Client | None = None) -> dict[str, Any] | None:
    """
    Agar ADMIN_ID > 0 bo'lsa va adminning profilida hali rezyume yuklanmagan bo'lsa,
    BASE_DIR (GitHub loyiha papkasi) dagi `rezyume_uz.pdf`, `rezyume_ru.pdf`, `rezyume_en.pdf`
    yoki `rezyume.pdf` fayllarini avtomatik o'qib, ADMIN_ID profiliga biriktirib qo'yadi.
    """
    if ADMIN_ID == 0:
        return None

    uid = str(ADMIN_ID)
    profile = load_user_profile(uid)
    if profile.get("has_resume") and profile.get("resume_text") and profile.get("keywords"):
        return profile

    print(f"\n[ADMIN AUTO-RESUME] Admin (ID: {ADMIN_ID}) uchun {BASE_DIR} papkasidan PDF rezyumelar qidirilmoqda...", flush=True)

    found_files: list[str] = []
    combined_texts: list[str] = []

    for fname in ADMIN_DEFAULT_PDF_FILES:
        fpath = BASE_DIR / fname
        if fpath.exists() and fpath.is_file():
            try:
                txt = extract_text_from_pdf_bytes(fpath.read_bytes())
                found_files.append(fname)
                if txt:
                    combined_texts.append(f"--- {fname} ---\n{txt}")
                    print(f"  [+] Admin PDF o'qildi: {fname} ({len(txt)} belgi)", flush=True)
                else:
                    print(f"  [~] Admin PDF topildi (matni bo'sh/skan): {fname}", flush=True)
            except Exception as exc:
                print(f"  [!] {fname} o'qishda xatolik: {exc}", flush=True)

    if not found_files and BASE_DIR.exists():
        for pdf_p in sorted(BASE_DIR.glob("*.pdf")):
            try:
                txt = extract_text_from_pdf_bytes(pdf_p.read_bytes())
                found_files.append(pdf_p.name)
                if txt:
                    combined_texts.append(f"--- {pdf_p.name} ---\n{txt}")
                    print(f"  [+] Admin PDF o'qildi: {pdf_p.name} ({len(txt)} belgi)", flush=True)
            except Exception:
                pass

    full_resume_text = "\n\n".join(combined_texts).strip()
    if not full_resume_text:
        full_resume_text = (
            "Инженер-энергетик / Инженер-проектировщик / Инженер ПТО / Electrical & Energy Project Engineer. "
            "Олий муҳандислик маълумоти (Энергетика ва электротехника). "
            "AutoCAD (2D/3D лойиҳалаш, бир чизиқли электр схемалар, ЭОМ, Электроснабжение, ПТО ҳужжатлари) "
            "ҳамда MS Office (Excel, Word, PowerPoint) дастурларини мукаммал билади."
        )

    keywords: list[str] = list(DEFAULT_FALLBACK_KEYWORDS)
    profession = "Инженер-энергетик / Electrical & Project Engineer"

    if client is not None and combined_texts:
        try:
            ai_kws, ai_prof, _ = extract_keywords_from_resume_with_gemini(client, full_resume_text)
            if ai_prof:
                profession = ai_prof
            for k in ai_kws:
                if k.lower() not in [ek.lower() for ek in keywords]:
                    keywords.append(k)
        except Exception:
            pass

    profile["has_resume"] = True
    profile["resume_filename"] = ", ".join(found_files) if found_files else "rezyume_uz/ru/en.pdf"
    profile["resume_text"] = full_resume_text
    profile["resume_lang"] = profile.get("ui_lang") or "uz"
    profile["profession"] = profession
    profile["keywords"] = keywords
    if not profile.get("ui_lang"):
        profile["ui_lang"] = "uz"

    save_user_profile(uid, profile)
    print(
        f"  [+] Admin (ID: {ADMIN_ID}) profiliga rezyume ({profile['resume_filename']}) "
        f"va {len(keywords)} ta kalit so'z avtomatik biriktirildi!",
        flush=True,
    )
    return profile


def update_user_identity(message: Message) -> dict[str, Any]:
    uid = str(message.chat.id)
    profile = load_user_profile(uid)
    if message.from_user:
        first = message.from_user.first_name or ""
        last = message.from_user.last_name or ""
        full_name = f"{first} {last}".strip()
        if full_name:
            profile["full_name"] = full_name
        if message.from_user.username:
            profile["username"] = message.from_user.username
    save_user_profile(uid, profile)
    return profile


def check_and_consume_search_quota(user_id: str | int) -> tuple[bool, str]:
    uid = str(user_id).strip()
    today = get_today_str()
    profile = load_user_profile(uid)
    lang = normalize_lang(profile.get("ui_lang"))

    if profile.get("daily_search_date") != today:
        profile["daily_search_date"] = today
        profile["daily_search_count"] = 0

    if is_admin(uid):
        profile["daily_search_count"] = int(profile.get("daily_search_count", 0)) + 1
        profile["total_searches"] = int(profile.get("total_searches", 0)) + 1
        save_user_profile(uid, profile)
        return True, tr(lang, "limit_unlimited")

    used_today = int(profile.get("daily_search_count", 0))
    bonus = int(profile.get("bonus_searches", 0))

    if used_today < DAILY_SEARCH_LIMIT:
        profile["daily_search_count"] = used_today + 1
        profile["total_searches"] = int(profile.get("total_searches", 0)) + 1
        save_user_profile(uid, profile)
        remaining = (DAILY_SEARCH_LIMIT - (used_today + 1)) + bonus
        return True, tr(
            lang,
            "quota_status_regular",
            remaining=remaining,
            used=used_today + 1,
            limit=DAILY_SEARCH_LIMIT,
        )

    if bonus > 0:
        profile["bonus_searches"] = bonus - 1
        profile["daily_search_count"] = used_today + 1
        profile["total_searches"] = int(profile.get("total_searches", 0)) + 1
        save_user_profile(uid, profile)
        return True, tr(lang, "quota_status_bonus", bonus=bonus - 1)

    return False, tr(lang, "quota_exhausted_user", limit=DAILY_SEARCH_LIMIT)


def list_all_user_ids() -> list[str]:
    if not USERS_DIR.exists():
        return []
    uids: list[str] = []
    for sub in USERS_DIR.iterdir():
        if sub.is_dir() and (sub / "profile.json").exists():
            uids.append(sub.name)
    return sorted(uids)


def collect_bot_statistics() -> dict[str, Any]:
    uids = list_all_user_ids()
    today = get_today_str()
    total_users = len(uids)
    with_resume = 0
    banned_users = 0
    today_searches = 0
    active_users_today = 0

    for uid in uids:
        prof = load_user_profile(uid)
        if prof.get("has_resume"):
            with_resume += 1
        if prof.get("is_banned"):
            banned_users += 1
        if prof.get("daily_search_date") == today:
            cnt = int(prof.get("daily_search_count", 0))
            today_searches += cnt
            if cnt > 0:
                active_users_today += 1

    return {
        "total_users": total_users,
        "with_resume": with_resume,
        "banned_users": banned_users,
        "today_searches": today_searches,
        "active_users_today": active_users_today,
    }


def load_user_seen_jobs(user_id: str | int) -> set[str]:
    uid = str(user_id).strip()
    seen_file = get_user_dir(uid) / "seen_jobs.txt"
    lock = get_user_data_lock(uid)
    with lock:
        try:
            if not seen_file.exists():
                seen_file.write_text("", encoding="utf-8")
                return set()
            lines = seen_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            return {line.strip() for line in lines if line.strip()}
        except Exception:
            return set()


def clear_user_seen_jobs(user_id: str | int) -> None:
    uid = str(user_id).strip()
    seen_file = get_user_dir(uid) / "seen_jobs.txt"
    lock = get_user_data_lock(uid)
    with lock:
        try:
            seen_file.write_text("", encoding="utf-8")
        except Exception:
            pass


def mark_user_job_as_seen(user_id: str | int, vac_id: str) -> None:
    uid = str(user_id).strip()
    vac_id = str(vac_id).strip()
    if not vac_id:
        return
    seen_file = get_user_dir(uid) / "seen_jobs.txt"
    lock = get_user_data_lock(uid)
    with lock:
        try:
            with seen_file.open("a", encoding="utf-8") as f:
                f.write(f"{vac_id}\n")
        except Exception:
            pass


def load_user_state(user_id: str | int) -> dict[str, dict[str, Any]]:
    uid = str(user_id).strip()
    state_file = get_user_dir(uid) / "vacancies_state.json"
    lock = get_user_data_lock(uid)
    with lock:
        if not state_file.exists():
            return {}
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}


def save_user_state(user_id: str | int, state: dict[str, dict[str, Any]]) -> None:
    uid = str(user_id).strip()
    state_file = get_user_dir(uid) / "vacancies_state.json"
    lock = get_user_data_lock(uid)
    with lock:
        try:
            state_file.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass


# =====================================================================
# 8. KONFIGURATSIYA VA YORDAMCHI FUNKSIYALAR
# =====================================================================

def get_config() -> dict[str, str]:
    env_path = BASE_DIR / ".env"
    load_dotenv(dotenv_path=env_path)

    gemini_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    tg_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()

    missing: list[str] = []
    if not gemini_key:
        missing.append("GEMINI_API_KEY")
    if not tg_token:
        missing.append("TELEGRAM_BOT_TOKEN")

    if missing:
        print("\n[XATOLIK] Quyidagi muhim kalitlar topilmadi:", ", ".join(missing), flush=True)
        sys.exit(1)

    return {
        "GEMINI_API_KEY": gemini_key,
        "TELEGRAM_BOT_TOKEN": tg_token,
    }


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


def find_matched_keywords(title: str, description: str, user_keywords: list[str], source_kw: str = "") -> list[str]:
    haystack = f"{title} {description}".lower()
    matched: list[str] = []
    if source_kw:
        matched.append(source_kw)
    for kw in user_keywords:
        if kw.lower() in haystack and kw.lower() not in [m.lower() for m in matched]:
            matched.append(kw)
    return matched[:5]


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
# 9. GEMINI YORDAMIDA REZYUMEDAN AVTOMATIK KALIT SO'Z TAXMIN QILISH
# =====================================================================

def _call_gemini_with_fallback(
    client: genai.Client,
    prompt: str,
    response_schema: Any = None,
) -> Any:
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
            if any(code in err_str for code in ("429", "RESOURCE_EXHAUSTED", "Quota", "404", "NOT_FOUND", "503")):
                ACTIVE_GEMINI_MODEL = None
                continue
            break

    raise RuntimeError(f"{last_error}")


def _extract_keywords_fallback_from_text(resume_text: str) -> tuple[list[str], str, Literal["uz", "ru", "en"]]:
    lang = detect_language_fast(resume_text)
    sample = resume_text.lower()

    common_roles = [
        ("энергетик", "Энергетик"),
        ("инженер-проектировщик", "Инженер-проектировщик"),
        ("проектировщик", "Проектировщик"),
        ("инженер пто", "Инженер ПТО"),
        ("пто", "Инженер ПТО"),
        ("электрик", "Электрик"),
        ("электромонтажник", "Электромонтажник"),
        ("главный инженер", "Главный инженер"),
        ("главный энергетик", "Главный энергетик"),
        ("electrical engineer", "Electrical Engineer"),
        ("energy", "Energy Engineer"),
        ("project manager", "Project Manager"),
        ("project engineer", "Project Engineer"),
        ("autocad", "Инженер AutoCAD"),
        ("python", "Python Developer"),
        ("backend", "Backend Developer"),
        ("frontend", "Frontend Developer"),
        ("бухгалтер", "Бухгалтер"),
        ("маркетолог", "Маркетолог"),
        ("менеджер", "Менеджер проектов"),
        ("инженер", "Инженер"),
        ("muhandis", "Muhandis"),
        ("energetik", "Energetik"),
    ]
    found: list[str] = []
    for needle, label in common_roles:
        if needle in sample and label not in found:
            found.append(label)

    if not found:
        found = list(DEFAULT_FALLBACK_KEYWORDS[:10])

    profession = found[0] if found else "Mutaxassis"
    return found[:12], profession, lang


def extract_keywords_from_resume_with_gemini(
    client: genai.Client,
    resume_text: str,
) -> tuple[list[str], str, Literal["uz", "ru", "en"]]:
    prompt = f"""
Siz professional HR va karyera maslahatchisisiz.
Quyidagi NOMZOD REZYUMESI matnini diqqat bilan o'qib chiqing va ushbu inson O'zbekistonda (hh.uz va LinkedIn orqali) ish topishi uchun ENG MOS keladigan 8 tadan 12 tagacha qidiruv kalit so'zlarini (`suggested_keywords`) aniqlab bering.

QOIDALAR:
1. Kalit so'zlar aniq lavozim nomlari va asosiy ixtisoslik so'zlari bo'lsin (rus, ingliz va o'zbek tillarida).
2. Har bir kalit so'z qisqa (1–3 so'zdan iborat), qidiruv tizimi (`hh.uz` va `LinkedIn`) oson topadigan formatda bo'lsin.

--- NOMZOD REZYUMESI MATNI ---
{resume_text[:8000]}
""".strip()

    try:
        resp = _call_gemini_with_fallback(
            client=client,
            prompt=prompt,
            response_schema=ResumeKeywordsExtraction,
        )
        if resp.parsed and isinstance(resp.parsed, ResumeKeywordsExtraction):
            parsed: ResumeKeywordsExtraction = resp.parsed
        else:
            raw_data = json.loads(resp.text or "{}")
            parsed = ResumeKeywordsExtraction(**raw_data)

        cleaned_kws = [kw.strip() for kw in parsed.suggested_keywords if kw.strip()]
        if cleaned_kws:
            return cleaned_kws[:12], parsed.candidate_profession, parsed.detected_language
    except Exception as exc:
        print(f"  [!] Rezyumedan kalit so'z ajratishda Gemini xatoligi ({str(exc)[:100]}) — lokal tahlil ishlatildi.", flush=True)

    return _extract_keywords_fallback_from_text(resume_text)


# =====================================================================
# 10. QIDIRUV MANBALARI: [hh.uz] (403 FIX) VA [LinkedIn] (TEGILMADI)
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
    stop_event: threading.Event,
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
        if stop_event.is_set():
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

            new_short_vacancies.append((item, kw))

        time.sleep(0.15)

    detailed_vacancies: list[dict[str, Any]] = []
    for item, source_kw in new_short_vacancies[:max_items]:
        if stop_event.is_set():
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

        matched_kws = find_matched_keywords(title, short_desc, keywords, source_kw=source_kw)
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
    keywords: list[str],
    seen_jobs: set[str],
    stop_event: threading.Event,
    max_results: int = MAX_LINKEDIN_JOBS_PER_SCAN,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if stop_event.is_set():
        return [], {"unique_found": 0, "new_count": 0, "skipped_seen": 0, "errors": []}

    now_str = get_tashkent_now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now_str} UZT] [LinkedIn Guest API] To'g'ridan-to'g'ri LinkedIn serveridan qidirilmoqda...", flush=True)

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    batch_ids: set[str] = set()
    raw_new_cards: list[dict[str, Any]] = []
    skipped_seen = 0
    li_errors: list[str] = []

    for kw in keywords[:6]:
        if stop_event.is_set():
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
            if len(li_errors) < 2:
                li_errors.append(str(exc)[:60])
        time.sleep(0.4)

    linkedin_vacancies: list[dict[str, Any]] = []
    for card in raw_new_cards[:max_results]:
        if stop_event.is_set():
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
        matched_kws = find_matched_keywords(card["title"], description_text, keywords, source_kw=card["source_kw"])

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

    diag = {
        "unique_found": len(batch_ids),
        "new_count": len(linkedin_vacancies),
        "skipped_seen": skipped_seen,
        "errors": li_errors,
    }
    return linkedin_vacancies, diag


# =====================================================================
# 11. GEMINI BATCH TAHLILI VA FOYDALANUVCHI TILIDA KOVER-XAT
# =====================================================================

def build_no_ai_simple_item(vac: dict[str, Any], default_lang: str = "uz") -> dict[str, Any]:
    return {
        "detected_language": default_lang,
        "match_score": 0,
        "contact_details": str(vac.get("contacts") or ""),
        "short_summary": "",
        "ai_failed": True,
    }


def analyze_vacancies_batch_with_gemini(
    client: genai.Client,
    batch: list[dict[str, Any]],
    resume_text: str,
    user_lang: str = "uz",
) -> tuple[dict[str, dict[str, Any]], bool]:
    if not batch:
        return {}, False

    lang_name = {"uz": "O'zbek", "ru": "Русский", "en": "English"}.get(user_lang, "O'zbek")
    vacancies_block: list[str] = []
    for idx, vac in enumerate(batch, start=1):
        vacancies_block.append(
            f"[{idx}] ID: {vac['id']}\n"
            f"Lavozim: {vac.get('title')}\n"
            f"Kompaniya: {vac.get('company')} ({vac.get('location')})\n"
            f"Kalit so'zlar: {', '.join(vac.get('matched_keywords') or [])}\n"
            f"Qisqa matn: {str(vac.get('description') or '')[:500]}\n"
        )

    prompt = f"""
Siz professional HR va karyera maslahatchisisiz. Quyidagi {len(batch)} ta vakansiyani foydalanuvchi rezyumesi bilan solishtirib, har biri uchun QISQA baho bering.

QAT'IY QOIDALAR:
1. Vakansiyadagi ish staji ("1-3 yil", "3-6 yil") talabi ballni (`match_score`) tushirmasin.
2. `short_summary` JUDA QISQA bo'lsin (maksimal 1-2 gap) va AYNAN {lang_name} ({user_lang.upper()}) tilida yozilsin!

FOYDALANUVCHI REZYUMESI:
{resume_text[:2500]}

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
            results_map[item_ev.vacancy_id] = {
                "detected_language": user_lang,
                "match_score": item_ev.match_score,
                "contact_details": item_ev.contact_details,
                "short_summary": item_ev.short_summary,
                "ai_failed": False,
            }
    except Exception as exc:
        ai_failed = True
        print(
            f"  [!] Gemini API limiti tugadi yoki xatolik ({str(exc)[:100]}) -> "
            "Vakansiyalar AI tahlilisiz oddiy matn ko'rinishida yuboriladi!",
            flush=True,
        )

    for vac in batch:
        vid = vac["id"]
        if vid not in results_map:
            results_map[vid] = build_no_ai_simple_item(vac, default_lang=user_lang)
        else:
            api_contacts = str(vac.get("contacts") or "").strip()
            if api_contacts and api_contacts not in results_map[vid]["contact_details"]:
                results_map[vid]["contact_details"] = api_contacts

    return results_map, ai_failed


def generate_cover_letter_on_demand(
    client: genai.Client,
    record: dict[str, Any],
    user_profile: dict[str, Any],
) -> tuple[str, bool]:
    vac = record["vacancy"]
    preferred_lang = normalize_lang(user_profile.get("ui_lang") or record.get("resume_lang") or "uz")
    lang_label = {"uz": "O'zbek (lotin)", "ru": "Русский", "en": "English"}[preferred_lang]
    resume_text = user_profile.get("resume_text") or ""

    prompt = f"""
Quyidagi vakansiya uchun nomzod nomidan AYNAN '{lang_label}' ({preferred_lang.upper()}) tilida qisqa, professional va yuborishga tayyor Cover Letter (ilova xati) yozib bering.
Faqat tayyor xat matnini qaytaring (ortiqcha izohsiz).

Vakansiya: {vac.get('title')}
Kompaniya: {vac.get('company')}
Tavsif: {vac.get('description', '')[:2000]}

Nomzod rezyumesi:
{resume_text[:4000]}
""".strip()

    try:
        resp = _call_gemini_with_fallback(client=client, prompt=prompt, response_schema=None)
        text = (resp.text or "").strip()
        if text:
            return text, True
    except Exception as exc:
        print(f"[!] Kover-xatda AI limiti tugagan ({str(exc)[:100]}) — tayyor shablon ishlatildi.", flush=True)

    title = vac.get("title", "Mutaxassis")
    company = vac.get("company", "Kompaniya")
    if preferred_lang == "uz":
        return (
            f"Assalomu alaykum, {company} jamoasi!\n\n"
            f"Sizning «{title}» vakansiyangiz bo‘yicha murojaat qilmoqdaman. "
            "Mening bilim va amaliy ko‘nikmalarim ushbu lavozim talablariga to‘liq mos keladi.\n\n"
            "Rezyumeimni ilova qilmoqdaman. Suhbatga tayyorman!",
            False,
        )
    if preferred_lang == "en":
        return (
            f"Dear Hiring Team at {company},\n\n"
            f"I am writing to apply for the {title} role. My educational background and technical skills "
            "align well with the requirements of this position.\n\n"
            "Please find my resume attached. Thank you for your time and consideration.",
            False,
        )
    return (
        f"Здравствуйте, уважаемая команда {company}!\n\n"
        f"Прошу рассмотреть мою кандидатуру на вакансию «{title}». Мое профильное образование "
        "и профессиональные навыки соответствуют требованиям данной позиции.\n\n"
        "Буду рад обсудить сотрудничество на собеседовании!",
        False,
    )


# =====================================================================
# 12. 3-TILLI YIG'ILADIGAN (is_persistent=False) KLAVIATURALAR
# =====================================================================

def build_language_selection_inline_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🇺🇿 O‘zbekcha", callback_data="set_lang|uz"),
        InlineKeyboardButton("🇷🇺 Русский", callback_data="set_lang|ru"),
        InlineKeyboardButton("🇬🇧 English", callback_data="set_lang|en"),
    )
    return kb


def build_main_reply_keyboard(lang: str | None = "uz", user_id: str | int | None = None) -> ReplyKeyboardMarkup:
    """
    Yig'iladigan (is_persistent=False) asosiy pastki klaviatura:
    - Foydalanuvchi Telegram kiritish maydonidagi klaviatura belgisi orqali menyuni istalgan payt yashira oladi.
    - Admin uchun pastda faqat bitta ixcham `👑 Admin Panel` tugmasi qo'shiladi (bosilganda barcha admin
      imkoniyatlari chiroyli inline tugmalar ko'rinishida ochiladi).
    """
    code = normalize_lang(lang)
    markup = ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=False, row_width=2)
    markup.add(
        KeyboardButton(tr(code, "btn_search_now")),
        KeyboardButton(tr(code, "btn_upload_resume")),
    )
    markup.add(
        KeyboardButton(tr(code, "btn_my_keywords")),
        KeyboardButton(tr(code, "btn_clear_seen")),
    )
    markup.add(
        KeyboardButton(tr(code, "btn_stop_search")),
        KeyboardButton(tr(code, "btn_resume_schedule")),
    )

    if is_admin(user_id):
        markup.add(
            KeyboardButton(tr(code, "btn_change_lang")),
            KeyboardButton(BTN_ADMIN_PANEL),
        )
    else:
        markup.add(
            KeyboardButton(tr(code, "btn_change_lang")),
        )

    return markup


def build_admin_inline_keyboard() -> InlineKeyboardMarkup:
    """👑 Admin Panel bosilganda ochiladigan to'liq inline admin boshqaruv menyusi."""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📊 Statistika", callback_data="admin|stats"),
        InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin|users"),
    )
    kb.add(
        InlineKeyboardButton("✉️ Foydalanuvchiga xabar", callback_data="admin|sendmsg"),
        InlineKeyboardButton("📢 Umumiy e'lon (Broadcast)", callback_data="admin|broadcast"),
    )
    kb.add(
        InlineKeyboardButton("➕ Limit berish (+3)", callback_data="admin|addlimit"),
        InlineKeyboardButton("🚫 Bloklash / Ochish (Ban)", callback_data="admin|ban"),
    )
    return kb


def build_keyword_confirm_inline_keyboard(lang: str | None = "uz") -> InlineKeyboardMarkup:
    code = normalize_lang(lang)
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(
            text=tr(code, "btn_kw_confirm"),
            callback_data="kw_action|confirm",
        ),
        InlineKeyboardButton(
            text=tr(code, "btn_kw_add"),
            callback_data="kw_action|add",
        ),
    )
    return kb


def build_vacancy_inline_keyboard(vac_id: str, lang: str | None = "uz") -> InlineKeyboardMarkup:
    code = normalize_lang(lang)
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(
            text=tr(code, "btn_write_cl"),
            callback_data=f"write_cl|{vac_id}",
        ),
        InlineKeyboardButton(
            text=tr(code, "btn_skip"),
            callback_data=f"skip|{vac_id}",
        ),
    )
    return keyboard


def format_plain_no_ai_vacancy_message(
    record: dict[str, Any],
    lang: str | None = "uz",
    cover_letter_text: str | None = None,
    status_note: str | None = None,
) -> str:
    code = normalize_lang(lang)
    vac = record["vacancy"]
    source_tag = vac.get("source", "[hh.uz]")

    msg = (
        f"📌 <b>{html.escape(str(vac.get('title', '')))}</b>  <code>{html.escape(source_tag)}</code>\n"
        f"🏢 <b>{tr(code, 'card_company')}:</b> {html.escape(str(vac.get('company', '')))}\n"
        f"📍 <b>{tr(code, 'card_location')}:</b> {html.escape(str(vac.get('location', '')))} | 💰 {html.escape(str(vac.get('salary', '')))}\n"
        f"🔗 <b>{tr(code, 'card_link')}:</b> {html.escape(str(vac.get('url', '')))}"
    )

    if cover_letter_text:
        msg += (
            f"\n\n{tr(code, 'card_cl_header', lang=code.upper())}\n"
            f"<pre>{html.escape(cover_letter_text.strip()[:2500])}</pre>"
        )

    if status_note:
        msg += f"\n\n{status_note}"

    return msg[:4050]


def format_short_vacancy_message(
    record: dict[str, Any],
    lang: str | None = "uz",
    cover_letter_text: str | None = None,
    status_note: str | None = None,
) -> str:
    code = normalize_lang(lang)
    ev = record.get("evaluation") or {}
    if ev.get("ai_failed"):
        return format_plain_no_ai_vacancy_message(
            record,
            lang=code,
            cover_letter_text=cover_letter_text,
            status_note=status_note,
        )

    vac = record["vacancy"]
    score = int(ev.get("match_score", 75))
    source_tag = vac.get("source", "[hh.uz]")
    contacts = (ev.get("contact_details") or vac.get("contacts") or "").strip() or "—"
    short_summary = (ev.get("short_summary") or "").strip()
    pub_date = vac.get("published_at") or "New"

    badge = tr(code, "card_high_badge") if score >= HIGH_MATCH_THRESHOLD else tr(code, "card_new_badge")

    msg = (
        f"{badge} (<b>{score}%</b>)  <code>{html.escape(source_tag)}</code>\n\n"
        f"🏢 <b>{html.escape(str(vac.get('company', '')))}</b>\n"
        f"💼 <b>{html.escape(str(vac.get('title', '')))}</b>\n"
        f"📍 {html.escape(str(vac.get('location', '')))} | 💰 {html.escape(str(vac.get('salary', '')))} | 📅 {html.escape(str(pub_date))}\n"
        f"☎️ <b>{tr(code, 'card_contacts')}:</b> {html.escape(contacts[:120])}\n"
        f"🔗 <a href=\"{html.escape(str(vac.get('url', '')))}\">{tr(code, 'card_open_link')}</a>\n\n"
        f"💡 {html.escape(short_summary[:240])}"
    )

    if cover_letter_text:
        cl_trimmed = cover_letter_text.strip()
        if len(cl_trimmed) > 2500:
            cl_trimmed = cl_trimmed[:2500] + "..."
        msg += (
            f"\n\n{tr(code, 'card_cl_header', lang=code.upper())}\n"
            f"<pre>{html.escape(cl_trimmed)}</pre>"
        )

    if status_note:
        msg += f"\n\n{status_note}"

    return msg[:4050]


# =====================================================================
# 13. HAR BIR FOYDALANUVCHI UCHUN PARALLEL QIDIRUV FUNKSIYASI
# =====================================================================

def run_user_scan(
    bot: telebot.TeleBot,
    client: genai.Client,
    user_id: str | int,
    notify_start_and_finish: bool = True,
    trigger_label: str = "Manual",
) -> None:
    uid = str(user_id).strip()
    profile = load_user_profile(uid)
    lang = normalize_lang(profile.get("ui_lang") or "uz")

    if profile.get("is_banned") and not is_admin(uid):
        try:
            bot.send_message(chat_id=uid, text=tr(lang, "banned_msg"), parse_mode="HTML")
        except Exception:
            pass
        return

    if is_admin(uid) and (not profile.get("has_resume") or not profile.get("resume_text")):
        auto_bind_admin_resume_from_base_dir(client)
        profile = load_user_profile(uid)

    if not profile.get("has_resume") or not profile.get("resume_text"):
        try:
            bot.send_message(
                chat_id=uid,
                text=tr(lang, "need_resume_first"),
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(lang, user_id=uid),
            )
        except Exception:
            pass
        return

    scan_lock = get_user_scan_lock(uid)
    stop_event = get_user_stop_event(uid)

    if not scan_lock.acquire(blocking=False):
        try:
            bot.send_message(
                chat_id=uid,
                text=tr(lang, "scan_already_running"),
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(lang, user_id=uid),
            )
        except Exception:
            pass
        return

    stop_event.clear()

    try:
        allowed, quota_info = check_and_consume_search_quota(uid)
        if not allowed:
            try:
                bot.send_message(
                    chat_id=uid,
                    text=quota_info,
                    parse_mode="HTML",
                    reply_markup=build_main_reply_keyboard(lang, user_id=uid),
                )
            except Exception:
                pass
            return

        profile = load_user_profile(uid)
        user_keywords: list[str] = profile.get("keywords") or list(DEFAULT_FALLBACK_KEYWORDS)
        resume_text: str = profile.get("resume_text") or ""
        seen_jobs = load_user_seen_jobs(uid)
        state = load_user_state(uid)

        if notify_start_and_finish:
            kws_preview = ", ".join(user_keywords[:8])
            try:
                bot.send_message(
                    chat_id=uid,
                    text=tr(
                        lang,
                        "scan_started",
                        trigger=html.escape(trigger_label),
                        quota_info=html.escape(quota_info),
                        keywords=html.escape(kws_preview),
                    ),
                    parse_mode="HTML",
                    reply_markup=build_main_reply_keyboard(lang, user_id=uid),
                )
            except Exception:
                pass

        try:
            hh_vacancies, hh_diag = fetch_new_hh_uz_vacancies(
                user_keywords,
                seen_jobs=seen_jobs,
                stop_event=stop_event,
                max_items=MAX_HH_VACANCIES_PER_SCAN,
            )
        except Exception as hh_exc:
            hh_vacancies, hh_diag = [], {"unique_found": 0, "new_count": 0, "skipped_seen": 0, "errors": [str(hh_exc)]}

        try:
            linkedin_vacancies, li_diag = search_linkedin_jobs(
                user_keywords,
                seen_jobs=seen_jobs,
                stop_event=stop_event,
                max_results=MAX_LINKEDIN_JOBS_PER_SCAN,
            )
        except Exception as li_exc:
            linkedin_vacancies, li_diag = [], {"unique_found": 0, "new_count": 0, "skipped_seen": 0, "errors": [str(li_exc)]}

        all_new_vacancies = hh_vacancies + linkedin_vacancies

        if stop_event.is_set():
            try:
                bot.send_message(
                    chat_id=uid,
                    text=tr(lang, "scan_stopped"),
                    parse_mode="HTML",
                    reply_markup=build_main_reply_keyboard(lang, user_id=uid),
                )
            except Exception:
                pass
            return

        if not all_new_vacancies:
            if notify_start_and_finish:
                hint = tr(lang, "scan_hint_clear") if (hh_diag["skipped_seen"] > 0 or li_diag["skipped_seen"] > 0) else ""
                try:
                    bot.send_message(
                        chat_id=uid,
                        text=tr(
                            lang,
                            "scan_no_new_results",
                            hh_new=hh_diag["new_count"],
                            hh_total=hh_diag["unique_found"],
                            hh_seen=hh_diag["skipped_seen"],
                            li_new=li_diag["new_count"],
                            li_total=li_diag["unique_found"],
                            li_seen=li_diag["skipped_seen"],
                            hint=hint,
                        ),
                        parse_mode="HTML",
                        reply_markup=build_main_reply_keyboard(lang, user_id=uid),
                    )
                except Exception:
                    pass
            return

        sent_count = 0
        ai_quota_exhausted = False
        quota_notice_sent = False

        for start_idx in range(0, len(all_new_vacancies), GEMINI_BATCH_SIZE):
            if stop_event.is_set():
                break

            batch = all_new_vacancies[start_idx : start_idx + GEMINI_BATCH_SIZE]

            if not ai_quota_exhausted:
                batch_evals, batch_failed = analyze_vacancies_batch_with_gemini(client, batch, resume_text, user_lang=lang)
                if batch_failed:
                    ai_quota_exhausted = True
            else:
                batch_evals = {vac["id"]: build_no_ai_simple_item(vac, default_lang=lang) for vac in batch}

            if ai_quota_exhausted and not quota_notice_sent:
                quota_notice_sent = True
                try:
                    bot.send_message(
                        chat_id=uid,
                        text=tr(lang, "ai_limit_fallback_notice"),
                        parse_mode="HTML",
                        reply_markup=build_main_reply_keyboard(lang, user_id=uid),
                    )
                except Exception:
                    pass

            for vac in batch:
                if stop_event.is_set():
                    break

                vac_id = str(vac["id"]).strip()
                if vac_id in seen_jobs:
                    continue

                ev_data = batch_evals.get(vac_id) or build_no_ai_simple_item(vac, default_lang=lang)

                record: dict[str, Any] = {
                    "vacancy": vac,
                    "resume_lang": lang,
                    "evaluation": ev_data,
                    "status": "notified",
                    "updated_at": get_tashkent_now().isoformat(timespec="seconds"),
                }

                tg_text = format_short_vacancy_message(record, lang=lang)
                keyboard = build_vacancy_inline_keyboard(vac_id, lang=lang)

                try:
                    sent_msg = bot.send_message(
                        chat_id=uid,
                        text=tg_text,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                        disable_web_page_preview=True,
                    )
                    record["telegram_message_id"] = sent_msg.message_id
                    state[vac_id] = record
                    save_user_state(uid, state)
                    mark_user_job_as_seen(uid, vac_id)
                    seen_jobs.add(vac_id)
                    sent_count += 1
                except Exception as tg_exc:
                    print(f"     [!] User {uid} ga yuborishda xatolik ({vac_id}): {tg_exc}", flush=True)

                time.sleep(0.3)

        if notify_start_and_finish:
            stopped_note = " (⏹️)" if stop_event.is_set() else ""
            mode_note = " (No-AI)" if ai_quota_exhausted else ""
            try:
                bot.send_message(
                    chat_id=uid,
                    text=tr(
                        lang,
                        "scan_finished",
                        stopped_note=stopped_note,
                        count=sent_count,
                        mode_note=mode_note,
                    ),
                    parse_mode="HTML",
                    reply_markup=build_main_reply_keyboard(lang, user_id=uid),
                )
            except Exception:
                pass

    except Exception as unexpected_exc:
        print(f"\n[!] User {uid} qidiruvida xatolik: {unexpected_exc}", flush=True)
    finally:
        scan_lock.release()


# =====================================================================
# 14. O'ZBEKISTON VAQTI BILAN (UTC+5: 09:00 va 19:00) BARCHA USERLAR UCHUN JADVAL
# =====================================================================

def setup_daily_schedule(
    bot: telebot.TeleBot,
    client: genai.Client,
) -> None:
    def scheduled_job_all_users(slot_time: str) -> None:
        user_ids = list_all_user_ids()
        for uid in user_ids:
            prof = load_user_profile(uid)
            if prof.get("is_banned") or not prof.get("has_resume") or not prof.get("auto_schedule_enabled", True):
                continue
            threading.Thread(
                target=run_user_scan,
                args=(bot, client, uid, True, f"Auto ({slot_time} UZT)"),
                daemon=True,
            ).start()

    schedule.clear()
    for t_str in SCHEDULE_TIMES_UZT:
        try:
            schedule.every().day.at(t_str, "Asia/Tashkent").do(scheduled_job_all_users, slot_time=t_str)
        except Exception:
            uz_h, uz_m = map(int, t_str.split(":"))
            now_uz = get_tashkent_now()
            target_uz = now_uz.replace(hour=uz_h, minute=uz_m, second=0, microsecond=0)
            target_local = target_uz.astimezone()
            local_time_str = target_local.strftime("%H:%M")
            schedule.every().day.at(local_time_str).do(scheduled_job_all_users, slot_time=t_str)


def schedule_runner_loop() -> None:
    while True:
        try:
            schedule.run_pending()
        except Exception as exc:
            print(f"[!] Jadval tekshiruvchisida xatolik: {exc}", flush=True)
        time.sleep(1)


# =====================================================================
# 15. ADMIN FUNKSIYALARI: PANEL, STATISTIKA, USERS, DIRECT MSG, BROADCAST
# =====================================================================

def send_admin_panel_overview(bot: telebot.TeleBot, chat_id: int | str) -> None:
    ADMIN_PENDING_ACTION.pop(ADMIN_ID, None)
    stats = collect_bot_statistics()
    admin_text = (
        "👑 <b>Admin Boshqaruv Menyusi</b>\n\n"
        f"• <b>Admin ID:</b> <code>{ADMIN_ID}</code> (Cheksiz qidiruv)\n"
        f"• <b>Jami ro‘yxatdan o‘tganlar:</b> <b>{stats['total_users']}</b> ta\n"
        f"• <b>PDF rezyume yuklaganlar:</b> <b>{stats['with_resume']}</b> ta\n"
        f"• <b>Bugungi faol qidiruvlar:</b> <b>{stats['today_searches']}</b> ta ({stats['active_users_today']} ta faol user)\n"
        f"• <b>Bloklanganlar (Ban):</b> <b>{stats['banned_users']}</b> ta\n\n"
        "💡 <b>Tezkor buyruqlar:</b>\n"
        "• <code>/send USER_ID Matn</code> — Foydalanuvchiga shaxsiy xabar yuborish\n"
        "• <code>/broadcast Matn</code> — Barchaga umumiy e'lon yuborish\n"
        "• <code>/ban USER_ID</code> — Foydalanuvchini bloklash\n"
        "• <code>/unban USER_ID</code> — Blokdan chiqarish\n"
        "• <code>/addlimit USER_ID 5</code> — Qo‘shimcha qidiruv limiti berish"
    )
    bot.send_message(
        chat_id=chat_id,
        text=admin_text,
        parse_mode="HTML",
        reply_markup=build_admin_inline_keyboard(),
    )


def send_admin_stats_message(bot: telebot.TeleBot, chat_id: int | str) -> None:
    stats = collect_bot_statistics()
    stats_text = (
        "📊 <b>Bot Statistikasi (Real-vaqt):</b>\n\n"
        f"• 👥 <b>Jami ro‘yxatdan o‘tganlar:</b> {stats['total_users']} ta\n"
        f"• 📄 <b>PDF rezyume yuklaganlar:</b> {stats['with_resume']} ta\n"
        f"• 🔍 <b>Bugungi faol qidiruvlar soni:</b> {stats['today_searches']} ta ({stats['active_users_today']} ta faol user)\n"
        f"• 🚫 <b>Bloklangan foydalanuvchilar:</b> {stats['banned_users']} ta\n"
        f"• 🕒 <b>Vaqt:</b> <code>{get_tashkent_now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
    )
    bot.send_message(
        chat_id=chat_id,
        text=stats_text,
        parse_mode="HTML",
        reply_markup=build_admin_inline_keyboard(),
    )


def send_admin_users_list_message(bot: telebot.TeleBot, chat_id: int | str) -> None:
    uids = list_all_user_ids()
    lines: list[str] = [f"👥 <b>Ro‘yxatdan o‘tgan foydalanuvchilar ({len(uids)} ta):</b>\n"]
    today = get_today_str()
    for idx, u in enumerate(uids[:30], start=1):
        p = load_user_profile(u)
        name = html.escape(p.get("full_name") or ("Admin" if is_admin(u) else "User"))
        uname = f" (@{html.escape(p['username'])})" if p.get("username") else ""
        u_lang = (p.get("ui_lang") or "uz").upper()
        used = p.get("daily_search_count", 0) if p.get("daily_search_date") == today else 0
        bonus = p.get("bonus_searches", 0)
        ban_tag = " 🚫[BAN]" if p.get("is_banned") else ""
        res_tag = "✅PDF" if p.get("has_resume") else "➖"
        lim_disp = "∞" if is_admin(u) else f"{used}/{DAILY_SEARCH_LIMIT} (+{bonus})"
        lines.append(
            f"{idx}. <b>{name}</b>{uname} — ID: <code>{u}</code> [{u_lang}] | {res_tag} | Bugun: {lim_disp}{ban_tag}"
        )
    bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines)[:4000],
        parse_mode="HTML",
        reply_markup=build_admin_inline_keyboard(),
    )


def send_direct_message_from_admin(
    bot: telebot.TeleBot,
    target_uid: str,
    msg_text: str,
) -> tuple[bool, str]:
    target_uid = str(target_uid).strip()
    prof = load_user_profile(target_uid)
    target_lang = normalize_lang(prof.get("ui_lang") or "uz")
    header = tr(target_lang, "admin_direct_msg_header")

    try:
        bot.send_message(
            chat_id=target_uid,
            text=f"{header}\n\n{html.escape(msg_text.strip())}",
            parse_mode="HTML",
        )
        return True, f"✅ Xabar foydalanuvchi <code>{html.escape(target_uid)}</code> ga ({target_lang.upper()}) tilida muvaffaqiyatli yetkazildi!"
    except Exception as exc:
        return False, f"⚠️ Foydalanuvchi <code>{html.escape(target_uid)}</code> ga xabar yuborishda xatolik: {html.escape(str(exc)[:150])}"


def notify_admin_new_user_pdf(
    bot: telebot.TeleBot,
    user_id: str,
    full_name: str,
    username: str,
    profession: str,
    file_name: str,
) -> None:
    if ADMIN_ID == 0:
        return
    user_label = html.escape(full_name or "Noma'lum")
    uname_part = f" (@{html.escape(username)})" if username else ""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ +3 Limit berish", callback_data=f"adm_quick_lim|{user_id}"),
        InlineKeyboardButton("🚫 Bloklash (Ban)", callback_data=f"adm_quick_ban|{user_id}"),
    )
    msg_text = (
        f"🆕 <b>Yangi foydalanuvchi qo‘shildi:</b> {user_label}{uname_part} [ID: <code>{user_id}</code>]\n"
        f"📄 <b>Rezyume:</b> <code>{html.escape(file_name)}</code>\n"
        f"👨‍💼 <b>Yo‘nalish:</b> {html.escape(profession)}"
    )
    try:
        bot.send_message(
            chat_id=ADMIN_ID,
            text=msg_text,
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception as exc:
        print(f"[!] Adminga yangi foydalanuvchi bildirishnomasini yuborishda xatolik: {exc}", flush=True)


def broadcast_message_to_all_users(bot: telebot.TeleBot, broadcast_text: str) -> None:
    uids = list_all_user_ids()
    sent_ok = 0
    failed = 0
    for uid in uids:
        prof = load_user_profile(uid)
        u_lang = normalize_lang(prof.get("ui_lang") or "uz")
        b_header = tr(u_lang, "admin_broadcast_header")
        try:
            bot.send_message(
                chat_id=uid,
                text=f"{b_header}\n\n{html.escape(broadcast_text)}",
                parse_mode="HTML",
            )
            sent_ok += 1
        except Exception:
            failed += 1
        time.sleep(0.1)

    if ADMIN_ID != 0:
        try:
            bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"✅ <b>Broadcast yakunlandi!</b>\n"
                    f"• Yetkazildi: <b>{sent_ok}</b> ta foydalanuvchiga\n"
                    f"• Yetib bormadi: <b>{failed}</b> ta"
                ),
                parse_mode="HTML",
                reply_markup=build_admin_inline_keyboard(),
            )
        except Exception:
            pass


# =====================================================================
# 16. TELEGRAM BOT HANDLERLARI (IXCHAM ADMIN TUGMASI BILAN)
# =====================================================================

def _send_welcome_for_user(
    bot: telebot.TeleBot,
    client: genai.Client,
    uid: str,
    first_name: str,
    profile: dict[str, Any],
) -> None:
    if is_admin(uid) and (not profile.get("has_resume") or not profile.get("resume_text")):
        auto_bind_admin_resume_from_base_dir(client)
        profile = load_user_profile(uid)

    lang = normalize_lang(profile.get("ui_lang") or "uz")
    limit_str = tr(lang, "limit_unlimited") if is_admin(uid) else tr(lang, "limit_daily", limit=DAILY_SEARCH_LIMIT)

    if profile.get("has_resume") and profile.get("keywords"):
        kws_str = ", ".join(profile["keywords"][:8])
        text = tr(
            lang,
            "welcome_with_resume",
            name=html.escape(first_name),
            filename=html.escape(profile.get("resume_filename", "resume.pdf")),
            keywords=html.escape(kws_str),
            limit_str=limit_str,
        )
    else:
        text = tr(
            lang,
            "welcome_no_resume",
            name=html.escape(first_name),
            limit_str=limit_str,
        )

    bot.send_message(
        chat_id=uid,
        text=text,
        parse_mode="HTML",
        reply_markup=build_main_reply_keyboard(lang, user_id=uid),
    )


def _process_uploaded_pdf_in_thread(
    bot: telebot.TeleBot,
    client: genai.Client,
    message: Message,
) -> None:
    uid = str(message.chat.id)
    profile = update_user_identity(message)
    lang = normalize_lang(profile.get("ui_lang") or "uz")

    if profile.get("is_banned") and not is_admin(uid):
        bot.reply_to(message, tr(lang, "banned_msg"), parse_mode="HTML")
        return

    doc = message.document
    if not doc:
        return

    file_name = (doc.file_name or "rezyume.pdf").strip()
    is_pdf = file_name.lower().endswith(".pdf") or (doc.mime_type or "").lower() == "application/pdf"

    if not is_pdf:
        bot.reply_to(
            message,
            tr(lang, "upload_pdf_only"),
            parse_mode="HTML",
            reply_markup=build_main_reply_keyboard(lang, user_id=uid),
        )
        return

    wait_msg = bot.reply_to(
        message,
        tr(lang, "pdf_received_wait"),
        parse_mode="HTML",
    )

    try:
        file_info = bot.get_file(doc.file_id)
        pdf_bytes = bot.download_file(file_info.file_path)

        user_pdf_path = get_user_dir(uid) / "resume.pdf"
        user_pdf_path.write_bytes(pdf_bytes)

        resume_text = extract_text_from_pdf_bytes(pdf_bytes)
        if not resume_text or len(resume_text.strip()) < 20:
            bot.edit_message_text(
                chat_id=uid,
                message_id=wait_msg.message_id,
                text=tr(lang, "pdf_empty_error"),
                parse_mode="HTML",
            )
            return

        keywords, profession, detected_lang = extract_keywords_from_resume_with_gemini(client, resume_text)

        profile = load_user_profile(uid)
        profile["has_resume"] = True
        profile["resume_filename"] = file_name
        profile["resume_text"] = resume_text
        profile["resume_lang"] = detected_lang
        profile["profession"] = profession
        profile["keywords"] = keywords
        profile["awaiting_extra_keywords"] = True
        save_user_profile(uid, profile)

        if not is_admin(uid):
            notify_admin_new_user_pdf(
                bot=bot,
                user_id=uid,
                full_name=profile.get("full_name", ""),
                username=profile.get("username", ""),
                profession=profession,
                file_name=file_name,
            )

        kws_formatted = "\n".join(f"• <code>{html.escape(k)}</code>" for k in keywords)
        reply_text = tr(
            lang,
            "pdf_analyzed_keywords",
            filename=html.escape(file_name),
            profession=html.escape(profession),
            keywords_list=kws_formatted,
        )

        bot.edit_message_text(
            chat_id=uid,
            message_id=wait_msg.message_id,
            text=reply_text,
            parse_mode="HTML",
            reply_markup=build_keyword_confirm_inline_keyboard(lang),
        )
        print(f"[USER {uid}] PDF rezyume yuklandi ({file_name}) -> {len(keywords)} ta kalit so'z aniqlandi.", flush=True)

    except Exception as exc:
        print(f"[!] User {uid} PDF qayta ishlashda xatolik: {exc}", flush=True)
        try:
            bot.edit_message_text(
                chat_id=uid,
                message_id=wait_msg.message_id,
                text=f"⚠️ Error: {html.escape(str(exc)[:150])}",
                parse_mode="HTML",
            )
        except Exception:
            pass


def register_bot_handlers(
    bot: telebot.TeleBot,
    client: genai.Client,
) -> None:

    # 1. /admin BUYRUG'I YOKI PASTKI [ 👑 Admin Panel ] TUGMASI
    @bot.message_handler(commands=["admin"])
    def handle_admin_command(message: Message) -> None:
        try:
            sender_id = message.from_user.id if message.from_user else message.chat.id
            if not is_admin(sender_id):
                bot.reply_to(message, "Kechirasiz, sizda bu bo'limga kirish huquqi yo'q")
                return
            send_admin_panel_overview(bot, message.chat.id)
        except Exception as exc:
            print(f"[!] /admin xatoligi: {exc}", flush=True)

    @bot.message_handler(func=lambda m: (m.text or "").strip() == BTN_ADMIN_PANEL or "Admin Panel" in (m.text or ""))
    def handle_admin_panel_reply_btn(message: Message) -> None:
        try:
            sender_id = message.from_user.id if message.from_user else message.chat.id
            if not is_admin(sender_id):
                bot.reply_to(message, "Kechirasiz, sizda bu bo'limga kirish huquqi yo'q")
                return
            send_admin_panel_overview(bot, message.chat.id)
        except Exception as exc:
            print(f"[!] Admin Panel tugmasida xatolik: {exc}", flush=True)

    # 2. ADMIN BUYRUQLARI: /send, /broadcast, /ban, /unban, /addlimit
    @bot.message_handler(commands=["send", "ban", "unban", "addlimit", "broadcast"])
    def handle_admin_quick_commands(message: Message) -> None:
        try:
            sender_id = message.from_user.id if message.from_user else message.chat.id
            if not is_admin(sender_id):
                bot.reply_to(message, "Kechirasiz, sizda bu bo'limga kirish huquqi yo'q")
                return

            raw = (message.text or "").strip()
            parts = raw.split(maxsplit=2)
            cmd = parts[0].lower().lstrip("/")

            if cmd == "send":
                if len(parts) < 3:
                    bot.reply_to(
                        message,
                        "⚠️ <b>Format:</b> <code>/send USER_ID Xabar matni</code>\n"
                        "<i>Masalan: /send 123456789 Assalomu alaykum, profilingiz tasdiqlandi</i>",
                        parse_mode="HTML",
                    )
                    return
                target_uid = parts[1].strip()
                msg_body = parts[2].strip()
                _, result_note = send_direct_message_from_admin(bot, target_uid, msg_body)
                bot.reply_to(message, result_note, parse_mode="HTML", reply_markup=build_admin_inline_keyboard())
                return

            if cmd == "broadcast":
                if len(parts) < 2:
                    bot.reply_to(message, "⚠️ Format: <code>/broadcast E'lon matni</code>", parse_mode="HTML")
                    return
                b_text = raw.split(maxsplit=1)[1]
                threading.Thread(
                    target=broadcast_message_to_all_users,
                    args=(bot, b_text),
                    daemon=True,
                ).start()
                return

            if len(parts) < 2:
                bot.reply_to(message, f"⚠️ Format: <code>/{cmd} USER_ID</code>", parse_mode="HTML")
                return

            target_uid = parts[1].strip()
            prof = load_user_profile(target_uid)
            u_lang = normalize_lang(prof.get("ui_lang") or "uz")

            if cmd == "ban":
                prof["is_banned"] = True
                save_user_profile(target_uid, prof)
                bot.reply_to(message, f"🚫 Foydalanuvchi <code>{target_uid}</code> bloklandi (Ban).", parse_mode="HTML")
                try:
                    bot.send_message(target_uid, tr(u_lang, "banned_msg"), parse_mode="HTML")
                except Exception:
                    pass
            elif cmd == "unban":
                prof["is_banned"] = False
                save_user_profile(target_uid, prof)
                bot.reply_to(message, f"✅ Foydalanuvchi <code>{target_uid}</code> blokdan chiqarildi.", parse_mode="HTML")
                try:
                    bot.send_message(target_uid, tr(u_lang, "admin_unbanned_notice"), parse_mode="HTML")
                except Exception:
                    pass
            elif cmd == "addlimit":
                add_cnt = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 3
                prof["bonus_searches"] = int(prof.get("bonus_searches", 0)) + add_cnt
                save_user_profile(target_uid, prof)
                bot.reply_to(
                    message,
                    f"➕ Foydalanuvchi <code>{target_uid}</code> ga <b>+{add_cnt}</b> ta qo‘shimcha qidiruv limiti berildi "
                    f"(Jami bonus: {prof['bonus_searches']} ta).",
                    parse_mode="HTML",
                )
                try:
                    bot.send_message(
                        target_uid,
                        tr(u_lang, "admin_bonus_gift", count=add_cnt),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        except Exception as exc:
            print(f"[!] Admin buyruq xatoligi: {exc}", flush=True)

    # 3. /start VA BIRINCHI KIRISHDA TIL TANLASH (ADMIN UCHUN AVTO-REZYUME BILAN)
    @bot.message_handler(commands=["start", "help"])
    def handle_start(message: Message) -> None:
        try:
            uid = str(message.chat.id)
            profile = update_user_identity(message)

            if is_admin(uid) and (not profile.get("has_resume") or not profile.get("resume_text")):
                auto_bind_admin_resume_from_base_dir(client)
                profile = load_user_profile(uid)

            if profile.get("is_banned") and not is_admin(uid):
                lang = normalize_lang(profile.get("ui_lang") or "uz")
                bot.send_message(uid, tr(lang, "banned_msg"), parse_mode="HTML")
                return

            if not profile.get("ui_lang"):
                bot.send_message(
                    chat_id=uid,
                    text=TRANSLATIONS["uz"]["choose_lang_prompt"],
                    parse_mode="HTML",
                    reply_markup=build_language_selection_inline_keyboard(),
                )
                return

            first_name = message.from_user.first_name if message.from_user else "Foydalanuvchi"
            _send_welcome_for_user(bot, client, uid, first_name, profile)
        except Exception as exc:
            print(f"[!] /start xatoligi: {exc}", flush=True)

    # 4. TILNI O'ZGARTIRISH TUGMASI YOKI /language BUYRUG'I
    @bot.message_handler(
        func=lambda m: (m.text or "").strip() in ALL_CHANGE_LANG_BTNS
        or any(x in (m.text or "") for x in ("Tilni o‘zgartirish", "Tilni o'zgartirish", "Сменить язык", "Change language"))
    )
    def handle_change_language(message: Message) -> None:
        try:
            bot.send_message(
                chat_id=message.chat.id,
                text=TRANSLATIONS["uz"]["choose_lang_prompt"],
                parse_mode="HTML",
                reply_markup=build_language_selection_inline_keyboard(),
            )
        except Exception as exc:
            print(f"[!] Tilni o'zgartirishda xatolik: {exc}", flush=True)

    @bot.message_handler(content_types=["document"])
    def handle_document(message: Message) -> None:
        threading.Thread(
            target=_process_uploaded_pdf_in_thread,
            args=(bot, client, message),
            daemon=True,
        ).start()

    @bot.message_handler(content_types=["photo", "audio", "video", "voice"])
    def handle_non_pdf_media(message: Message) -> None:
        try:
            uid = str(message.chat.id)
            prof = load_user_profile(uid)
            lang = normalize_lang(prof.get("ui_lang") or "uz")
            bot.reply_to(
                message,
                tr(lang, "upload_pdf_only"),
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(lang, user_id=uid),
            )
        except Exception:
            pass

    @bot.message_handler(
        func=lambda m: (m.text or "").strip() in ALL_UPLOAD_RESUME_BTNS
        or any(x in (m.text or "") for x in ("Rezyume yuklash", "Загрузить резюме", "Upload Resume"))
    )
    def handle_upload_resume_btn(message: Message) -> None:
        try:
            uid = str(message.chat.id)
            prof = load_user_profile(uid)
            lang = normalize_lang(prof.get("ui_lang") or "uz")
            bot.send_message(
                chat_id=uid,
                text=tr(lang, "upload_pdf_prompt"),
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(lang, user_id=uid),
            )
        except Exception:
            pass

    @bot.message_handler(
        func=lambda m: (m.text or "").strip() in ALL_MY_KEYWORDS_BTNS
        or any(x in (m.text or "") for x in ("Kalit so‘zlarim", "Kalit so'zlarim", "Мои ключевые слова", "My Keywords"))
    )
    def handle_my_keywords_btn(message: Message) -> None:
        try:
            uid = str(message.chat.id)
            profile = load_user_profile(uid)
            if is_admin(uid) and not profile.get("has_resume"):
                auto_bind_admin_resume_from_base_dir(client)
                profile = load_user_profile(uid)

            lang = normalize_lang(profile.get("ui_lang") or "uz")

            if profile.get("is_banned") and not is_admin(uid):
                bot.send_message(uid, tr(lang, "banned_msg"), parse_mode="HTML")
                return
            if not profile.get("has_resume"):
                bot.send_message(
                    chat_id=uid,
                    text=tr(lang, "need_resume_first"),
                    parse_mode="HTML",
                    reply_markup=build_main_reply_keyboard(lang, user_id=uid),
                )
                return

            kws = profile.get("keywords") or []
            kws_formatted = "\n".join(f"• <code>{html.escape(k)}</code>" for k in kws) or "—"
            profile["awaiting_extra_keywords"] = True
            save_user_profile(uid, profile)

            bot.send_message(
                chat_id=uid,
                text=tr(lang, "my_keywords_msg", keywords_list=kws_formatted),
                parse_mode="HTML",
                reply_markup=build_keyword_confirm_inline_keyboard(lang),
            )
        except Exception as exc:
            print(f"[!] Kalit so'zlarim tugmasida xatolik: {exc}", flush=True)

    @bot.message_handler(
        func=lambda m: (m.text or "").strip() in ALL_SEARCH_NOW_BTNS
        or any(x in (m.text or "") for x in ("Hozir qidirish", "Искать сейчас", "Search now"))
    )
    def handle_search_now(message: Message) -> None:
        try:
            uid = str(message.chat.id)
            prof = load_user_profile(uid)
            lang = normalize_lang(prof.get("ui_lang") or "uz")
            threading.Thread(
                target=run_user_scan,
                args=(bot, client, uid, True, tr(lang, "btn_search_now")),
                daemon=True,
            ).start()
        except Exception as exc:
            print(f"[!] Hozir qidirish tugmasida xatolik: {exc}", flush=True)

    @bot.message_handler(
        func=lambda m: (m.text or "").strip() in ALL_CLEAR_SEEN_BTNS
        or any(x in (m.text or "") for x in ("Tarixni tozalash", "Очистить историю", "Clear History"))
    )
    def handle_clear_seen(message: Message) -> None:
        try:
            uid = str(message.chat.id)
            prof = load_user_profile(uid)
            lang = normalize_lang(prof.get("ui_lang") or "uz")
            old_count = len(load_user_seen_jobs(uid))
            clear_user_seen_jobs(uid)
            bot.send_message(
                chat_id=uid,
                text=tr(lang, "history_cleared", count=old_count),
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(lang, user_id=uid),
            )
        except Exception as exc:
            print(f"[!] Tarixni tozalashda xatolik: {exc}", flush=True)

    @bot.message_handler(
        func=lambda m: (m.text or "").strip() in ALL_STOP_SEARCH_BTNS
        or any(x in (m.text or "") for x in ("Qidiruvni to", "Остановить поиск", "Stop Search"))
    )
    def handle_stop_search(message: Message) -> None:
        try:
            uid = str(message.chat.id)
            get_user_stop_event(uid).set()
            profile = load_user_profile(uid)
            lang = normalize_lang(profile.get("ui_lang") or "uz")
            profile["auto_schedule_enabled"] = False
            save_user_profile(uid, profile)
            bot.send_message(
                chat_id=uid,
                text=tr(lang, "scan_stopped"),
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(lang, user_id=uid),
            )
        except Exception as exc:
            print(f"[!] Qidiruvni to'xtatish tugmasida xatolik: {exc}", flush=True)

    @bot.message_handler(
        func=lambda m: (m.text or "").strip() in ALL_RESUME_SCHED_BTNS
        or any(x in (m.text or "") for x in ("Qayta faollashtirish", "Возобновить авто-поиск", "Resume Auto-Search"))
    )
    def handle_resume_schedule(message: Message) -> None:
        try:
            uid = str(message.chat.id)
            get_user_stop_event(uid).clear()
            profile = load_user_profile(uid)
            lang = normalize_lang(profile.get("ui_lang") or "uz")
            profile["auto_schedule_enabled"] = True
            save_user_profile(uid, profile)
            bot.send_message(
                chat_id=uid,
                text=tr(lang, "schedule_resumed"),
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(lang, user_id=uid),
            )
        except Exception as exc:
            print(f"[!] Qayta faollashtirish tugmasida xatolik: {exc}", flush=True)

    @bot.message_handler(content_types=["text"])
    def handle_text_input(message: Message) -> None:
        try:
            uid = str(message.chat.id)
            sender_id = message.from_user.id if message.from_user else message.chat.id
            raw_text = (message.text or "").strip()
            if not raw_text or raw_text.startswith("/"):
                return

            # A) Admin interaktiv amali ("broadcast", "sendmsg", "ban", "addlimit")
            if is_admin(sender_id) and ADMIN_PENDING_ACTION.get(ADMIN_ID):
                pending_mode = ADMIN_PENDING_ACTION.pop(ADMIN_ID, None)

                if pending_mode == "broadcast":
                    bot.reply_to(message, "📢 Xabar barcha foydalanuvchilarga yuborilmoqda...")
                    threading.Thread(
                        target=broadcast_message_to_all_users,
                        args=(bot, raw_text),
                        daemon=True,
                    ).start()
                    return

                if pending_mode == "sendmsg":
                    parts = raw_text.split(maxsplit=1)
                    if len(parts) < 2:
                        bot.reply_to(
                            message,
                            "⚠️ <b>ID raqam va xabar matnini bo‘sh joy bilan ajratib yozing:</b>\n"
                            "<i>Masalan:</i> <code>123456789 Assalomu alaykum, profilingiz tasdiqlandi</code>",
                            parse_mode="HTML",
                            reply_markup=build_admin_inline_keyboard(),
                        )
                        return
                    target_uid, msg_body = parts[0].strip(), parts[1].strip()
                    _, res_note = send_direct_message_from_admin(bot, target_uid, msg_body)
                    bot.reply_to(message, res_note, parse_mode="HTML", reply_markup=build_admin_inline_keyboard())
                    return

                if pending_mode == "ban":
                    target_uid = raw_text.split()[0].strip()
                    prof = load_user_profile(target_uid)
                    prof["is_banned"] = not bool(prof.get("is_banned"))
                    save_user_profile(target_uid, prof)
                    state_label = "🚫 Bloklandi (Ban)" if prof["is_banned"] else "✅ Blokdan chiqarildi (Unban)"
                    bot.reply_to(
                        message,
                        f"Foydalanuvchi <code>{html.escape(target_uid)}</code> holati: <b>{state_label}</b>",
                        parse_mode="HTML",
                        reply_markup=build_admin_inline_keyboard(),
                    )
                    return

                if pending_mode == "addlimit":
                    parts = raw_text.split()
                    target_uid = parts[0].strip()
                    add_cnt = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 3
                    prof = load_user_profile(target_uid)
                    u_lang = normalize_lang(prof.get("ui_lang") or "uz")
                    prof["bonus_searches"] = int(prof.get("bonus_searches", 0)) + add_cnt
                    save_user_profile(target_uid, prof)
                    bot.reply_to(
                        message,
                        f"➕ Foydalanuvchi <code>{html.escape(target_uid)}</code> ga <b>+{add_cnt}</b> ta qo‘shimcha limit berildi "
                        f"(Jami bonus: {prof['bonus_searches']} ta).",
                        parse_mode="HTML",
                        reply_markup=build_admin_inline_keyboard(),
                    )
                    try:
                        bot.send_message(
                            target_uid,
                            tr(u_lang, "admin_bonus_gift", count=add_cnt),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    return

            # B) Foydalanuvchi kalit so'z qo'shishi
            profile = load_user_profile(uid)
            if is_admin(uid) and not profile.get("has_resume"):
                auto_bind_admin_resume_from_base_dir(client)
                profile = load_user_profile(uid)

            lang = normalize_lang(profile.get("ui_lang") or "uz")

            if profile.get("is_banned") and not is_admin(uid):
                bot.send_message(uid, tr(lang, "banned_msg"), parse_mode="HTML")
                return

            if not profile.get("has_resume"):
                bot.send_message(
                    chat_id=uid,
                    text=tr(lang, "need_resume_first"),
                    parse_mode="HTML",
                    reply_markup=build_main_reply_keyboard(lang, user_id=uid),
                )
                return

            new_kws = [
                k.strip()
                for k in re.split(r"[,;\n]+", raw_text)
                if k.strip() and len(k.strip()) >= 2
            ]
            if not new_kws:
                return

            existing_kws: list[str] = profile.get("keywords") or []
            for nk in new_kws:
                if nk.lower() not in [ek.lower() for ek in existing_kws]:
                    existing_kws.append(nk)

            profile["keywords"] = existing_kws
            profile["awaiting_extra_keywords"] = False
            save_user_profile(uid, profile)

            kws_str = ", ".join(existing_kws)
            bot.send_message(
                chat_id=uid,
                text=tr(lang, "keywords_added", keywords=html.escape(kws_str)),
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(lang, user_id=uid),
            )

            threading.Thread(
                target=run_user_scan,
                args=(bot, client, uid, True, tr(lang, "btn_search_now")),
                daemon=True,
            ).start()
        except Exception as exc:
            print(f"[!] Matnni qayta ishlashda xatolik: {exc}", flush=True)

    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback(call: CallbackQuery) -> None:
        try:
            uid = str(call.message.chat.id)
            sender_id = call.from_user.id if call.from_user else call.message.chat.id
            data = call.data or ""
            if "|" not in data:
                bot.answer_callback_query(call.id)
                return

            action, param = data.split("|", 1)

            # 0. TIL TANLASH TUGMALARI (set_lang|uz, set_lang|ru, set_lang|en)
            if action == "set_lang":
                chosen_lang = normalize_lang(param)
                profile = load_user_profile(uid)
                profile["ui_lang"] = chosen_lang
                save_user_profile(uid, profile)

                bot.answer_callback_query(call.id, "✅")
                try:
                    bot.edit_message_text(
                        chat_id=uid,
                        message_id=call.message.message_id,
                        text=tr(chosen_lang, "lang_saved"),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

                first_name = call.from_user.first_name if call.from_user else (profile.get("full_name") or "User")
                _send_welcome_for_user(bot, client, uid, first_name, profile)
                return

            # 1. ADMIN PANEL INLINE TUGMALARI
            if action in ("admin", "adm_quick_lim", "adm_quick_ban"):
                if not is_admin(sender_id):
                    bot.answer_callback_query(call.id, "Kechirasiz, sizda bu bo'limga kirish huquqi yo'q", show_alert=True)
                    return

                if action == "adm_quick_lim":
                    prof = load_user_profile(param)
                    u_lang = normalize_lang(prof.get("ui_lang") or "uz")
                    prof["bonus_searches"] = int(prof.get("bonus_searches", 0)) + 3
                    save_user_profile(param, prof)
                    bot.answer_callback_query(call.id, f"+3 limit berildi (User {param}) ✅", show_alert=True)
                    try:
                        bot.send_message(param, tr(u_lang, "admin_bonus_gift", count=3), parse_mode="HTML")
                    except Exception:
                        pass
                    return

                if action == "adm_quick_ban":
                    prof = load_user_profile(param)
                    prof["is_banned"] = not bool(prof.get("is_banned"))
                    save_user_profile(param, prof)
                    st_txt = "Bloklandi 🚫" if prof["is_banned"] else "Blokdan ochildi ✅"
                    bot.answer_callback_query(call.id, f"User {param}: {st_txt}", show_alert=True)
                    return

                if param == "stats":
                    bot.answer_callback_query(call.id, "Statistika yangilandi 📊")
                    send_admin_stats_message(bot, ADMIN_ID)
                    return

                if param == "users":
                    bot.answer_callback_query(call.id)
                    send_admin_users_list_message(bot, ADMIN_ID)
                    return

                if param == "sendmsg":
                    ADMIN_PENDING_ACTION[ADMIN_ID] = "sendmsg"
                    bot.answer_callback_query(call.id)
                    bot.send_message(
                        chat_id=ADMIN_ID,
                        text=(
                            "✉️ <b>Xabar yubormoqchi bo‘lgan foydalanuvchi ID raqami va xabar matnini yozib yuboring:</b>\n"
                            "<i>Masalan:</i> <code>123456789 Assalomu alaykum, profilingiz tasdiqlandi</code>\n"
                            "<i>(Yoki to‘g‘ridan-to‘g‘ri</i> <code>/send USER_ID Matn</code> <i>buyrug‘ini ishlating)</i>"
                        ),
                        parse_mode="HTML",
                    )
                    return

                if param == "broadcast":
                    ADMIN_PENDING_ACTION[ADMIN_ID] = "broadcast"
                    bot.answer_callback_query(call.id)
                    bot.send_message(
                        chat_id=ADMIN_ID,
                        text="📢 <b>Barcha foydalanuvchilarga yubormoqchi bo‘lgan xabar matnini yozib yuboring:</b>",
                        parse_mode="HTML",
                    )
                    return

                if param == "addlimit":
                    ADMIN_PENDING_ACTION[ADMIN_ID] = "addlimit"
                    bot.answer_callback_query(call.id)
                    bot.send_message(
                        chat_id=ADMIN_ID,
                        text=(
                            "➕ <b>Qo‘shimcha limit bermoqchi bo‘lgan foydalanuvchi ID raqamini (va limit sonini) yozing:</b>\n"
                            "<i>Masalan:</i> <code>123456789 5</code>"
                        ),
                        parse_mode="HTML",
                    )
                    return

                if param == "ban":
                    ADMIN_PENDING_ACTION[ADMIN_ID] = "ban"
                    bot.answer_callback_query(call.id)
                    bot.send_message(
                        chat_id=ADMIN_ID,
                        text=(
                            "🚫 <b>Bloklamoqchi yoki blokdan chiqarmoqchi bo‘lgan foydalanuvchi ID raqamini yozing:</b>\n"
                            "<i>Masalan:</i> <code>123456789</code>"
                        ),
                        parse_mode="HTML",
                    )
                    return

            # 2. FOYDALANUVCHI KALIT SO'Z TASDIQLASH TUGMALARI
            if action == "kw_action":
                profile = load_user_profile(uid)
                lang = normalize_lang(profile.get("ui_lang") or "uz")
                if profile.get("is_banned") and not is_admin(uid):
                    bot.answer_callback_query(call.id, "🚫", show_alert=True)
                    return

                if param == "confirm":
                    profile["awaiting_extra_keywords"] = False
                    save_user_profile(uid, profile)
                    bot.answer_callback_query(call.id, "🔍")
                    threading.Thread(
                        target=run_user_scan,
                        args=(bot, client, uid, True, tr(lang, "btn_kw_confirm")),
                        daemon=True,
                    ).start()
                    return

                if param == "add":
                    profile["awaiting_extra_keywords"] = True
                    save_user_profile(uid, profile)
                    bot.answer_callback_query(call.id, "✍️")
                    bot.send_message(
                        chat_id=uid,
                        text=tr(lang, "enter_extra_keywords"),
                        parse_mode="HTML",
                        reply_markup=build_main_reply_keyboard(lang, user_id=uid),
                    )
                    return

            # 3. VAKANSIYA UCHUN KOVER-XAT YOZISH YOKI O'TKAZIB YUBORISH
            vac_id = param

            def _handle_vacancy_action() -> None:
                user_profile = load_user_profile(uid)
                lang = normalize_lang(user_profile.get("ui_lang") or "uz")
                state = load_user_state(uid)
                record = state.get(vac_id)
                if not record:
                    bot.answer_callback_query(call.id, "Not found", show_alert=True)
                    return

                if action in ("write_cl", "view"):
                    bot.answer_callback_query(call.id, tr(lang, "cl_generating_toast"))

                    cover_letter = record.get("generated_cover_letter")
                    used_ai = True
                    if not cover_letter or record.get("cl_lang") != lang:
                        cover_letter, used_ai = generate_cover_letter_on_demand(client, record, user_profile)
                        record["generated_cover_letter"] = cover_letter
                        record["cl_lang"] = lang
                        record["status"] = "cover_letter_generated"
                        record["updated_at"] = get_tashkent_now().isoformat(timespec="seconds")
                        state[vac_id] = record
                        save_user_state(uid, state)

                    note = None if used_ai else tr(lang, "card_cl_template_note")
                    updated_text = format_short_vacancy_message(
                        record,
                        lang=lang,
                        cover_letter_text=cover_letter,
                        status_note=note,
                    )
                    skip_only_kb = InlineKeyboardMarkup(row_width=1)
                    skip_only_kb.add(
                        InlineKeyboardButton(
                            text=tr(lang, "btn_skip"),
                            callback_data=f"skip|{vac_id}",
                        )
                    )
                    bot.edit_message_text(
                        chat_id=uid,
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
                    save_user_state(uid, state)

                    dismissed_text = format_short_vacancy_message(
                        record,
                        lang=lang,
                        status_note=tr(lang, "card_dismissed"),
                    )
                    bot.edit_message_text(
                        chat_id=uid,
                        message_id=call.message.message_id,
                        text=dismissed_text,
                        parse_mode="HTML",
                        reply_markup=None,
                        disable_web_page_preview=True,
                    )
                    bot.answer_callback_query(call.id, tr(lang, "skip_toast"))

            threading.Thread(target=_handle_vacancy_action, daemon=True).start()

        except Exception as exc:
            print(f"[!] Callback xatoligi: {exc}", flush=True)


# =====================================================================
# 17. ASOSIY ISHGA TUSHIRISH FUNKSIYASI (MAIN)
# =====================================================================

def main() -> None:
    print("=" * 88, flush=True)
    print(" 3-TILLI (UZ/RU/EN) MULTI-USER AI ISH QIDIRISH BOTI (IXCHAM YIG'ILADIGAN MENYU)", flush=True)
    print(f" Ishchi papka   : {BASE_DIR}", flush=True)
    print(f" Userlar bazasi : {USERS_DIR}", flush=True)
    print(f" Admin ID       : {'Sozlangan (ENV)' if ADMIN_ID != 0 else 'Kiritilmagan (0)'}", flush=True)
    print(f" Toshkent vaqti : {get_tashkent_now().strftime('%Y-%m-%d %H:%M:%S (UTC+5)')}", flush=True)
    print("=" * 88, flush=True)

    auto_setup()
    start_health_check_server()

    config = get_config()
    client = genai.Client(api_key=config["GEMINI_API_KEY"])

    if ADMIN_ID != 0:
        auto_bind_admin_resume_from_base_dir(client)

    bot = telebot.TeleBot(config["TELEGRAM_BOT_TOKEN"], threaded=True, num_threads=16)

    register_bot_handlers(bot, client)
    setup_daily_schedule(bot, client)

    sched_thread = threading.Thread(target=schedule_runner_loop, daemon=True)
    sched_thread.start()

    if ADMIN_ID != 0:
        try:
            admin_prof = load_user_profile(ADMIN_ID)
            admin_lang = normalize_lang(admin_prof.get("ui_lang") or "uz")
            bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "✅ <b>Ixcham va yig‘iladigan menyu faollashtirildi!</b>\n\n"
                    "• Endi pastki klaviaturani kiritish maydonidagi klaviatura belgisi orqali bemalol yashirish va ochish mumkin (<code>is_persistent=False</code>).\n"
                    "• Pastki menyuda faqat bitta <b>[ 👑 Admin Panel ]</b> tugmasi qoldirildi — uni bosganingizda barcha admin funksiyalari inline tugmalar ko‘rinishida ochiladi."
                ),
                parse_mode="HTML",
                reply_markup=build_main_reply_keyboard(admin_lang, user_id=ADMIN_ID),
            )
        except Exception as exc:
            print(f"  [!] Admin xabarini yuborishda xatolik: {exc}", flush=True)

    print("\n[4/4] 3-Tilli Multi-User Telegram Bot (is_persistent=False) faol!", flush=True)

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
