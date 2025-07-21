import os, json, re
from flask import Flask, request, abort
import requests
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials
from collections import defaultdict

from linebot import (LineBotApi, WebhookHandler)
from linebot.exceptions import (InvalidSignatureError, LineBotApiError)
from linebot.models import (
    MessageEvent, ImageMessage, TextSendMessage, JoinEvent, FollowEvent, SourceUser, SourceGroup, TextMessage,
    # --- ส่วนประกอบของ Flex Message ที่ต้องนำเข้า ---
    FlexSendMessage, BubbleContainer, BoxComponent, TextComponent, SeparatorComponent, SpacerComponent
)

from slip_parser import parse_slip

# (โค้ดส่วนตั้งค่าและเริ่มต้น เหมือนเดิม)
# ...
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.environ.get('CHANNEL_SECRET')
OCR_SPACE_API_KEY = os.environ.get('OCR_SPACE_API_KEY')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID')
GOOGLE_CREDENTIALS_JSON_STRING = os.environ.get('GOOGLE_CREDENTIALS_JSON')
GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID')

app = Flask(__name__)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# (โค้ดส่วน Cache และ get_spreadsheet, get_aliases, get_config, get_string ทั้งหมดเหมือนเดิม)
# ...
_spreadsheet = None
_aliases_cache = None
_config_cache = None

def get_spreadsheet():
    global _spreadsheet
    if _spreadsheet: return _spreadsheet
    try:
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive.file']
        credentials = Credentials.from_service_account_info(json.loads(GOOGLE_CREDENTIALS_JSON_STRING), scopes=scopes)
        gc = gspread.authorize(credentials)
        _spreadsheet = gc.open_by_key(GOOGLE_SHEET_ID)
        return _spreadsheet
    except: return None
def get_aliases():
    global _aliases_cache
    if _aliases_cache is not None: return _aliases_cache
    spreadsheet = get_spreadsheet()
    if not spreadsheet: _aliases_cache = {}; return _aliases_cache
    try:
        alias_sheet = spreadsheet.worksheet("Aliases")
        records = alias_sheet.get_all_records()
        _aliases_cache = {record['OriginalName']: record['Nickname'] for record in records if record.get('OriginalName')}
        return _aliases_cache
    except: _aliases_cache = {}; return _aliases_cache
def get_config():
    global _config_cache
    if _config_cache is not None: return _config_cache
    spreadsheet = get_spreadsheet()
    if not spreadsheet: _config_cache = {}; return _config_cache
    try:
        config_sheet = spreadsheet.worksheet("Config")
        records = config_sheet.get_all_records()
        _config_cache = {record['Key']: record['Value'] for record in records if record.get('Key')}
        return _config_cache
    except: _config_cache = {}; return _config_cache
def get_string(key, **kwargs):
    config = get_config()
    template = config.get(key, key)
    return template.format(**kwargs) if kwargs else template

# =========================================================
#  **ฟังก์ชันใหม่: สร้าง Flex Message สรุปยอด**
# =========================================================
def create_summary_flex_message(summary_result):
    if isinstance(summary_result, str): # กรณีเกิด Error หรือไม่พบข้อมูล
        return TextSendMessage(text=summary_result)

    period_text = "เดือนนี้" if summary_result['period'] == 'month' else "ปีนี้"
    total_amount = summary_result['total']
    details = summary_result['details']

    # สร้าง Body Components (รายการรายละเอียด)
    body_contents = []
    for recipient, amount in details:
        body_contents.append(
            BoxComponent(
                layout='horizontal',
                contents=[
                    TextComponent(text=recipient, size='sm', color='#555555', flex=4),
                    TextComponent(text=f"{amount:,.2f} บาท", size='sm', color='#111111', align='end', flex=2)
                ]
            )
        )
    
    bubble = BubbleContainer(
        header=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(text=f"สรุปรายจ่าย{period_text}", weight='bold', size='xl', color='#1DB446')
            ]
        ),
        hero=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(text="รายจ่ายทั้งหมด", size='sm', color='#AAAAAA'),
                TextComponent(text=f"{total_amount:,.2f}", size='3xl', weight='bold', color='#111111'),
                TextComponent(text="บาท", size='sm', color='#AAAAAA'),
                SeparatorComponent(margin='lg')
            ]
        ),
        body=BoxComponent(
            layout='vertical',
            spacing='md',
            contents=[
                TextComponent(text="รายละเอียด", weight='bold', color='#1DB446', margin='md'),
                *body_contents # Unpack list ของ detail items
            ]
        )
    )
    
    alt_text = f"สรุปรายจ่าย{period_text}: {total_amount:,.2f} บาท"
    return FlexSendMessage(alt_text=alt_text, contents=bubble)

