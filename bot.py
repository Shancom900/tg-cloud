import subprocess
import os
import json
import base64
import logging
import requests
from uuid import uuid4
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (ApplicationBuilder, CommandHandler, MessageHandler,
                          CallbackQueryHandler, filters, ContextTypes)
from google.cloud import firestore

# Install dependencies
subprocess.run(['pip', 'install', 'python-telegram-bot', 'google-cloud-firestore', 'requests'], check=True)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Decode Firestore credentials from base64
base64_credentials = os.environ.get("GOOGLE_CREDENTIALS_BASE64")
if base64_credentials:
    credentials_json = base64.b64decode(base64_credentials).decode("utf-8")
    with open("serviceAccountKey.json", "w") as f:
        f.write(credentials_json)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "serviceAccountKey.json"

# Initialize Firestore
db = firestore.Client()

# Telegram Bot Token
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")  # e.g., "@your_channel"

# Verification API
SHRINKME_API_KEY = os.environ.get("SHRINKME_API_KEY")

# User verification helpers
def is_user_verified(user_id: int) -> bool:
    doc = db.collection("users").document(str(user_id)).get()
    if not doc.exists:
        return False
    expiry = doc.to_dict().get("expiry")
    if not expiry:
        return False
    expiry_dt = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
    return datetime.now() < expiry_dt

def mark_user_verified(user_id: int):
    expiry_time = datetime.now() + timedelta(hours=24)
    db.collection("users").document(str(user_id)).set({
        "verified": True,
        "expiry": expiry_time.strftime("%Y-%m-%d %H:%M:%S")
    })

def shorten_link(user_id):
    try:
        long_url = f"https://veribot.netlify.app/verify.html?uid={user_id}"
        res = requests.get("https://shrinkme.io/api", params={
            "api": SHRINKME_API_KEY,
            "url": long_url
        }, timeout=5)
        data = res.json()
        return data["shortenedUrl"] if data["status"] == "success" else long_url
    except:
        return long_url

# Command: /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_user_verified(user_id):
        await update.message.reply_text("✅ You already have 24-hour access.")
        return
    link = shorten_link(user_id)
    keyboard = [[InlineKeyboardButton("🔐 Verify Now", url=link)]]
    await update.message.reply_text("⚠️ Click to verify (watch ad). You'll get 24-hour access.",
                                    reply_markup=InlineKeyboardMarkup(keyboard))

# Button: Storage
async def storage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    docs = db.collection("files").where("owner", "==", user_id).stream()
    results = [doc.to_dict() for doc in docs]
    if not results:
        await update.message.reply_text("📂 You have no stored files.")
        return
    for file in results:
        await update.message.reply_text(
            f"🗂️ File: {file['type'].capitalize()}\nID: {file['id']}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Delete", callback_data=f"delete:{file['id']}")]
            ])
        )

# Handle delete button
async def delete_file_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, file_id = query.data.split(":")
    doc = db.collection("files").document(file_id).get()
    if not doc.exists:
        await query.edit_message_text("❌ File not found.")
        return
    db.collection("files").document(file_id).delete()
    await query.edit_message_text("✅ File deleted.")

# Command: /money
async def money(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    doc = db.collection("users").document(user_id).get()
    balance = doc.to_dict().get("balance", 0.0) if doc.exists else 0.0
    await update.message.reply_text(f"💰 Your Balance: ₹{balance:.2f}")

# Handle file or message
async def store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_user_verified(user.id):
        await start(update, context)
        return
    unique_id = str(uuid4())[:8]
    file_type = None
    file_info = None
    
    if update.message.video:
        file_type = "video"
        file_info = update.message.video
    elif update.message.photo:
        file_type = "photo"
        file_info = update.message.photo[-1]
    elif update.message.document:
        file_type = "document"
        file_info = update.message.document
    elif update.message.text:
        file_type = "text"
        file_info = update.message.text
    else:
        await update.message.reply_text("❌ Unsupported file type.")
        return

    if file_type == "text":
        message = await context.bot.send_message(chat_id=CHANNEL_ID, text=file_info)
        message_id = message.message_id
    else:
        file_id = file_info.file_id
        message = await context.bot.send_video(chat_id=CHANNEL_ID, video=file_id) if file_type == "video" else \
                  await context.bot.send_photo(chat_id=CHANNEL_ID, photo=file_id) if file_type == "photo" else \
                  await context.bot.send_document(chat_id=CHANNEL_ID, document=file_id)
        message_id = message.message_id

    db.collection("files").document(unique_id).set({
        "id": unique_id,
        "owner": str(user.id),
        "type": file_type,
        "message_id": message_id
    })

    await update.message.reply_text(f"✅ Stored! Access your file here: https://t.me/{CHANNEL_ID.replace('@', '')}/{message_id}")

# Main entry
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("storage", storage))
    app.add_handler(CommandHandler("money", money))
    app.add_handler(CallbackQueryHandler(delete_file_button, pattern=r"^delete:"))
    app.add_handler(MessageHandler(filters.ALL, store))
    logger.info("[Bot] Started polling…")
    app.run_polling()
