#Copyright @Arslan-MD
#Updates Channel t.me/arslanmd
from flask import Flask, request, jsonify
from datetime import datetime
import cloudscraper
import json
from bs4 import BeautifulSoup
import logging
import os
import gzip
from io import BytesIO
import brotli
from telegram import Bot

import time
import threading

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# ------------------ Telegram config ------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = Bot(token=TELEGRAM_TOKEN)

sent_otps = {}

def send_to_telegram(message_text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram token or chat ID not set. Skipping send.")
        return
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message_text)
        logger.debug(f"Sent to Telegram: {message_text}")
    except Exception as e:
        logger.error(f"Error sending to Telegram: {e}")

# ------------------ IVAS SMS Client ------------------
class IVASSMSClient:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper()
        self.base_url = "https://www.ivasms.com"
        self.logged_in = False
        self.csrf_token = None
        
        self.scraper.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        })

    def decompress_response(self, response):
        encoding = response.headers.get('Content-Encoding', '').lower()
        content = response.content
        try:
            if encoding == 'gzip':
                content = gzip.decompress(content)
            elif encoding == 'br':
                content = brotli.decompress(content)
            return content.decode('utf-8', errors='replace')
        except Exception:
            return response.text

    def load_cookies(self, file_path="cookies.json"):
        try:
            if os.getenv("COOKIES_JSON"):
                cookies_raw = json.loads(os.getenv("COOKIES_JSON"))
            else:
                with open(file_path, 'r') as file:
                    cookies_raw = json.load(file)

            if isinstance(cookies_raw, dict):
                return cookies_raw
            elif isinstance(cookies_raw, list):
                cookies = {}
                for cookie in cookies_raw:
                    if 'name' in cookie and 'value' in cookie:
                        cookies[cookie['name']] = cookie['value']
                return cookies
        except:
            return None

    def login_with_cookies(self, cookies_file="cookies.json"):
        cookies = self.load_cookies(cookies_file)
        if not cookies:
            return False
        
        for name, value in cookies.items():
            self.scraper.cookies.set(name, value, domain="www.ivasms.com")
        
        try:
            response = self.scraper.get(f"{self.base_url}/portal/sms/received", timeout=10)
            if response.status_code == 200:
                html_content = self.decompress_response(response)
                soup = BeautifulSoup(html_content, 'html.parser')
                csrf_input = soup.find('input', {'name': '_token'})
                if csrf_input:
                    self.csrf_token = csrf_input.get('value')
                    self.logged_in = True
                    return True
        except:
            return False

    def check_otps(self, from_date="", to_date=""):
        if not self.logged_in:
            return None
        
        payload = {
            'from': from_date,
            'to': to_date,
            '_token': self.csrf_token
        }

        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Origin': self.base_url,
            'Referer': f"{self.base_url}/portal/sms/received"
        }

        response = self.scraper.post(
            f"{self.base_url}/portal/sms/received/getsms",
            data=payload,
            headers=headers
        )

        if response.status_code != 200:
            return None

        html_content = self.decompress_response(response)
        soup = BeautifulSoup(html_content, 'html.parser')

        sms_details = []
        items = soup.select("div.item")

        for item in items:
            country_number = item.select_one(".col-sm-4").text.strip()

            sms_details.append({
                'country_number': country_number
            })

        return {'sms_details': sms_details}

    def get_sms_details(self, phone_range, from_date="", to_date=""):
        payload = {
            '_token': self.csrf_token,
            'start': from_date,
            'end': to_date,
            'range': phone_range
        }

        response = self.scraper.post(
            f"{self.base_url}/portal/sms/received/getsms/number",
            data=payload
        )

        html_content = self.decompress_response(response)
        soup = BeautifulSoup(html_content, 'html.parser')

        number_details = []
        items = soup.select("div.card.card-body")

        for item in items:
            phone_number = item.select_one(".col-sm-4").text.strip()

            number_details.append({
                'phone_number': phone_number
            })

        return number_details

    def get_otp_message(self, phone_number, phone_range, from_date="", to_date=""):
        payload = {
            '_token': self.csrf_token,
            'start': from_date,
            'end': to_date,
            'Number': phone_number,
            'Range': phone_range
        }

        response = self.scraper.post(
            f"{self.base_url}/portal/sms/received/getsms/number/sms",
            data=payload
        )

        html_content = self.decompress_response(response)
        soup = BeautifulSoup(html_content, 'html.parser')

        message = soup.select_one(".col-9.col-sm-6 p")
        return message.text.strip() if message else None

    def get_all_otp_messages(self, sms_details, from_date="", to_date="", limit=None):
        all_otp_messages = []

        for detail in sms_details:
            phone_range = detail['country_number']
            number_details = self.get_sms_details(phone_range, from_date, to_date)

            if number_details:
                for number_detail in number_details:

                    if limit and len(all_otp_messages) >= limit:
                        return all_otp_messages

                    phone_number = number_detail['phone_number']
                    otp_message = self.get_otp_message(phone_number, phone_range, from_date, to_date)

                    if otp_message:
                        all_otp_messages.append({
                            'range': phone_range,
                            'phone_number': phone_number,
                            'otp_message': otp_message
                        })

                        otp_key = f"{phone_number}_{otp_message}"
                        current_time = time.time()

                        if otp_key not in sent_otps:
                            sent_otps[otp_key] = current_time
                            send_to_telegram(f"OTP for {phone_number}: {otp_message}")

                        for key in list(sent_otps.keys()):
                            if current_time - sent_otps[key] > 300:
                                del sent_otps[key]

        return all_otp_messages


# ------------------ Flask App ------------------
app = Flask(__name__)
client = IVASSMSClient()

def auto_check_loop():
    logger.info("🔁 Auto OTP loop started")

    while True:
        try:
            today = datetime.now().strftime("%d/%m/%Y")

            result = client.check_otps(from_date=today)

            if result:
                client.get_all_otp_messages(
                    result.get('sms_details', []),
                    from_date=today
                )

        except Exception as e:
            logger.error(f"Loop error: {e}")

        time.sleep(10)

with app.app_context():
    client.login_with_cookies()

@app.route('/')
def welcome():
    return jsonify({'status': 'API running'})

if __name__ == '__main__':
    loop_thread = threading.Thread(target=auto_check_loop)
    loop_thread.start()

    app.run(host='0.0.0.0', port=5000)