# --- ฟังก์ชันสรุปยอด (อัปเกรด: คืนค่าเป็น Dictionary) ---
def generate_summary(period):
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return "ไม่สามารถเชื่อมต่อกับฐานข้อมูลได้"
    try:
        # ... (โค้ดส่วนดึงและกรองข้อมูลเหมือนเดิม)
        transactions_sheet = spreadsheet.worksheet("Transactions")
        all_records = transactions_sheet.get_all_records()
        aliases = get_aliases()
        known_nicknames = set(aliases.values())
        now = datetime.now(timezone(timedelta(hours=7)))
        filtered_records = []
        accepted_date_formats = ['%Y-%m-%d', '%m-%d-%Y', '%d-%m-%Y']
        for record in all_records:
            record_date = None
            date_str = record.get('TransactionDate')
            if not date_str: continue
            for fmt in accepted_date_formats:
                try:
                    record_date = datetime.strptime(date_str, fmt)
                    break
                except (ValueError, TypeError): continue
            if not record_date: continue
            if period == 'month' and record_date.year == now.year and record_date.month == now.month:
                filtered_records.append(record)
            elif period == 'year' and record_date.year == now.year:
                filtered_records.append(record)
        
        if not filtered_records:
            return f"ไม่พบข้อมูลรายจ่ายสำหรับ{'เดือนนี้' if period == 'month' else 'ปีนี้'}"

        # ... (โค้ดส่วนรวมยอดเหมือนเดิม)
        summary_data = defaultdict(float)
        total_amount = 0.0
        for record in filtered_records:
            try:
                amount_str = str(record.get('Amount', '0')).replace(',', '')
                amount = float(amount_str)
                recipient = record['ToRecipient']
                total_amount += amount
                if recipient in known_nicknames: summary_data[recipient] += amount
                else: summary_data['อื่นๆ'] += amount
            except (ValueError, TypeError): continue

        sorted_summary = sorted(summary_data.items(), key=lambda item: item[1], reverse=True)
        
        # คืนค่าเป็น Dictionary แทนที่จะเป็น String
        return {
            'period': period,
            'total': total_amount,
            'details': sorted_summary
        }

    except Exception as e:
        return f"เกิดข้อผิดพลาดในการสร้างสรุป: {e}"

# --- Event Handler: ข้อความ (อัปเกรด!) ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    text = event.message.text.lower().strip()
    source = event.source
    user_id = source.user_id
    
    # --- ตรรกะการอนุมัติแบบใหม่ ---
    source_id_for_approval = source.group_id if isinstance(source, SourceGroup) else user_id

    # คำสั่งสำหรับผู้ใช้ที่ Approved แล้ว
    if is_approved(source_id_for_approval):
        if text == "สรุปเดือนนี้":
            summary_result = generate_summary('month')
            reply_message = create_summary_flex_message(summary_result)
            line_bot_api.reply_message(event.reply_token, reply_message)
            return
        elif text == "สรุปปีนี้":
            summary_result = generate_summary('year')
            reply_message = create_summary_flex_message(summary_result)
            line_bot_api.reply_message(event.reply_token, reply_message)
            return

    # คำสั่งสำหรับแอดมินเท่านั้น
    if user_id == ADMIN_USER_ID:
        original_text = event.message.text
        if original_text.lower().startswith("alias:"):
            # ... (โค้ดส่วนนี้เหมือนเดิม)
            try:
                command_body = original_text[len("alias:"):].strip()
                original_name, nickname = [part.strip() for part in command_body.split('=', 1)]
                success, message = add_alias_to_sheet(original_name, nickname)
                reply_text = message
            except ValueError: reply_text = get_string('MSG_ALIAS_CMD_ERROR')
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return
        elif text == "reload aliases":
            # ... (โค้ดส่วนนี้เหมือนเดิม)
            global _aliases_cache
            _aliases_cache = None; aliases = get_aliases()
            reply_text = get_string('MSG_ALIAS_RELOAD_SUCCESS', count=len(aliases))
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return
        elif text == "reload config":
            # ... (โค้ดส่วนนี้เหมือนเดิม)
            global _config_cache
            _config_cache = None; config = get_config()
            reply_text = f"โหลดข้อความใหม่ {len(config)} รายการสำเร็จ!"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

    # คำสั่งปลุกบอท (สำหรับทุกคน)
    if text in ["ping", "wake up", "ตื่น", "หวัดดี", "สวัสดี"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=get_string('MSG_WAKE_UP')))

