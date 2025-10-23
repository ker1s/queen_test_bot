import telebot
import json
import time
import requests
import logging


# === CONFIG ===
BOT_TOKEN = "8465030829:AAG3-7ZIQGiTuWwhUeQNsv66f24mabMwqcY"
bot = telebot.TeleBot(BOT_TOKEN)
QUESTIONS_PATH = "questions.json"
PROMPTS_PATH = "prompts.json"
OPENROUTER_API_KEY = "ТОКЕН_OPENROUTER"
LLM_MODEL = "openrouter/model-name"
TEXT2IMG_MODEL = "openrouter/image-model"

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


@bot.message_handler(commands=["start"])
def start_test(message):
    user_id = message.chat.id
    bot.delete_message(user_id, message.message_id)

    user_data[user_id] = {"answers": [], "current_q": 0, "last_msg_id": None}
    send_question(user_id)


def send_question(user_id):
    data = user_data[user_id]
    q_index = data["current_q"]

    if q_index >= len(QUESTIONS):
        return finish_test(user_id)

    q = QUESTIONS[q_index]
    markup = telebot.types.InlineKeyboardMarkup()

    for opt in q["options"]:
        markup.add(telebot.types.InlineKeyboardButton(opt, callback_data=opt))
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
    if not data:
        bot.answer_callback_query(call.id, "Начни с /start")
        return

    q_index = data["current_q"]
    question = QUESTIONS[q_index]

    if call.data == "custom_input":
        bot.answer_callback_query(call.id)
        msg = bot.send_message(user_id, "✏️ Введи свой вариант:")
        bot.register_next_step_handler(msg, handle_custom_input)
        return

    data["answers"].append(
        {"question": question["question_text"], "answer": call.data})
    data["current_q"] += 1
    bot.answer_callback_query(call.id)

    try:
        bot.delete_message(user_id, data["last_msg_id"])
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
        bot.delete_message(user_id, message.message_id-1)
        bot.delete_message(user_id, message.message_id)
    except Exception:
        pass

    send_question(user_id)


def finish_test(user_id):
    try:
        bot.delete_message(user_id, user_data[user_id]["last_msg_id"])
    except Exception:
        pass

    msg = bot.send_message(
        user_id, "🤖 Обрабатываем твои ответы... подожди пару секунд 💅"
    )
    user_data[user_id]["last_msg_id"] = msg.message_id

    answers_json = json.dumps(
        user_data[user_id]["answers"], ensure_ascii=False, indent=2
    )

    characteristic = generate_characteristic(answers_json)
    image_url = generate_image(characteristic)

    try:
        bot.delete_message(user_id, msg.message_id)
    except Exception:
        pass

    bot.send_photo(user_id, image_url, caption=f"🧠 {characteristic}")
    user_data.pop(user_id, None)


# === LLM (характеристика) ===
def generate_characteristic(answers_json):
    prompt = REPORT_PROMPT.replace("{{answers}}", answers_json)
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "Ты создаёшь абсурдные, острые и саркастичные характеристики девушек по тестам.",
            },
            {"role": "user", "content": prompt},
        ],
    }

    # временный мок
    return "Ты — ходячий хаос и ангел абсурда с лычкой «main cringe character»."


# === IMG GEN (портрет) ===
def generate_image(description):
    prompt = IMG_GEN_PROMPT.replace("{{characteristic}}", description)
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {"model": TEXT2IMG_MODEL, "prompt": prompt}

    # временный мок
    return "https://placekitten.com/512/512"


# telebot.logger.setLevel(logging.DEBUG)
bot.remove_webhook()

bot.infinity_polling(
    timeout=20, long_polling_timeout=20, allowed_updates=["message", "callback_query"]
)
