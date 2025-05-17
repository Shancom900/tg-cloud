import os
import uuid
import requests
import base64
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from google.cloud import firestore

# ─── Decode Firestore key from environment variable ────────────────────────────
encoded_key = os.environ.get("GOOGLE_CREDENTIALS_BASE64")
if encoded_key:
    with open("serviceAccountKey.json", "wb") as f:
        f.write(base64.b64decode(encoded_key))
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "serviceAccountKey.json"

# ─── Setup ─────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")                      # Required
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))              # Required
BOT_USERNAME = os.environ.get("BOT_USERNAME", "filestoragebot")
SHRINKME_API_KEY = os.environ.get("SHRINKME_API_KEY")       # Required

db = firestore.Client()

# ─── Verification ──────────────────────────────────────────────────────────────
def is_user_verified(user_id: int) -> bool:
    doc = db.collection("users").document(str(user_id)).get()
    if not doc.exists: return False
    expiry = doc.to_dict().get("expiry")
    if not expiry: return False
    return datetime.now() < datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")

def mark_user_verified(user_id: int, referral: str = None):
    expiry_time = datetime.now() + timedelta(hours=24)
    user_ref = db.collection("users").document(str(user_id))

    if not user_ref.get().exists and referral:
        ref_user = db.collection("users").document(referral)
        ref_data = ref_user.get().to_dict() or {}
        ref_user.set({
            "balance": round(ref_data.get("balance", 0) + 0.05, 2),
            "expiry": ref_data.get("expiry"),
            "verified": ref_data.get("verified", True)
        }, merge=True)

    user_ref.set({
        "verified": True,
        "expiry": expiry_time.strftime("%Y-%m-%d %H:%M:%S"),
        "balance": user_ref.get().to_dict().get("balance", 0.0) if user_ref.get().exists else 0.0
    }, merge=True)

def shorten_link(user_id):
    try:
        long_url = f"https://veribot.netlify.app/verify.html?uid={user_id}"
        res = requests.get("https://shrinkme.io/api", params={"api": SHRINKME_API_KEY, "url": long_url}, timeout=5)
        return res.json().get("shortenedUrl", long_url)
    except Exception:
        return long_url

# ─── Bot Handlers ──────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    referral = context.args[0] if context.args else None
    if is_user_verified(user_id):
        await update.message.reply_text("✅ You're verified for 24 hours.")
        return

    link = shorten_link(user_id)
    await update.message.reply_text(
        "🔐 Click to verify (watch ad) for 24-hour access:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Verify Now", url=link)]])
    )
    mark_user_verified(user_id, referral)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📥 Store", callback_data="store")],
        [InlineKeyboardButton("📂 Storage", callback_data="storage")],
        [InlineKeyboardButton("💰 Money", callback_data="money")],
        [InlineKeyboardButton("🗑️ Delete File", callback_data="delete")]
    ]
    await update.message.reply_text("Choose an action:", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_verified(user_id):
        await update.message.reply_text("⚠️ Please verify first using /start.")
        return

    message = update.message
    file = message.document or message.video or message.photo[-1] or message
    unique_id = str(uuid.uuid4())[:8]
    sent = await context.bot.forward_message(chat_id=CHANNEL_ID, from_chat_id=message.chat.id, message_id=message.message_id)

    db.collection("files").document(unique_id).set({
        "user_id": user_id,
        "channel_msg_id": sent.message_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

    await message.reply_text(f"✅ Stored!\n🔗 Link: https://t.me/{BOT_USERNAME}?start={unique_id}")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "store":
        await query.message.reply_text("📤 Send the file you want to store.")
    elif query.data == "storage":
        docs = db.collection("files").where("user_id", "==", user_id).stream()
        has_files = False
        for doc in docs:
            has_files = True
            uid = doc.id
            url = f"https://t.me/{BOT_USERNAME}?start={uid}"
            btn = [[InlineKeyboardButton("🗑️ Delete", callback_data=f"del_{uid}")]]
            await query.message.reply_text(f"📄 {url}", reply_markup=InlineKeyboardMarkup(btn))
        if not has_files:
            await query.message.reply_text("📂 No files stored.")
    elif query.data == "money":
        doc = db.collection("users").document(str(user_id)).get()
        bal = doc.to_dict().get("balance", 0.0) if doc.exists else 0.0
        await query.message.reply_text(f"💸 Your Balance: ₹{bal:.2f}")
    elif query.data.startswith("del_"):
        uid = query.data.split("_")[1]
        db.collection("files").document(uid).delete()
        await query.message.reply_text(f"✅ Deleted file with ID `{uid}`")

# ─── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.Video.ALL | filters.PHOTO, handle_document))
    print("✅ Bot is running...")
    app.run_polling()
