import os
import re
import random
import asyncio
import time
import json
import logging
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse
from pyrogram import Client, filters, errors
from pyrogram.raw import functions, types
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
import mysql.connector
from mysql.connector import Error
import threading
import keep_alive

# ==================== CONFIGURATION ====================
api_id = int(os.getenv('API_ID', '22732923'))
api_hash = os.getenv('API_HASH', 'd5428680920f87cb1b78c328a7f6c6e7')
bot_token = os.getenv('BOT_TOKEN', '8143252179:AAFMGene_1wUcLwWYse0qHLJPU2NO992iFo')
owner_id = int(os.getenv('OWNER_ID', '7798986445'))

# تنظیمات دیتابیس
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'root')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_NAME = os.getenv('DB_NAME', 'telegram_report_bot')

# تنظیمات پیشرفته
sleeping = int(os.getenv('SLEEP_TIME', '2'))
max_accounts_per_report = int(os.getenv('MAX_ACCOUNTS_PER_REPORT', '60'))
cooldown_time = int(os.getenv('COOLDOWN_TIME', '600'))
admin_cooldown = int(os.getenv('ADMIN_COOLDOWN', '1200'))
health_check_interval = int(os.getenv('HEALTH_CHECK_INTERVAL', '1800'))
account_warmup_interval = int(os.getenv('ACCOUNT_WARMUP_INTERVAL', '1200'))
notification_channel = os.getenv('NOTIFICATION_CHANNEL', '-1002720494714')  # آیدی عددی کانال خصوصی

# پیکربندی پیشرفته لاگینگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
# ======================================================

