import telebot
import json
import time
import requests
import logging
import base64
from io import BytesIO
import argparse
from datetime import datetime
from pathlib import Path


# === CONFIG ===
BOT_TOKEN = "8465030829:AAG3-7ZIQGiTuWwhUeQNsv66f24mabMwqcY"
bot = telebot.TeleBot(BOT_TOKEN)
QUESTIONS_PATH = "questions.json"
PROMPTS_PATH = "prompts.json"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = (
    "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"
)
LLM_MODEL = "x-ai/grok-4-fast"
TEXT2IMG_MODEL = "openai/gpt-5-image"

PHOTO_CAPTION_LIMIT = 1024

# === STORAGE ===
user_data = {}

# === LOAD QUESTIONS & PROMPTS ===
with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
    QUESTIONS = json.load(f)

with open(PROMPTS_PATH, "r", encoding="utf-8") as f:
    PROMPTS = json.load(f)
REPORT_PROMPT = PROMPTS["report"]
IMG_GEN_PROMPT = PROMPTS["img_gen"]

# === START ===


def setup_logging() -> logging.Logger:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging severity threshold",
    )
    args, unknown = parser.parse_known_args()
    logging.root.setLevel(logging.NOTSET)

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    log_file = log_dir / f"queen-bot-{timestamp}.log"

    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )

    bot_logger = logging.getLogger("telebot")
    bot_logger.setLevel(getattr(logging, args.log_level))

    logging.getLogger(__name__).info(
        "Log file created at %s with level %s", log_file, args.log_level
    )
    return logging.getLogger(__name__)


@bot.message_handler(commands=["start"])
def first_message(message):
    user_id = message.chat.id
    bot.delete_message(user_id, message.message_id)

    markup = telebot.types.InlineKeyboardMarkup()
    start_test = telebot.types.InlineKeyboardButton(
        text="Начать тестирование", callback_data="start_test"
    )
    markup.add(start_test)
    bot.send_message(
        message.from_user.id,
        "Приветствую на тесте на звание настоящей женщины.",
    )
    bot.send_message(
        message.from_user.id,
        "Готовы начинать?😈😈😈",
        reply_markup=markup,
    )


def start_test(message):
    user_id = message.chat.id
    bot.delete_message(user_id, message.message_id)

    user_data[user_id] = {
        "answers": [],
        "current_q": 0,
        "last_msg_id": None,
        "awaiting_photo": False,
        "characteristic": None,
    }
    send_question(user_id)


def send_question(user_id):
    data = user_data[user_id]
    q_index = data["current_q"]

    if q_index >= len(QUESTIONS):
        return finish_test(user_id)

    q = QUESTIONS[q_index]
    markup = telebot.types.InlineKeyboardMarkup()

    # Используем индексы вместо полного текста для callback_data
    for idx, opt in enumerate(q["options"]):
        # callback_data должен быть коротким (1-64 байта)
        markup.add(telebot.types.InlineKeyboardButton(
            opt, callback_data=f"opt_{idx}"))

    markup.add(
        telebot.types.InlineKeyboardButton(
            "✏️ Свой вариант", callback_data="custom_input"
        )
    )

    if data["last_msg_id"]:
        try:
            bot.delete_message(user_id, data["last_msg_id"])
        except Exception:
            pass

    msg = bot.send_message(
        user_id, f"❓ {q['question_text']}", reply_markup=markup)
    user_data[user_id]["last_msg_id"] = msg.message_id


@bot.callback_query_handler(func=lambda call: True)
def handle_answer(call):
    user_id = call.message.chat.id
    data = user_data.get(user_id)

    # отвечаем один раз — дальше повторять нельзя
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        logger.warning("Failed to answer callback_query %s",
                       call.id, exc_info=True)

    if call.data == "start_test":
        class MockMessage:
            def __init__(self, chat_id):
                self.chat = type("Chat", (), {"id": chat_id})()
                self.message_id = call.message.message_id
                self.from_user = call.from_user

        start_test(MockMessage(user_id))
        return

    if call.data == "custom_input":
        msg = bot.send_message(user_id, "✏️ Введи свой вариант:")
        bot.register_next_step_handler(msg, handle_custom_input)
        return

    if call.data == "message_from_author":
        bot.send_message(
            user_id,
            "Дорогие дамы я надеюсь вы поняли что все тесты полная хуйня, огромное вам спасибо что вы есть. Оставайтесь такими какие есть вы все крутые😘😘😘. Обняли🫂."
        )
        user_data.pop(user_id, None)
        return

    if not data:
        return  # можно отправить текст первым вызовом, если нужно уведомление

    if call.data.startswith("opt_"):
        try:
            opt_index = int(call.data.split("_")[1])
        except (ValueError, IndexError):
            bot.send_message(user_id, "Ошибка обработки ответа")
            return

        question = QUESTIONS[data["current_q"]]
        if not (0 <= opt_index < len(question["options"])):
            bot.send_message(user_id, "Неверный вариант")
            return

        selected_option = question["options"][opt_index]
        data["answers"].append(
            {"question": question["question_text"],
                "options": question["options"], "answer": selected_option}
        )
        data["current_q"] += 1

        try:
            bot.delete_message(user_id, call.message.message_id)
        except Exception:
            pass

        send_question(user_id)


