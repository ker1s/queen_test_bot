import telebot
import json
import time
import logging
import argparse
import os
from datetime import datetime
from pathlib import Path
from queue import Queue
from threading import Thread

from routerai_client import RouterAIClient


logger = logging.getLogger(__name__)


# === CONFIG ===
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
SETTINGS = {}
if CONFIG_PATH.exists():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        SETTINGS = json.load(f)
if not isinstance(SETTINGS, dict):
    raise ValueError("config.json must contain a JSON object")


def get_setting(name, default=None):
    value = os.environ.get(name, SETTINGS.get(name, default))
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Set {name} in config.json or environment variables")
    return value.strip()


BOT_TOKEN = get_setting("BOT_TOKEN")
ROUTERAI_API_KEY = get_setting("ROUTERAI_API_KEY")
LLM_MODEL = get_setting("LLM_MODEL", "x-ai/grok-4.7")
TEXT2IMG_MODEL = get_setting("TEXT2IMG_MODEL", "openai/gpt-5-image")
bot = telebot.TeleBot(BOT_TOKEN)
QUESTIONS_PATH = BASE_DIR / "questions.json"
PROMPTS_PATH = BASE_DIR / "prompts.json"

# === STORAGE ===
user_data = {}

# === QUEUE FOR ROUTERAI REQUESTS ===
routerai_queue = Queue()

# === LOAD QUESTIONS & PROMPTS ===
with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
    QUESTIONS = json.load(f)

with open(PROMPTS_PATH, "r", encoding="utf-8") as f:
    PROMPTS = json.load(f)
REPORT_PROMPT = PROMPTS["report"]
IMG_GEN_PROMPT = PROMPTS.get("img_gen")

routerai_client = RouterAIClient(
    api_key=ROUTERAI_API_KEY,
    model=LLM_MODEL,
    report_prompt=REPORT_PROMPT,
    image_model=TEXT2IMG_MODEL,
    image_prompt=IMG_GEN_PROMPT,
)

# === LOGGING SETUP ===


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


# === ROUTERAI QUEUE WORKER ===
def routerai_worker():
    """Обрабатывает запросы к RouterAI по одному из очереди"""
    while True:
        task = routerai_queue.get()
        if task is None:
            break
        
        try:
            task_type = task["type"]
            user_id = task["user_id"]
            
            if task_type == "characteristic":
                answers_json = task["answers_json"]
                try:
                    characteristic = routerai_client.generate_characteristic(answers_json)
                    task["callback"](user_id, characteristic, None)
                except Exception as e:
                    logger.exception("Failed to generate characteristic for %s", user_id)
                    task["callback"](user_id, None, str(e))
            
        except Exception as e:
            logger.exception("Unexpected error in routerai_worker")
        
        finally:
            routerai_queue.task_done()
            time.sleep(1)  # Небольшая задержка между запросами


# === START ===
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
        "Готова начинать?😈😈😈",
        reply_markup=markup,
    )


def start_test(message):
    user_id = message.chat.id
    bot.delete_message(user_id, message.message_id)

    user_data[user_id] = {
        "answers": [],
        "current_q": 0,
        "last_msg_id": None,
        "characteristic": None,
    }
    send_question(user_id)


def send_question(user_id):
    data = user_data[user_id]
    q_index = data["current_q"]

    if q_index >= len(QUESTIONS):
        return finish_test(user_id)

    q = QUESTIONS[q_index]
    markup = None
    question_text = f"❓ {q['question_text']}"
    if q["options"]:
        markup = telebot.types.InlineKeyboardMarkup()
        for idx, opt in enumerate(q["options"]):
            markup.add(telebot.types.InlineKeyboardButton(opt, callback_data=f"opt_{idx}"))
        markup.add(
            telebot.types.InlineKeyboardButton(
                "✏️ Свой вариант", callback_data="custom_input"
            )
        )
    else:
        question_text += "\n\n✍️ Напиши ответ сообщением."

    if data["last_msg_id"]:
        try:
            bot.delete_message(user_id, data["last_msg_id"])
        except Exception:
            pass

    msg = bot.send_message(user_id, question_text, reply_markup=markup)
    user_data[user_id]["last_msg_id"] = msg.message_id
    if not q["options"]:
        bot.register_next_step_handler(msg, handle_custom_input)


