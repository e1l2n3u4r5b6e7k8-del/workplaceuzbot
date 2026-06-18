"""
WorkPlaceUZ — To'liq Telegram Bot
"""
import asyncio
import logging
import sqlite3
import os
from datetime import datetime, timedelta
from math import radians, sin, cos, sqrt, atan2

from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters,
    ContextTypes
)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8974847080:AAFWxv49OIiOY0ksxOeb7nSaj04scMss_8o")
ADMIN_IDS = [7296492641]
DB_PATH = "workplaceuz.db"
COMMISSION = 0.05
DAILY_PASS_PRICE = 3000
REFERRAL_BONUS = 1000

# ── States ───────────────────────────────────────────────────────────────────
(
    REG_PHONE, REG_NAME, REG_ID_CARD, REG_SELFIE,
    JOB_TITLE, JOB_CATEGORY, JOB_SALARY, JOB_LOCATION,
    JOB_DATE, JOB_TIME, JOB_WORKERS, JOB_DESC, JOB_CONFIRM,
    SEARCH_LOCATION, SEARCH_RADIUS, SEARCH_CATEGORY,
    CHAT_MSG,
    RATE_STARS, RATE_COMMENT,
    DISPUTE_REASON, DISPUTE_EVIDENCE,
    WITHDRAW_AMOUNT, WITHDRAW_CARD,
    BROADCAST_MSG,
    ADMIN_BALANCE_USER, ADMIN_BALANCE_AMOUNT,
) = range(26)

# ── Database ──────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        phone TEXT,
        full_name TEXT,
        role TEXT DEFAULT 'seeker',
        kyc_status TEXT DEFAULT 'pending',
        rating REAL DEFAULT 0.0,
        rating_count INTEGER DEFAULT 0,
        balance INTEGER DEFAULT 0,
        is_blocked INTEGER DEFAULT 0,
        id_card_file_id TEXT,
        selfie_file_id TEXT,
        bank_card TEXT,
        last_lat REAL,
        last_lon REAL,
        referral_code TEXT,
        referred_by INTEGER,
        welcome_pass_given INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employer_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL,
        salary INTEGER NOT NULL,
        location_lat REAL,
        location_lon REAL,
        location_name TEXT,
        work_date TEXT,
        work_time TEXT,
        max_workers INTEGER DEFAULT 1,
        description TEXT,
        status TEXT DEFAULT 'active',
        escrow_amount INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        worker_id INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        applied_at TEXT DEFAULT (datetime('now')),
        worker_confirmed INTEGER DEFAULT 0,
        employer_confirmed INTEGER DEFAULT 0,
        UNIQUE(job_id, worker_id)
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        receiver_id INTEGER NOT NULL,
        msg_type TEXT DEFAULT 'text',
        content TEXT,
        file_id TEXT,
        is_read INTEGER DEFAULT 0,
        sent_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        pay_type TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        note TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS daily_passes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ratings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user_id INTEGER NOT NULL,
        to_user_id INTEGER NOT NULL,
        job_id INTEGER NOT NULL,
        stars INTEGER NOT NULL,
        comment TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(from_user_id, job_id)
    );
    CREATE TABLE IF NOT EXISTS disputes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        reporter_id INTEGER NOT NULL,
        reason TEXT,
        evidence_ids TEXT,
        status TEXT DEFAULT 'open',
        admin_decision TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()
    conn.close()

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ── DB Helpers ────────────────────────────────────────────────────────────────
def get_user(tid):
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (tid,)).fetchone()
        return dict(row) if row else None

def upsert_user(tid, **kw):
    with db() as conn:
        cols = list(kw.keys())
        vals = list(kw.values())
        if cols:
            sets = ",".join(f"{c}=?" for c in cols)
            conn.execute(
                f"INSERT INTO users(telegram_id,{','.join(cols)}) VALUES(?{',?'*len(cols)}) "
                f"ON CONFLICT(telegram_id) DO UPDATE SET {sets}",
                [tid]+vals+vals
            )
        else:
            conn.execute("INSERT OR IGNORE INTO users(telegram_id) VALUES(?)", (tid,))
        conn.commit()

def update_user(tid, **kw):
    with db() as conn:
        sets = ",".join(f"{k}=?" for k in kw)
        conn.execute(f"UPDATE users SET {sets} WHERE telegram_id=?", [*kw.values(), tid])
        conn.commit()

def add_balance(tid, amount):
    with db() as conn:
        conn.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?", (amount, tid))
        conn.commit()

def has_valid_pass(uid):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM daily_passes WHERE user_id=? AND expires_at > datetime('now') LIMIT 1",
            (uid,)
        ).fetchone()
        return row is not None

def grant_pass(uid):
    expires = (datetime.utcnow() + timedelta(hours=24)).isoformat()
    with db() as conn:
        conn.execute("INSERT INTO daily_passes(user_id,expires_at) VALUES(?,?)", (uid, expires))
        conn.commit()

def create_job(employer_id, **kw):
    with db() as conn:
        cols = ["employer_id"] + list(kw.keys())
        vals = [employer_id] + list(kw.values())
        cur = conn.execute(
            f"INSERT INTO jobs({','.join(cols)}) VALUES({','.join('?'*len(cols))})", vals
        )
        conn.commit()
        return cur.lastrowid

def get_job(job_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

def update_job(job_id, **kw):
    with db() as conn:
        sets = ",".join(f"{k}=?" for k in kw)
        conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*kw.values(), job_id])
        conn.commit()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2-lat1)
    dlon = radians(lon2-lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

def jobs_near(lat, lon, radius_km, category=None):
    with db() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE status='active'").fetchall()
    result = []
    for r in rows:
        r = dict(r)
        if r.get("location_lat") is None:
            continue
        dist = haversine(lat, lon, r["location_lat"], r["location_lon"])
        if dist <= radius_km:
            if category and category != "Barchasi" and r["category"] != category:
                continue
            r["distance_km"] = round(dist, 2)
            result.append(r)
    result.sort(key=lambda x: x["distance_km"])
    return result

def create_application(job_id, worker_id):
    try:
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO applications(job_id,worker_id) VALUES(?,?)", (job_id, worker_id)
            )
            conn.commit()
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None

def get_application(app_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        return dict(row) if row else None

def get_app_by_job_worker(job_id, worker_id):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE job_id=? AND worker_id=?", (job_id, worker_id)
        ).fetchone()
        return dict(row) if row else None

def update_application(app_id, **kw):
    with db() as conn:
        sets = ",".join(f"{k}=?" for k in kw)
        conn.execute(f"UPDATE applications SET {sets} WHERE id=?", [*kw.values(), app_id])
        conn.commit()

def get_worker_apps(worker_id):
    with db() as conn:
        rows = conn.execute(
            "SELECT a.*,j.title,j.salary,j.location_name,j.work_date,j.employer_id "
            "FROM applications a JOIN jobs j ON a.job_id=j.id "
            "WHERE a.worker_id=? ORDER BY a.applied_at DESC", (worker_id,)
        ).fetchall()
        return [dict(r) for r in rows]