def handle_custom_input(message):
    user_id = message.chat.id
    data = user_data.get(user_id)
    if not data:
        bot.send_message(user_id, "Начни с /start")
        return

    q_index = data["current_q"]
    question = QUESTIONS[q_index]

    data["answers"].append(
        {"question": question["question_text"], "answer": message.text}
    )
    data["current_q"] += 1

    try:
        bot.delete_message(user_id, message.message_id - 1)
        bot.delete_message(user_id, message.message_id)
    except Exception:
        pass

    send_question(user_id)


def finish_test(user_id):
    data = user_data[user_id]

    try:
        bot.delete_message(user_id, data.get("last_msg_id"))
    except Exception:
        pass

    answers_json = json.dumps(
        data["answers"],
        ensure_ascii=False,
        indent=2,
    )

    characteristic = generate_characteristic(answers_json)
    data["characteristic"] = characteristic
    data["awaiting_photo"] = True
    data["last_msg_id"] = None

    bot.send_message(
        user_id,
        (
            "Отлично! Теперь пришлите своё фото (селфи или портрет), оно нужно для подведения итогов.\n"
            "Фото не сохраняется и никуда не передаётся — оно остаётся только в вашем телефоне.\n"
            "Отправьте его как обычное фото, не как файл."
        ),
    )


