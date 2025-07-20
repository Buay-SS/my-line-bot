import re
from datetime import datetime

# --- พจนานุกรมและฟังก์ชันช่วยเหลือ (ไม่มีการเปลี่ยนแปลง) ---
THAI_MONTH_MAP = {
    'ม.ค.': 1, 'ก.พ.': 2, 'มี.ค.': 3, 'เม.ย.': 4, 'พ.ค.': 5, 'มิ.ย.': 6,
    'ก.ค.': 7, 'ส.ค.': 8, 'ก.ย.': 9, 'ต.ค.': 10, 'พ.ย.': 11, 'ธ.ค.': 12
}

def normalize_date(day_str, month_str, year_str):
    try:
        day = int(day_str)
        month = THAI_MONTH_MAP.get(month_str.replace('.', '').strip())
        year = int(year_str)
        if year < 100: year += 2500
        if year > 2500: year -= 543
        return f"{year:04d}-{month:02d}-{day:02d}"
    except:
        return None

def find_amount(text):
    patterns = [
        r'(?:จำนวน|Amount)[\s:]*([,\d]+\.\d{2})',
        r'([,\d]+\.\d{2})\s*THB'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(',', ''))
    
    all_amounts = [float(amount.replace(',', '')) for amount in re.findall(r'(\d{1,3}(?:,\d{3})*\.\d{2})', text)]
    return max(all_amounts) if all_amounts else None

# --- Parser เฉพาะสำหรับแต่ละธนาคาร (แก้ไขใหม่!) ---

def _parse_kbank_slip(text):
    """Parser สำหรับ KBank จะหาแค่ 'account' กับ 'recipient'"""
    # <-- เปลี่ยน: เริ่มต้นด้วย dict ว่าง
    data = {}
    
    account_match = re.search(r'(?:น\.ส\.|นาย)\s+(.*?)\n', text)
    if account_match:
        data['account'] = account_match.group(1).strip()

    if "TrueMoney Wallet" in text:
        data['recipient'] = "TrueMoney Wallet"
    elif "ShopeePay" in text or "รี Shopee" in text:
        data['recipient'] = "ShopeePay"
    else:
        recipient_match = re.search(r'Prompt\s*Pay\s*\n(.*?)\n', text, re.MULTILINE)
        if recipient_match:
            data['recipient'] = recipient_match.group(1).strip()
            
    return data # <-- จะ return กลับไปแค่ข้อมูลที่หาเจอจริงๆ

def _parse_scb_slip(text):
    """Parser สำหรับ SCB จะหาแค่ 'account' กับ 'recipient'"""
    data = {}
    from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
    if from_match:
        data['account'] = from_match.group(1).strip()
    to_match = re.search(r'ไปยัง\s*\n(.*?)\n', text, re.MULTILINE)
    if to_match:
        data['recipient'] = to_match.group(1).strip()
    return data

def _parse_bbl_slip(text):
    """Parser สำหรับ BBL จะหาแค่ 'account' กับ 'recipient'"""
    data = {}
    from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
    if from_match:
        data['account'] = from_match.group(1).strip()
    to_match = re.search(r'ไปที่\s*\n(.*?)\n', text, re.MULTILINE)
    if to_match:
        data['recipient'] = to_match.group(1).strip()
    return data

# --- ฟังก์ชันหลัก (ตัวจัดการ/Router - แก้ไขใหม่!) ---

def parse_slip(text):
    # <-- เปลี่ยน: สร้าง dict สุดท้ายที่นี่ พร้อมค่าเริ่มต้น
    final_data = {'date': 'N/A', 'amount': 'N/A', 'recipient': 'N/A', 'account': 'N/A'}

    # 1. หาข้อมูลพื้นฐานก่อน
    date_match = re.search(r'(\d{1,2})\s+(ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)\s+(\d{2,4})', text)
    if date_match:
        # <-- เปลี่ยน: อัปเดต final_data โดยตรง
        final_data['date'] = normalize_date(date_match.group(1), date_match.group(2), date_match.group(3))

    # <-- เปลี่ยน: อัปเดต final_data โดยตรง
    final_data['amount'] = find_amount(text)

    # 2. ตรวจสอบว่าเป็นสลิปของธนาคารไหน
    specific_data = {}
    if "K+" in text or "กสิกรไทย" in text:
        specific_data = _parse_kbank_slip(text)
    elif "SCB" in text:
        specific_data = _parse_scb_slip(text)
    elif "Bangkok Bank" in text:
        specific_data = _parse_bbl_slip(text)
    
    # 3. รวมผลลัพธ์ โดยข้อมูลจาก specific_data จะเขียนทับถ้ามี
    final_data.update(specific_data)

    # 4. ทำความสะอาดข้อมูลครั้งสุดท้าย (แปลง None เป็น 'N/A')
    for key, value in final_data.items():
        if value is None:
            final_data[key] = 'N/A'
            
    return final_data