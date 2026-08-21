# VK OpenRouter Bot

Личный VK-бот для общения с LLM через OpenRouter API.

Бот работает через сообщения сообщества ВК, отвечает только одному разрешённому пользователю по `ALLOWED_USER_ID`, поддерживает текстовый чат, выбор LLM-пресетов, генерацию изображений, TTS голосовыми сообщениями, обработку фото и голосовых.

## Возможности

- текстовый чат через OpenRouter;
- выбор LLM-пресетов кнопками;
- генерация изображений через OpenRouter;
- TTS: отправка озвучки как голосового сообщения ВК;
- обработка фото с вопросом;
- обработка голосовых сообщений;
- whitelist по VK user ID;
- текстовая клавиатура ВК;
- локальное хранение состояния в `state.json`.

## Используемые модели

### LLM

- `x-ai/grok-4.1-fast` — default;
- `openai/gpt-5.4-nano` — smart;
- `openai/gpt-oss-120b` — fallback;
- `openai/gpt-5-nano` — experimental;
- `deepseek/deepseek-v3.2` — experimental.

### Мультимодальные модели

- STT: `openai/whisper-large-v3-turbo`;
- TTS: `google/gemini-3.1-flash-tts-preview`;
- Image: `bytedance-seed/seedream-4.5`;
- Video: `google/veo-3.1-lite`.

## Установка

### 1. Клонировать проект

```bash
git clone https://github.com/cifroluma/vk-openrouter-bot.git
cd vk-openrouter-bot
````

### 2. Создать виртуальное окружение

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Установить зависимости

```bash
pip install --upgrade pip
pip install vk-api requests python-dotenv
```

### 4. Установить ffmpeg

Нужен для конвертации TTS-аудио в формат голосового сообщения ВК.

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install ffmpeg
```

Termux:

```bash
pkg update
pkg install ffmpeg python git
```

## Настройка `.env`

Создать файл `.env`:

```bash
cp .env.example .env
nano .env
```

Пример:

```env
VK_GROUP_TOKEN=vk1.a.xxxxxxxxxxxxxxxxx
VK_GROUP_ID=123456789
ALLOWED_USER_ID=123456789
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxx
APP_NAME=VK AI Bot
APP_URL=http://localhost
```

### Что значит каждое поле

| Поле                 | Значение                                   |
| -------------------- | ------------------------------------------ |
| `VK_GROUP_TOKEN`     | токен сообщества ВК                        |
| `VK_GROUP_ID`        | ID группы без минуса                       |
| `ALLOWED_USER_ID`    | твой личный VK ID, только ему бот отвечает |
| `OPENROUTER_API_KEY` | API-ключ OpenRouter                        |
| `APP_NAME`           | название приложения для OpenRouter         |
| `APP_URL`            | referer URL для OpenRouter                 |

## Настройка VK

В сообществе ВК нужно включить сообщения и Long Poll API.

### 1. Создать токен сообщества

Путь:

```text
Сообщество → Управление → Работа с API → Ключи доступа
```

Нужные права:

* сообщения сообщества;
* фотографии;
* документы;
* видео;
* управление сообществом.

Важно: для Long Poll может требоваться право `manage`.

### 2. Включить Long Poll API

Путь:

```text
Сообщество → Управление → Работа с API → Long Poll API
```

Настройки:

* Long Poll API: включено;
* версия API: `5.199`;
* типы событий:

  * входящее сообщение;
  * исходящее сообщение, опционально.

### 3. Включить сообщения сообщества

Путь:

```text
Сообщество → Управление → Сообщения
```

Включить сообщения сообщества.

## Запуск

```bash
source .venv/bin/activate
python3 main.py
```

Если всё нормально, в терминале появится:

```text
VK OpenRouter bot started.
Allowed user id: ...
Current LLM: ...
```

После этого можно написать боту в ВК:

```text
/menu
```

## Команды

| Команда            | Описание                  |
| ------------------ | ------------------------- |
| `/menu`            | показать меню             |
| `/models`          | показать пресеты моделей  |
| `/set default`     | выбрать default-модель    |
| `/set smart`       | выбрать smart-модель      |
| `/set fallback`    | выбрать fallback-модель   |
| `/set exp1`        | выбрать experimental 1    |
| `/set exp2`        | выбрать experimental 2    |
| `/img prompt`      | сгенерировать изображение |
| `/tts текст`       | озвучить текст голосовым  |
| `/video prompt`    | запустить генерацию видео |
| `/video_status ID` | проверить статус видео    |
| `/reset`           | очистить историю          |

## Примеры

### Текстовый чат

```text
Объясни RAG простыми словами
```

### Выбор модели

```text
/set smart
```

### Генерация картинки

```text
/img realistic cute robot in vk chat style, soft natural light, cinematic
```

### TTS

```text
/tts [softly] [warm] Я тебя люблю. [whispers] Очень.
```

Лучше отправлять короткие TTS-фразы, потому что длинные аудио могут обрезаться провайдером.

## Проверка VK-токена

Можно создать `debug.py`:

```python
import os
from dotenv import load_dotenv
import vk_api

load_dotenv()

vk_session = vk_api.VkApi(
    token=os.getenv("VK_GROUP_TOKEN"),
    api_version="5.199"
)

vk = vk_session.get_api()
group_id = int(os.getenv("VK_GROUP_ID"))

print(vk.groups.getTokenPermissions())
print(vk.groups.getLongPollSettings(group_id=group_id))
print(vk.groups.getLongPollServer(group_id=group_id))
```

Запуск:

```bash
python3 debug.py
```

Если `getLongPollServer` возвращает `key`, `server`, `ts`, значит VK Long Poll настроен правильно.

## Частые ошибки

### `Access denied: no access to call this method`

Обычно у токена нет нужных прав.

Проверь, что у токена есть:

* `messages`;
* `docs`;
* `photos`;
* `manage`.

Также проверь, что Long Poll API включён в настройках группы.

### `No endpoints found that support the requested output modalities`

Значит модель не поддерживает указанные `modalities`.

Например, для image-only модели надо:

```python
"modalities": ["image"]
```

а не:

```python
"modalities": ["image", "text"]
```

### `Provider returned 403`

OpenRouter нашёл модель, но провайдер запретил запрос.

Возможные причины:

* модель недоступна через API;
* провайдер режет регион/аккаунт;
* неподдерживаемый голос/формат;
* временная ошибка провайдера.

### TTS обрезается

Если TTS обрывается, лучше отправлять короткие фразы. Например:

```text
/tts [softly] [warm] Я тебя люблю.
```

а не длинный монолог.

## Запуск на телефоне через Termux

Установить зависимости:

```bash
pkg update
pkg install python git ffmpeg
```

Клонировать проект:

```bash
git clone https://github.com/cifroluma/vk-openrouter-bot.git
cd vk-openrouter-bot
```

Создать окружение:

```bash
python -m venv .venv
source .venv/bin/activate
pip install vk-api requests python-dotenv
```

Запуск:

```bash
python main.py
```

Чтобы Android не убивал процесс:

```bash
termux-wake-lock
```

## О проекте

Бот собран с активной помощью ИИ (ChatGPT) — для личного использования и на случай, если кому-то ещё пригодится тот же функционал.

Отключить wake lock:

```bash
termux-wake-unlock
```