# === LLM (характеристика) ===
def generate_characteristic(answers_json):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": REPORT_PROMPT},
            {"role": "user", "content": answers_json},
        ],
    }

    response = requests.post(
        OPENROUTER_URL,
        headers=headers,
        data=json.dumps(body, ensure_ascii=False),
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()

    try:
        return payload["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        raise RuntimeError(f"Unexpected OpenRouter response: {payload}")


# === IMG GEN (портрет) ===
def generate_image(description, reference_image_b64=None, retries=2, backoff=3):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    user_content = [{"type": "text", "text": description}]
    if reference_image_b64:
        user_content.append(
            {"type": "input_image", "image_base64": reference_image_b64}
        )

    body = {
        "model": TEXT2IMG_MODEL,
        "modalities": ["image"],
        "messages": [
            {
                "role": "system",
                "content": [{"type": "text", "text": IMG_GEN_PROMPT}],
            },
            {"role": "user", "content": user_content},
        ],
    }

    def parse_image_resource(resource):
        def from_data_url(url: str):
            header, b64_data = url.split(",", 1)
            mime = header.split(";")[0].split(":", 1)[1]
            return {"kind": "base64", "data": b64_data, "mime": mime}
        if isinstance(resource, str):
            if resource.startswith("data:image/"):
                return from_data_url(resource)
            return {"kind": "url", "data": resource}
        if isinstance(resource, dict) and resource.get("url"):
            inner_url = resource["url"]
            if isinstance(inner_url, str) and inner_url.startswith("data:image/"):
                return from_data_url(inner_url)
            return {"kind": "url", "data": inner_url}
        return None

    def extract_image(message):
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "output_image":
                image_url = block.get("image_url")
                parsed = parse_image_resource(image_url)
                if parsed:
                    logger.info(
                        "Image payload received via content (kind=%s)", parsed["kind"]
                    )
                    return parsed
                for key in ("image_base64", "b64_json"):
                    if block.get(key):
                        logger.info(
                            "Image payload received via %s in content", key)
                        return {
                            "kind": "base64",
                            "data": block[key],
                            "mime": block.get("mime_type", "image/png"),
                        }
        for image_item in message.get("images") or []:
            if not isinstance(image_item, dict):
                continue
            if image_item.get("type") == "image_url":
                parsed = parse_image_resource(image_item.get("image_url"))
                if parsed:
                    logger.info(
                        "Image payload received via images[] (kind=%s)", parsed["kind"]
                    )
                    return parsed
            if image_item.get("type") == "image_base64" and image_item.get("image_base64"):
                logger.info(
                    "Image payload received via image_base64 in images[]")
                return {
                    "kind": "base64",
                    "data": image_item["image_base64"],
                    "mime": image_item.get("mime_type", "image/png"),
                }
        return None

    for attempt in range(retries + 1):
        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=body,
            timeout=240,
        )
        response.raise_for_status()
        payload = response.json()

        choice = (payload.get("choices") or [{}])[0]
        error_block = choice.get("error")
        if error_block:
            logger.warning(
                "Image generation error (code=%s, req_id=%s): %s",
                error_block.get("code"),
                error_block.get("metadata", {}).get("provider_request_id")
                or error_block.get("metadata", {}).get("request_id"),
                error_block.get("message"),
            )
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            raise RuntimeError(f"Image generation failed: {payload}")

        message = choice.get("message") or {}
        image_payload = extract_image(message)
        if image_payload:
            return image_payload

        logger.warning("No image found in response on attempt %d", attempt + 1)

    raise RuntimeError(
        f"No image payload found in OpenRouter response: {payload}")


@bot.message_handler(content_types=["photo"])
def handle_user_photo(message):
    user_id = message.chat.id
    data = user_data.get(user_id)
    if not data or not data.get("awaiting_photo"):
        return

    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        photo_bytes = bot.download_file(file_info.file_path)
    except Exception:
        logger.warning("Failed to download user photo for %s",
                       user_id, exc_info=True)
        bot.send_message(
            user_id,
            "Не удалось получить фото. Попробуй отправить его ещё раз как обычное изображение.",
        )
        return

    photo_b64 = base64.b64encode(photo_bytes).decode("utf-8")
    del photo_bytes

    progress_msg = None
    try:
        with open("./cat-kitty.gif", "rb") as gif:
            progress_msg = bot.send_animation(
                chat_id=user_id,
                animation=gif,
                caption="🤖 Обрабатываем ваше фото... это займёт пару минут 💅",
            )

        image_payload = generate_image(
            data["characteristic"],
            reference_image_b64=photo_b64,
        )
    except Exception:
        logger.exception("Photo-based generation failed for %s", user_id)
        if progress_msg:
            try:
                bot.delete_message(user_id, progress_msg.message_id)
            except Exception:
                pass
        bot.send_message(
            user_id,
            "Не вышло обработать фото. Пришли другое изображение или попробуй позже.",
        )
        data["awaiting_photo"] = True
        photo_b64 = None
        return
    finally:
        if progress_msg:
            try:
                bot.delete_message(user_id, progress_msg.message_id)
            except Exception:
                pass
        photo_b64 = None

    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(
            text="Сообщение от авторов", callback_data="message_from_author"
        )
    )

    if image_payload["kind"] == "url" and image_payload["data"].startswith("data:image/"):
        header, b64_data = image_payload["data"].split(",", 1)
        mime = header.split(";")[0].split(":", 1)[1]
        image_payload = {"kind": "base64", "data": b64_data, "mime": mime}

    if image_payload["kind"] == "url":
        logger.info("Sending image to Telegram via URL (no caption)")
        bot.send_photo(user_id, image_payload["data"])
    else:
        logger.info(
            "Sending image to Telegram via base64 (%s bytes)",
            len(image_payload["data"]),
        )
        image_bytes = base64.b64decode(image_payload["data"])
        bio = BytesIO(image_bytes)
        bio.name = "character.png"
        bot.send_photo(user_id, bio)

    bot.send_message(
        user_id,
        f"🧠 {data['characteristic']}",
        parse_mode="HTML",
        reply_markup=markup,
    )
    data["awaiting_photo"] = False


@bot.message_handler(
    func=lambda message: user_data.get(
        message.chat.id, {}).get("awaiting_photo"),
    content_types=[
        "text",
        "document",
        "audio",
        "video",
        "voice",
        "video_note",
        "sticker",
        "animation",
        "location",
        "contact",
    ],
)
def handle_non_photo_during_upload(message):
    bot.send_message(
        message.chat.id,
        "Чтобы продолжить, пришли своё фото как обычное изображение. "
        "Мы его не сохраняем и никому не передаём.",
    )


logger = setup_logging()

bot.remove_webhook()

bot.infinity_polling(
    timeout=20, long_polling_timeout=20, allowed_updates=["message", "callback_query"]
)
