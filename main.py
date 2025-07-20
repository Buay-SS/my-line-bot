import os
import json
from flask import Flask, request, abort
import requests
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

from linebot import (LineBotApi, WebhookHandler)
from linebot.exceptions import (InvalidSignatureError)
from linebot.models import (MessageEvent, ImageMessage, TextSendMessage, JoinEvent, FollowEvent, SourceUser, SourceGroup, TextMessage)

from slip_parser import parse_slip

# --- ส่วนตั้งค่า ---
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.environ.get('CHANNEL_SECRET')
OCR_SPACE_API_KEY = os.environ.get('OCR_SPACE_API_KEY')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID')
GOOGLE_CREDENTIALS_JSON_STRING = os.environ.get('GOOGLE_CREDENTIALS_JSON')
GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID')

# --- ส่วนเริ่มต้นโปรแกรม ---
app = Flask(__name__)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# --- ระบบจัดการการเชื่อมต่อและ Cache ---
_spreadsheet = None # เปลี่ยนจาก worksheet เป็น spreadsheet object
_aliases_cache = None # Cache สำหรับเก็บนามแฝง

def get_spreadsheet():
    """เชื่อมต่อกับ Google Spreadsheet และเก็บไว้ใน Cache"""
    global _spreadsheet
    if _spreadsheet:
        return _spreadsheet

    print("--- First time access. Connecting to Google Spreadsheet... ---")
    try:
        # (โค้ดเชื่อมต่อส่วนนี้เหมือนเดิม)
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive.file']
        credentials = Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDENTIALS_JSON_STRING), scopes=scopes
        )
        gc = gspread.authorize(credentials)
        _spreadsheet = gc.open_by_key(GOOGLE_SHEET_ID)
        print("--- Successfully connected to Google Spreadsheet! ---")
        return _spreadsheet
    except Exception as e:
        print(f"--- CRITICAL ERROR during Google Spreadsheet connection: {e} ---")
        return None

def get_aliases():
    """อ่านข้อมูลนามแฝงจากชีท 'Aliases' และเก็บไว้ใน Cache"""
    global _aliases_cache
    if _aliases_cache is not None:
        return _aliases_cache
    
    print("--- First time access. Reading aliases... ---")
    spreadsheet = get_spreadsheet()
    if not spreadsheet:
        _aliases_cache = {} # ถ้าเชื่อมต่อไม่ได้ ให้ใช้ dict ว่าง
        return _aliases_cache

    try:
        alias_sheet = spreadsheet.worksheet("Aliases")
        records = alias_sheet.get_all_records()
        # แปลง list of dicts ให้เป็น dict เดียวเพื่อง่ายต่อการค้นหา
        _aliases_cache = {record['OriginalName']: record['Nickname'] for record in records if record.get('OriginalName')}
        print(f"--- Successfully loaded {_aliases_cache.__len__()} aliases. ---")
        return _aliases_cache
    except gspread.exceptions.WorksheetNotFound:
        print("--- WARNING: 'Aliases' worksheet not found. Alias system disabled. ---")
        _aliases_cache = {}
        return _aliases_cache
    except Exception as e:
        print(f"--- ERROR reading aliases: {e} ---")
        _aliases_cache = {}
        return _aliases_cache


# --- ฟังก์ชันสำหรับระบบอนุมัติ (แก้ไขเล็กน้อย) ---
def register_source(source_id, display_name, source_type):
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return
    try:
        worksheet = spreadsheet.worksheet("Sheet1") # ระบุชีทที่ถูกต้อง
        if not worksheet.find(source_id):
            new_row = [source_id, display_name, source_type, 'pending', datetime.now().isoformat()]
            worksheet.append_row(new_row)
            if ADMIN_USER_ID:
                line_bot_api.push_message(ADMIN_USER_ID, TextSendMessage(text=f"New {source_type} needs approval:\nName: {display_name}"))
    except Exception as e:
        print(f"Error registering source: {e}")

def is_approved(source_id):
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return False
    try:
        worksheet = spreadsheet.worksheet("Sheet1") # ระบุชีทที่ถูกต้อง
        cell = worksheet.find(source_id)
        return cell and worksheet.cell(cell.row, 4).value.lower() == 'approved'
    except Exception as e:
        return False

# --- Event Handler: รูปภาพ (อัปเกรดใหม่!) ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    source_id = event.source.sender_id
    if not is_approved(source_id):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="บอทกำลังรอการอนุมัติจากผู้ดูแลระบบครับ"))
        return

    # ถ้าได้รับอนุมัติแล้ว...
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
        
        # --- ส่วนของระบบนามแฝง ---
        aliases = get_aliases()
        display_account = aliases.get(parsed_data['account'], parsed_data['account'])
        display_recipient = aliases.get(parsed_data['recipient'], parsed_data['recipient'])
        # -------------------------
        
        reply_text = (
            f"สรุปรายการ:\n"
            f"-------------------\n"
            f"วันที่: {parsed_data['date']}\n"
            f"จาก: {display_account}\n"    # <-- ใช้ชื่อที่ผ่านการแปลงแล้ว
            f"ถึง: {display_recipient}\n" # <-- ใช้ชื่อที่ผ่านการแปลงแล้ว
            f"จำนวน: {parsed_data['amount']} บาท"
        )
    else:
        reply_text = "ขออภัยครับ ไม่สามารถอ่านข้อความจากรูปภาพได้"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

# (โค้ดส่วนที่เหลือทั้งหมดเหมือนเดิม ไม่ต้องแก้ไข)
# ... (Route, Text handler, Join/Follow handlers, etc.) ...
@app.route("/", methods=['GET', 'HEAD'])
def home():
    return "OK", 200

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    text = event.message.text.lower()
    if text in ["ping", "wake up", "ตื่น", "หวัดดี", "สวัสดี"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ระบบพร้อมทำงานแล้วครับ! 🏓"))
    # เพิ่มคำสั่งสำหรับเคลียร์ Cache ของ Aliases
    elif text == "reload aliases" and event.source.user_id == ADMIN_USER_ID:
        global _aliases_cache
        _aliases_cache = None # ตั้งค่าให้เป็น None เพื่อให้ครั้งต่อไปอ่านใหม่
        get_aliases() # เรียกเพื่อโหลดใหม่ทันที
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"โหลดข้อมูลนามแฝงใหม่ {_aliases_cache.__len__()} รายการสำเร็จ!"))

@handler.add(JoinEvent)
def handle_join(event):
    if isinstance(event.source, SourceGroup):
        register_source(event.source.group_id, "Unknown Group", 'group')
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="บอทกำลังรอการอนุมัติเพื่อใช้งานในกลุ่มนี้ครับ"))

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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)