import os
import json
import re
from flask import Flask, request, abort
import requests
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials

from linebot import (LineBotApi, WebhookHandler)
from linebot.exceptions import (InvalidSignatureError)
from linebot.models import (MessageEvent, ImageMessage, TextSendMessage, JoinEvent, FollowEvent, SourceUser, SourceGroup, TextMessage)

from slip_parser import parse_slip

# --- ส่วนตั้งค่า (เหมือนเดิม) ---
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.environ.get('CHANNEL_SECRET')
OCR_SPACE_API_KEY = os.environ.get('OCR_SPACE_API_KEY')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID')
GOOGLE_CREDENTIALS_JSON_STRING = os.environ.get('GOOGLE_CREDENTIALS_JSON')
GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID')

# --- ส่วนเริ่มต้นโปรแกรม (เหมือนเดิม) ---
app = Flask(__name__)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# --- ระบบจัดการการเชื่อมต่อและ Cache (เหมือนเดิม) ---
_spreadsheet = None
_aliases_cache = None

def get_spreadsheet():
    global _spreadsheet
    if _spreadsheet: return _spreadsheet
    print("--- Connecting to Google Spreadsheet... ---")
    try:
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive.file']
        credentials = Credentials.from_service_account_info(json.loads(GOOGLE_CREDENTIALS_JSON_STRING), scopes=scopes)
        gc = gspread.authorize(credentials)
        _spreadsheet = gc.open_by_key(GOOGLE_SHEET_ID)
        print("--- Successfully connected to Google Spreadsheet! ---")
        return _spreadsheet
    except Exception as e:
        print(f"--- CRITICAL ERROR during Google Spreadsheet connection: {e} ---")
        return None

def get_aliases():
    global _aliases_cache
    if _aliases_cache is not None: return _aliases_cache
    print("--- Reading aliases... ---")
    spreadsheet = get_spreadsheet()
    if not spreadsheet:
        _aliases_cache = {}
        return _aliases_cache
    try:
        alias_sheet = spreadsheet.worksheet("Aliases")
        records = alias_sheet.get_all_records()
        _aliases_cache = {record['OriginalName']: record['Nickname'] for record in records if record.get('OriginalName')}
        print(f"--- Loaded {_aliases_cache.__len__()} aliases. ---")
        return _aliases_cache
    except Exception as e:
        print(f"--- ERROR reading aliases: {e} ---")
        _aliases_cache = {}
        return _aliases_cache

# =========================================================
#  **ฟังก์ชันใหม่ล่าสุด: บันทึกรายการลงชีท Transactions**
# =========================================================
def log_transaction_to_sheet(log_data):
    spreadsheet = get_spreadsheet()
    if not spreadsheet:
        return False, "ไม่สามารถเชื่อมต่อกับฐานข้อมูล (Sheet) ได้"
    try:
        worksheet = spreadsheet.worksheet("Transactions")
        
        # สร้าง Timestamp เวลาประเทศไทย (UTC+7)
        thai_tz = timezone(timedelta(hours=7))
        timestamp = datetime.now(thai_tz).strftime("%Y-%m-%d %H:%M:%S")
        
        new_row = [
            timestamp,
            log_data.get('date', 'N/A'),
            log_data.get('from', 'N/A'),
            log_data.get('to', 'N/A'),
            log_data.get('amount', 0.0),
            log_data.get('recorded_by', 'N/A')
        ]
        worksheet.append_row(new_row, value_input_option='USER_ENTERED')
        print(f"--- Successfully logged transaction for {log_data.get('recorded_by')} ---")
        return True, "บันทึกรายการเรียบร้อย!"
    except Exception as e:
        print(f"--- ERROR logging transaction: {e} ---")
        return False, "เกิดข้อผิดพลาดในการบันทึกข้อมูล"