@bot.callback_query_handler(func=lambda call: True)
def handle_answer(call):
    user_id = call.message.chat.id
    data = user_data.get(user_id)

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        logger.warning("Failed to answer callback_query %s", call.id, exc_info=True)

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
            "Дорогие дамы я надеюсь вы поняли что все тесты полная хуйня, огромное вам спасибо что вы есть. Оставайтесь такими какие есть вы все крутые😘😘😘. Обняли🫂.",
        )
        user_data.pop(user_id, None)
        return

    if not data:
        return

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
            {
                "question": question["question_text"],
                "options": question["options"],
                "answer": selected_option,
                "is_custom": False,
            }
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

    if not message.text or not message.text.strip():
        msg = bot.send_message(user_id, "✍️ Напиши ответ текстом.")
        bot.register_next_step_handler(msg, handle_custom_input)
        return

    q_index = data["current_q"]
    question = QUESTIONS[q_index]

    data["answers"].append(
        {
            "question": question["question_text"],
            "options": question["options"],
            "answer": message.text,
            "is_custom": True,
        }
    )
    data["current_q"] += 1

    try:
        bot.delete_message(user_id, message.message_id - 1)
        bot.delete_message(user_id, message.message_id)
    except Exception:
        pass

    send_question(user_id)


def format_answers_summary(answers):
    """Форматирует ответы пользователя для отображения"""
    summary = "📋 *Твои ответы:*\n\n"
    
    for i, answer_data in enumerate(answers, 1):
        summary += f"*{i}. {answer_data['question']}*\n"
        
        if answer_data.get("is_custom"):
            # Пользовательский ответ как цитата
            summary += f"_{answer_data['answer']}_\n\n"
        else:
            # Показываем все варианты, выделяя выбранный жирным
            for opt in answer_data.get("options", []):
                if opt == answer_data["answer"]:
                    summary += f"• *{opt}*\n"
                else:
                    summary += f"• {opt}\n"
            summary += "\n"
    
    return summary


def finish_test(user_id):
    data = user_data[user_id]

    try:
        bot.delete_message(user_id, data.get("last_msg_id"))
    except Exception:
        pass

    # Показываем все ответы перед обработкой
    answers_summary = format_answers_summary(data["answers"])
    bot.send_message(
        user_id,
        answers_summary,
        parse_mode="Markdown",
    )
    
    bot.send_message(
        user_id,
        "🤖 Отлично! Пока наш индусский специалист анализирует твои ответы, ты можешь их посмотреть и обсудить с друзьями.\n\n"
        "Написание твоей характеристики может занять пару минут... 💅",
    )

    # Подготовка данных для LLM
    answers_json = json.dumps(
        data["answers"],
        ensure_ascii=False,
        indent=2,
    )

    # Добавляем задачу в очередь
    def on_characteristic_complete(uid, characteristic, error):
        if error:
            bot.send_message(
                uid,
                "Произошла ошибка при генерации характеристики. Попробуй позже.",
            )
            return
        
        data["characteristic"] = characteristic
        
        # Генерация портрета временно отключена.
        # image_payload = routerai_client.generate_image(characteristic)
        send_final_result(uid, characteristic)

    routerai_queue.put({
        "type": "characteristic",
        "user_id": user_id,
        "answers_json": answers_json,
        "callback": on_characteristic_complete,
    })


def send_final_result(user_id, characteristic):
    """Отправляет характеристику и кнопку сообщения от авторов."""
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(
            text="Сообщение от авторов", callback_data="message_from_author"
        )
    )

    # Отправка характеристики
    bot.send_message(
        user_id,
        f"🧠 {characteristic}",
        parse_mode="HTML",
        reply_markup=markup,
    )


if __name__ == "__main__":
    logger = setup_logging()

    worker_thread = Thread(target=routerai_worker, daemon=True)
    worker_thread.start()

    bot.remove_webhook()
    bot.infinity_polling(
        timeout=20, long_polling_timeout=20, allowed_updates=["message", "callback_query"]
    )
