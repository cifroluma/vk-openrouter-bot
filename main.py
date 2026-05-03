import os
import json
import base64
import random
import tempfile
import wave
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.upload import VkUpload
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from dotenv import load_dotenv

load_dotenv()

VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID", "0"))
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
APP_NAME = os.getenv("APP_NAME", "VK AI Bot")
APP_URL = os.getenv("APP_URL", "http://localhost")

STATE_PATH = Path("state.json")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"


# =========================
# МОДЕЛИ
# =========================

LLM_PRESETS = {
    "default": {
        "title": "Default",
        "model": "x-ai/grok-4.1-fast",
        "desc": "быстрая свободная болталка на разные темы",
    },
    "smart": {
        "title": "Smart",
        "model": "openai/gpt-5.4-nano",
        "desc": "умная модель для сложных вопросов",
    },
    "fallback": {
        "title": "Fallback",
        "model": "openai/gpt-oss-120b",
        "desc": "запасной вариант, если основные тупят/падают",
    },
    "exp1": {
        "title": "Experimental 1",
        "model": "openai/gpt-5-nano",
        "desc": "экспериментальная OpenAI nano",
    },
    "exp2": {
        "title": "Experimental 2",
        "model": "deepseek/deepseek-v3.2",
        "desc": "экспериментальная DeepSeek",
    },
}

STT_MODEL = "openai/whisper-large-v3-turbo"
TTS_MODEL = "google/gemini-3.1-flash-tts-preview"
VIDEO_MODEL = "google/veo-3.1-lite"
IMAGE_MODEL = "bytedance-seed/seedream-4.5"
TTS_FORMAT = "pcm"
TTS_VOICE = "Aoede"


# =========================
# МОДЕЛИ PRICE
# =========================
EST_PRICES = {
    # text: USD за 1M токенов
    "x-ai/grok-4.1-fast": {
        "input_per_m": 0.20,
        "output_per_m": 0.50,
    },
    "openai/gpt-5.4-nano": {
        "input_per_m": 0.2,
        "output_per_m": 1.25,
    },
    "openai/gpt-oss-120b": {
        "input_per_m": 0.039,
        "output_per_m": 0.18,
    },
    "openai/gpt-5-nano": {
        "input_per_m": 0.05,
        "output_per_m": 0.40,
    },
    "deepseek/deepseek-v3.2": {
        "input_per_m": 0.252,
        "output_per_m": 0.378,
    },
    # твои мультимодалки — вручную по OpenRouter
    "bytedance-seed/seedream-4.5": {
        "image": 0.04,
    },
    "google/gemini-3.1-flash-tts-preview": {
        "input_per_m": 1.00,
        "output_per_m": 20.00,
    },
}

# =========================
# ПРОВЕРКА ENV
# =========================

if not VK_GROUP_TOKEN:
    raise RuntimeError("Нет VK_GROUP_TOKEN в .env")
if not OPENROUTER_API_KEY:
    raise RuntimeError("Нет OPENROUTER_API_KEY в .env")
if not VK_GROUP_ID:
    raise RuntimeError("Нет VK_GROUP_ID в .env")
if not ALLOWED_USER_ID:
    raise RuntimeError("Нет ALLOWED_USER_ID в .env")


# =========================
# STATE
# =========================


def load_state() -> Dict[str, Any]:
    default_state = {
        "llm_preset": "default",
        "history": [],
        "video_jobs": {},
    }

    if not STATE_PATH.exists():
        return default_state

    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception:
        return default_state

    # Миграция старого state.json
    for key, value in default_state.items():
        loaded.setdefault(key, value)

    if loaded.get("llm_preset") not in LLM_PRESETS:
        loaded["llm_preset"] = "default"

    if not isinstance(loaded.get("history"), list):
        loaded["history"] = []

    if not isinstance(loaded.get("video_jobs"), dict):
        loaded["video_jobs"] = {}

    return loaded


