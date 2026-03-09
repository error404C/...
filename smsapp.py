import os
import time
import re
import asyncio
import threading
import secrets
from datetime import datetime
from flask import Flask, request, render_template_string, redirect, url_for, session
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from telegram import Bot
from werkzeug.security import generate_password_hash, check_password_hash

# ================= SECURE CONFIG =================
app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# Generate random web password on startup
WEB_PASSWORD = secrets.token_hex(8).upper()
print(f"🔐 WEB LOGIN PASSWORD: {WEB_PASSWORD}")
print("🌐 Go to your Render URL and login with this password!")

# Config storage (empty until web setup)
config = {
    'setup_complete': False,
    'bot_token': '',
    'admin_id': '',
    'chat_id': '',
    'ivas_email': '',
    'ivas_password': ''
}

# Load config from file if exists
CONFIG_FILE = 'config.json'
try:
    import json
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config.update(json.load(f))
        if config.get('setup_complete'):
            print("✅ Config loaded from file!")
except:
    print("ℹ️ No existing config - use web setup")

# Global bot and stats
bot = None
last_scraped_message_id = None
stats = {"checks": 0, "otps_found": 0, "last_sms": None}

# ================= CHROME SETUP =================
def get_chrome_service():
    try:
        return ChromeService()
    except:
        return ChromeService()

def get_chrome_options():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920x1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    return options

# ================= OTP FUNCTIONS =================
def extract_otp_and_service(sms_text):
    otp_match = re.search(r"\bd{4,8}\b", sms_text)
    otp_code = otp_match.group() if otp_match else "N/A"
    
    sms_lower = sms_text.lower()
    service = "Unknown"
    if any(x in sms_lower for x in ["whatsapp", "wa"]): service = "WhatsApp"
    elif any(x in sms_lower for x in ["facebook", "fb"]): service = "Facebook"
    elif any(x in sms_lower for x in ["google", "gmail"]): service = "Google"
    elif "telegram" in sms_lower: service = "Telegram"
    elif any(x in sms_lower for x in ["instagram", "ig"]): service = "Instagram"
    
    return otp_code, service

def scrape_ivasms():
    global last_scraped_message_id, stats
    if not config['setup_complete']:
        return None
        
    stats["checks"] += 1
    driver = None

    try:
        print(f"🔍 [#{stats['checks']}] Scraping iVASMS...")
        service = get_chrome_service()
        driver = webdriver.Chrome(service=service, options=get_chrome_options())
        
        driver.get("https://www.ivasms.com/portal/sms/received")
        
        # Login
        email_field = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.NAME, "email"))
        )
        email_field.send_keys(config['ivas_email'])
        
        driver.find_element(By.NAME, "password").send_keys(config['ivas_password'])
        driver.find_element(By.XPATH, "//button[contains(text(),'Sign in')]").click()
        
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//table"))
        )

        first_row = driver.find_element(By.XPATH, "//table/tbody/tr[1]")
        cols = first_row.find_elements(By.TAG_NAME, "td")

        if len(cols) < 4:
            return None

        date_time = cols[1].text.strip()
        number = cols[2].text.strip()
        sms_text = cols[3].text.strip()
        stats["last_sms"] = sms_text[:50]

        current_id = f"{date_time}-{number}-{sms_text}"
        if current_id == last_scraped_message_id:
            return None

        last_scraped_message_id = current_id
        stats["otps_found"] += 1
        
        otp, service_name = extract_otp_and_service(sms_text)
        message = f"""
🔥 **{service_name} OTP DETECTED** 🔥

⏰ `{date_time}`
📱 `{number}`
🔑 **OTP: `{otp}`**

💬 `{sms_text}`
        """
        return message

    except Exception as e:
        print(f"❌ Scrape error: {e}")
        return None
    finally:
        if driver:
            driver.quit()

async def send_telegram(chat_id, message):
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
        print(f"✅ OTP sent to {chat_id}")
    except Exception as e:
        print(f"❌ Telegram error: {e}")

# ================= MAIN SCRAPER =================
async def scraper_loop():
    while True:
        if config['setup_complete'] and bot:
            otp_msg = scrape_ivasms()
            if otp_msg:
                await send_telegram(config['chat_id'], otp_msg)
        await asyncio.sleep(30)

def start_scraper():
    print("🚀 Scraper started!")
    asyncio.run(scraper_loop())

