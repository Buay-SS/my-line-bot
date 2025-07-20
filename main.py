import os
import json
from flask import Flask, request, abort
import requests
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, ImageMessage, TextSendMessage,
    JoinEvent, FollowEvent, SourceUser, SourceGroup
)

# --- ส่วนตั้งค่า ---
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.environ.get('CHANNEL_SECRET')
OCR_SPACE_API_KEY = os.environ.get('OCR_SPACE_API_KEY')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID')
GOOGLE_CREDENTIALS_JSON_STRING = os.environ.get('GOOGLE_CREDENTIALS_JSON')
GOOGLE_SHEET_NAME = os.environ.get('GOOGLE_SHEET_NAME', "LineBotAccessControl")

# --- ส่วนเริ่มต้นโปรแกรม ---
app = Flask(__name__)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)
worksheet = None # กำหนดค่าเริ่มต้นเป็น None

# --- ฟังก์ชันสำหรับเชื่อมต่อ Google Sheets ---
def connect_to_google_sheets():
    global worksheet
    try:
        credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON_STRING)
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive.file'
        ]
        credentials = Credentials.from_service_account_info(credentials_info, scopes=scopes)
        gc = gspread.authorize(credentials)
        worksheet = gc.open(GOOGLE_SHEET_NAME).sheet1
        print("Successfully connected to Google Sheets.")
    except Exception as e:
        print(f"Error connecting to Google Sheets: {e}")
        worksheet = None

# เรียกใช้ฟังก์ชันเชื่อมต่อตอนเริ่มต้นแอป
connect_to_google_sheets()

# (ส่วนที่เหลือของโค้ดเหมือนเดิมทั้งหมด...)

# --- ฟังก์ชันตรวจสอบสิทธิ์ ---
def is_approved(source_id):
    if not worksheet: return False
    try:
        cell = worksheet.find(source_id)
        if cell:
            status = worksheet.cell(cell.row, 4).value
            return status.lower() == 'approved'
        return False
    except Exception as e:
        print(f"Error checking approval status: {e}")
        return False

# --- ฟังก์ชันบันทึกผู้ใช้/กลุ่มใหม่ ---
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
                    TextSendMessage(text=f"New {source_type} needs approval:\nName: {display_name}\nID: {source_id}")
                )
    except Exception as e:
        print(f"Error registering source: {e}")

# --- Event: เมื่อมีคนส่งรูปภาพ (เพิ่มระบบเช็คสิทธิ์) ---
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
            reply_text = "ข้อความที่อ่านได้จากรูปภาพ:\n\n" + detected_text
        else:
            error_message = result.get("ErrorMessage", ["เกิดข้อผิดพลาด"])[0]
            reply_text = f"ขออภัยครับ ไม่สามารถอ่านข้อความได้: {error_message}"
    else:
        reply_text = "บอทกำลังรอการอนุมัติจากผู้ดูแลระบบครับ โปรดติดต่อผู้สร้างบอท"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

# --- Event: เมื่อบอทถูกเชิญเข้ากลุ่ม (ใหม่) ---
@handler.add(JoinEvent)
def handle_join(event):
    if isinstance(event.source, SourceGroup):
        group_id = event.source.group_id
        try:
            group_summary = line_bot_api.get_group_summary(group_id)
            group_name = group_summary.group_name
        except:
            group_name = "Unknown Group"
        register_source(group_id, group_name, 'group')
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="สวัสดีครับ! บอทนี้กำลังรอการอนุมัติจากผู้ดูแลระบบเพื่อเริ่มใช้งานในกลุ่มนี้ครับ")
        )

# --- Event: เมื่อมีคนแอดบอทเป็นเพื่อน (ใหม่) ---
@handler.add(FollowEvent)
def handle_follow(event):
    if isinstance(event.source, SourceUser):
        user_id = event.source.user_id
        try:
            profile = line_bot_api.get_profile(user_id)
            display_name = profile.display_name
        except:
            display_name = "Unknown User"
        register_source(user_id, display_name, 'user')
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="ขอบคุณที่เพิ่มเป็นเพื่อนครับ! ขณะนี้กำลังรอการอนุมัติจากผู้ดูแลระบบเพื่อเริ่มใช้งานครับ")
        )

# --- Webhook Callback (เหมือนเดิม) ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'
    
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)