def save_state(state: Dict[str, Any]) -> None:
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


state = load_state()


# =========================
# ОБЩИЕ УТИЛИТЫ
# =========================


def current_llm_model() -> str:
    preset = state.get("llm_preset", "default")
    return LLM_PRESETS.get(preset, LLM_PRESETS["default"])["model"]


def current_llm_title() -> str:
    preset = state.get("llm_preset", "default")
    item = LLM_PRESETS.get(preset, LLM_PRESETS["default"])
    return f"{item['title']} — {item['model']}"


def or_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": APP_URL,
        "X-Title": APP_NAME,
    }
    if extra:
        headers.update(extra)
    return headers


def download_file(url: str, suffix: str = "") -> Path:
    r = requests.get(url, timeout=120)
    r.raise_for_status()

    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)

    p = Path(path)
    p.write_bytes(r.content)
    return p


# =========================
# БАЛАНС
# =========================


def get_openrouter_key_info() -> Dict[str, Any]:
    r = requests.get(
        f"{OPENROUTER_BASE}/key",
        headers=or_headers(),
        timeout=30,
    )

    try:
        r.raise_for_status()
    except Exception:
        raise RuntimeError(f"{r.status_code}: {r.text[:1000]}")

    return r.json().get("data", r.json())


def usd(value: float) -> str:
    return f"${value:.4f}"


def safe_float(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


# =========================
# VK ОТПРАВКА
# =========================


def main_keyboard():
    kb = VkKeyboard(one_time=False, inline=False)

    kb.add_button("🤖 Default", color=VkKeyboardColor.PRIMARY)
    kb.add_button("🧠 Smart", color=VkKeyboardColor.PRIMARY)

    kb.add_line()
    kb.add_button("🛟 Fallback", color=VkKeyboardColor.SECONDARY)
    kb.add_button("🧪 Exp", color=VkKeyboardColor.SECONDARY)

    kb.add_line()
    kb.add_button("🖼 Картинка", color=VkKeyboardColor.POSITIVE)
    kb.add_button("🎙 TTS", color=VkKeyboardColor.POSITIVE)

    kb.add_line()
    kb.add_button("⚙️ Настройки", color=VkKeyboardColor.SECONDARY)
    kb.add_button("📋 Меню", color=VkKeyboardColor.SECONDARY)

    kb.add_line()
    kb.add_button("🧹 Reset", color=VkKeyboardColor.NEGATIVE)

    kb.add_line()
    kb.add_button("💰 Баланс", color=VkKeyboardColor.SECONDARY)
    kb.add_button("🧮 Расчёт", color=VkKeyboardColor.SECONDARY)

    return kb.get_keyboard()


def vk_send(
    vk,
    peer_id: int,
    text: str,
    attachment: Optional[str] = None,
    keyboard=None,
) -> None:
    params = {
        "peer_id": peer_id,
        "message": text[:4000] if text else "",
        "random_id": random.randint(1, 2**31 - 1),
    }

    if attachment:
        params["attachment"] = attachment

    if keyboard:
        params["keyboard"] = keyboard

    vk.messages.send(**params)


def vk_send_chunks(vk, peer_id: int, text: str, keyboard=None) -> None:
    if not text:
        text = "Пустой ответ."

    chunks = [text[i : i + 3500] for i in range(0, len(text), 3500)]

    for idx, chunk in enumerate(chunks):
        is_last = idx == len(chunks) - 1
        vk_send(
            vk,
            peer_id,
            chunk,
            keyboard=keyboard if is_last else None,
        )


# =========================
# OPENROUTER CHAT
# =========================


def openrouter_chat(messages: List[Dict[str, Any]], model: Optional[str] = None) -> str:
    payload = {
        "model": model or current_llm_model(),
        "messages": messages,
    }

    r = requests.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers=or_headers({"Content-Type": "application/json"}),
        json=payload,
        timeout=120,
    )

    try:
        r.raise_for_status()
    except Exception:
        raise RuntimeError(f"{r.status_code}: {r.text[:1000]}")

    data = r.json()
    return data["choices"][0]["message"].get("content", "")


