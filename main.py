import os
import json
from flask import Flask, request, abort
import requests
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# ส่วน Imports ทั้งหมดที่จำเป็น
from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, ImageMessage, TextSendMessage,
    JoinEvent, FollowEvent, SourceUser, SourceGroup, TextMessage
)

# นำเข้า Parser ที่เราสร้างไว้
from slip_parser import parse_slip

# --- ส่วนตั้งค่า Environment Variables ---
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.environ.get('CHANNEL_SECRET')
OCR_SPACE_API_KEY = os.environ.get('OCR_SPACE_API_KEY')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID')
GOOGLE_CREDENTIALS_JSON_STRING = os.environ.get('GOOGLE_CREDENTIALS_JSON')
GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID')

# --- ส่วนเริ่มต้นโปรแกรมที่สำคัญ ---
app = Flask(__name__) # <-- บรรทัดสำคัญที่หายไป ได้ถูกนำกลับมาแล้ว
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)
worksheet = None

# --- ฟังก์ชันสำหรับเชื่อมต่อ Google Sheets ---
def connect_to_google_sheets():
    global worksheet
    try:
        if not GOOGLE_CREDENTIALS_JSON_STRING or not GOOGLE_SHEET_ID:
            print("CRITICAL ERROR: Google Sheets environment variables are not set.")
            return
        
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive.file']
        credentials = Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDENTIALS_JSON_STRING), scopes=scopes
        )
        gc = gspread.authorize(credentials)
        spreadsheet = gc.open_by_key(GOOGLE_SHEET_ID)
        worksheet = spreadsheet.sheet1
        print("--- Successfully connected to Google Sheets! Worksheet is ready. ---")
    except Exception as e:
        print(f"--- CRITICAL ERROR during Google Sheets connection: {e} ---")
        worksheet = None

connect_to_google_sheets()

# --- ฟังก์ชันสำหรับระบบอนุมัติ ---
def is_approved(source_id):
    if not worksheet: return False
    try:
        cell = worksheet.find(source_id)
        return cell and worksheet.cell(cell.row, 4).value.lower() == 'approved'
    except Exception as e:
        print(f"Error in is_approved: {e}")
        return False

def register_source(source_id, display_name, source_type):
    if not worksheet:
        print("Worksheet not available. Cannot register source.")
        return
    try:
        if not worksheet.find(source_id):
            new_row = [source_id, display_name, source_type, 'pending', datetime.now().isoformat()]
            worksheet.append_row(new_row)
            if ADMIN_USER_ID:
                line_bot_api.push_message(
                    ADMIN_USER_ID,
                    TextSendMessage(text=f"New {source_type} needs approval:\nName: {display_name}")
                )
    except Exception as e:
        print(f"Error registering source: {e}")

# --- Route สำหรับ UptimeRobot ---
@app.route("/", methods=['GET'])
def home():
    return "OK. I'm awake!", 200

# --- Webhook หลักสำหรับ LINE ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# --- Event Handler: รูปภาพ ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    source_id = event.source.sender_id
    
    if is_approved(source_id):
        message_content = line_bot_api.get_message_content(event.message.id)
        url_api = "https://api.ocr.space/parse/image"
        response = requests.post(url_api, 
            files={"image": ("receipt.jpg", message_content.content, "image/jpeg")},
            data={"apikey": OCR_SPACE_API_KEY, "language": "tha", "OCREngine": "2"}
        )
        result = response.json()

        if result.get("IsErroredOnProcessing") == False and result.get("ParsedResults"):
            detected_text = result["ParsedResults"][0]["ParsedText"]
            parsed_data = parse_slip(detected_text)
            
            reply_text = (
                f"สรุปรายการ:\n"
                f"-------------------\n"
                f"วันที่: {parsed_data['date']}\n"
                f"ผู้โอน: {parsed_data['account']}\n"
                f"ผู้รับ: {parsed_data['recipient']}\n"
                f"จำนวน: {parsed_data['amount']} บาท"
            )
        else:
            reply_text = "ขออภัยครับ ไม่สามารถอ่านข้อความจากรูปภาพได้"
    else:
        reply_text = "บอทกำลังรอการอนุมัติจากผู้ดูแลระบบครับ"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

# --- Event Handler: ข้อความ ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    text = event.message.text.lower()
    if text in ["ping", "wake up", "ตื่น", "หวัดดี", "สวัสดี"]:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="ระบบพร้อมทำงานแล้วครับ! 🏓")
        )

# --- Event Handler: เข้ากลุ่ม ---
@handler.add(JoinEvent)
def handle_join(event):
    if isinstance(event.source, SourceGroup):
        register_source(event.source.group_id, "Unknown Group", 'group')
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="บอทกำลังรอการอนุมัติเพื่อใช้งานในกลุ่มนี้ครับ"))

# --- Event Handler: เพิ่มเพื่อน ---
@handler.add(FollowEvent)
def handle_follow(event):
    if isinstance(event.source, SourceUser):
        try:
            profile = line_bot_api.get_profile(event.source.user_id)
            display_name = profile.display_name
        except:
            display_name = "Unknown User"
        register_source(event.source.user_id, display_name, 'user')
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ขอบคุณที่เพิ่มเป็นเพื่อนครับ! กำลังรอการอนุมัติเพื่อเริ่มใช้งาน"))

# --- ส่วนสำหรับรันโปรแกรม ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)