# --- Event Handler: รูปภาพ (อัปเกรดครั้งสุดท้าย!) ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    source_id = event.source.sender_id
    if not is_approved(source_id):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="บอทกำลังรอการอนุมัติจากผู้ดูแลระบบครับ"))
        return

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
        
        aliases = get_aliases()
        display_account = aliases.get(parsed_data.get('account'), parsed_data.get('account'))
        display_recipient = aliases.get(parsed_data.get('recipient'), parsed_data.get('recipient'))
        
        summary_text = (
            f"สรุปรายการ:\n"
            f"-------------------\n"
            f"วันที่: {parsed_data.get('date', 'N/A')}\n"
            f"จาก: {display_account}\n"
            f"ถึง: {display_recipient}\n"
            f"จำนวน: {parsed_data.get('amount', 'N/A')} บาท"
        )

        # --- ส่วนของการบันทึกข้อมูล ---
        log_data = {
            'date': parsed_data.get('date', 'N/A'),
            'from': display_account,
            'to': display_recipient,
            'amount': parsed_data.get('amount', 0.0),
            'recorded_by': source_id
        }
        log_success, log_message = log_transaction_to_sheet(log_data)
        # -------------------------

        # เพิ่มสถานะการบันทึกลงในข้อความตอบกลับ
        final_reply_text = f"{summary_text}\n-------------------\nสถานะ: {log_message}"

    else:
        final_reply_text = "ขออภัยครับ ไม่สามารถอ่านข้อความจากรูปภาพได้"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=final_reply_text))


# (โค้ดส่วนอื่นๆ ที่เหลือทั้งหมดเหมือนเดิม ไม่ต้องแก้ไข)
# ...
def add_alias_to_sheet(original_name, nickname):
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return False, "ไม่สามารถเชื่อมต่อกับฐานข้อมูลได้"
    try:
        alias_sheet = spreadsheet.worksheet("Aliases")
        cell = alias_sheet.find(original_name, in_column=1)
        if cell:
            alias_sheet.update_cell(cell.row, 2, nickname)
            message = "อัปเดตนามแฝงสำเร็จ!"
        else:
            alias_sheet.append_row([original_name, nickname])
            message = "เพิ่มนามแฝงใหม่สำเร็จ!"
        global _aliases_cache
        _aliases_cache = None
        return True, message
    except Exception as e:
        return False, f"เกิดข้อผิดพลาด: {e}"

def is_approved(source_id):
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return False
    try:
        worksheet = spreadsheet.worksheet("Sheet1")
        cell = worksheet.find(source_id)
        return cell and worksheet.cell(cell.row, 4).value.lower() == 'approved'
    except Exception as e: return False

def register_source(source_id, display_name, source_type):
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return
    try:
        worksheet = spreadsheet.worksheet("Sheet1")
        if not worksheet.find(source_id):
            worksheet.append_row([source_id, display_name, source_type, 'pending', datetime.now().isoformat()])
            if ADMIN_USER_ID:
                line_bot_api.push_message(ADMIN_USER_ID, TextSendMessage(text=f"New {source_type} needs approval:\nName: {display_name}"))
    except Exception as e: print(f"Error registering source: {e}")

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
    text = event.message.text
    user_id = event.source.user_id
    if user_id == ADMIN_USER_ID:
        if text.lower().startswith("alias:"):
            try:
                command_body = text[len("alias:"):].strip()
                original_name, nickname = [part.strip() for part in command_body.split('=', 1)]
                success, message = add_alias_to_sheet(original_name, nickname)
                reply_text = message
            except ValueError:
                reply_text = "รูปแบบคำสั่งผิดพลาด\nกรุณใช้: alias: ชื่อจริง = ชื่อเล่น"
            except Exception as e:
                reply_text = f"เกิดข้อผิดพลาด: {e}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return
        elif text.lower() == "reload aliases":
            global _aliases_cache
            _aliases_cache = None
            get_aliases()
            reply_text = f"โหลดข้อมูลนามแฝงใหม่ {_aliases_cache.__len__()} รายการสำเร็จ!"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return
    if text.lower() in ["ping", "wake up", "ตื่น", "หวัดดี", "สวัสดี"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ระบบพร้อมทำงานแล้วครับ! 🏓"))

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