# ================= WEB SETUP PAGES =================
SETUP_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>IVASMS OTP Bot Setup</title>
    <style>
        body { font-family: Arial; max-width: 600px; margin: 50px auto; padding: 20px; background: #1a1a1a; color: white; }
        input, button { width: 100%; padding: 12px; margin: 8px 0; border: none; border-radius: 6px; font-size: 16px; }
        input { background: #333; color: white; }
        button { background: #00ff88; color: black; font-weight: bold; cursor: pointer; }
        .status { padding: 15px; margin: 10px 0; border-radius: 6px; }
        .success { background: #004d00; }
        .error { background: #660000; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    </style>
</head>
<body>
    <h1>🚀 IVASMS OTP Bot Setup</h1>
    
    {% if not config.setup_complete %}
    <div class="status error">
        <h3>🔐 Login Required</h3>
        <p><b>Web Password:</b> <code>{{ web_password }}</code></p>
        {% if session.get('failed') %}
            <p>❌ Wrong password!</p>
        {% endif %}
        <form method="POST">
            <input type="password" name="web_password" placeholder="Enter web password" required>
            <button type="submit">Login → Setup</button>
        </form>
    {% else %}
    <div class="status success">
        <h3>✅ Bot Configured!</h3>
        <p>🔄 Scraping every 30 seconds</p>
        <p>📊 Checks: {{ stats.checks }} | OTPS: {{ stats.otps_found }}</p>
    </div>
    
    <h3>📱 Telegram Bot</h3>
    <form method="POST">
        <input type="text" name="bot_token" value="{{ config.bot_token }}" placeholder="Bot Token" required>
        <div class="grid">
            <input type="text" name="admin_id" value="{{ config.admin_id }}" placeholder="Admin ID (7578254597)">
            <input type="text" name="chat_id" value="{{ config.chat_id }}" placeholder="Group ID (-100...)">
        </div>
        
    <h3>🌐 iVASMS Login</h3>
        <div class="grid">
            <input type="email" name="ivas_email" value="{{ config.ivas_email }}" placeholder="iVASMS Email">
            <input type="password" name="ivas_password" value="{{ config.ivas_password }}" placeholder="iVASMS Password">
        </div>
        <button type="submit" name="action" value="save">💾 Save & Start Bot</button>
        <button type="submit" name="action" value="test">🧪 Test Scrape</button>
    </form>
    
    {% endif %}
    
    <hr>
    <h3>📋 Status</h3>
    <p>⏰ Last Check: {{ stats.last_sms or 'None' }}</p>
    <p>🔗 Startup Password: <code>{{ web_password }}</code></p>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def setup():
    if request.method == "POST":
        # Login check
        if not config['setup_complete']:
            if request.form['web_password'] != WEB_PASSWORD:
                session['failed'] = True
                return render_template_string(SETUP_HTML, config=config, stats=stats, web_password=WEB_PASSWORD, session=session)
            
            session['logged_in'] = True
            config['setup_complete'] = True
            save_config()
        
        # Save config
        if request.form.get('action') == 'save':
            config.update({
                'bot_token': request.form['bot_token'],
                'admin_id': request.form['admin_id'],
                'chat_id': request.form['chat_id'],
                'ivas_email': request.form['ivas_email'],
                'ivas_password': request.form['ivas_password']
            })
            global bot
            bot = Bot(token=config['bot_token'])
            
            # Send startup message
            asyncio.create_task(send_startup_message())
            save_config()
            return render_template_string(SETUP_HTML, config=config, stats=stats, web_password=WEB_PASSWORD)
        
        # Test scrape
        if request.form.get('action') == 'test':
            otp_msg = scrape_ivasms()
            if otp_msg:
                stats["otps_found"] += 1
                return f"✅ TEST SUCCESS! Found OTP: {otp_msg[:100]}..."
    
    return render_template_string(SETUP_HTML, config=config, stats=stats, web_password=WEB_PASSWORD)

def save_config():
    try:
        import json
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)
    except:
        pass

async def send_startup_message():
    try:
        msg = f"""
🤖 **IVASMS BOT STARTED** (Render)

✅ Config saved via web panel
✅ Scraping started (30s intervals)
✅ OTPS → {config['chat_id']}

📊 Stats LIVE on web dashboard
        """
        await bot.send_message(chat_id=config['admin_id'], text=msg, parse_mode='Markdown')
    except:
        pass

@app.route("/health")
def health():
    return "OK", 200

# ================= START =================
if __name__ == "__main__":
    print("🎯 SECURE IVASMS BOT STARTING...")
    print(f"🔐 WEB PASSWORD: {WEB_PASSWORD}")
    print("🌐 Visit your Render URL to setup!")
    
    # Start scraper if already configured
    if config['setup_complete']:
        scraper_thread = threading.Thread(target=start_scraper, daemon=True)
        scraper_thread.start()
        bot = Bot(token=config['bot_token'])
    
    # Render auto-port
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)