import os
import json
from flask import Flask, request, abort
import requests

from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, ImageMessage, TextSendMessage,
)

# --- ส่วนตั้งค่า ---
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.environ.get('CHANNEL_SECRET')
OCR_SPACE_API_KEY = os.environ.get('OCR_SPACE_API_KEY') 

# --- ส่วนเริ่มต้นโปรแกรม ---
app = Flask(__name__)

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)


# --- ส่วน Webhook ที่จะรับข้อมูลจาก LINE ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# --- ส่วนจัดการ Event เมื่อผู้ใช้ส่ง "รูปภาพ" ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    message_content = line_bot_api.get_message_content(event.message.id)
    
    url_api = "https://api.ocr.space/parse/image"

    # ส่งรูปภาพไปให้ ocr.space API พร้อมระบุ Engine 2
    response = requests.post(url_api, 
        files={"image": ("receipt.jpg", message_content.content, "image/jpeg")},
        data={
            "apikey": OCR_SPACE_API_KEY,
            "language": "tha", # รหัสภาษาไทยที่ถูกต้อง
            "OCREngine": "2"   # ระบุให้ใช้ Engine 2 ที่รองรับภาษาไทย
        }
    )

    result = response.json()

    if result.get("IsErroredOnProcessing") == False and result.get("ParsedResults"):
        detected_text = result["ParsedResults"][0]["ParsedText"]
        reply_text = "ข้อความที่อ่านได้จากรูปภาพ (โดย ocr.space):\n\n" + detected_text
    else:
        error_message = result.get("ErrorMessage", ["เกิดข้อผิดพลาดไม่ทราบสาเหตุ"])[0]
        reply_text = f"ขออภัยครับ ไม่สามารถอ่านข้อความได้: {error_message}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

# --- ส่วนสำหรับรัน Flask App ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)