def add_history(role: str, content: str) -> None:
    hist = state.setdefault("history", [])
    hist.append({"role": role, "content": content})
    state["history"] = hist[-20:]
    save_state(state)


# =========================
# КОМАНДЫ
# =========================


def cmd_menu(vk, peer_id: int) -> None:
    text = f"""
Меню бота:

Текущая LLM:
{current_llm_title()}

Служебные модели:
STT: {STT_MODEL}
TTS: {TTS_MODEL} / voice: {TTS_VOICE}
Image: {IMAGE_MODEL}
Video: {VIDEO_MODEL}

Команды:
/models — список пресетов
/set default — обычная модель
/set smart — умная модель
/set fallback — запасная модель
/set exp1 — эксперимент 1
/set exp2 — эксперимент 2

/img промпт — генерация картинки
/tts текст — озвучить текст
/video промпт — генерация видео
/video_status ID — проверить видео
/reset — очистить историю
/menu — это меню

Можно просто писать обычный текст.
Фото + текст — анализ фото.
Голосовое — транскрибация.
""".strip()

    vk_send(vk, peer_id, text, keyboard=main_keyboard())


def cmd_models(vk, peer_id: int) -> None:
    lines = ["Список LLM-пресетов:\n"]

    for key, item in LLM_PRESETS.items():
        mark = "✅" if state.get("llm_preset", "default") == key else "▫️"
        lines.append(
            f"{mark} /set {key}\n"
            f"{item['title']}: {item['model']}\n"
            f"{item['desc']}"
        )

    lines.append("\nСлужебные модели:")
    lines.append(f"STT: {STT_MODEL}")
    lines.append(f"TTS: {TTS_MODEL} / voice: {TTS_VOICE}")
    lines.append(f"Image: {IMAGE_MODEL}")
    lines.append(f"Video: {VIDEO_MODEL}")

    vk_send(vk, peer_id, "\n\n".join(lines), keyboard=main_keyboard())


def cmd_set(vk, peer_id: int, arg: str) -> None:
    key = arg.strip().lower()

    if key not in LLM_PRESETS:
        allowed = ", ".join(LLM_PRESETS.keys())
        vk_send(
            vk,
            peer_id,
            f"Нет такого пресета: {key}\nДоступно: {allowed}",
            keyboard=main_keyboard(),
        )
        return

    state["llm_preset"] = key
    save_state(state)

    item = LLM_PRESETS[key]
    vk_send(
        vk,
        peer_id,
        f"Ок, выбрана модель:\n{item['title']} — {item['model']}\n\n{item['desc']}",
        keyboard=main_keyboard(),
    )


def cmd_settings(vk, peer_id: int) -> None:
    text = f"""
Настройки:

Текущая LLM:
{current_llm_title()}

Пресет:
{state.get("llm_preset", "default")}

Служебные модели:
STT: {STT_MODEL}
TTS: {TTS_MODEL}
TTS voice: {TTS_VOICE}
Image: {IMAGE_MODEL}
Video: {VIDEO_MODEL}

Команды:
/models
/set default
/set smart
/set fallback
/set exp1
/set exp2
/reset
""".strip()

    vk_send(vk, peer_id, text, keyboard=main_keyboard())


