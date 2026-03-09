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
    
    if request.method == 'POST':
        try:
            # Web login
            if not config['setup_complete']:
                if request.form.get('web_password') != WEB_PASSWORD:
                    return render_template_string(HTML_TEMPLATE, 
                        config=config, stats=stats, web_password=WEB_PASSWORD, error="❌ Wrong password!")
                
                config['setup_complete'] = True
                return render_template_string(HTML_TEMPLATE, config=config, stats=stats, 
                    web_password=WEB_PASSWORD, success="✅ Logged in! Fill credentials below.")
            
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
                    f"🤖 **IVASMS BOT STARTED!**

✅ Config saved
📤 OTPS → {config['chat_id']}
🔄 Scraping every 30s

📊 Stats on web dashboard")
                
                # Start scraper
                if not scraper_running:
                    scraper_running = True
                    thread = threading.Thread(target=scraper_loop, daemon=True)
                    thread.start()
                
                return render_template_string(HTML_TEMPLATE, config=config, stats=stats, 
                    web_password=WEB_PASSWORD, success="✅ Bot started! Scraping live!")
            else:
                return render_template_string(HTML_TEMPLATE, config=config, stats=stats, 
                    web_password=WEB_PASSWORD, error="❌ Save failed!")
                
        except Exception as e:
            return render_template_string(HTML_TEMPLATE, coonfig['chat_id']}

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
