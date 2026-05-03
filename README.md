# VK OpenRouter Bot

Личный VK-бот с OpenRouter API.

## Возможности

- текстовый чат
- выбор LLM-пресетов
- генерация изображений
- TTS голосовыми сообщениями
- обработка фото/голосовых
- whitelist по VK user ID

## Запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install vk-api requests python-dotenv
cp .env.example .env
python3 main.py