def cmd_balance(vk, peer_id: int) -> None:
    try:
        data = get_openrouter_key_info()
    except Exception as e:
        vk_send(
            vk, peer_id, f"Ошибка баланса OpenRouter:\n{e}", keyboard=main_keyboard()
        )
        return

    # Поля у /key могут отличаться, поэтому вытаскиваем мягко
    usage = safe_float(data.get("usage"))
    limit = data.get("limit")
    limit_float = safe_float(limit) if limit is not None else None

    if limit_float is not None and limit_float > 0:
        remaining = max(limit_float - usage, 0)
        text = (
            f"Баланс API key:\n\n"
            f"Лимит ключа: {usd(limit_float)}\n"
            f"Потрачено ключом: {usd(usage)}\n"
            f"Осталось по ключу: {usd(remaining)}"
        )
    else:
        text = (
            f"Инфа по API key:\n\n"
            f"Потрачено ключом: {usd(usage)}\n"
            f"Лимит ключа: не задан / не пришёл\n\n"
            f"Для полного баланса аккаунта нужен Management API key и /credits."
        )

    vk_send(vk, peer_id, text, keyboard=main_keyboard())


def estimate_text_messages(
    balance: float, model_id: str, input_tokens: int, output_tokens: int
) -> int:
    p = EST_PRICES.get(model_id)
    if not p:
        return 0

    input_cost = input_tokens / 1_000_000 * p.get("input_per_m", 0)
    output_cost = output_tokens / 1_000_000 * p.get("output_per_m", 0)
    one_msg = input_cost + output_cost

    if one_msg <= 0:
        return 0

    return int(balance / one_msg)


def cmd_calc(vk, peer_id: int, arg: str = "") -> None:
    balance = None

    # /calc 1.25 — ручной баланс
    if arg.strip():
        balance = safe_float(arg.strip(), None)

    # если баланс не передан — пробуем взять из /key
    if balance is None:
        try:
            data = get_openrouter_key_info()
            usage = safe_float(data.get("usage"))
            limit = data.get("limit")
            if limit is not None:
                balance = max(safe_float(limit) - usage, 0)
        except Exception:
            balance = None

    if balance is None:
        vk_send(
            vk,
            peer_id,
            "Не смог понять баланс.\nНапиши так:\n/calc 1.25",
            keyboard=main_keyboard(),
        )
        return

    current_model = current_llm_model()

    # Типовой короткий чат: 500 input + 500 output tokens
    short_msgs = estimate_text_messages(balance, current_model, 500, 500)

    # Средний чат: 1500 input + 1500 output tokens
    medium_msgs = estimate_text_messages(balance, current_model, 1500, 1500)

    # Условная оценка TTS
    tts_price = EST_PRICES["google/gemini-3.1-flash-tts-preview"]
    # очень грубо: 1 сек аудио ≈ 40-80 output tokens, берём 60
    tts_output_tokens_per_sec = 60
    tts_sec_cost = tts_output_tokens_per_sec / 1_000_000 * tts_price["output_per_m"]
    tts_seconds = int(balance / tts_sec_cost) if tts_sec_cost > 0 else 0

    image_price = EST_PRICES["bytedance-seed/seedream-4.5"]["image"]
    images = int(balance / image_price) if image_price > 0 else 0

    text = f"""
Расчёт по балансу: {usd(balance)}

Текущая LLM:
{current_llm_title()}

Примерно хватит на:

Текст:
короткие сообщения 500+500 токенов: ~{short_msgs} шт.
средние сообщения 1500+1500 токенов: ~{medium_msgs} шт.

TTS Gemini:
примерно ~{tts_seconds} сек аудио
грубо, потому что speech-токены считаются не как обычные секунды

Картинки Seedream:
примерно ~{images} изображений
если цена {usd(image_price)} за картинку

Команды:
/balance — проверить API key
/calc 1.25 — расчёт от ручного баланса
""".strip()

    vk_send(vk, peer_id, text, keyboard=main_keyboard())


# =========================
# ТЕКСТ
# =========================


def ask_text(vk, peer_id: int, user_text: str) -> None:
    add_history("user", user_text)

    messages = [
        {
            "role": "system",
            "content": (
                "Ты личный помощник пользователя в VK. "
                "Отвечай по-русски, кратко, практично, без лишней воды."
            ),
        }
    ]

    messages.extend(state.get("history", [])[-20:])

    try:
        answer = openrouter_chat(messages)
    except Exception as e:
        vk_send(vk, peer_id, f"Ошибка OpenRouter:\n{e}", keyboard=main_keyboard())
        return

    add_history("assistant", answer)
    vk_send_chunks(vk, peer_id, answer, keyboard=main_keyboard())


