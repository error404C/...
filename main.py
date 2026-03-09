import os
import json
import time
import re
import threading
import secrets
from datetime import datetime
from flask import Flask, request, render_template_string
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from telegram import Bot
from telegram.error import TelegramError

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# ================= CONFIG =================
WEB_PASSWORD = secrets.token_hex(8).upper()
print(f"🔐 WEB LOGIN PASSWORD: {WEB_PASSWORD}")
print("🌐 Go to your Render URL and login!")

config = {'setup_complete': False, 'bot_token': '', 'admin_id': '', 'chat_id': '', 'ivas_email': '', 'ivas_password': ''}
stats = {"checks": 0, "otps_found": 0, "last_sms": None}
last_scraped_message_id = None
scraper_running = False

# Load existing config
CONFIG_FILE = 'config.json'
try:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config.update(json.load(f))
        if config.get('setup_complete'):
            print("✅ Config loaded from file!")
except:
    print("ℹ️ No config file found")

# ================= HTML TEMPLATE =================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head><title>IVASMS Bot</title>
<style>
body { font-family: Arial; max-width: 800px; margin: 50px auto; padding: 20px; }
input, button { padding: 10px; margin: 5px; width: 100%; box-sizing: border-box; }
button { background: #0088cc; color: white; border: none; cursor: pointer; }
.stats { background: #f0f8ff; padding: 20px; border-radius: 10px; margin: 20px 0; }
</style></head>
<body>
<h1>🤖 IVASMS OTP Bot</h1>
{{ web_password_msg|safe }}
<div class="stats">
<h3>📊 Stats</h3>
Checks: {{ stats.checks }} | OTPS: {{ stats.otps_found }}<br>
Last SMS: {{ stats.last_sms or 'None' }}
</div>
<form method="POST">
{% if not config.setup_complete %}
<input type="password" name="web_password" placeholder="🔐 Enter web password">
<button>Login</button>
{% else %}
<input type="text" name="bot_token" value="{{ config.bot_token }}" placeholder="Bot Token">
<input type="text" name="admin_id" value="{{ config.admin_id }}" placeholder="Admin ID">
<input type="text" name="chat_id" value="{{ config.chat_id }}" placeholder="Chat ID">
<input type="text" name="ivas_email" value="{{ config.ivas_email }}" placeholder="iVASMS Email">
<input type="password" name="ivas_password" value="{{ config.ivas_password }}" placeholder="iVASMS Password">
<button>🚀 Start Bot</button>
{% endif %}
</form>
</body></html>
"""

# ================= CHROME SETUP =================
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
    otp = otp_match.group() if otp_match else "No OTP"
    
    sms_lower = sms_text.lower()
    service = "Unknown"
    if "whatsapp" in sms_lower or "wa" in sms_lower: service = "WhatsApp"
    elif "facebook" in sms_lower or "fb" in sms_lower: service = "Facebook"
    elif "google" in sms_lower: service = "Google"
    elif "telegram" in sms_lower: service = "Telegram"
    elif "instagram" in sms_lower: service = "Instagram"
    
    return otp, service

def scrape_ivasms():
    global last_scraped_message_id, stats
    if not config.get('setup_complete'):
        return None
        
    stats["checks"] += 1
    driver = None
    
    try:
        print(f"🔍 Scraping iVASMS... (Check #{stats['checks']})")
        service = ChromeService()
        driver = webdriver.Chrome(service=service, options=get_chrome_options())
        
        # Login
        driver.get("https://www.ivasms.com/portal/sms/received")
        email_field = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.NAME, "email"))
        )
        email_field.clear()
        email_field.send_keys(config['ivas_email'])
        
        password_field = driver.find_element(By.NAME, "password")
        password_field.clear()
        password_field.send_keys(config['ivas_password'])
        
        driver.find_element(By.XPATH, "//button[contains(text(),'Sign in')]").click()
        
        # Wait for SMS table
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//table/tbody/tr"))
        )
        
        # Get latest SMS
        first_row = driver.find_element(By.XPATH, "//table/tbody/tr[1]")
        cols = first_row.find_elements(By.TAG_NAME, "td")
        
        if len(cols) >= 4:
            date_time = cols[1].text.strip()
            number = cols[2].text.strip()
            sms_text = cols[3].text.strip()
            
            current_id = f"{date_time}-{number}"
            if current_id != last_scraped_message_id:
                last_scraped_message_id = current_id
                stats["last_sms"] = sms_text[:50]
                
                otp, service = extract_otp_and_service(sms_text)
                message = f"""
🔥 **{service} OTP!** 🔥
⏰ {date_time}
📱 {number}
🔑 **{otp}**
💬 {sms_text}
                """
                stats["otps_found"] += 1
                print(f"✅ NEW OTP FOUND: {otp}")
                return message.strip()
                
    except Exception as e:
        print(f"❌ Scrape error: {str(e)[:100]}")
        return None
    finally:
        if driver:
            driver.quit()
    return None

# ================= TELEGRAM =================
def send_telegram(chat_id, message):
    try:
        bot = Bot(token=config['bot_token'])
        bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
        print(f"✅ Sent to {chat_id}")
        return True
    except TelegramError as e:
        print(f"❌ Telegram error: {e}")
        return False

def save_config():
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)
        print("✅ Config saved!")
        return True
    except Exception as e:
        print(f"❌ Save error: {e}")
        return False

# ================= SCRAPER LOOP =================
def scraper_loop():
    global scraper_running
    print("🚀 Scraper started!")
    while scraper_running:
        try:
            if config.get('setup_complete') and config.get('bot_token'):
                otp_message = scrape_ivasms()
                if otp_message:
                    send_telegram(config['chat_id'], otp_message)
            time.sleep(30)
        except:
            time.sleep(30)

# ================= WEB PAGES =================
@app.route("/", methods=['GET', 'POST'])
def index():
    global scraper_running
    
    web_password_msg = ""
    
    if request.method == 'POST':
        try:
            # Web login
            if not config['setup_complete']:
                if request.form.get('web_password') != WEB_PASSWORD:
                    web_password_msg = "❌ Wrong password!"
                    return render_template_string(HTML_TEMPLATE, 
                        config=config, stats=stats, web_password=WEB_PASSWORD, 
                        web_password_msg=web_password_msg)
                
                config['setup_complete'] = True
                web_password_msg = "✅ Logged in! Fill credentials below."
                return render_template_string(HTML_TEMPLATE, config=config, stats=stats, 
                    web_password=WEB_PASSWORD, web_password_msg=web_password_msg)
            
            # Save config
            config.update({
                'bot_token': request.form['bot_token'],
                'admin_id': request.form['admin_id'],
                'chat_id': request.form['chat_id'],
                'ivas_email': request.form['ivas_email'],
                'ivas_password': request.form['ivas_password']
            })
            
            if save_config():
                # Send startup message
                send_telegram(config['admin_id'], 
                    f"""🤖 **IVASMS BOT STARTED!**
✅ Config saved
📤 OTPS → {config['chat_id']}
🔄 Scraping every 30s
📊 Stats on web dashboard""")
                
                # Start scraper
                if not scraper_running:
                    scraper_running = True
                    thread = threading.Thread(target=scraper_loop, daemon=True)
                    thread.start()
                
                web_password_msg = "✅ Bot started! Scraping live!"
            else:
                web_password_msg = "❌ Save failed!"
                
        except Exception as e:
            web_password_msg = f"❌ Error: {str(e)[:50]}"
    
    return render_template_string(HTML_TEMPLATE, config=config, stats=stats, 
        web_password=WEB_PASSWORD, web_password_msg=web_password_msg)

@app.route("/health")
def health():
    return "OK", 200

# ================= START =================
if __name__ == "__main__":
    print("🎯 SECURE IVASMS BOT STARTING...")
    print(f"🔐 WEB PASSWORD: {WEB_PASSWORD}")
    print("🌐 Visit your Render URL to setup!")
    
    # Render auto-port
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