def get_job_apps(job_id):
    with db() as conn:
        rows = conn.execute(
            "SELECT a.*,u.full_name,u.rating,u.kyc_status "
            "FROM applications a JOIN users u ON a.worker_id=u.telegram_id "
            "WHERE a.job_id=? ORDER BY a.applied_at", (job_id,)
        ).fetchall()
        return [dict(r) for r in rows]

def save_message(job_id, sender_id, receiver_id, msg_type, content, file_id=None):
    with db() as conn:
        conn.execute(
            "INSERT INTO messages(job_id,sender_id,receiver_id,msg_type,content,file_id) VALUES(?,?,?,?,?,?)",
            (job_id, sender_id, receiver_id, msg_type, content, file_id)
        )
        conn.commit()

def get_chats(user_id):
    with db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT m.job_id, j.title,
               SUM(CASE WHEN m.receiver_id=? AND m.is_read=0 THEN 1 ELSE 0 END) AS unread
               FROM messages m JOIN jobs j ON m.job_id=j.id
               WHERE m.sender_id=? OR m.receiver_id=?
               GROUP BY m.job_id ORDER BY MAX(m.sent_at) DESC""",
            (user_id, user_id, user_id)
        ).fetchall()
        return [dict(r) for r in rows]

def get_chat_messages(job_id, limit=20):
    with db() as conn:
        rows = conn.execute(
            "SELECT m.*,u.full_name FROM messages m JOIN users u ON m.sender_id=u.telegram_id "
            "WHERE m.job_id=? ORDER BY m.sent_at DESC LIMIT ?", (job_id, limit)
        ).fetchall()
        return [dict(r) for r in rows][::-1]

def mark_read(job_id, receiver_id):
    with db() as conn:
        conn.execute(
            "UPDATE messages SET is_read=1 WHERE job_id=? AND receiver_id=?", (job_id, receiver_id)
        )
        conn.commit()

def create_rating(from_u, to_u, job_id, stars, comment):
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO ratings(from_user_id,to_user_id,job_id,stars,comment) VALUES(?,?,?,?,?)",
                (from_u, to_u, job_id, stars, comment)
            )
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        return False

def update_rating_avg(uid, stars):
    with db() as conn:
        row = conn.execute(
            "SELECT rating,rating_count FROM users WHERE telegram_id=?", (uid,)
        ).fetchone()
        if row:
            old_r, old_c = row["rating"] or 0.0, row["rating_count"] or 0
            new_c = old_c + 1
            new_r = (old_r * old_c + stars) / new_c
            conn.execute(
                "UPDATE users SET rating=?,rating_count=? WHERE telegram_id=?",
                (new_r, new_c, uid)
            )
            conn.commit()
            if new_r < 2.5 and new_c >= 3:
                conn.execute("UPDATE users SET is_blocked=1 WHERE telegram_id=?", (uid,))
                conn.commit()

def create_dispute(job_id, reporter_id, reason, evidence=""):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO disputes(job_id,reporter_id,reason,evidence_ids) VALUES(?,?,?,?)",
            (job_id, reporter_id, reason, evidence)
        )
        conn.commit()
        return cur.lastrowid

def stats():
    with db() as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='active'").fetchone()[0]
        rev = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM payments WHERE pay_type='commission' AND status='completed'"
        ).fetchone()[0]
        return users, jobs, active, rev

# ── Keyboards ─────────────────────────────────────────────────────────────────
def main_menu_kb(role):
    if role == "seeker":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔎 Ish qidirish", callback_data="find_jobs")],
            [InlineKeyboardButton("📋 Mening arizalarim", callback_data="my_apps")],
            [InlineKeyboardButton("💬 Xabarlar", callback_data="my_chats")],
            [InlineKeyboardButton("💰 Balans", callback_data="balance")],
            [InlineKeyboardButton("👤 Profil", callback_data="profile")],
            [InlineKeyboardButton("🔄 Ish beruvchiga o'tish", callback_data="switch_role")],
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Ish e'lon qilish", callback_data="post_job")],
            [InlineKeyboardButton("📋 Mening ishlarim", callback_data="my_jobs")],
            [InlineKeyboardButton("💬 Xabarlar", callback_data="my_chats")],
            [InlineKeyboardButton("💰 Balans", callback_data="balance")],
            [InlineKeyboardButton("👤 Profil", callback_data="profile")],
            [InlineKeyboardButton("🔄 Ishchiga o'tish", callback_data="switch_role")],
        ])

def back_kb(cb="main_menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data=cb)]])

# ── Helpers ───────────────────────────────────────────────────────────────────
async def safe_delete(msg):
    try:
        await msg.delete()
    except Exception:
        pass

async def send_main_menu(update, context, user=None):
    tid = update.effective_user.id
    if user is None:
        user = get_user(tid)
    role = user.get("role", "seeker") if user else "seeker"
    text = (
        f"👋 Xush kelibsiz, {user.get('full_name','')  if user else ''}!\n\n"
        f"🎭 Rol: {'🔎 Ish izlovchi' if role=='seeker' else '🏢 Ish beruvchi'}\n"
        f"💰 Balans: {user.get('balance',0) if user else 0:,} so'm\n"
        f"⭐ Reyting: {user.get('rating',0.0) if user else 0:.1f}"
    )
    kb = main_menu_kb(role)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb)
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)

async def notify(context, uid, text, kb=None):
    try:
        await context.bot.send_message(uid, text, reply_markup=kb)
    except Exception:
        pass

# ── /start & Registration ─────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    user = get_user(tid)

    # Handle referral
    args = context.args
    if args and args[0].startswith("ref_"):
        ref_id = args[0][4:]
        try:
            context.user_data["referred_by"] = int(ref_id)
        except Exception:
            pass

    if user and user.get("full_name"):
        if user.get("is_blocked"):
            await update.message.reply_text("❌ Hisobingiz bloklangan. Admin bilan bog'laning.")
            return ConversationHandler.END
        await send_main_menu(update, context, user)
        return ConversationHandler.END

    upsert_user(tid)
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Telefon raqamni ulashish", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text(
        "🇺🇿 *WorkPlaceUZ* ga xush kelibsiz!\n\n"
        "Bu O'zbekistondagi ishchilar va ish beruvchilar uchun xavfsiz platforma.\n\n"
        "📱 Ro'yxatdan o'tish uchun telefon raqamingizni yuboring:",
        parse_mode="Markdown", reply_markup=kb
    )
    return REG_PHONE

async def reg_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()
    update_user(tid, phone=phone)
    await update.message.reply_text(
        "✅ Telefon qabul qilindi!\n\n👤 Ism va familiyangizni kiriting:",
        reply_markup=ReplyKeyboardRemove()
    )
    return REG_NAME

async def reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    name = update.message.text.strip()
    if len(name) < 3:
        await update.message.reply_text("❌ Iltimos, to'liq ism-familiya kiriting:")
        return REG_NAME
    update_user(tid, full_name=name)
    await update.message.reply_text(
        f"✅ Ism: {name}\n\n📷 Shaxsiy guvohnomangizning rasmini yuboring:"
    )
    return REG_ID_CARD

async def reg_id_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    if not update.message.photo:
        await update.message.reply_text("❌ Iltimos, rasm yuboring:")
        return REG_ID_CARD
    file_id = update.message.photo[-1].file_id
    update_user(tid, id_card_file_id=file_id)
    await update.message.reply_text("✅ Guvohnoma qabul qilindi!\n\n🤳 Endi selfie (o'z rasmi) yuboring:")
    return REG_SELFIE

async def reg_selfie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    if not update.message.photo:
        await update.message.reply_text("❌ Iltimos, selfie yuboring:")
        return REG_SELFIE
    file_id = update.message.photo[-1].file_id
    ref_code = f"ref_{tid}"
    update_user(tid, selfie_file_id=file_id, referral_code=ref_code, welcome_pass_given=1)
    grant_pass(tid)

    referred_by = context.user_data.pop("referred_by", None)
    if referred_by:
        update_user(tid, referred_by=referred_by)
        add_balance(referred_by, REFERRAL_BONUS)
        await notify(context, referred_by, f"🎁 Referalingiz ro'yxatdan o'tdi! +{REFERRAL_BONUS:,} so'm bonusi berildi!")

    for admin_id in ADMIN_IDS:
        user = get_user(tid)
        await notify(context, admin_id,
            f"🆕 Yangi foydalanuvchi!\n"
            f"👤 {user.get('full_name')}\n"
            f"📱 {user.get('phone')}\n"
            f"🆔 ID: {tid}"
        )

    user = get_user(tid)
    await update.message.reply_text(
        f"🎉 Ro'yxatdan o'tdingiz!\n\n"
        f"👤 {user.get('full_name')}\n"
        f"🎟 1 ta bepul kunlik pass berildi!\n"
        f"🕐 KYC hujjatlaringiz ko'rib chiqilmoqda (1-24 soat).\n\n"
        f"🔗 Referral havolangiz:\n`https://t.me/WorkPlaceUZbot?start={ref_code}`",
        parse_mode="Markdown"
    )
    await send_main_menu(update, context, user)
    return ConversationHandler.END

# ── Main Menu Callbacks ───────────────────────────────────────────────────────
async def main_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = get_user(q.from_user.id)
    if not user:
        await q.message.reply_text("❌ Avval ro'yxatdan o'ting: /start")
        return
    await send_main_menu(update, context, user)

async def switch_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tid = q.from_user.id
    user = get_user(tid)
    new_role = "employer" if user["role"] == "seeker" else "seeker"
    update_user(tid, role=new_role)
    user = get_user(tid)
    role_text = "🏢 Ish beruvchi" if new_role == "employer" else "🔎 Ish izlovchi"
    await q.answer(f"✅ Rol o'zgardi: {role_text}", show_alert=True)
    await send_main_menu(update, context, user)

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tid = q.from_user.id
    user = get_user(tid)
    kyc_emoji = {"pending": "⏳", "verified": "✅", "rejected": "❌"}.get(user.get("kyc_status","pending"), "⏳")
    role_text = "🏢 Ish beruvchi" if user.get("role") == "employer" else "🔎 Ish izlovchi"
    ref_link = f"https://t.me/WorkPlaceUZbot?start={user.get('referral_code','')}"
    text = (
        f"👤 *Profil*\n\n"
        f"📛 Ism: {user.get('full_name','')}\n"
        f"📱 Tel: {user.get('phone','')}\n"
        f"🎭 Rol: {role_text}\n"
        f"🔐 KYC: {kyc_emoji} {user.get('kyc_status','pending')}\n"
        f"⭐ Reyting: {user.get('rating',0.0):.1f} ({user.get('rating_count',0)} baho)\n"
        f"💰 Balans: {user.get('balance',0):,} so'm\n\n"
        f"🔗 Referral: `{ref_link}`"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Karta qo'shish", callback_data="add_card")],
        [InlineKeyboardButton("💸 Pul chiqarish", callback_data="withdraw")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")],
    ])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tid = q.from_user.id
    user = get_user(tid)
    with db() as conn:
        pays = conn.execute(
            "SELECT * FROM payments WHERE user_id=? ORDER BY created_at DESC LIMIT 5", (tid,)
        ).fetchall()
    hist = ""
    for p in pays:
        p = dict(p)
        hist += f"\n• {p['pay_type']}: {p['amount']:,} so'm ({p['status']})"
    pass_status = "✅ Faol" if has_valid_pass(tid) else "❌ Yoq"
    hist_text = hist if hist else " Yoq"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Kunlik pass sotib olish (3,000 so'm)", callback_data="buy_pass")],
        [InlineKeyboardButton("💸 Pul chiqarish", callback_data="withdraw")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")],
    ])
    await q.edit_message_text(
        f"💰 *Balans*\n\n"
        f"💵 Joriy balans: *{user.get('balance',0):,} so'm*\n"
        f"🎟 Kunlik pass: {pass_status}\n\n"
        f"📋 So'nggi tranzaksiyalar:{hist_text}",
        parse_mode="Markdown", reply_markup=kb
    )

async def buy_pass_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if has_valid_pass(q.from_user.id):
        await q.answer("✅ Sizda faol kunlik pass mavjud!", show_alert=True)
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Payme", url="https://payme.uz"),
         InlineKeyboardButton("💳 Click", url="https://click.uz")],
        [InlineKeyboardButton("💳 Uzum Pay", url="https://uzum.uz")],
        [InlineKeyboardButton("✅ To'lov qildim", callback_data="pass_paid")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="balance")],
    ])
    await q.edit_message_text(
        f"🎟 *Kunlik Pass — 3,000 so'm*\n\n"
        f"Bu pass 24 soat davomida istalgan ishga murojaat qilish imkonini beradi.\n\n"
        f"To'lov qiling va '✅ To'lov qildim' tugmasini bosing:",
        parse_mode="Markdown", reply_markup=kb
    )

async def pass_paid_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tid = q.from_user.id
    grant_pass(tid)
    with db() as conn:
        conn.execute(
            "INSERT INTO payments(user_id,amount,pay_type,status,note) VALUES(?,?,?,?,?)",
            (tid, 3000, "daily_pass", "completed", "Kunlik pass")
        )
        conn.commit()
    await q.answer("✅ Kunlik pass faollashtirildi! 24 soat amal qiladi.", show_alert=True)
    await send_main_menu(update, context, get_user(tid))

# ── Job Search ────────────────────────────────────────────────────────────────
async def find_jobs_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Joylashuvni ulashish", request_location=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await q.message.reply_text(
        "🔎 *Ish qidirish*\n\nYaqin atrofdagi ishlarni topish uchun joylashuvingizni yuboring:",
        parse_mode="Markdown", reply_markup=kb
    )
    return SEARCH_LOCATION

async def search_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.location:
        lat = update.message.location.latitude
        lon = update.message.location.longitude
        update_user(update.effective_user.id, last_lat=lat, last_lon=lon)
        context.user_data["search_lat"] = lat
        context.user_data["search_lon"] = lon
    else:
        await update.message.reply_text("❌ Iltimos, joylashuvni yuboring:", reply_markup=ReplyKeyboardRemove())
        return SEARCH_LOCATION

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("500m", callback_data="radius_0.5"),
         InlineKeyboardButton("1 km", callback_data="radius_1")],
        [InlineKeyboardButton("3 km", callback_data="radius_3"),
         InlineKeyboardButton("5 km", callback_data="radius_5")],
        [InlineKeyboardButton("10 km", callback_data="radius_10")],
    ])
    await update.message.reply_text(
        "📏 Qidiruv radiusini tanlang:",
        reply_markup=ReplyKeyboardRemove()
    )
    await update.message.reply_text("👇 Radiusni tanlang:", reply_markup=kb)
    return SEARCH_RADIUS

async def search_radius(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    radius = float(q.data.split("_")[1])
    context.user_data["search_radius"] = radius
    cats = ["Barchasi", "Talaba", "Ayol", "Erkak", "Nogironligi bor", "Pensioner"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(c, callback_data=f"cat_{c}")] for c in cats
    ])
    await q.edit_message_text("📂 Kategoriyani tanlang:", reply_markup=kb)
    return SEARCH_CATEGORY

async def search_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    category = q.data[4:]
    lat = context.user_data.get("search_lat")
    lon = context.user_data.get("search_lon")
    radius = context.user_data.get("search_radius", 5)
    jobs = jobs_near(lat, lon, radius, category)
    if not jobs:
        kb = back_kb("main_menu")
        await q.edit_message_text("😔 Yaqin atrofda ish topilmadi. Radiusni kengaytiring.", reply_markup=kb)
        return ConversationHandler.END

    await q.edit_message_text(f"✅ {len(jobs)} ta ish topildi! Ko'rsatilmoqda...")
    tid = q.from_user.id
    for job in jobs[:10]:
        employer = get_user(job["employer_id"])
        text = (
            f"💼 *{job['title']}*\n"
            f"📂 {job['category']}\n"
            f"💰 {job['salary']:,} so'm\n"
            f"📅 {job.get('work_date','')}\n"
            f"⏰ {job.get('work_time','')}\n"
            f"👷 {job.get('max_workers',1)} ta ishchi\n"
            f"📏 {job['distance_km']} km uzoqlikda\n"
            f"⭐ Ish beruvchi: {employer.get('rating',0):.1f}\n"
            f"📝 {job.get('description','')}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Murojaat qilish", callback_data=f"apply_{job['id']}")],
            [InlineKeyboardButton("💬 Chat", callback_data=f"chat_job_{job['id']}")],
        ])
        if job.get("location_lat"):
            await context.bot.send_venue(
                tid,
                latitude=job["location_lat"],
                longitude=job["location_lon"],
                title=job["title"],
                address=job.get("location_name", "")
            )
        await context.bot.send_message(tid, text, parse_mode="Markdown", reply_markup=kb)
    await context.bot.send_message(
        tid, "🏠 Bosh menyuga qaytish:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Bosh menyu", callback_data="main_menu")]])
    )
    return ConversationHandler.END

# ── Apply for Job ─────────────────────────────────────────────────────────────
async def apply_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tid = q.from_user.id
    job_id = int(q.data.split("_")[1])
    job = get_job(job_id)
    user = get_user(tid)

    if user.get("is_blocked"):
        await q.answer("❌ Hisobingiz bloklangan!", show_alert=True)
        return

    if tid == job["employer_id"]:
        await q.answer("❌ O'z ishingizga murojaat qila olmaysiz!", show_alert=True)
        return

    if not has_valid_pass(tid):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Pass sotib olish (3,000 so'm)", callback_data="buy_pass")]
        ])
        await q.message.reply_text(
            "❌ Murojaat qilish uchun kunlik pass kerak!\n\n🎟 3,000 so'm to'lang:",
            reply_markup=kb
        )
        return

    app_id = create_application(job_id, tid)
    if app_id is None:
        await q.answer("⚠️ Siz allaqachon murojaat qilgansiz!", show_alert=True)
        return

    employer = get_user(job["employer_id"])
    await notify(context, job["employer_id"],
        f"📬 Yangi murojaat!\n"
        f"💼 Ish: {job['title']}\n"
        f"👤 Ishchi: {user.get('full_name')}\n"
        f"⭐ Reyting: {user.get('rating',0):.1f}\n",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Qabul qilish", callback_data=f"acc_app_{app_id}"),
             InlineKeyboardButton("❌ Rad etish", callback_data=f"rej_app_{app_id}")]
        ])
    )
    await q.answer("✅ Murojaat yuborildi! Ish beruvchi javobini kuting.", show_alert=True)

# ── My Applications ───────────────────────────────────────────────────────────
async def my_apps_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tid = q.from_user.id
    apps = get_worker_apps(tid)
    if not apps:
        await q.edit_message_text(
            "📋 Hozircha murojaat yo'q.",
            reply_markup=back_kb("main_menu")
        )
        return
    status_map = {"pending":"⏳","accepted":"✅","rejected":"❌","completed":"🏁"}
    buttons = []
    for a in apps:
        s = status_map.get(a["status"],"⏳")
        buttons.append([InlineKeyboardButton(
            f"{s} {a['title']} — {a['salary']:,} so'm",
            callback_data=f"view_app_{a['id']}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")])
    await q.edit_message_text("📋 *Mening arizalarim:*", parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup(buttons))

async def view_app_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    app_id = int(q.data.split("_")[2])
    app = get_application(app_id)
    job = get_job(app["job_id"])
    status_map = {"pending":"⏳ Kutilmoqda","accepted":"✅ Qabul qilindi","rejected":"❌ Rad etildi","completed":"🏁 Bajarildi"}
    text = (
        f"💼 *{job['title']}*\n"
        f"💰 {job['salary']:,} so'm\n"
        f"📅 {job.get('work_date','')}\n"
        f"⏰ {job.get('work_time','')}\n"
        f"📊 Holat: {status_map.get(app['status'],'⏳')}"
    )
    buttons = [[InlineKeyboardButton("🔙 Orqaga", callback_data="my_apps")]]
    if app["status"] == "accepted" and not app["worker_confirmed"]:
        buttons.insert(0, [InlineKeyboardButton("✅ Ishni tugatdim", callback_data=f"done_app_{app_id}")])
        buttons.insert(0, [InlineKeyboardButton("💬 Chat", callback_data=f"chat_job_{job['id']}")])
    if app["status"] == "accepted":
        buttons.insert(0, [InlineKeyboardButton("⚖️ Shikoyat", callback_data=f"dispute_app_{app_id}")])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def done_app_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    app_id = int(q.data.split("_")[2])
    app = get_application(app_id)
    job = get_job(app["job_id"])
    update_application(app_id, worker_confirmed=1)
    await notify(context, job["employer_id"],
        f"🔔 Ishchi ishni yakunlaganini bildirdi!\n💼 {job['title']}\n\nTasdiqlansinmi?",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Tasdiqlayman", callback_data=f"confirm_app_{app_id}"),
             InlineKeyboardButton("❌ Rad etaman", callback_data=f"dispute_app_{app_id}")]
        ])
    )
    await q.answer("✅ Ish beruvchiga xabar yuborildi!", show_alert=True)

# ── Employer Accept/Reject ────────────────────────────────────────────────────
async def acc_app_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    app_id = int(q.data.split("_")[2])
    app = get_application(app_id)
    job = get_job(app["job_id"])
    update_application(app_id, status="accepted")
    await notify(context, app["worker_id"],
        f"🎉 Arizangiz qabul qilindi!\n💼 {job['title']}\n💰 {job['salary']:,} so'm\n\n"
        f"Ishni bajaring va 'Ishni tugatdim' tugmasini bosing.",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Chat", callback_data=f"chat_job_{job['id']}")]
        ])
    )
    await q.edit_message_text(f"✅ Qabul qilindi! Ishchi xabardor qilindi.")

async def rej_app_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    app_id = int(q.data.split("_")[2])
    app = get_application(app_id)
    job = get_job(app["job_id"])
    update_application(app_id, status="rejected")
    await notify(context, app["worker_id"],
        f"😔 Arizangiz rad etildi.\n💼 {job['title']}\n\nBoshqa ishlarni qidiring."
    )
    await q.edit_message_text("❌ Rad etildi.")

async def confirm_app_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    app_id = int(q.data.split("_")[2])
    app = get_application(app_id)
    job = get_job(app["job_id"])
    salary = job["salary"]
    commission = int(salary * COMMISSION)
    worker_pay = salary - commission
    update_application(app_id, status="completed", employer_confirmed=1)
    update_job(job["id"], status="completed")
    add_balance(app["worker_id"], worker_pay)
    with db() as conn:
        conn.execute(
            "INSERT INTO payments(user_id,amount,pay_type,status,note) VALUES(?,?,?,?,?)",
            (app["worker_id"], worker_pay, "job_payment", "completed", f"Ish #{job['id']}")
        )
        conn.execute(
            "INSERT INTO payments(user_id,amount,pay_type,status,note) VALUES(?,?,?,?,?)",
            (job["employer_id"], commission, "commission", "completed", f"Komissiya ish #{job['id']}")
        )
        conn.commit()
    await notify(context, app["worker_id"],
        f"🎉 To'lov qabul qilindi!\n"
        f"💰 +{worker_pay:,} so'm balansingizga tushdi!\n"
        f"💼 Ish: {job['title']}",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ Baho berish", callback_data=f"rate_emp_{job['id']}_{job['employer_id']}")]
        ])
    )
    await q.edit_message_text(
        f"✅ Ish tasdiqlandi!\n💰 {worker_pay:,} so'm ishchiga o'tkazildi.\n📊 Komissiya: {commission:,} so'm",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ Baho berish", callback_data=f"rate_wkr_{job['id']}_{app['worker_id']}")]
        ])
    )

# ── Post Job ──────────────────────────────────────────────────────────────────
async def post_job_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["new_job"] = {}
    await q.edit_message_text(
        "➕ *Yangi ish e'loni*\n\n1️⃣ Ish nomini kiriting (masalan: Yuk tashish, Uy tozalash):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor qilish", callback_data="main_menu")]])
    )
    return JOB_TITLE

async def job_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if len(title) < 3:
        await update.message.reply_text("❌ Iltimos, aniqroq nom kiriting:")
        return JOB_TITLE
    context.user_data["new_job"]["title"] = title
    cats = ["Talaba","Ayol","Erkak","Nogironligi bor","Pensioner","Barchasi"]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(c, callback_data=f"jcat_{c}")] for c in cats])
    await update.message.reply_text(f"✅ Nom: {title}\n\n2️⃣ Kategoriyani tanlang:", reply_markup=kb)
    return JOB_CATEGORY

async def job_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cat = q.data[5:]
    context.user_data["new_job"]["category"] = cat
    await q.edit_message_text(f"✅ Kategoriya: {cat}\n\n3️⃣ Maosh miqdorini so'mda kiriting:")
    return JOB_SALARY

async def job_salary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        salary = int(update.message.text.strip().replace(" ", "").replace(",", ""))
        if salary < 1000:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ To'g'ri miqdor kiriting (masalan: 50000):")
        return JOB_SALARY
    context.user_data["new_job"]["salary"] = salary
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Joylashuvni ulashish", request_location=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text(
        f"✅ Maosh: {salary:,} so'm\n\n4️⃣ Ish joylashuvini yuboring:",
        reply_markup=kb
    )
    return JOB_LOCATION

async def job_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.location:
        lat = update.message.location.latitude
        lon = update.message.location.longitude
        context.user_data["new_job"]["location_lat"] = lat
        context.user_data["new_job"]["location_lon"] = lon
        context.user_data["new_job"]["location_name"] = f"{lat:.4f},{lon:.4f}"
    elif update.message.venue:
        context.user_data["new_job"]["location_lat"] = update.message.venue.location.latitude
        context.user_data["new_job"]["location_lon"] = update.message.venue.location.longitude
        context.user_data["new_job"]["location_name"] = update.message.venue.title
    else:
        await update.message.reply_text("❌ Iltimos, joylashuv yuboring:")
        return JOB_LOCATION
    await update.message.reply_text(
        "✅ Joylashuv qabul qilindi!\n\n5️⃣ Ish sanasini kiriting (masalan: 20.06.2026):",
        reply_markup=ReplyKeyboardRemove()
    )
    return JOB_DATE

async def job_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date = update.message.text.strip()
    context.user_data["new_job"]["work_date"] = date
    await update.message.reply_text(f"✅ Sana: {date}\n\n6️⃣ Ish vaqtini kiriting (masalan: 09:00-18:00):")
    return JOB_TIME

async def job_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time = update.message.text.strip()
    context.user_data["new_job"]["work_time"] = time
    await update.message.reply_text(f"✅ Vaqt: {time}\n\n7️⃣ Nechta ishchi kerak? (raqam kiriting):")
    return JOB_WORKERS

async def job_workers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        workers = int(update.message.text.strip())
        if workers < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ To'g'ri raqam kiriting:")
        return JOB_WORKERS
    context.user_data["new_job"]["max_workers"] = workers
    await update.message.reply_text(f"✅ Ishchilar soni: {workers}\n\n8️⃣ Ish haqida qisqacha ma'lumot kiriting:")
    return JOB_DESC

async def job_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    context.user_data["new_job"]["description"] = desc
    job = context.user_data["new_job"]
    text = (
        f"📋 *Ish e'loni xulasasi:*\n\n"
        f"💼 Nom: {job['title']}\n"
        f"📂 Kategoriya: {job['category']}\n"
        f"💰 Maosh: {job['salary']:,} so'm\n"
        f"📅 Sana: {job['work_date']}\n"
        f"⏰ Vaqt: {job['work_time']}\n"
        f"👷 Ishchilar: {job['max_workers']}\n"
        f"📝 Tavsif: {desc}\n\n"
        f"⚠️ E'lon berishdan oldin {job['salary']:,} so'm garov sifatida yechiladi."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ E'lon berish", callback_data="confirm_job"),
         InlineKeyboardButton("❌ Bekor", callback_data="main_menu")]
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    return JOB_CONFIRM

async def confirm_job_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tid = q.from_user.id
    user = get_user(tid)
    job_data = context.user_data.get("new_job", {})
    if not job_data:
        await q.edit_message_text("❌ Ma'lumot topilmadi. Qaytadan boshlang.")
        return ConversationHandler.END
    salary = job_data.get("salary", 0)
    if user.get("balance", 0) < salary:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Payme", url="https://payme.uz")],
            [InlineKeyboardButton("💳 Click", url="https://click.uz")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")],
        ])
        await q.edit_message_text(
            f"❌ Balansingiz yetarli emas!\n"
            f"💰 Kerak: {salary:,} so'm\n"
            f"💵 Balans: {user['balance']:,} so'm\n\n"
            f"Avval hisobingizni to'ldiring:",
            reply_markup=kb
        )
        return ConversationHandler.END
    add_balance(tid, -salary)
    job_id = create_job(tid, **job_data, escrow_amount=salary)
    update_job(job_id, escrow_amount=salary)
    context.user_data.pop("new_job", None)
    for admin_id in ADMIN_IDS:
        await notify(context, admin_id, f"📢 Yangi ish e'loni #{job_id}: {job_data['title']}")
    await q.edit_message_text(
        f"🎉 Ish e'loni #{job_id} muvaffaqiyatli joylashtirildi!\n"
        f"💰 {salary:,} so'm garovga olindi.\n"
        f"Arizalarni 'Mening ishlarim' bo'limida kuzating.",
        reply_markup=back_kb("main_menu")
    )
    return ConversationHandler.END

# ── My Jobs (Employer) ────────────────────────────────────────────────────────
async def my_jobs_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tid = q.from_user.id
    with db() as conn:
        jobs = conn.execute(
            "SELECT * FROM jobs WHERE employer_id=? ORDER BY created_at DESC", (tid,)
        ).fetchall()
    if not jobs:
        await q.edit_message_text("📋 Hozircha e'lon yo'q.", reply_markup=back_kb("main_menu"))
        return
    status_map = {"active":"🟢","completed":"🏁","cancelled":"❌"}
    buttons = []
    for j in jobs:
        j = dict(j)
        s = status_map.get(j["status"],"🟢")
        buttons.append([InlineKeyboardButton(
            f"{s} {j['title']} — {j['salary']:,} so'm",
            callback_data=f"view_job_{j['id']}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")])
    await q.edit_message_text("📋 *Mening ishlarim:*", parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup(buttons))

async def view_job_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    job_id = int(q.data.split("_")[2])
    job = get_job(job_id)
    apps = get_job_apps(job_id)
    pending = [a for a in apps if a["status"]=="pending"]
    accepted = [a for a in apps if a["status"]=="accepted"]
    text = (
        f"💼 *{job['title']}*\n"
        f"📊 Holat: {job['status']}\n"
        f"💰 Maosh: {job['salary']:,} so'm\n"
        f"📅 {job.get('work_date','')}\n"
        f"👥 Arizalar: {len(apps)} ta (⏳{len(pending)} | ✅{len(accepted)})"
    )
    buttons = []
    if pending:
        buttons.append([InlineKeyboardButton(f"👥 Arizalarni ko'rish ({len(pending)})", callback_data=f"job_apps_{job_id}")])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="my_jobs")])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def job_apps_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    job_id = int(q.data.split("_")[2])
    apps = get_job_apps(job_id)
    pending = [a for a in apps if a["status"]=="pending"]
    if not pending:
        await q.edit_message_text("⏳ Kutayotgan arizalar yo'q.", reply_markup=back_kb(f"view_job_{job_id}"))
        return
    buttons = []
    for a in pending:
        buttons.append([
            InlineKeyboardButton(f"✅ {a['full_name']} (⭐{a['rating']:.1f})", callback_data=f"acc_app_{a['id']}"),
            InlineKeyboardButton("❌", callback_data=f"rej_app_{a['id']}")
        ])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data=f"view_job_{job_id}")])
    await q.edit_message_text("👥 *Arizalar:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

# ── Chat ──────────────────────────────────────────────────────────────────────
async def my_chats_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tid = q.from_user.id
    chats = get_chats(tid)
    if not chats:
        await q.edit_message_text("💬 Hozircha xabarlar yo'q.", reply_markup=back_kb("main_menu"))
        return
    buttons = []
    for c in chats:
        unread = f" 🔴{c['unread']}" if c.get("unread") else ""
        buttons.append([InlineKeyboardButton(f"💬 {c['title']}{unread}", callback_data=f"chat_job_{c['job_id']}")])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")])
    await q.edit_message_text("💬 *Xabarlar:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def chat_job_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    job_id = int(q.data.split("_")[2])
    tid = q.from_user.id
    job = get_job(job_id)
    mark_read(job_id, tid)
    msgs = get_chat_messages(job_id)
    chat_text = f"💬 *{job['title']}* — Chat\n\n"
    if msgs:
        for m in msgs[-10:]:
            sender = "Siz" if m["sender_id"] == tid else m.get("full_name","")
            chat_text += f"*{sender}:* {m.get('content','📎')}\n"
    else:
        chat_text += "Hozircha xabar yo'q. Birinchi xabarni yuboring!"
    context.user_data["active_chat_job"] = job_id
    app = get_app_by_job_worker(job_id, tid)
    if not app:
        with db() as conn:
            app_row = conn.execute(
                "SELECT * FROM applications WHERE job_id=? LIMIT 1", (job_id,)
            ).fetchone()
            app = dict(app_row) if app_row else None
    context.user_data["active_chat_receiver"] = (
        job["employer_id"] if tid != job["employer_id"] else (app["worker_id"] if app else None)
    )
    buttons = [
        [InlineKeyboardButton("🔙 Orqaga", callback_data="my_chats")]
    ]
    await q.edit_message_text(chat_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
    await q.message.reply_text("✍️ Xabar yozing (matn, rasm yoki ovozli):")
    return CHAT_MSG

async def chat_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    job_id = context.user_data.get("active_chat_job")
    receiver_id = context.user_data.get("active_chat_receiver")
    if not job_id or not receiver_id:
        await update.message.reply_text("❌ Chat topilmadi. Qaytadan boshlang.")
        return ConversationHandler.END
    user = get_user(tid)
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        save_message(job_id, tid, receiver_id, "photo", update.message.caption or "📷", file_id)
        await notify(context, receiver_id,
            f"📷 {user.get('full_name')} rasm yubordi:",
            InlineKeyboardMarkup([[InlineKeyboardButton("💬 Javob berish", callback_data=f"chat_job_{job_id}")]])
        )
        await context.bot.send_photo(receiver_id, file_id, caption=f"📷 {user.get('full_name')}: {update.message.caption or ''}")
    elif update.message.voice:
        file_id = update.message.voice.file_id
        save_message(job_id, tid, receiver_id, "voice", "🎤 Ovozli xabar", file_id)
        await notify(context, receiver_id, f"🎤 {user.get('full_name')} ovozli xabar yubordi:",
            InlineKeyboardMarkup([[InlineKeyboardButton("💬 Javob berish", callback_data=f"chat_job_{job_id}")]])
        )
        await context.bot.send_voice(receiver_id, file_id, caption=f"🎤 {user.get('full_name')}")
    else:
        text = update.message.text
        save_message(job_id, tid, receiver_id, "text", text)
        await notify(context, receiver_id,
            f"💬 {user.get('full_name')}: {text}",
            InlineKeyboardMarkup([[InlineKeyboardButton("💬 Javob berish", callback_data=f"chat_job_{job_id}")]])
        )
    await update.message.reply_text("✅ Xabar yuborildi! Yana yozing yoki:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Chiqish", callback_data="my_chats")]])
    )
    return CHAT_MSG

# ── Rating ────────────────────────────────────────────────────────────────────
async def rate_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split("_")
    context.user_data["rate_job_id"] = int(parts[2])
    context.user_data["rate_to_id"] = int(parts[3])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐", callback_data="stars_1"),
         InlineKeyboardButton("⭐⭐", callback_data="stars_2"),
         InlineKeyboardButton("⭐⭐⭐", callback_data="stars_3")],
        [InlineKeyboardButton("⭐⭐⭐⭐", callback_data="stars_4"),
         InlineKeyboardButton("⭐⭐⭐⭐⭐", callback_data="stars_5")],
    ])
    await q.edit_message_text("⭐ Nechta yulduz berasiz?", reply_markup=kb)
    return RATE_STARS

async def rate_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    stars = int(q.data.split("_")[1])
    context.user_data["rate_stars"] = stars
    await q.edit_message_text(f"✅ {stars} ⭐\n\nIzoh qoldiring (yoki 'Yo'q' yozing):")
    return RATE_COMMENT

async def rate_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    comment = update.message.text.strip()
    job_id = context.user_data.get("rate_job_id")
    to_id = context.user_data.get("rate_to_id")
    stars = context.user_data.get("rate_stars")
    if create_rating(tid, to_id, job_id, stars, comment):
        update_rating_avg(to_id, stars)
        await update.message.reply_text(f"✅ Bahoingiz qabul qilindi! {stars} ⭐")
    else:
        await update.message.reply_text("⚠️ Siz allaqachon baho bergansiz.")
    await send_main_menu(update, context, get_user(tid))
    return ConversationHandler.END

# ── Dispute ───────────────────────────────────────────────────────────────────
async def dispute_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    app_id = int(q.data.split("_")[2])
    context.user_data["dispute_app_id"] = app_id
    await q.edit_message_text("⚖️ Muammoni tasvirlab yozing:")
    return DISPUTE_REASON

async def dispute_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["dispute_reason"] = update.message.text.strip()
    await update.message.reply_text("📷 Dalil rasmi yuboring (yoki 'Yo'q' yozing):")
    return DISPUTE_EVIDENCE

async def dispute_evidence(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    app_id = context.user_data.get("dispute_app_id")
    reason = context.user_data.get("dispute_reason")
    evidence = ""
    if update.message.photo:
        evidence = update.message.photo[-1].file_id
    app = get_application(app_id)
    dispute_id = create_dispute(app["job_id"], tid, reason, evidence)
    for admin_id in ADMIN_IDS:
        job = get_job(app["job_id"])
        await notify(context, admin_id,
            f"⚖️ Yangi shikoyat #{dispute_id}\n"
            f"💼 Ish: {job['title']}\n"
            f"👤 Shikoyatchi: {tid}\n"
            f"📝 Sabab: {reason}",
            InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✅ Ishchiga ber", callback_data=f"dis_worker_{dispute_id}_{app_id}"),
                 InlineKeyboardButton(f"❌ Ish beruvchiga qaytар", callback_data=f"dis_employer_{dispute_id}_{app_id}")]
            ])
        )
    await update.message.reply_text(
        f"✅ Shikoyat #{dispute_id} yuborildi!\n"
        f"Admin ko'rib chiqadi va qaror qabul qiladi.\nPul muzlatilgan holatda qoladi.",
        reply_markup=back_kb("main_menu")
    )
    return ConversationHandler.END

async def dispute_decide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split("_")
    decision = parts[1]
    dispute_id = int(parts[2])
    app_id = int(parts[3])
    app = get_application(app_id)
    job = get_job(app["job_id"])
    salary = job["salary"]
    if decision == "worker":
        commission = int(salary * COMMISSION)
        worker_pay = salary - commission
        add_balance(app["worker_id"], worker_pay)
        await notify(context, app["worker_id"], f"✅ Shikoyat hal qilindi! +{worker_pay:,} so'm berildi.")
        await notify(context, job["employer_id"], f"⚖️ Shikoyat: Ishchiga to'lash qaror qilindi.")
        update_job(job["id"], status="completed")
    else:
        add_balance(job["employer_id"], salary)
        await notify(context, job["employer_id"], f"✅ Shikoyat hal qilindi! +{salary:,} so'm qaytarildi.")
        await notify(context, app["worker_id"], f"⚖️ Shikoyat: Ish beruvchiga qaytarish qaror qilindi.")
        update_job(job["id"], status="cancelled")
    with db() as conn:
        conn.execute("UPDATE disputes SET status='resolved' WHERE id=?", (dispute_id,))
        conn.commit()
    await q.edit_message_text(f"✅ Shikoyat #{dispute_id} hal qilindi.")

# ── Admin ─────────────────────────────────────────────────────────────────────
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    if tid not in ADMIN_IDS:
        await update.message.reply_text("❌ Ruxsat yo'q.")
        return
    u, j, a, r = stats()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="adm_users"),
         InlineKeyboardButton("🔐 KYC", callback_data="adm_kyc")],
        [InlineKeyboardButton("⚖️ Nizolar", callback_data="adm_disputes"),
         InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")],
    ])
    await update.message.reply_text(
        f"🛠 *Admin Panel*\n\n"
        f"👥 Foydalanuvchilar: {u}\n"
        f"💼 Jami ishlar: {j}\n"
        f"🟢 Faol ishlar: {a}\n"
        f"💰 Daromad: {r:,} so'm",
        parse_mode="Markdown", reply_markup=kb
    )

async def adm_kyc_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    with db() as conn:
        pending = conn.execute(
            "SELECT * FROM users WHERE kyc_status='pending' AND id_card_file_id IS NOT NULL"
        ).fetchall()
    if not pending:
        await q.edit_message_text("✅ KYC kutayotgan foydalanuvchi yo'q.", reply_markup=back_kb("main_menu"))
        return
    for u in pending[:5]:
        u = dict(u)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"kyc_ok_{u['telegram_id']}"),
             InlineKeyboardButton("❌ Rad etish", callback_data=f"kyc_rej_{u['telegram_id']}")]
        ])
        await context.bot.send_message(
            q.from_user.id,
            f"👤 {u['full_name']}\n📱 {u['phone']}\n🆔 {u['telegram_id']}",
            reply_markup=kb
        )
        if u.get("id_card_file_id"):
            await context.bot.send_photo(q.from_user.id, u["id_card_file_id"], caption="📄 Guvohnoma")
        if u.get("selfie_file_id"):
            await context.bot.send_photo(q.from_user.id, u["selfie_file_id"], caption="🤳 Selfie")

async def kyc_ok_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = int(q.data.split("_")[2])
    update_user(uid, kyc_status="verified")
    await notify(context, uid, "✅ KYC tasdiqlandi! Profiling to'liq tasdiqlandi.")
    await q.edit_message_text(f"✅ {uid} tasdiqlandi.")

async def kyc_rej_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = int(q.data.split("_")[2])
    update_user(uid, kyc_status="rejected")
    await notify(context, uid, "❌ KYC rad etildi. Hujjatlarni qayta yuboring.")
    await q.edit_message_text(f"❌ {uid} rad etildi.")

async def adm_broadcast_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("📢 Broadcast xabarini yozing:")
    return BROADCAST_MSG

async def broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return ConversationHandler.END
    text = update.message.text
    with db() as conn:
        users = conn.execute("SELECT telegram_id FROM users WHERE is_blocked=0").fetchall()
    sent = 0
    for u in users:
        try:
            await context.bot.send_message(u[0], f"📢 *Xabar:*\n\n{text}", parse_mode="Markdown")
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ {sent} ta foydalanuvchiga yuborildi.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Bekor qilindi.")
    user = get_user(update.effective_user.id)
    if user and user.get("full_name"):
        await send_main_menu(update, context, user)
    return ConversationHandler.END

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            REG_PHONE: [MessageHandler(filters.CONTACT | filters.TEXT & ~filters.COMMAND, reg_phone)],
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
            REG_ID_CARD: [MessageHandler(filters.PHOTO, reg_id_card)],
            REG_SELFIE: [MessageHandler(filters.PHOTO, reg_selfie)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    post_job_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(post_job_cb, pattern="^post_job$")],
        states={
            JOB_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, job_title)],
            JOB_CATEGORY: [CallbackQueryHandler(job_category, pattern="^jcat_")],
            JOB_SALARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, job_salary)],
            JOB_LOCATION: [MessageHandler(filters.LOCATION | filters.VENUE, job_location)],
            JOB_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, job_date)],
            JOB_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, job_time)],
            JOB_WORKERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, job_workers)],
            JOB_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, job_desc)],
            JOB_CONFIRM: [CallbackQueryHandler(confirm_job_cb, pattern="^confirm_job$")],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(main_menu_cb, pattern="^main_menu$")],
        allow_reentry=True,
    )

    search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(find_jobs_cb, pattern="^find_jobs$")],
        states={
            SEARCH_LOCATION: [MessageHandler(filters.LOCATION, search_location)],
            SEARCH_RADIUS: [CallbackQueryHandler(search_radius, pattern="^radius_")],
            SEARCH_CATEGORY: [CallbackQueryHandler(search_category, pattern="^cat_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    chat_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(chat_job_cb, pattern="^chat_job_")],
        states={
            CHAT_MSG: [MessageHandler(filters.TEXT | filters.PHOTO | filters.VOICE, chat_msg)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(my_chats_cb, pattern="^my_chats$")],
        allow_reentry=True,
    )

    rate_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(rate_cb, pattern="^rate_")],
        states={
            RATE_STARS: [CallbackQueryHandler(rate_stars, pattern="^stars_")],
            RATE_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, rate_comment)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    dispute_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(dispute_cb, pattern="^dispute_app_")],
        states={
            DISPUTE_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, dispute_reason)],
            DISPUTE_EVIDENCE: [MessageHandler(filters.PHOTO | filters.TEXT, dispute_evidence)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_broadcast_cb, pattern="^adm_broadcast$")],
        states={
            BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_msg)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(reg_conv)
    app.add_handler(post_job_conv)
    app.add_handler(search_conv)
    app.add_handler(chat_conv)
    app.add_handler(rate_conv)
    app.add_handler(dispute_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(main_menu_cb, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(switch_role, pattern="^switch_role$"))
    app.add_handler(CallbackQueryHandler(show_profile, pattern="^profile$"))
    app.add_handler(CallbackQueryHandler(show_balance, pattern="^balance$"))
    app.add_handler(CallbackQueryHandler(buy_pass_cb, pattern="^buy_pass$"))
    app.add_handler(CallbackQueryHandler(pass_paid_cb, pattern="^pass_paid$"))
    app.add_handler(CallbackQueryHandler(my_apps_cb, pattern="^my_apps$"))
    app.add_handler(CallbackQueryHandler(view_app_cb, pattern="^view_app_"))
    app.add_handler(CallbackQueryHandler(done_app_cb, pattern="^done_app_"))
    app.add_handler(CallbackQueryHandler(acc_app_cb, pattern="^acc_app_"))
    app.add_handler(CallbackQueryHandler(rej_app_cb, pattern="^rej_app_"))
    app.add_handler(CallbackQueryHandler(confirm_app_cb, pattern="^confirm_app_"))
    app.add_handler(CallbackQueryHandler(my_jobs_cb, pattern="^my_jobs$"))
    app.add_handler(CallbackQueryHandler(view_job_cb, pattern="^view_job_"))
    app.add_handler(CallbackQueryHandler(job_apps_cb, pattern="^job_apps_"))
    app.add_handler(CallbackQueryHandler(my_chats_cb, pattern="^my_chats$"))
    app.add_handler(CallbackQueryHandler(adm_kyc_cb, pattern="^adm_kyc$"))
    app.add_handler(CallbackQueryHandler(kyc_ok_cb, pattern="^kyc_ok_"))
    app.add_handler(CallbackQueryHandler(kyc_rej_cb, pattern="^kyc_rej_"))
    app.add_handler(CallbackQueryHandler(dispute_decide, pattern="^dis_"))
    app.add_handler(CallbackQueryHandler(apply_job, pattern="^apply_"))

    logger.info("✅ WorkPlaceUZ Bot ishga tushdi!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