# =========================
# ФОТО / ВИДЕО АНАЛИЗ
# =========================


def image_url_from_vk_photo(photo: Dict[str, Any]) -> Optional[str]:
    sizes = photo.get("sizes", [])
    if not sizes:
        return None

    best = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
    return best.get("url")


def ask_with_image(vk, peer_id: int, text: str, image_url: str) -> None:
    prompt = text.strip() or "Опиши изображение и скажи, что на нём важно."

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": image_url},
                },
            ],
        }
    ]

    try:
        answer = openrouter_chat(messages, model=current_llm_model())
    except Exception as e:
        vk_send(vk, peer_id, f"Ошибка анализа фото:\n{e}", keyboard=main_keyboard())
        return

    vk_send_chunks(vk, peer_id, answer, keyboard=main_keyboard())


def ask_with_video_url(vk, peer_id: int, text: str, video_url: str) -> None:
    prompt = (
        text.strip() or "Опиши видео: что происходит, важные детали, краткий вывод."
    )

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "video_url",
                    "video_url": {"url": video_url},
                },
            ],
        }
    ]

    try:
        answer = openrouter_chat(messages, model=current_llm_model())
    except Exception as e:
        vk_send(vk, peer_id, f"Ошибка анализа видео:\n{e}", keyboard=main_keyboard())
        return

    vk_send_chunks(vk, peer_id, answer, keyboard=main_keyboard())


# =========================
# STT
# =========================


def transcribe_audio_url(vk, peer_id: int, audio_url: str, fmt: str = "ogg") -> None:
    try:
        audio_path = download_file(audio_url, suffix=f".{fmt}")
        b64 = base64.b64encode(audio_path.read_bytes()).decode("utf-8")

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Транскрибируй аудио на русский. Верни только текст.",
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": b64,
                            "format": fmt,
                        },
                    },
                ],
            }
        ]

        answer = openrouter_chat(messages, model=STT_MODEL)
        vk_send_chunks(
            vk,
            peer_id,
            "Транскрибация:\n\n" + answer,
            keyboard=main_keyboard(),
        )

    except Exception as e:
        vk_send(vk, peer_id, f"Ошибка транскрибации:\n{e}", keyboard=main_keyboard())


# =========================
# IMAGE GENERATION
# =========================


def generate_image(vk, upload: VkUpload, peer_id: int, prompt: str) -> None:
    if not prompt.strip():
        vk_send(
            vk,
            peer_id,
            "Напиши так:\n/img кот в бронежилете, cinematic",
            keyboard=main_keyboard(),
        )
        return

    payload = {
        "model": IMAGE_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "modalities": ["image"],
    }

    try:
        r = requests.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers=or_headers({"Content-Type": "application/json"}),
            json=payload,
            timeout=180,
        )

        try:
            r.raise_for_status()
        except Exception:
            raise RuntimeError(f"{r.status_code}: {r.text[:1000]}")

        data = r.json()

        msg = data["choices"][0]["message"]
        images = msg.get("images") or []

        if not images:
            content = msg.get("content", "Картинка не пришла.")
            vk_send_chunks(vk, peer_id, content, keyboard=main_keyboard())
            return

        image_url = images[0]["image_url"]["url"]

        if image_url.startswith("data:image"):
            _, b64data = image_url.split(",", 1)
            img_bytes = base64.b64decode(b64data)
        else:
            img_bytes = requests.get(image_url, timeout=120).content

        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)

        img_path = Path(path)
        img_path.write_bytes(img_bytes)

        photo = upload.photo_messages(str(img_path), peer_id=peer_id)[0]
        attachment = f"photo{photo['owner_id']}_{photo['id']}"

        vk_send(
            vk,
            peer_id,
            "Готово.",
            attachment=attachment,
            keyboard=main_keyboard(),
        )

    except Exception as e:
        vk_send(
            vk, peer_id, f"Ошибка генерации картинки:\n{e}", keyboard=main_keyboard()
        )


