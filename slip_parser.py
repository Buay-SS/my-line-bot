import re
from datetime import datetime

# พจนานุกรมสำหรับแปลงเดือนไทยเป็นตัวเลข
THAI_MONTH_MAP = {
    'ม.ค.': 1, 'ก.พ.': 2, 'มี.ค.': 3, 'เม.ย.': 4, 'พ.ค.': 5, 'มิ.ย.': 6,
    'ก.ค.': 7, 'ส.ค.': 8, 'ก.ย.': 9, 'ต.ค.': 10, 'พ.ย.': 11, 'ธ.ค.': 12
}

def normalize_date(day, month_str, year_str):
    """แปลงข้อมูลวันที่ที่ได้จากสลิปให้เป็นรูปแบบสากล (YYYY-MM-DD)"""
    try:
        day = int(day)
        month = THAI_MONTH_MAP.get(month_str.replace(' ', ''))
        year = int(year_str)

        if year < 2500: # ถ้าเป็นปี ค.ศ. 2 ตัวท้าย เช่น 68
            year += 2500 + 543 - 2000 # แปลงเป็น พ.ศ. ก่อน
        
        # แปลงจาก พ.ศ. เป็น ค.ศ.
        year_ad = year - 543
        
        return f"{year_ad:04d}-{month:02d}-{day:02d}"
    except (ValueError, TypeError):
        return None

def find_amount(text):
    """ค้นหาจำนวนเงินที่น่าเชื่อถือที่สุดในสลิป"""
    # Regex สำหรับค้นหาตัวเลขในรูปแบบ 1,234.56 หรือ 1234.56
    amount_pattern = re.compile(r'(\d{1,3}(?:,\d{3})*\.\d{2})')
    
    # ลองค้นหาจากคำสำคัญก่อน
    keywords = ["จำนวนเงิน", "จำนวน:", "จำนวน", "Amount"]
    for keyword in keywords:
        match = re.search(f"{keyword}\\s*([\\d,]+\\.\\d{{2}})", text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(',', ''))
            
    # ถ้าไม่เจอจากคำสำคัญ ให้หาตัวเลขที่ใหญ่ที่สุดในสลิป
    all_amounts = [float(amount.replace(',', '')) for amount in amount_pattern.findall(text)]
    if all_amounts:
        return max(all_amounts)
        
    return None

def parse_slip(text):
    """ฟังก์ชันหลักในการวิเคราะห์ข้อความจาก OCR"""
    data = {'date': None, 'amount': None, 'recipient': None, 'account': None}

    # 1. ค้นหาวันที่
    date_match = re.search(r'(\d{1,2})\s+(ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)\s+(\d{2,4})', text)
    if date_match:
        data['date'] = normalize_date(date_match.group(1), date_match.group(2), date_match.group(3))

    # 2. ค้นหาจำนวนเงิน
    data['amount'] = find_amount(text)

    # 3. ค้นหาผู้รับและผู้โอน (ใช้ตรรกะตามธนาคาร)
    if "K+" in text: # กรณีสลิปจาก KBank
        data['account'] = (re.search(r'น\.[สส]\.\s(.*?)\n', text) or re.search(r'นาย\s(.*?)\n', text)).group(1)
        # หาผู้รับจาก PromptPay, TrueMoney, ShopeePay
        recipient_match = re.search(r'Prompt\s*Pay\s*\n(.*?)\n', text, re.MULTILINE) or \
                          re.search(r'TrueMoney Wallet\s*\n(.*?)\n', text, re.MULTILINE) or \
                          re.search(r'Shopee\s*Pay\s*\n(.*?)\n', text, re.MULTILINE) or \
                          re.search(r'รี\s*(Shopee\s*Pay)', text)
        if recipient_match:
            # group(1) หรือ group(2) เพื่อจัดการกับกรณีที่แตกต่างกัน
            data['recipient'] = recipient_match.group(1) or recipient_match.group(2)

    elif "SCB" in text: # กรณีสลิปจาก SCB
        from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
        to_match = re.search(r'ไปยัง\s*\n(.*?)\n', text, re.MULTILINE)
        if from_match:
            data['account'] = from_match.group(1).strip()
        if to_match:
            data['recipient'] = to_match.group(1).strip()
            
    elif "Bangkok Bank" in text: # กรณีสลิปจาก BBL
        from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
        to_match = re.search(r'ไปที่\s*\n(.*?)\n', text, re.MULTILINE)
        if from_match:
            data['account'] = from_match.group(1).strip()
        if to_match:
            data['recipient'] = to_match.group(1).strip()
    
    # ทำความสะอาดข้อมูล None ให้เป็น 'N/A'
    for key, value in data.items():
        if value is None:
            data[key] = 'N/A'

    return data