# (โค้ดส่วนที่เหลือทั้งหมดเหมือนเดิม ไม่ต้องแก้ไข)
# ...
def add_alias_to_sheet(original_name, nickname):
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return False, "DB connection error"
    try:
        alias_sheet = spreadsheet.worksheet("Aliases")
        cell = alias_sheet.find(original_name, in_column=1)
        if cell:
            alias_sheet.update_cell(cell.row, 2, nickname)
            message = get_string('MSG_ALIAS_UPDATED')
        else:
            alias_sheet.append_row([original_name, nickname])
            message = get_string('MSG_ALIAS_ADDED')
        global _aliases_cache
        _aliases_cache = None
        return True, message
    except Exception as e: return False, f"เกิดข้อผิดพลาด: {e}"
def is_approved(source_id):
    spreadsheet = get_spreadsheet()
    if not spreadsheet: return False
    try:
        worksheet = spreadsheet.worksheet("Sheet1")
        cell = worksheet.find(source_id)
        return cell and worksheet.cell(cell.row, 4).value.lower() == 'approved'
    except: return False
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
        thai_tz = timezone(timedelta(hours=7))
        timestamp = datetime.now(thai_tz).strftime("%Y-%m-%d %H:%M:%S")
        new_row = [ timestamp, log_data.get('date', 'N/A'), log_data.get('from', 'N/A'), log_data.get('to', 'N/A'), log_data.get('amount', 0.0), ref_id, log_data.get('recorded_by_id', 'N/A'), log_data.get('recorded_by_name', 'N/A'), log_data.get('source_group', 'N/A (Direct Message)')]
        worksheet.append_row(new_row, value_input_option='USER_ENTERED')
        return True, get_string('MSG_LOG_SUCCESS')
    except Exception as e: return False, get_string('MSG_LOG_ERROR')
@app.route("/", methods=['GET', 'HEAD'])
def home(): return "OK", 200
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
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
        summary_text = (
            f"{get_string('LABEL_SUMMARY')} ({get_string('LABEL_RECORDED_BY')}: {recorder_name}):\n-------------------\n"
            f"{get_string('LABEL_DATE')}: {parsed_data.get('date', 'N/A')}\n{get_string('LABEL_FROM')}: {display_account}\n"
            f"{get_string('LABEL_TO')}: {display_recipient}\n{get_string('LABEL_AMOUNT')}: {parsed_data.get('amount', 'N/A')} {get_string('LABEL_AMOUNT_UNIT')}\n"
            f"{get_string('LABEL_REF')}: {parsed_data.get('ref_id', 'N/A')}"
        )
        log_data = {'date': parsed_data.get('date', 'N/A'), 'from': display_account, 'to': display_recipient, 'amount': parsed_data.get('amount', 0.0), 'ref_id': parsed_data.get('ref_id', 'N/A'), 'recorded_by_id': user_id, 'recorded_by_name': recorder_name, 'source_group': group_name}
        log_success, log_message = log_transaction_to_sheet(log_data)
        final_reply_text = f"{summary_text}\n-------------------\n{get_string('LABEL_STATUS')}: {log_message}"
    else:
        final_reply_text = get_string('MSG_OCR_ERROR')
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=final_reply_text))

@handler.add(JoinEvent)
def handle_join(event):
    if isinstance(event.source, SourceGroup):
        try: group_name = line_bot_api.get_group_summary(event.source.group_id).group_name
        except: group_name = "Unknown Group"
        register_source(event.source.group_id, group_name, 'group')
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"สวัสดีครับ! บอทได้รับการเพิ่มเข้ากลุ่ม '{group_name}' แล้ว และกำลังรอการอนุมัติเพื่อเริ่มใช้งานครับ"))
@handler.add(FollowEvent)
def handle_follow(event):
    if isinstance(event.source, SourceUser):
        try: display_name = line_bot_api.get_profile(event.source.user_id).display_name
        except: display_name = "Unknown User"
        register_source(event.source.user_id, display_name, 'user')
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ขอบคุณที่เพิ่มเป็นเพื่อนครับ! กำลังรอการอนุมัติเพื่อเริ่มใช้งาน"))
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)