# =========================
# TTS
# =========================


def text_to_speech(vk, upload: VkUpload, peer_id: int, text: str) -> None:
    if not text.strip():
        vk_send(
            vk,
            peer_id,
            "Напиши так:\n/tts Привет, браток",
            keyboard=main_keyboard(),
        )
        return

    payload = {
        "model": TTS_MODEL,
        "input": text,
        "voice": TTS_VOICE,
        "response_format": TTS_FORMAT,
    }

    try:
        r = requests.post(
            f"{OPENROUTER_BASE}/audio/speech",
            headers=or_headers({"Content-Type": "application/json"}),
            json=payload,
            timeout=120,
        )

        try:
            r.raise_for_status()
        except Exception:
            raise RuntimeError(f"{r.status_code}: {r.text[:1000]}")

        # 1. Сохраняем сырой PCM от Gemini
        pcm_fd, pcm_path_raw = tempfile.mkstemp(suffix=".pcm")
        os.close(pcm_fd)
        pcm_path = Path(pcm_path_raw)
        pcm_path.write_bytes(r.content)

        # 2. Заворачиваем PCM 24kHz 16-bit mono в WAV
        wav_fd, wav_path_raw = tempfile.mkstemp(suffix=".wav")
        os.close(wav_fd)
        wav_path = Path(wav_path_raw)

        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(r.content)

        # 3. Конвертим WAV в OGG Opus, чтобы VK принял как голосовое
        ogg_fd, ogg_path_raw = tempfile.mkstemp(suffix=".ogg")
        os.close(ogg_fd)
        ogg_path = Path(ogg_path_raw)

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(wav_path),
                "-c:a",
                "libopus",
                "-b:a",
                "32k",
                str(ogg_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # 4. Грузим именно как голосовое сообщение
        audio_message = upload.audio_message(
            audio=str(ogg_path),
            peer_id=peer_id,
        )

        attachment = (
            f"audio_message"
            f"{audio_message['audio_message']['owner_id']}_"
            f"{audio_message['audio_message']['id']}"
        )

        vk_send(
            vk,
            peer_id,
            "",
            attachment=attachment,
            keyboard=main_keyboard(),
        )

    except Exception as e:
        vk_send(vk, peer_id, f"Ошибка TTS:\n{e}", keyboard=main_keyboard())


# =========================
# VIDEO GENERATION
# =========================


def generate_video(vk, peer_id: int, prompt: str) -> None:
    if not prompt.strip():
        vk_send(
            vk,
            peer_id,
            "Напиши так:\n/video cyberpunk city, rain, camera flythrough",
            keyboard=main_keyboard(),
        )
        return

    payload = {
        "model": VIDEO_MODEL,
        "prompt": prompt,
        "duration": 4,
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "generate_audio": True,
    }

    try:
        r = requests.post(
            f"{OPENROUTER_BASE}/videos",
            headers=or_headers({"Content-Type": "application/json"}),
            json=payload,
            timeout=60,
        )

        try:
            r.raise_for_status()
        except Exception:
            raise RuntimeError(f"{r.status_code}: {r.text[:1000]}")

        data = r.json()

        job_id = data.get("id")
        polling_url = data.get("polling_url")

        if not job_id:
            raise RuntimeError(f"OpenRouter не вернул job id: {data}")

        if polling_url:
            state.setdefault("video_jobs", {})[job_id] = polling_url
            save_state(state)

        vk_send(
            vk,
            peer_id,
            f"Видео поставлено в очередь.\nID: {job_id}\nПроверка: /video_status {job_id}",
            keyboard=main_keyboard(),
        )

    except Exception as e:
        vk_send(vk, peer_id, f"Ошибка запуска видео:\n{e}", keyboard=main_keyboard())


def check_video_status(vk, upload: VkUpload, peer_id: int, job_id: str) -> None:
    job_id = job_id.strip()

    if not job_id:
        vk_send(
            vk,
            peer_id,
            "Напиши так:\n/video_status ID",
            keyboard=main_keyboard(),
        )
        return

    polling_url = state.get("video_jobs", {}).get(job_id)
    if not polling_url:
        polling_url = f"{OPENROUTER_BASE}/videos/{job_id}"

    try:
        r = requests.get(
            polling_url,
            headers=or_headers(),
            timeout=60,
        )

        try:
            r.raise_for_status()
        except Exception:
            raise RuntimeError(f"{r.status_code}: {r.text[:1000]}")

        data = r.json()

        status = data.get("status")
        if status != "completed":
            vk_send(
                vk,
                peer_id,
                f"Статус видео: {status}\nЕсли не готово — проверь позже.",
                keyboard=main_keyboard(),
            )
            return

        urls = data.get("unsigned_urls") or []
        if not urls:
            vk_send(
                vk, peer_id, "Видео готово, но URL не найден.", keyboard=main_keyboard()
            )
            return

        video_bytes = requests.get(urls[0], timeout=300).content

        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)

        video_path = Path(path)
        video_path.write_bytes(video_bytes)

        doc = upload.document_message(
            doc=str(video_path),
            peer_id=peer_id,
            title=f"video_{job_id}.mp4",
        )

        attachment = f"doc{doc['doc']['owner_id']}_{doc['doc']['id']}"

        vk_send(
            vk,
            peer_id,
            "Видео готово.",
            attachment=attachment,
            keyboard=main_keyboard(),
        )

    except Exception as e:
        vk_send(vk, peer_id, f"Ошибка проверки видео:\n{e}", keyboard=main_keyboard())


