import os
import json
from flask import Flask, request, abort

from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, ImageMessage, TextSendMessage,
)
from google.cloud import vision
from google.oauth2 import service_account

# --- ส่วนตั้งค่า ---
# ดึงค่าจาก Environment Variables บน Render.com
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.environ.get('CHANNEL_SECRET')
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')

# --- ส่วนเริ่มต้นโปรแกรม ---
app = Flask(__name__)

# ตั้งค่า LINE Bot
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ตั้งค่า Google Vision AI ด้วย Service Account Key
# โดยอ่านข้อมูลจากตัวแปร GOOGLE_CREDENTIALS_JSON
try:
    credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(credentials_info)
    vision_client = vision.ImageAnnotatorClient(credentials=credentials)
except Exception as e:
    # หากมีปัญหาในการโหลด credentials ให้ใช้ client เริ่มต้น (อาจไม่ทำงานบน Render)
    print(f"Error loading credentials, falling back to default. Error: {e}")
    vision_client = vision.ImageAnnotatorClient()


# --- ส่วน Webhook ที่จะรับข้อมูลจาก LINE ---
# URL ของเราจะเป็น https://your-app-name.onrender.com/callback
@app.route("/callback", methods=['POST'])
def callback():
    # รับ X-Line-Signature header value
    signature = request.headers['X-Line-Signature']

    # รับ request body เป็น text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # จัดการ webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

# --- ส่วนจัดการ Event เมื่อผู้ใช้ส่ง "รูปภาพ" ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    # ดึงเนื้อหาของรูปภาพจาก LINE
    message_content = line_bot_api.get_message_content(event.message.id)

    # ส่งรูปภาพไปให้ Google Cloud Vision API
    image = vision.Image(content=message_content.content)
    
    # สั่งให้ Vision API อ่านตัวอักษรจากภาพ (Text Detection)
    response = vision_client.text_detection(image=image)
    texts = response.text_annotations

    # ตรวจสอบว่าเจอข้อความหรือไม่
    if texts:
        # texts[0].description คือข้อความทั้งหมดที่อ่านได้
        detected_text = texts[0].description
        
        # --- ส่วนวิเคราะห์ข้อความ (สามารถพัฒนาต่อได้) ---
        # ในขั้นตอนนี้ เราจะส่งข้อความที่อ่านได้ทั้งหมดกลับไปก่อน
        reply_text = "ข้อความที่อ่านได้จากรูปภาพ:\n\n" + detected_text
    else:
        reply_text = "ขออภัยครับ ไม่สามารถอ่านข้อความจากรูปภาพได้"

    # ตอบกลับข้อความไปหาผู้ใช้
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

# --- ส่วนสำหรับรัน Flask App (จำเป็นสำหรับ Render) ---
if __name__ == "__main__":
    # ใช้สำหรับทดสอบบนเครื่อง Local เท่านั้น
    # Port จะถูกกำหนดโดย Render โดยอัตโนมัติเมื่อ deploy
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)