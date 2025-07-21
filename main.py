import os, json, re
from flask import Flask, request, abort
import requests
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials

from linebot import (LineBotApi, WebhookHandler)
from linebot.exceptions import (InvalidSignatureError, LineBotApiError)
from linebot.models import (MessageEvent, ImageMessage, TextSendMessage, JoinEvent, FollowEvent, SourceUser, SourceGroup, TextMessage)

from slip_parser import parse_slip

# --- ส่วนตั้งค่า ---
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN')
# ... (ตัวแปรอื่นๆ เหมือนเดิม)
CHANNEL_SECRET = os.environ.get('CHANNEL_SECRET')
OCR_SPACE_API_KEY = os.environ.get('OCR_SPACE_API_KEY')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID')
GOOGLE_CREDENTIALS_JSON_STRING = os.environ.get('GOOGLE_CREDENTIALS_JSON')
GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID')

app = Flask(__name__)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# --- ระบบจัดการการเชื่อมต่อและ Cache ---
_spreadsheet = None
_aliases_cache = None
_config_cache = None # <-- Cache ใหม่สำหรับ Config

# --- Default Strings (ค่าเริ่มต้นกรณีหาใน Sheet ไม่เจอ) ---
DEFAULT_STRINGS = {
    'LABEL_FROM': "จาก", 'LABEL_TO': "ถึง", 'MSG_ALIAS_RELOAD_SUCCESS': "โหลดข้อมูลนามแฝงใหม่ {count} รายการสำเร็จ!",
    # (เพิ่มค่าเริ่มต้นอื่นๆ ที่นี่ถ้าต้องการ)
}

def get_spreadsheet():
    # ... (โค้ดส่วนนี้เหมือนเดิม)
    global _spreadsheet
    if _spreadsheet: return _spreadsheet
    try:
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive.file']
        credentials = Credentials.from_service_account_info(json.loads(GOOGLE_CREDENTIALS_JSON_STRING), scopes=scopes)
        gc = gspread.authorize(credentials)
        _spreadsheet = gc.open_by_key(GOOGLE_SHEET_ID)
        return _spreadsheet
    except: return None