# =========================
# ATTACHMENTS
# =========================


def handle_attachments(
    vk,
    peer_id: int,
    text: str,
    attachments: List[Dict[str, Any]],
) -> bool:
    if not attachments:
        return False

    for att in attachments:
        att_type = att.get("type")

        if att_type == "photo":
            image_url = image_url_from_vk_photo(att.get("photo", {}))
            if image_url:
                ask_with_image(vk, peer_id, text, image_url)
                return True

        if att_type == "audio_message":
            audio_msg = att.get("audio_message", {})
            link = audio_msg.get("link_ogg") or audio_msg.get("link_mp3")
            if link:
                fmt = "ogg" if "ogg" in link else "mp3"
                transcribe_audio_url(vk, peer_id, link, fmt=fmt)
                return True

        if att_type == "doc":
            doc = att.get("doc", {})
            url = doc.get("url")
            ext = (doc.get("ext") or "").lower()

            if url and ext in ["ogg", "mp3", "wav", "m4a"]:
                transcribe_audio_url(
                    vk,
                    peer_id,
                    url,
                    fmt="mp3" if ext == "mp3" else ext,
                )
                return True

            if url and ext in ["mp4", "webm", "mov"]:
                ask_with_video_url(vk, peer_id, text, url)
                return True

    return False


# =========================
# ROUTER
# =========================