# ==================== DATABASE SETUP ====================
class Database:
    def __init__(self):
        self.connection = None
        self.connect()
        self.init_tables()
    
    def connect(self):
        try:
            self.connection = mysql.connector.connect(
                host=DB_HOST,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                autocommit=True
            )
            logger.info("Connected to MySQL database")
        except Error as e:
            logger.error(f"Error connecting to MySQL: {e}")
            try:
                connection = mysql.connector.connect(
                    host=DB_HOST,
                    user=DB_USER,
                    password=DB_PASSWORD
                )
                cursor = connection.cursor()
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
                connection.close()
                
                self.connection = mysql.connector.connect(
                    host=DB_HOST,
                    user=DB_USER,
                    password=DB_PASSWORD,
                    database=DB_NAME,
                    autocommit=True
                )
                logger.info("Created and connected to MySQL database")
            except Error as e:
                logger.error(f"Error creating database: {e}")
    
    def init_tables(self):
        try:
            cursor = self.connection.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    role ENUM('owner', 'admin', 'user') DEFAULT 'user',
                    admin_expiry TIMESTAMP NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    phone_number VARCHAR(20) PRIMARY KEY,
                    api_id INT,
                    api_hash VARCHAR(100),
                    user_id BIGINT,
                    username VARCHAR(100),
                    first_name VARCHAR(100),
                    last_name VARCHAR(100),
                    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_check TIMESTAMP NULL,
                    status ENUM('active', 'inactive') DEFAULT 'inactive',
                    has_2fa BOOLEAN DEFAULT FALSE,
                    proxy TEXT NULL,
                    warmup_time TIMESTAMP NULL,
                    report_count INT DEFAULT 0,
                    last_report TIMESTAMP NULL
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_configs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    api_id INT,
                    api_hash VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id VARCHAR(36) PRIMARY KEY,
                    user_id BIGINT,
                    operation_type ENUM('report', 'health_check', 'warmup'),
                    target_entity VARCHAR(100),
                    target_message_id INT NULL,
                    report_reason VARCHAR(50),
                    report_description TEXT NULL,
                    account_count INT,
                    status ENUM('pending', 'running', 'completed', 'failed', 'cancelled'),
                    success_count INT DEFAULT 0,
                    failed_count INT DEFAULT 0,
                    start_time TIMESTAMP NULL,
                    end_time TIMESTAMP NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operation_accounts (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    operation_id VARCHAR(36),
                    phone_number VARCHAR(20),
                    status ENUM('success', 'failed'),
                    error_message TEXT NULL,
                    FOREIGN KEY (operation_id) REFERENCES operations(operation_id) ON DELETE CASCADE,
                    FOREIGN KEY (phone_number) REFERENCES accounts(phone_number) ON DELETE CASCADE
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operation_queue (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT,
                    operation_type VARCHAR(50),
                    data TEXT,
                    status ENUM('pending', 'processing'),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    setting_key VARCHAR(50) PRIMARY KEY,
                    setting_value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channel_reports (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    operation_id VARCHAR(36),
                    message TEXT,
                    report_type ENUM('start', 'end'),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (operation_id) REFERENCES operations(operation_id) ON DELETE CASCADE
                )
            """)
            
            cursor.execute("SELECT COUNT(*) FROM users WHERE user_id = %s", (owner_id,))
            if cursor.fetchone()[0] == 0:
                cursor.execute("INSERT INTO users (user_id, role) VALUES (%s, 'owner')", (owner_id,))
                logger.info(f"Added owner with ID: {owner_id}")
            
            self.connection.commit()
            cursor.close()
            logger.info("Database tables initialized successfully")
            
        except Error as e:
            logger.error(f"Error initializing tables: {e}")
    
    def execute_query(self, query, params=None):
        try:
            cursor = self.connection.cursor(dictionary=True)
            cursor.execute(query, params or ())
            result = cursor.fetchall()
            cursor.close()
            return result
        except Error as e:
            logger.error(f"Error executing query: {e}")
            return None
    
    def execute_insert(self, query, params=None):
        try:
            cursor = self.connection.cursor()
            cursor.execute(query, params or ())
            last_id = cursor.lastrowid
            cursor.close()
            return last_id
        except Error as e:
            logger.error(f"Error executing insert: {e}")
            return None
    
    def execute_update(self, query, params=None):
        try:
            cursor = self.connection.cursor()
            cursor.execute(query, params or ())
            affected_rows = cursor.rowcount
            cursor.close()
            return affected_rows
        except Error as e:
            logger.error(f"Error executing update: {e}")
            return 0

db = Database()
# ==================== UTILITY FUNCTIONS ====================
async def get_user_role(user_id):
    result = db.execute_query("SELECT role, admin_expiry FROM users WHERE user_id = %s", (user_id,))
    if result:
        role = result[0]['role']
        expiry = result[0]['admin_expiry']
        if role == 'admin' and expiry and expiry < datetime.now():
            db.execute_update("UPDATE users SET role = 'user', admin_expiry = NULL WHERE user_id = %s", (user_id,))
            return 'user'
        return role
    return 'user'

async def is_owner(user_id):
    return await get_user_role(user_id) == 'owner'

async def is_admin(user_id):
    role = await get_user_role(user_id)
    return role in ['owner', 'admin']

async def can_manage_accounts(user_id):
    return await is_owner(user_id)

async def can_view_accounts(user_id):
    return await is_owner(user_id)

async def get_report_limit(user_id):
    if await is_owner(user_id):
        return 0
    
    accounts_count_result = db.execute_query("SELECT COUNT(*) as count FROM accounts WHERE status = 'active'")
    accounts_count = accounts_count_result[0]['count'] if accounts_count_result else 0
    
    if accounts_count < 20:
        return 3
    elif accounts_count <= 30:
        return 2
    else:
        return 1

async def check_cooldown(user_id):
    if await is_owner(user_id):
        return True, 0
    
    last_operation = db.execute_query(
        "SELECT MAX(end_time) as last_op FROM operations WHERE user_id = %s AND status = 'completed'",
        (user_id,)
    )
    
    if last_operation and last_operation[0]['last_op']:
        last_op_time = last_operation[0]['last_op']
        if isinstance(last_op_time, str):
            last_op_time = datetime.strptime(last_op_time, '%Y-%m-%d %H:%M:%S')
        
        cooldown_period = admin_cooldown if await get_user_role(user_id) == 'admin' else cooldown_time
        time_diff = (datetime.now() - last_op_time).total_seconds()
        remaining = cooldown_period - time_diff
        return remaining <= 0, max(0, int(remaining))
    
    return True, 0

async def get_available_accounts_count():
    result = db.execute_query("""
        SELECT COUNT(*) as count FROM accounts 
        WHERE status = 'active' 
        AND (warmup_time IS NULL OR warmup_time <= %s)
    """, (datetime.now(),))
    return result[0]['count'] if result else 0

async def create_operation(user_id, operation_type, target_entity=None, target_message_id=None, report_reason=None, report_description=None, account_count=0):
    operation_id = str(uuid.uuid4())
    db.execute_insert("""
        INSERT INTO operations (operation_id, user_id, operation_type, target_entity, 
        target_message_id, report_reason, report_description, account_count, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
    """, (operation_id, user_id, operation_type, target_entity, target_message_id, report_reason, report_description, account_count))
    return operation_id

async def update_operation_status(operation_id, status, success_count=0, failed_count=0):
    if status in ['completed', 'failed', 'cancelled']:
        db.execute_update("""
            UPDATE operations SET status = %s, success_count = %s, failed_count = %s, 
            end_time = %s WHERE operation_id = %s
        """, (status, success_count, failed_count, datetime.now(), operation_id))
    else:
        db.execute_update("""
            UPDATE operations SET status = %s, start_time = %s 
            WHERE operation_id = %s
        """, (status, datetime.now(), operation_id))

async def add_operation_account(operation_id, phone_number, status, error_message=None):
    db.execute_insert("""
        INSERT INTO operation_accounts (operation_id, phone_number, status, error_message)
        VALUES (%s, %s, %s, %s)
    """, (operation_id, phone_number, status, error_message))

async def send_channel_report(operation_id, report_type, message):
    db.execute_insert("""
        INSERT INTO channel_reports (operation_id, report_type, message)
        VALUES (%s, %s, %s)
    """, (operation_id, report_type, message))
    
    if notification_channel:
        try:
            await bot.send_message(notification_channel, message)
        except Exception as e:
            logger.error(f"Failed to send message to channel: {e}")

async def get_operation_accounts(operation_id):
    return db.execute_query("""
        SELECT phone_number, status, error_message FROM operation_accounts 
        WHERE operation_id = %s ORDER BY id
    """, (operation_id,))

async def get_random_api_config():
    configs = db.execute_query("SELECT api_id, api_hash FROM api_configs")
    if configs:
        return random.choice(configs)
    return {"api_id": api_id, "api_hash": api_hash}

async def get_report_reason(reason_key):
    reason_map = {
        "spam": types.InputReportReasonSpam(),
        "violence": types.InputReportReasonViolence(),
        "pornography": types.InputReportReasonPornography(),
        "child_abuse": types.InputReportReasonChildAbuse(),
        "copyright": types.InputReportReasonCopyright(),
        "fake": types.InputReportReasonFake(),
        "scam": types.InputReportReasonScam(),
        "illegal": types.InputReportReasonIllegalDrugs(),
        "other": types.InputReportReasonOther()
    }
    return reason_map.get(reason_key, types.InputReportReasonOther())

async def parse_proxy(proxy_str):
    try:
        parsed = urlparse(proxy_str)
        scheme = parsed.scheme
        hostname = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password

        return {
            "scheme": scheme,
            "hostname": hostname,
            "port": port,
            "username": username,
            "password": password
        }
    except Exception as e:
        logger.error(f"Proxy parsing error: {str(e)}")
        return None

async def extract_entity_from_link(link):
    patterns = [
        r"(?:https?://)?(?:t\.me|telegram\.me)/(?:c/)?([^/]+)/(\d+)",
        r"(?:https?://)?(?:t\.me|telegram\.me)/(?:joinchat/)?([a-zA-Z0-9_]+)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, link)
        if match:
            if len(match.groups()) == 2:
                return match.group(1), int(match.group(2))
            else:
                return match.group(1), None
    
    return None, None

def error_handler(func):
    async def wrapper(client, message):
        try:
            return await func(client, message)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {str(e)}")
            await message.reply_text("❌ خطایی رخ داد. لطفاً دوباره尝试 کنید.")
    return wrapper

def error_handler_callback(func):
    async def wrapper(client, callback_query):
        try:
            return await func(client, callback_query)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {str(e)}")
            await callback_query.message.edit_text("❌ خطایی رخ داد. لطفاً دوباره尝试 کنید.")
    return wrapper

# ==================== USER MANAGEMENT ====================
async def add_admin(user_id, admin_id, duration_hours=24):
    if not await is_owner(user_id):
        return False, "فقط مالک می‌تواند ادمین اضافه کند."
    
    expiry_time = datetime.now() + timedelta(hours=duration_hours)
    result = db.execute_update(
        "INSERT INTO users (user_id, role, admin_expiry) VALUES (%s, 'admin', %s) ON DUPLICATE KEY UPDATE role='admin', admin_expiry=%s",
        (admin_id, expiry_time, expiry_time)
    )
    
    if result > 0:
        return True, f"ادمین با موفقیت اضافه شد. مدت زمان: {duration_hours} ساعت"
    else:
        return False, "خطا در اضافه کردن ادمین"

async def remove_admin(user_id, admin_id):
    if not await is_owner(user_id):
        return False, "فقط مالک می‌تواند ادمین حذف کند."
    
    result = db.execute_update(
        "UPDATE users SET role = 'user', admin_expiry = NULL WHERE user_id = %s AND role = 'admin'",
        (admin_id,)
    )
    
    if result > 0:
        return True, "ادمین با موفقیت حذف شد."
    else:
        return False, "کاربر یافت نشد یا ادمین نیست."

async def add_owner(user_id, new_owner_id):
    if user_id != owner_id:
        return False, "فقط مالک اصلی می‌تواند مالک جدید اضافه کند."
    
    result = db.execute_update(
        "INSERT INTO users (user_id, role) VALUES (%s, 'owner') ON DUPLICATE KEY UPDATE role='owner'",
        (new_owner_id,)
    )
    
    if result > 0:
        return True, "مالک جدید با موفقیت اضافه شد."
    else:
        return False, "خطا در اضافه کردن مالک"

async def remove_owner(user_id, owner_id_to_remove):
    if user_id != owner_id:
        return False, "فقط مالک اصلی می‌تواند مالک حذف کند."
    
    if owner_id_to_remove == owner_id:
        return False, "نمی‌توان مالک اصلی را حذف کرد."
    
    result = db.execute_update(
        "UPDATE users SET role = 'user' WHERE user_id = %s AND role = 'owner'",
        (owner_id_to_remove,)
    )
    
    if result > 0:
        return True, "مالک با موفقیت حذف شد."
    else:
        return False, "مالک یافت نشد."

# ==================== ACCOUNT MANAGEMENT ====================
async def add_account(phone_number, user_id, proxy=None):
    if not await can_manage_accounts(user_id):
        return False, "شما دسترسی لازم را ندارید."
    
    result = db.execute_query("SELECT * FROM accounts WHERE phone_number = %s", (phone_number,))
    if result:
        return False, "این شماره قبلاً اضافه شده است."
    
    config = await get_random_api_config()
    
    try:
        proxy_dict = json.loads(proxy) if proxy and isinstance(proxy, str) else proxy
        client = Client(f"sessions/{phone_number}", config["api_id"], config["api_hash"], proxy=proxy_dict)
        await client.connect()
        
        sent_code = await client.send_code(phone_number)
        
        db.execute_insert("""
            INSERT INTO accounts (phone_number, api_id, api_hash, proxy, status)
            VALUES (%s, %s, %s, %s, 'pending')
        """, (phone_number, config["api_id"], config["api_hash"], json.dumps(proxy_dict) if proxy_dict else None))
        
        db.execute_insert("""
            INSERT INTO user_states (user_id, step, phone_number, phone_code_hash, api_id, api_hash, proxy)
            VALUES (%s, 'waiting_for_code', %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE step='waiting_for_code', phone_number=%s, phone_code_hash=%s, api_id=%s, api_hash=%s, proxy=%s
        """, (user_id, phone_number, sent_code.phone_code_hash, config["api_id"], config["api_hash"], 
              json.dumps(proxy_dict) if proxy_dict else None, phone_number, sent_code.phone_code_hash, 
              config["api_id"], config["api_hash"], json.dumps(proxy_dict) if proxy_dict else None))
        
        await client.disconnect()
        return True, "کد تأیید برای شما ارسال شد. لطفاً کد را وارد کنید."
        
    except errors.PhoneNumberInvalid:
        return False, "شماره تلفن نامعتبر است."
    except errors.PhoneNumberFlood:
        return False, "شماره تلفن به دلیل فعالیت مشکوک مسدود شده است."
    except errors.PhoneNumberBanned:
        return False, "شماره تلفن مسدود شده است."
    except Exception as e:
        logger.error(f"Error adding account {phone_number}: {str(e)}")
        return False, f"خطا در افزودن اکانت: {str(e)}"

async def verify_code(user_id, code):
    result = db.execute_query("SELECT * FROM user_states WHERE user_id = %s AND step = 'waiting_for_code'", (user_id,))
    if not result:
        return False, "درخواست نامعتبر. لطفاً از ابتدا شروع کنید."
    
    state = result[0]
    phone_number = state['phone_number']
    proxy = json.loads(state['proxy']) if state['proxy'] else None
    
    try:
        client = Client(
            f"sessions/{phone_number}", 
            state['api_id'], 
            state['api_hash'],
            proxy=proxy
        )
        await client.connect()
        
        try:
            await client.sign_in(phone_number, state['phone_code_hash'], code)
        except errors.SessionPasswordNeeded:
            db.execute_update(
                "UPDATE user_states SET step = 'waiting_for_password' WHERE user_id = %s",
                (user_id,)
            )
            await client.disconnect()
            return False, "این اکانت دارای رمز دو مرحله‌ای است. لطفاً رمز را وارد کنید."
        
        me = await client.get_me()
        
        db.execute_update("""
            UPDATE accounts SET 
            user_id = %s, username = %s, first_name = %s, last_name = %s,
            added_date = %s, last_check = %s, status = 'active'
            WHERE phone_number = %s
        """, (me.id, me.username, me.first_name, me.last_name, datetime.now(), datetime.now(), phone_number))
        
        db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))
        
        await client.disconnect()
        
        return True, f"اکانت با موفقیت اضافه شد!\n👤 نام: {me.first_name or ''} {me.last_name or ''}\n🔗 یوزرنیم: @{me.username}" if me.username else "ندارد"
        
    except errors.PhoneCodeInvalid:
        return False, "کد وارد شده نامعتبر است."
    except errors.PhoneCodeExpired:
        return False, "کد وارد شده منقضی شده است. لطفاً دوباره尝试 کنید."
    except Exception as e:
        logger.error(f"Error verifying code for {phone_number}: {str(e)}")
        return False, f"خطا در تأیید کد: {str(e)}"

async def verify_password(user_id, password):
    result = db.execute_query("SELECT * FROM user_states WHERE user_id = %s AND step = 'waiting_for_password'", (user_id,))
    if not result:
        return False, "درخواست نامعتبر. لطفاً از ابتدا شروع کنید."
    
    state = result[0]
    phone_number = state['phone_number']
    proxy = json.loads(state['proxy']) if state['proxy'] else None
    
    try:
        client = Client(
            f"sessions/{phone_number}", 
            state['api_id'], 
            state['api_hash'],
            proxy=proxy
        )
        await client.connect()
        
        await client.check_password(password)
        
        me = await client.get_me()
        
        db.execute_update("""
            UPDATE accounts SET 
            user_id = %s, username = %s, first_name = %s, last_name = %s,
            added_date = %s, last_check = %s, status = 'active', has_2fa = TRUE
            WHERE phone_number = %s
        """, (me.id, me.username, me.first_name, me.last_name, datetime.now(), datetime.now(), phone_number))
        
        db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))
        
        await client.disconnect()
        
        return True, f"اکانت با موفقیت اضافه شد!\n👤 نام: {me.first_name or ''} {me.last_name or ''}\n🔗 یوزرنیم: @{me.username}" if me.username else "ندارد"
        
    except errors.PasswordHashInvalid:
        return False, "رمز وارد شده نامعتبر است."
    except Exception as e:
        logger.error(f"Error verifying password for {phone_number}: {str(e)}")
        return False, f"خطا در تأیید رمز: {str(e)}"

async def remove_account(phone_number, user_id):
    if not await can_manage_accounts(user_id):
        return False, "شما دسترسی لازم را ندارید."
    
    result = db.execute_update("DELETE FROM accounts WHERE phone_number = %s", (phone_number,))
    
    session_file = f"sessions/{phone_number}.session"
    if os.path.exists(session_file):
        os.remove(session_file)
    
    if result > 0:
        return True, "اکانت با موفقیت حذف شد."
    else:
        return False, "این شماره در سیستم وجود ندارد."

async def check_account_health(phone_number):
    try:
        account = db.execute_query("SELECT api_id, api_hash, proxy FROM accounts WHERE phone_number = %s", (phone_number,))
        if not account:
            return False
        
        account = account[0]
        proxy = json.loads(account['proxy']) if account['proxy'] else None
        
        async with Client(
            f"sessions/{phone_number}", 
            account['api_id'], 
            account['api_hash'],
            proxy=proxy
        ) as client:
            me = await client.get_me()
            
            db.execute_update("""
                UPDATE accounts SET 
                user_id = %s, username = %s, first_name = %s, last_name = %s,
                last_check = %s, status = 'active'
                WHERE phone_number = %s
            """, (me.id, me.username, me.first_name, me.last_name, datetime.now(), phone_number))
            
            return True
    except Exception as e:
        logger.error(f"Health check failed for {phone_number}: {str(e)}")
        db.execute_update("UPDATE accounts SET status = 'inactive', last_check = %s WHERE phone_number = %s", 
                         (datetime.now(), phone_number))
        return False

async def check_all_accounts():
    accounts = db.execute_query("SELECT phone_number FROM accounts WHERE status = 'active'")
    results = {"active": 0, "inactive": 0, "details": []}
    
    for account in accounts:
        phone_number = account['phone_number']
        is_healthy = await check_account_health(phone_number)
        
        if is_healthy:
            results["active"] += 1
            status = "فعال"
        else:
            results["inactive"] += 1
            status = "غیرفعال"
            await bot.send_message(owner_id, f"⚠️ اکانت مشکل دارد: {phone_number}")
        
        results["details"].append({
            "phone": phone_number,
            "status": status
        })
    
    return results

# ==================== REPORTING SYSTEM ====================
async def start_report_process(user_id, report_type, target_link):
    entity, message_id = await extract_entity_from_link(target_link)
    
    if not entity:
        return False, "لینک وارد شده نامعتبر است."
    
    can_proceed, remaining = await check_cooldown(user_id)
    if not can_proceed:
        return False, f"لطفاً {remaining} ثانیه دیگر دوباره尝试 کنید."
    
    operation_id = await create_operation(user_id, 'report', entity, message_id)
    
    db.execute_insert("""
        INSERT INTO user_states (user_id, step, operation_id, report_type, target_entity, target_message_id, target_link)
        VALUES (%s, 'report_choose_reason', %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE step='report_choose_reason', operation_id=%s, report_type=%s, target_entity=%s, target_message_id=%s, target_link=%s
    """, (user_id, operation_id, report_type, entity, message_id, target_link, 
          operation_id, report_type, entity, message_id, target_link))
    
    reasons = {
        "spam": "هرزنامه",
        "violence": "خشونت",
        "pornography": "محتوای مستهجن",
        "child_abuse": "سوء استفاده از کودکان",
        "copyright": "نقض کپی رایت",
        "fake": "حساب جعلی",
        "scam": "کلاهبرداری",
        "illegal": "فعالیت غیرقانونی",
        "other": "سایر"
    }
    
    keyboard = []
    row = []
    for i, (key, text) in enumerate(reasons.items()):
        row.append(InlineKeyboardButton(text, callback_data=f"report_reason_{key}"))
        if (i + 1) % 2 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("🔙 لغو", callback_data="cancel_report")])
    
    return True, InlineKeyboardMarkup(keyboard)

async def handle_report_reason(user_id, reason):
    result = db.execute_query("SELECT * FROM user_states WHERE user_id = %s AND step = 'report_choose_reason'", (user_id,))
    if not result:
        return False, "درخواست نامعتبر."
    
    state = result[0]
    operation_id = state['operation_id']
    
    if reason == "scam":
        db.execute_update("UPDATE user_states SET step = 'report_choose_subreason' WHERE user_id = %s", (user_id,))
        
        sub_reasons = {
            "phishing": "فیشینگ",
            "impersonation": "جعل هویت", 
            "fake_sale": "فروش تقلبی",
            "spam": "اسپم"
        }
        
        keyboard = []
        row = []
        for i, (key, text) in enumerate(sub_reasons.items()):
            row.append(InlineKeyboardButton(text, callback_data=f"report_subreason_{key}"))
            if (i + 1) % 2 == 0:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="report_back_to_main_reasons")])
        
        return True, "لطفاً نوع کلاهبرداری را انتخاب کنید:", InlineKeyboardMarkup(keyboard)
    else:
        db.execute_update("UPDATE operations SET report_reason = %s WHERE operation_id = %s", (reason, operation_id))
        db.execute_update("UPDATE user_states SET step = 'report_enter_description' WHERE user_id = %s", (user_id,))
        return True, "لطفاً توضیحات گزارش خود را وارد کنید:"

async def handle_report_subreason(user_id, sub_reason):
    result = db.execute_query("SELECT * FROM user_states WHERE user_id = %s AND step = 'report_choose_subreason'", (user_id,))
    if not result:
        return False, "درخواست نامعتبر."
    
    state = result[0]
    operation_id = state['operation_id']
    
    db.execute_update("UPDATE operations SET report_reason = %s WHERE operation_id = %s", (f"scam_{sub_reason}", operation_id))
    db.execute_update("UPDATE user_states SET step = 'report_enter_description' WHERE user_id = %s", (user_id,))
    
    return True, "لطفاً توضیحات گزارش خود را وارد کنید:"

async def handle_report_description(user_id, description):
    result = db.execute_query("SELECT * FROM user_states WHERE user_id = %s AND step = 'report_enter_description'", (user_id,))
    if not result:
        return False, "درخواست نامعتبر."
    
    state = result[0]
    operation_id = state['operation_id']
    
    db.execute_update("UPDATE operations SET report_description = %s WHERE operation_id = %s", (description, operation_id))
    db.execute_update("UPDATE user_states SET step = 'report_select_accounts' WHERE user_id = %s", (user_id,))
    
    available_accounts = await get_available_accounts_count()
    
    if available_accounts == 0:
        db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))
        return False, "هیچ اکانت فعالی برای گزارش‌دهی وجود ندارد."
    
    keyboard = []
    max_selectable = min(available_accounts, max_accounts_per_report)
    
    buttons = []
    for i in range(1, max_selectable + 1):
        buttons.append(InlineKeyboardButton(str(i), callback_data=f"report_accounts_{i}"))
        if i % 5 == 0 or i == max_selectable:
            keyboard.append(buttons)
            buttons = []
    
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="report_back_to_reason")])
    keyboard.append([InlineKeyboardButton("🔚 لغو گزارش", callback_data="cancel_report")])
    
    return True, InlineKeyboardMarkup(keyboard)

async def handle_report_accounts_selection(user_id, account_count):
    result = db.execute_query("SELECT * FROM user_states WHERE user_id = %s AND step = 'report_select_accounts'", (user_id,))
    if not result:
        return False, "درخواست نامعتبر."
    
    state = result[0]
    operation_id = state['operation_id']
    
    report_limit = await get_report_limit(user_id)
    if report_limit > 0 and account_count > report_limit:
        return False, f"شما مجاز به استفاده از بیش از {report_limit} گزارش از هر اکانت نیستید."
    
    db.execute_update("UPDATE operations SET account_count = %s WHERE operation_id = %s", (account_count, operation_id))
    db.execute_update("UPDATE user_states SET step = 'report_ask_join' WHERE user_id = %s", (user_id,))
    
    keyboard = [
        [InlineKeyboardButton("✅ بله", callback_data="report_join_yes")],
        [InlineKeyboardButton("❌ خیر", callback_data="report_join_no")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="report_back_to_accounts")],
        [InlineKeyboardButton("🔚 لغو گزارش", callback_data="cancel_report")]
    ]
    
    return True, InlineKeyboardMarkup(keyboard)

async def handle_report_join_selection(user_id, join_selection):
    result = db.execute_query("SELECT * FROM user_states WHERE user_id = %s AND step = 'report_ask_join'", (user_id,))
    if not result:
        return False, "درخواست نامعتبر."
    
    state = result[0]
    operation_id = state['operation_id']
    target_entity = state['target_entity']
    target_message_id = state['target_message_id']
    target_link = state['target_link']
    report_reason = state['report_reason']
    
    db.execute_update("UPDATE user_states SET step = 'report_confirmation' WHERE user_id = %s", (user_id,))
    
    reason_mapping = {
        "spam": "هرزنامه",
        "violence": "خشونت", 
        "pornography": "محتوای مستهجن",
        "child_abuse": "سوء استفاده از کودکان",
        "copyright": "نقض کپی رایت",
        "fake": "حساب جعلی",
        "scam_phishing": "کلاهبرداری (فیشینگ)",
        "scam_impersonation": "کلاهبرداری (جعل هویت)",
        "scam_fake_sale": "کلاهبرداری (فروش تقلبی)", 
        "scam_spam": "کلاهبرداری (اسپم)",
        "illegal": "فعالیت غیرقانونی",
        "other": "سایر"
    }
    
    reason_text = reason_mapping.get(report_reason, "نامشخص")
    
    summary = (
        f"📋 خلاصه گزارش:\n\n"
        f"🔗 لینک هدف: {target_link}\n"
        f"📝 دلیل: {reason_text}\n"
        f"📄 توضیحات: {state['report_description']}\n"
        f"👥 تعداد اکانت‌ها: {state['account_count']}\n"
        f"{"✅" if join_selection == "yes" else "❌"} جوین شدن: {"بله" if join_selection == "yes" else "خیر"}\n\n"
        f"آیا می‌خواهید گزارش ارسال شود؟"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ تأیید و ارسال", callback_data="report_confirm_yes")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="report_back_to_join")],
        [InlineKeyboardButton("🔚 لغو گزارش", callback_data="cancel_report")]
    ]
    
    return True, summary, InlineKeyboardMarkup(keyboard)

async def execute_report(operation_id):
    operation = db.execute_query("SELECT * FROM operations WHERE operation_id = %s", (operation_id,))
    if not operation:
        return False, "عملیات پیدا نشد."
    
    operation = operation[0]
    user_id = operation['user_id']
    target_entity = operation['target_entity']
    target_message_id = operation['target_message_id']
    report_reason = operation['report_reason']
    report_description = operation['report_description']
    account_count = operation['account_count']
    
    accounts = db.execute_query("""
        SELECT phone_number, api_id, api_hash, proxy FROM accounts 
        WHERE status = 'active' AND (warmup_time IS NULL OR warmup_time <= %s)
        LIMIT %s
    """, (datetime.now(), account_count))
    
    if not accounts:
        await update_operation_status(operation_id, 'failed')
        return False, "هیچ اکانت فعالی برای گزارش‌دهی وجود ندارد."
    
    await update_operation_status(operation_id, 'running')
    
    # گزارش شروع به کانال (بدون لینک)
    await send_channel_report(operation_id, 'start', 
        f"🚀 شروع عملیات گزارش‌دهی\n👤 کاربر: {user_id}\n🔢 تعداد اکانت: {account_count}\n⏰ زمان: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # گزارش شروع به مالک (با لینک)
    if user_id != owner_id:
        await bot.send_message(owner_id, 
            f"📋 شروع عملیات گزارش‌دهی\n👤 کاربر: {user_id}\n🆔 Operation ID: {operation_id}\n🔗 لینک هدف: {operation['target_entity']}\n📝 دلیل: {report_reason}")
    
    success = 0
    failed = 0
    
    for i, account in enumerate(accounts):
        phone_number = account['phone_number']
        api_id = account['api_id']
        api_hash = account['api_hash']
        proxy = json.loads(account['proxy']) if account['proxy'] else None
        
        try:
            async with Client(f"sessions/{phone_number}", api_id, api_hash, proxy=proxy) as client:
                await client.connect()
                
                peer = await client.resolve_peer(target_entity)
                
                if target_message_id:
                    await client.invoke(functions.messages.Report(
                        peer=peer,
                        id=[target_message_id],
                        reason=await get_report_reason(report_reason.split('_')[0] if '_' in report_reason else report_reason),
                        message=report_description or ""
                    ))
                else:
                    await client.invoke(functions.messages.Report(
                        peer=peer,
                        id=[],
                        reason=await get_report_reason(report_reason.split('_')[0] if '_' in report_reason else report_reason),
                        message=report_description or ""
                    ))
                
                success += 1
                await add_operation_account(operation_id, phone_number, 'success')
                
                db.execute_update("""
                    UPDATE accounts SET warmup_time = %s, report_count = report_count + 1, 
                    last_report = %s WHERE phone_number = %s
                """, (datetime.now() + timedelta(seconds=account_warmup_interval), datetime.now(), phone_number))
                
                await client.disconnect()
                
        except Exception as e:
            failed += 1
            await add_operation_account(operation_id, phone_number, 'failed', str(e))
            logger.error(f"Report failed for {phone_number}: {str(e)}")
        
        await asyncio.sleep(sleeping)
    
    await update_operation_status(operation_id, 'completed', success, failed)
    
    # گزارش پایان به کانال
    await send_channel_report(operation_id, 'end',
        f"✅ اتمام عملیات گزارش‌دهی\n👤 کاربر: {user_id}\n✅ موفق: {success}\n❌ ناموفق: {failed}\n⏰ زمان انجام: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # گزارش پایان به مالک
    if user_id != owner_id:
        await bot.send_message(owner_id,
            f"✅ اتمام عملیات گزارش‌دهی\n👤 کاربر: {user_id}\n🆔 Operation ID: {operation_id}\n🔗 لینک هدف: {operation['target_entity']}\n✅ موفق: {success}\n❌ ناموفق: {failed}")
    
    db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))
    
    return True, f"عملیات کامل شد. موفق: {success}, ناموفق: {failed}"

# ==================== BOT SETUP ====================
bot = Client(
    "AdvancedReportBot",
    bot_token=bot_token,
    api_id=api_id,
    api_hash=api_hash
)

# ==================== BOT HANDLERS ====================
@bot.on_message(filters.command("start") & filters.private)
@error_handler
async def start_command(client, message):
    user_id = message.from_user.id
    
    if not db.execute_query("SELECT * FROM users WHERE user_id = %s", (user_id,)):
        role = 'owner' if user_id == owner_id else 'user'
        db.execute_insert("INSERT INTO users (user_id, role) VALUES (%s, %s)", (user_id, role))
    
    role = await get_user_role(user_id)
    
    if role == 'user':
        await message.reply_text("❌ شما اجازه استفاده از این ربات را ندارید. باید اشتراک تهیه کنید.")
        return
    
    keyboard = []
    if role == 'owner':
        keyboard = [
            [InlineKeyboardButton("📱 مدیریت اکانت‌ها", callback_data="manage_accounts")],
            [InlineKeyboardButton("⚠️ سیستم گزارش‌دهی", callback_data="report_system")],
            [InlineKeyboardButton("👥 مدیریت کاربران", callback_data="user_management")],
            [InlineKeyboardButton("⚙️ تنظیمات", callback_data="settings")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("⚠️ سیستم گزارش‌دهی", callback_data="report_system")]
        ]
    
    await message.reply_text(
        "🔰 به ربات DRAGON ROPORTER خوش آمدید\n\n"
        "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@bot.on_message(filters.command("cancel") & filters.private)
@error_handler
async def cancel_command(client, message):
    user_id = message.from_user.id
    db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))
    await message.reply_text("✅ تمام عملیات‌های در حال انجام لغو شد.", reply_markup=ReplyKeyboardRemove())

@bot.on_callback_query()
@error_handler_callback
async def handle_callbacks(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    try:
        if data == "manage_accounts":
            if not await can_manage_accounts(user_id):
                await callback_query.answer("شما دسترسی لازم را ندارید.", show_alert=True)
                return
                
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ افزودن اکانت", callback_data="add_account")],
                [InlineKeyboardButton("🗑 حذف اکانت", callback_data="remove_account")],
                [InlineKeyboardButton("📋 لیست اکانت‌ها", callback_data="list_accounts")],
                [InlineKeyboardButton("🔍 بررسی سلامت اکانت‌ها", callback_data="check_accounts")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")]
            ])
            
            await callback_query.message.edit_text(
                "📱 منوی مدیریت اکانت‌ها\n\nلطفاً عمل مورد نظر را انتخاب کنید:",
                reply_markup=keyboard
            )
        
        elif data == "report_system":
            can_proceed, remaining = await check_cooldown(user_id)
            if not can_proceed:
                await callback_query.answer(f"لطفاً {remaining} ثانیه دیگر دوباره尝试 کنید.", show_alert=True)
                return
                
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 گزارش پست", callback_data="report_post")],
                [InlineKeyboardButton("👥 گزارش گروه/کانال", callback_data="report_channel")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")]
            ])
            
            await callback_query.message.edit_text(
                "⚠️ سیستم گزارش‌دهی\n\nلطفاً نوع گزارش را انتخاب کنید:",
                reply_markup=keyboard
            )
        
        elif data == "report_post":
            await callback_query.message.edit_text(
                "📝 گزارش پست\n\nلطفاً لینک پست مورد نظر را ارسال کنید:"
            )
            db.execute_update("""
                INSERT INTO user_states (user_id, step, report_type)
                VALUES (%s, 'report_get_link', 'post')
                ON DUPLICATE KEY UPDATE step='report_get_link', report_type='post'
            """, (user_id,))
        
        elif data == "report_channel":
            await callback_query.message.edit_text(
                "👥 گزارش گروه/کانال\n\nلطفاً لینک گروه یا کانال مورد نظر را ارسال کنید:"
            )
            db.execute_update("""
                INSERT INTO user_states (user_id, step, report_type)
                VALUES (%s, 'report_get_link', 'channel')
                ON DUPLICATE KEY UPDATE step='report_get_link', report_type='channel'
            """, (user_id,))
        
        elif data.startswith("report_reason_"):
            reason = data.replace("report_reason_", "")
            success, response = await handle_report_reason(user_id, reason)
            
            if success:
                if isinstance(response, tuple):
                    await callback_query.message.edit_text(response[0], reply_markup=response[1])
                else:
                    await callback_query.message.edit_text(response)
            else:
                await callback_query.message.edit_text(response)
                db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))
        
        elif data.startswith("report_subreason_"):
            sub_reason = data.replace("report_subreason_", "")
            success, response = await handle_report_subreason(user_id, sub_reason)
            
            if success:
                await callback_query.message.edit_text(response)
            else:
                await callback_query.message.edit_text(response)
                db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))
        
        elif data == "report_back_to_main_reasons":
            result = db.execute_query("SELECT * FROM user_states WHERE user_id = %s", (user_id,))
            if result and result[0]['step'] == 'report_choose_subreason':
                db.execute_update("UPDATE user_states SET step = 'report_choose_reason' WHERE user_id = %s", (user_id,))
                
                reasons = {
                    "spam": "هرزنامه",
                    "violence": "خشونت",
                    "pornography": "محتوای مستهجن",
                    "child_abuse": "سوء استفاده از کودکان",
                    "copyright": "نقض کپی رایت",
                    "fake": "حساب جعلی",
                    "scam": "کلاهبرداری",
                    "illegal": "فعالیت غیرقانونی",
                    "other": "سایر"
                }
                
                keyboard = []
                row = []
                for i, (key, text) in enumerate(reasons.items()):
                    row.append(InlineKeyboardButton(text, callback_data=f"report_reason_{key}"))
                    if (i + 1) % 2 == 0:
                        keyboard.append(row)
                        row = []
                if row:
                    keyboard.append(row)
                
                keyboard.append([InlineKeyboardButton("🔙 لغو", callback_data="cancel_report")])
                
                await callback_query.message.edit_text(
                    "لطفاً دلیل گزارش را انتخاب کنید:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        
        elif data.startswith("report_accounts_"):
            account_count = data.replace("report_accounts_", "")
            success, response = await handle_report_accounts_selection(user_id, int(account_count))
            
            if success:
                await callback_query.message.edit_text("تعداد اکانت‌های مورد نظر برای گزارش را انتخاب کنید:", reply_markup=response)
            else:
                await callback_query.message.edit_text(response)
                db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))
        
        elif data in ["report_join_yes", "report_join_no"]:
            join_selection = "yes" if data == "report_join_yes" else "no"
            success, response = await handle_report_join_selection(user_id, join_selection)
            
            if success:
                await callback_query.message.edit_text(response[0], reply_markup=response[1])
            else:
                await callback_query.message.edit_text(response)
                db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))
        
        elif data == "report_confirm_yes":
            result = db.execute_query("SELECT operation_id FROM user_states WHERE user_id = %s", (user_id,))
            if not result:
                await callback_query.message.edit_text("❌ خطا در پیدا کردن عملیات.")
                return
                
            operation_id = result[0]['operation_id']
            await callback_query.message.edit_text("در حال آغاز عملیات گزارش‌دهی...")
            
            asyncio.create_task(execute_report(operation_id))
            
            await callback_query.message.reply_text("✅ عملیات گزارش‌دهی آغاز شد. نتیجه به زودی اعلام خواهد شد.")
        
        elif data == "cancel_report":
            db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))
            await callback_query.message.edit_text("✅ عملیات گزارش‌دهی لغو شد.")
        
        elif data in ["report_back_to_reason", "report_back_to_accounts", "report_back_to_join"]:
            result = db.execute_query("SELECT * FROM user_states WHERE user_id = %s", (user_id,))
            if not result:
                await callback_query.message.edit_text("❌ وضعیت یافت نشد.")
                return
            
            if data == "report_back_to_reason":
                db.execute_update("UPDATE user_states SET step = 'report_choose_reason' WHERE user_id = %s", (user_id,))
                
                reasons = {
                    "spam": "هرزنامه",
                    "violence": "خشونت",
                    "pornography": "محتوای مستهجن",
                    "child_abuse": "سوء استفاده از کودکان",
                    "copyright": "نقض کپی رایت",
                    "fake": "حساب جعلی",
                    "scam": "کلاهبرداری",
                    "illegal": "فعالیت غیرقانونی",
                    "other": "سایر"
                }
                
                keyboard = []
                row = []
                for i, (key, text) in enumerate(reasons.items()):
                    row.append(InlineKeyboardButton(text, callback_data=f"report_reason_{key}"))
                    if (i + 1) % 2 == 0:
                        keyboard.append(row)
                        row = []
                if row:
                    keyboard.append(row)
                
                keyboard.append([InlineKeyboardButton("🔙 لغو", callback_data="cancel_report")])
                
                await callback_query.message.edit_text(
                    "لطفاً دلیل گزارش را انتخاب کنید:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            
            elif data == "report_back_to_accounts":
                db.execute_update("UPDATE user_states SET step = 'report_select_accounts' WHERE user_id = %s", (user_id,))
                
                available_accounts = await get_available_accounts_count()
                
                keyboard = []
                max_selectable = min(available_accounts, max_accounts_per_report)
                
                buttons = []
                for i in range(1, max_selectable + 1):
                    buttons.append(InlineKeyboardButton(str(i), callback_data=f"report_accounts_{i}"))
                    if i % 5 == 0 or i == max_selectable:
                        keyboard.append(buttons)
                        buttons = []
                
                keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="report_back_to_reason")])
                keyboard.append([InlineKeyboardButton("🔚 لغو گزارش", callback_data="cancel_report")])
                
                await callback_query.message.edit_text(
                    "تعداد اکانت‌های مورد نظر برای گزارش را انتخاب کنید:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            
            elif data == "report_back_to_join":
                db.execute_update("UPDATE user_states SET step = 'report_ask_join' WHERE user_id = %s", (user_id,))
                
                keyboard = [
                    [InlineKeyboardButton("✅ بله", callback_data="report_join_yes")],
                    [InlineKeyboardButton("❌ خیر", callback_data="report_join_no")],
                    [InlineKeyboardButton("🔙 بازگشت", callback_data="report_back_to_accounts")],
                    [InlineKeyboardButton("🔚 لغو گزارش", callback_data="cancel_report")]
                ]
                
                await callback_query.message.edit_text(
                    "آیا می‌خواهید اکانت‌ها قبل از گزارش به کانال/گروه جوین شوند؟",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        
        elif data == "main_menu":
            await start_command(client, callback_query.message)
        
        elif data == "add_account":
            await callback_query.message.edit_text("لطفاً شماره تلفن اکانت را ارسال کنید (با کد کشور):")
            db.execute_update("""
                INSERT INTO user_states (user_id, step)
                VALUES (%s, 'add_account_get_phone')
                ON DUPLICATE KEY UPDATE step='add_account_get_phone'
            """, (user_id,))
        
        elif data == "remove_account":
            accounts = db.execute_query("SELECT phone_number FROM accounts")
            if not accounts:
                await callback_query.message.edit_text("❌ هیچ اکانتی برای حذف وجود ندارد.")
                return
            
            keyboard = []
            for account in accounts:
                keyboard.append([InlineKeyboardButton(account['phone_number'], callback_data=f"remove_acc_{account['phone_number']}")])
            
            keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="manage_accounts")])
            
            await callback_query.message.edit_text(
                "🗑 حذف اکانت\n\nلطفاً اکانتی که می‌خواهید حذف کنید را انتخاب کنید:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data.startswith("remove_acc_"):
            phone = data.replace("remove_acc_", "")
            success, response = await remove_account(phone, user_id)
            
            if success:
                await callback_query.message.edit_text(f"✅ {response}")
            else:
                await callback_query.message.edit_text(f"❌ {response}")
        
        elif data == "list_accounts":
            if not await can_view_accounts(user_id):
                await callback_query.answer("شما دسترسی لازم را ندارید.", show_alert=True)
                return
                
            accounts = db.execute_query("SELECT phone_number, username, status FROM accounts")
            if not accounts:
                await callback_query.message.edit_text("❌ هیچ اکانتی وجود ندارد.")
                return
            
            text = "📋 لیست اکانت‌ها:\n\n"
            for i, account in enumerate(accounts, 1):
                status = "✅ فعال" if account['status'] == 'active' else "❌ غیرفعال"
                username = f"@{account['username']}" if account['username'] else "ندارد"
                text += f"{i}. {account['phone_number']} - {username} - {status}\n"
            
            await callback_query.message.edit_text(text)
        
        elif data == "check_accounts":
            await callback_query.message.edit_text("🔍 در حال بررسی سلامت اکانت‌ها...")
            results = await check_all_accounts()
            
            text = (
                f"✅ بررسی سلامت اکانت‌ها تکمیل شد!\n\n"
                f"📊 نتایج:\n"
                f"• ✅ اکانت‌های فعال: {results['active']}\n"
                f"• ❌ اکانت‌های غیرفعال: {results['inactive']}\n\n"
            )
            
            if results['inactive'] > 0:
                text += "⚠️ برخی اکانت‌ها مشکل دارند. به مالک گزارش ارسال شد."
            
            await callback_query.message.edit_text(text)
        
        elif data == "user_management":
            if not await is_owner(user_id):
                await callback_query.answer("فقط مالک می‌تواند کاربران را مدیریت کند.", show_alert=True)
                return
                
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ افزودن ادمین", callback_data="add_admin")],
                [InlineKeyboardButton("🗑 حذف ادمین", callback_data="remove_admin")],
                [InlineKeyboardButton("➕ افزودن مالک", callback_data="add_owner")],
                [InlineKeyboardButton("🗑 حذف مالک", callback_data="remove_owner")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")]
            ])
            
            await callback_query.message.edit_text(
                "👥 مدیریت کاربران\n\nلطفاً عمل مورد نظر را انتخاب کنید:",
                reply_markup=keyboard
            )
        
        elif data == "add_admin":
            await callback_query.message.edit_text("لطفاً آیدی عددی کاربر را برای اضافه کردن به عنوان ادمین ارسال کنید:")
            db.execute_update("""
                INSERT INTO user_states (user_id, step)
                VALUES (%s, 'add_admin_get_id')
                ON DUPLICATE KEY UPDATE step='add_admin_get_id'
            """, (user_id,))
        
        elif data == "remove_admin":
            admins = db.execute_query("SELECT user_id FROM users WHERE role = 'admin'")
            if not admins:
                await callback_query.message.edit_text("❌ هیچ ادمینی وجود ندارد.")
                return
            
            keyboard = []
            for admin in admins:
                keyboard.append([InlineKeyboardButton(str(admin['user_id']), callback_data=f"remove_admin_{admin['user_id']}")])
            
            keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="user_management")])
            
            await callback_query.message.edit_text(
                "🗑 حذف ادمین\n\nلطفاً ادمینی که می‌خواهید حذف کنید را انتخاب کنید:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    except Exception as e:
        logger.error(f"Error in callback handler: {str(e)}")
        await callback_query.message.edit_text("❌ خطایی رخ داد. لطفاً دوباره尝试 کنید.")
        db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))

@bot.on_message(filters.text & filters.private)
@error_handler
async def handle_text_messages(client, message):
    user_id = message.from_user.id
    text = message.text
    
    result = db.execute_query("SELECT * FROM user_states WHERE user_id = %s", (user_id,))
    if not result:
        return
    
    state = result[0]
    step = state['step']
    
    try:
        if step == "add_account_get_phone":
            db.execute_update("""
                UPDATE user_states SET step = 'add_account_ask_proxy', phone_number = %s 
                WHERE user_id = %s
            """, (text, user_id))
            await message.reply_text("آیا می‌خواهید از پروکسی برای این اکانت استفاده کنید؟ (بله/خیر)")
        
        elif step == "add_account_ask_proxy":
            if text.lower() in ["بله", "yes"]:
                db.execute_update("""
                    UPDATE user_states SET step = 'add_account_get_proxy' WHERE user_id = %s
                """, (user_id,))
                await message.reply_text("لطفاً پروکسی را در قالب format وارد کنید:\nمثال: socks5://user:pass@host:port")
            elif text.lower() in ["خیر", "no"]:
                phone_number = state['phone_number']
                success, response = await add_account(phone_number, user_id)
                if success:
                    await message.reply_text(response)
                else:
                    await message.reply_text(f"❌ {response}")
            else:
                await message.reply_text("لطفاً بله یا خیر پاسخ دهید.")
        
        elif step == "add_account_get_proxy":
            proxy_dict = await parse_proxy(text)
            if proxy_dict is None:
                await message.reply_text("❌ فرمت پروکسی نامعتبر است. لطفاً دوباره尝试 کنید.")
                return
                
            phone_number = state['phone_number']
            success, response = await add_account(phone_number, user_id, proxy_dict)
            if success:
                await message.reply_text(response)
            else:
                await message.reply_text(f"❌ {response}")
        
        elif step == "waiting_for_code":
            success, response = await verify_code(user_id, text)
            if success:
                await message.reply_text(response)
            else:
                await message.reply_text(f"❌ {response}")
        
        elif step == "waiting_for_password":
            success, response = await verify_password(user_id, text)
            if success:
                await message.reply_text(response)
            else:
                await message.reply_text(f"❌ {response}")
        
        elif step == "report_get_link":
            report_type = state['report_type']
            success, response = await start_report_process(user_id, report_type, text)
            if success:
                await message.reply_text("لطفاً دلیل گزارش را انتخاب کنید:", reply_markup=response)
            else:
                await message.reply_text(f"❌ {response}")
                db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))
        
        elif step == "report_enter_description":
            success, response = await handle_report_description(user_id, text)
            if success:
                await message.reply_text("تعداد اکانت‌های مورد نظر برای گزارش را انتخاب کنید:", reply_markup=response)
            else:
                await message.reply_text(f"❌ {response}")
                db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))
        
        elif step == "add_admin_get_id":
            try:
                admin_id = int(text)
                success, response = await add_admin(user_id, admin_id)
                if success:
                    await message.reply_text(response)
                else:
                    await message.reply_text(f"❌ {response}")
                db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))
            except ValueError:
                await message.reply_text("❌ آیدی باید یک عدد باشد.")
    
    except Exception as e:
        logger.error(f"Error in text handler: {str(e)}")
        await message.reply_text("❌ خطایی رخ داد. لطفاً دوباره尝试 کنید.")
        db.execute_update("DELETE FROM user_states WHERE user_id = %s", (user_id,))

# ==================== BACKGROUND TASKS ====================
async def background_health_check():
    while True:
        try:
            logger.info("Running automatic health check...")
            await check_all_accounts()
            await asyncio.sleep(health_check_interval)
        except Exception as e:
            logger.error(f"Error in background health check: {e}")
            await asyncio.sleep(300)

# ==================== MAIN LOOP ====================
if __name__ == "__main__":
    print("🤖 ربات در حال اجراست...")
    
    threading.Thread(target=keep_alive.run_flask, daemon=True).start()
    
    if not api_id or not api_hash or not bot_token or not owner_id:
        print("❌ خطا: متغیرهای محیطی ضروری تنظیم نشده‌اند!")
        exit(1)
    
    db.execute_query("""
        CREATE TABLE IF NOT EXISTS user_states (
            user_id BIGINT PRIMARY KEY,
            step VARCHAR(50),
            phone_number VARCHAR(20),
            phone_code_hash VARCHAR(100),
            api_id INT,
            api_hash VARCHAR(100),
            proxy TEXT,
            operation_id VARCHAR(36),
            report_type VARCHAR(20),
            target_entity VARCHAR(100),
            target_message_id INT,
            target_link TEXT,
            report_reason VARCHAR(50),
            account_count INT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
    """)
    
    asyncio.create_task(background_health_check())
    
    print("✅ ربات آماده است!")
    bot.run()