# =========================================================
#  **ระบบจัดการ Config และข้อความ**
# =========================================================
def get_config():
    """อ่านข้อมูล Config จากชีท 'Config' และเก็บไว้ใน Cache"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    
    print("--- Reading config strings... ---")
    spreadsheet = get_spreadsheet()
    if not spreadsheet:
        _config_cache = {}
        return _config_cache
    try:
        config_sheet = spreadsheet.worksheet("Config")
        records = config_sheet.get_all_records()
        _config_cache = {record['Key']: record['Value'] for record in records if record.get('Key')}
        print(f"--- Loaded {_config_cache.__len__()} config strings. ---")
        return _config_cache
    except Exception as e:
        print(f"--- ERROR reading config: {e} ---")
        _config_cache = {}
        return _config_cache

def get_string(key, **kwargs):
    """ดึงข้อความจาก Cache, ถ้าไม่เจอก็ใช้ค่าเริ่มต้น"""
    config = get_config()
    # ดึง template จาก sheet หรือจาก default
    template = config.get(key, DEFAULT_STRINGS.get(key, key))
    # แทนที่ตัวแปรใน template (ถ้ามี)
    return template.format(**kwargs) if kwargs else template

# --- ฟังก์ชันจัดการผู้ใช้และนามแฝง (เหมือนเดิม) ---
# ...
def get_aliases():
    global _aliases_cache
    if _aliases_cache is not None: return _aliases_cache
    spreadsheet = get_spreadsheet()
    if not spreadsheet:
        _aliases_cache = {}
        return _aliases_cache
    try:
        alias_sheet = spreadsheet.worksheet("Aliases")
        records = alias_sheet.get_all_records()
        _aliases_cache = {record['OriginalName']: record['Nickname'] for record in records if record.get('OriginalName')}
        return _aliases_cache
    except Exception:
        _aliases_cache = {}
        return _aliases_cache
def add_alias_to_sheet(original_name, nickname):
    # ... (โค้ดส่วนนี้เหมือนเดิม)
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return False, "ไม่สามารถเชื่อมต่อกับฐานข้อมูลได้"
    try:
        alias_sheet = spreadsheet.worksheet("Aliases")
        cell = alias_sheet.find(original_name, in_column=1)
        if cell:
            alias_sheet.update_cell(cell.row, 2, nickname)
            message = get_string('MSG_ALIAS_UPDATED') # <-- เปลี่ยนมาใช้ get_string
        else:
            alias_sheet.append_row([original_name, nickname])
            message = get_string('MSG_ALIAS_ADDED') # <-- เปลี่ยนมาใช้ get_string
        global _aliases_cache
        _aliases_cache = None
        return True, message
    except Exception as e:
        return False, f"เกิดข้อผิดพลาด: {e}"

def is_approved(source_id):
    # ... (โค้ดส่วนนี้เหมือนเดิม)
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return False
    try:
        worksheet = spreadsheet.worksheet("Sheet1")
        cell = worksheet.find(source_id)
        return cell and worksheet.cell(cell.row, 4).value.lower() == 'approved'
    except Exception as e: return False
def register_source(source_id, display_name, source_type):
    # ... (โค้ดส่วนนี้เหมือนเดิม)
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return
    try:
        worksheet = spreadsheet.worksheet("Sheet1")
        if not worksheet.find(source_id):
            worksheet.append_row([source_id, display_name, source_type, 'pending', datetime.now().isoformat()])
            if ADMIN_USER_ID:
                line_bot_api.push_message(ADMIN_USER_ID, TextSendMessage(text=f"New {source_type} needs approval:\nName: {display_name}"))
    except Exception as e: print(f"Error registering source: {e}")

# --- ฟังก์ชันบันทึกรายการ (อัปเกรด!) ---
def log_transaction_to_sheet(log_data):
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return False, "DB connection error"
    try:
        worksheet = spreadsheet.worksheet("Transactions")
        ref_id = log_data.get('ref_id')
        if not ref_id or ref_id == 'N/A':
            return False, get_string('MSG_LOG_NO_REF')
        cell = worksheet.find(ref_id, in_column=6)
        if cell:
            return False, get_string('MSG_LOG_DUPLICATE', row=cell.row)
        # ... (โค้ดบันทึกเหมือนเดิม)
        thai_tz = timezone(timedelta(hours=7))
        timestamp = datetime.now(thai_tz).strftime("%Y-%m-%d %H:%M:%S")
        new_row = [ timestamp, log_data.get('date', 'N/A'), log_data.get('from', 'N/A'), log_data.get('to', 'N/A'), log_data.get('amount', 0.0), ref_id, log_data.get('recorded_by_id', 'N/A'), log_data.get('recorded_by_name', 'N/A'), log_data.get('source_group', 'N/A (Direct Message)')]
        worksheet.append_row(new_row, value_input_option='USER_ENTERED')
        return True, get_string('MSG_LOG_SUCCESS')
    except Exception as e:
        return False, get_string('MSG_LOG_ERROR')

# --- Event Handler: รูปภาพ (อัปเกรด!) ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    # ... (โค้ดส่วนดึงข้อมูลผู้ส่ง เหมือนเดิม)
    source = event.source
    user_id = source.user_id
    recorder_name, group_name = "N/A", "N/A (Direct Message)"
    source_for_approval = user_id
    if isinstance(source, SourceGroup):
        group_id = source.group_id
        source_for_approval = group_id
        try:
            group_summary = line_bot_api.get_group_summary(group_id)
            group_name = group_summary.group_name
            member_profile = line_bot_api.get_group_member_profile(group_id, user_id)
            recorder_name = member_profile.display_name
        except LineBotApiError: recorder_name = "N/A (API Error)"
    elif isinstance(source, SourceUser):
        try:
            profile = line_bot_api.get_profile(user_id)
            recorder_name = profile.display_name
        except LineBotApiError: recorder_name = "N/A (API Error)"

    if not is_approved(source_for_approval):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=get_string('MSG_APPROVAL_PENDING')))
        return

    # ... (โค้ดส่วนประมวลผล OCR เหมือนเดิม)
    message_content = line_bot_api.get_message_content(event.message.id)
    url_api = "https://api.ocr.space/parse/image"
    response = requests.post(url_api, files={"image": ("receipt.jpg", message_content.content, "image/jpeg")}, data={"apikey": OCR_SPACE_API_KEY, "language": "tha", "OCREngine": "2"})
    result = response.json()

    if result.get("IsErroredOnProcessing") == False and result.get("ParsedResults"):
        detected_text = result["ParsedResults"][0]["ParsedText"]
        parsed_data = parse_slip(detected_text)
        aliases = get_aliases()
        display_account = aliases.get(parsed_data.get('account'), parsed_data.get('account'))
        display_recipient = aliases.get(parsed_data.get('recipient'), parsed_data.get('recipient'))
        
        # --- สร้างข้อความตอบกลับโดยใช้ get_string ---
        summary_text = (
            f"{get_string('LABEL_SUMMARY')} ({get_string('LABEL_RECORDED_BY')}: {recorder_name}):\n-------------------\n"
            f"{get_string('LABEL_DATE')}: {parsed_data.get('date', 'N/A')}\n"
            f"{get_string('LABEL_FROM')}: {display_account}\n"
            f"{get_string('LABEL_TO')}: {display_recipient}\n"
            f"{get_string('LABEL_AMOUNT')}: {parsed_data.get('amount', 'N/A')} {get_string('LABEL_AMOUNT_UNIT')}\n"
            f"{get_string('LABEL_REF')}: {parsed_data.get('ref_id', 'N/A')}"
        )
        # ... (โค้ดส่วน log data เหมือนเดิม)
        log_data = {'date': parsed_data.get('date', 'N/A'), 'from': display_account,'to': display_recipient, 'amount': parsed_data.get('amount', 0.0), 'ref_id': parsed_data.get('ref_id', 'N/A'), 'recorded_by_id': user_id, 'recorded_by_name': recorder_name, 'source_group': group_name}
        log_success, log_message = log_transaction_to_sheet(log_data)
        
        final_reply_text = f"{summary_text}\n-------------------\n{get_string('LABEL_STATUS')}: {log_message}"
    else:
        final_reply_text = get_string('MSG_OCR_ERROR')

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=final_reply_text))

# --- Event Handler: ข้อความ (อัปเกรด!) ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    text = event.message.text
    user_id = event.source.user_id
    if user_id == ADMIN_USER_ID:
        # (คำสั่ง alias เหมือนเดิม แต่เปลี่ยนข้อความตอบกลับ)
        if text.lower().startswith("alias:"):
            try:
                command_body = text[len("alias:"):].strip()
                original_name, nickname = [part.strip() for part in command_body.split('=', 1)]
                success, message = add_alias_to_sheet(original_name, nickname)
                reply_text = message
            except ValueError:
                reply_text = get_string('MSG_ALIAS_CMD_ERROR')
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

        elif text.lower() == "reload aliases":
            global _aliases_cache
            _aliases_cache = None
            aliases = get_aliases() # เรียกเพื่อโหลดใหม่
            reply_text = get_string('MSG_ALIAS_RELOAD_SUCCESS', count=len(aliases))
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return
        
        # --- คำสั่งใหม่สำหรับ Reload Config ---
        elif text.lower() == "reload config":
            global _config_cache
            _config_cache = None
            config = get_config() # เรียกเพื่อโหลดใหม่
            reply_text = f"โหลดข้อความใหม่ {len(config)} รายการสำเร็จ!"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

    # คำสั่งปลุกบอท (สำหรับผู้ใช้ทุกคน)
    if text.lower() in ["ping", "wake up", "ตื่น", "หวัดดี", "สวัสดี"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=get_string('MSG_WAKE_UP')))

# (โค้ดส่วนที่เหลือทั้งหมด เช่น Join/Follow, Webhook Callback, __main__ เหมือนเดิม)
@app.route("/", methods=['GET', 'HEAD'])
def home(): return "OK", 200

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

@handler.add(JoinEvent)
def handle_join(event):
    # ...
    if isinstance(event.source, SourceGroup):
        try:
            group_summary = line_bot_api.get_group_summary(event.source.group_id)
            group_name = group_summary.group_name
        except LineBotApiError: group_name = "Unknown Group"
        register_source(event.source.group_id, group_name, 'group')
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"สวัสดีครับ! บอทได้รับการเพิ่มเข้ากลุ่ม '{group_name}' แล้ว และกำลังรอการอนุมัติเพื่อเริ่มใช้งานครับ"))


@handler.add(FollowEvent)
def handle_follow(event):
    # ...
    if isinstance(event.source, SourceUser):
        try:
            profile = line_bot_api.get_profile(event.source.user_id)
            display_name = profile.display_name
        except LineBotApiError: display_name = "Unknown User"
        register_source(event.source.user_id, display_name, 'user')
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ขอบคุณที่เพิ่มเป็นเพื่อนครับ! กำลังรอการอนุมัติเพื่อเริ่มใช้งาน"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)