def handle_message(
    vk,
    upload: VkUpload,
    peer_id: int,
    user_id: int,
    text: str,
    attachments: List[Dict[str, Any]],
) -> None:
    # Главная защита: бот отвечает только тебе.
    if user_id != ALLOWED_USER_ID:
        return

    text = (text or "").strip()

    # Сначала вложения: фото/голос/доки
    if handle_attachments(vk, peer_id, text, attachments):
        return

    if not text:
        vk_send(
            vk,
            peer_id,
            "Пустое сообщение. Напиши /menu",
            keyboard=main_keyboard(),
        )
        return

    # Кнопки
    if text == "📋 Меню":
        cmd_menu(vk, peer_id)
        return

    if text == "⚙️ Настройки":
        cmd_settings(vk, peer_id)
        return

    if text == "🤖 Default":
        cmd_set(vk, peer_id, "default")
        return

    if text == "🧠 Smart":
        cmd_set(vk, peer_id, "smart")
        return

    if text == "🛟 Fallback":
        cmd_set(vk, peer_id, "fallback")
        return

    if text == "🧪 Exp":
        vk_send(
            vk,
            peer_id,
            "Экспериментальные модели:\n\n"
            "/set exp1 — openai/gpt-5-nano\n"
            "/set exp2 — deepseek/deepseek-v3.2",
            keyboard=main_keyboard(),
        )
        return

    if text == "🖼 Картинка":
        vk_send(
            vk,
            peer_id,
            "Напиши так:\n/img кот в бронежилете, cinematic",
            keyboard=main_keyboard(),
        )
        return

    if text == "🎙 TTS":
        vk_send(
            vk,
            peer_id,
            "Напиши так:\n/tts Привет, браток",
            keyboard=main_keyboard(),
        )
        return

    if text == "🧹 Reset":
        state["history"] = []
        save_state(state)
        vk_send(vk, peer_id, "История очищена.", keyboard=main_keyboard())
        return

    if text == "💰 Баланс":
        cmd_balance(vk, peer_id)
        return

    if text == "🧮 Расчёт":
        cmd_calc(vk, peer_id)
        return

    # Slash-команды
    if text == "/menu":
        cmd_menu(vk, peer_id)
        return

    if text == "/models":
        cmd_models(vk, peer_id)
        return

    if text.startswith("/set "):
        cmd_set(vk, peer_id, text.removeprefix("/set "))
        return

    if text == "/reset":
        state["history"] = []
        save_state(state)
        vk_send(vk, peer_id, "История очищена.", keyboard=main_keyboard())
        return

    if text.startswith("/chat "):
        ask_text(vk, peer_id, text.removeprefix("/chat "))
        return

    if text.startswith("/img "):
        generate_image(vk, upload, peer_id, text.removeprefix("/img "))
        return

    if text.startswith("/tts "):
        text_to_speech(vk, upload, peer_id, text.removeprefix("/tts "))
        return

    if text.startswith("/video "):
        generate_video(vk, peer_id, text.removeprefix("/video "))
        return

    if text.startswith("/video_status "):
        check_video_status(
            vk,
            upload,
            peer_id,
            text.removeprefix("/video_status ").strip(),
        )
        return

    if text == "/balance":
        cmd_balance(vk, peer_id)
        return

    if text.startswith("/calc"):
        cmd_calc(vk, peer_id, text.removeprefix("/calc").strip())
        return

    # Обычный текст
    ask_text(vk, peer_id, text)


# =========================
# MAIN
# =========================


def main() -> None:
    vk_session = vk_api.VkApi(
        token=VK_GROUP_TOKEN,
        api_version="5.199",
    )

    vk = vk_session.get_api()
    upload = VkUpload(vk_session)
    longpoll = VkBotLongPoll(vk_session, VK_GROUP_ID)

    print("VK OpenRouter bot started.")
    print(f"Allowed user id: {ALLOWED_USER_ID}")
    print(f"Current LLM: {current_llm_title()}")

    for event in longpoll.listen():
        try:
            if event.type != VkBotEventType.MESSAGE_NEW:
                continue

            msg = event.object.message

            peer_id = msg.get("peer_id")
            user_id = msg.get("from_id")
            text = msg.get("text", "")
            attachments = msg.get("attachments", [])

            print(f"MESSAGE FROM {user_id}: {text}")

            handle_message(
                vk=vk,
                upload=upload,
                peer_id=peer_id,
                user_id=user_id,
                text=text,
                attachments=attachments,
            )

        except Exception as e:
            print("EVENT ERROR:", repr(e))


if __name__ == "__main__":
    main()
