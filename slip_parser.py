import re
from datetime import datetime

# --- พจนานุกรมและฟังก์ชันช่วยเหลือ ---
THAI_MONTH_MAP = {
    'ม.ค.': 1, 'ก.พ.': 2, 'มี.ค.': 3, 'เม.ย.': 4, 'พ.ค.': 5, 'มิ.ย.': 6,
    'ก.ค.': 7, 'ส.ค.': 8, 'ก.ย.': 9, 'ต.ค.': 10, 'พ.ย.': 11, 'ธ.ค.': 12
}

def normalize_date(day_str, month_str, year_str):
    """
    แปลงข้อมูลวันที่ที่ได้จากสลิปให้เป็นรูปแบบสากล (YYYY-MM-DD)
    เวอร์ชันแก้ไข: เอา .replace('.', '') ออกแล้ว
    """
    try:
        day = int(day_str)
        # --- จุดที่แก้ไข ---
        month = THAI_MONTH_MAP.get(month_str.strip()) # .strip() ยังคงไว้เพื่อลบช่องว่าง
        # -----------------
        year = int(year_str)
        
        if year < 100:  # กรณีปีเป็น 2 หลัก เช่น 68
            year += 2500
        
        if year > 2500: # ถ้าเป็น พ.ศ. ให้แปลงเป็น ค.ศ.
            year -= 543
        
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, TypeError, AttributeError):
        return None # คืนค่า None ถ้ามีปัญหา เพื่อให้ฟังก์ชันหลักจัดการต่อ

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

# --- Parser เฉพาะสำหรับแต่ละธนาคาร (เหมือนเดิม) ---

def _parse_kbank_slip(text):
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
    return data

def _parse_scb_slip(text):
    data = {}
    from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
    if from_match:
        data['account'] = from_match.group(1).strip()
    to_match = re.search(r'ไปยัง\s*\n(.*?)\n', text, re.MULTILINE)
    if to_match:
        data['recipient'] = to_match.group(1).strip()
    return data

def _parse_bbl_slip(text):
    data = {}
    from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
    if from_match:
        data['account'] = from_match.group(1).strip()
    to_match = re.search(r'ไปที่\s*\n(.*?)\n', text, re.MULTILINE)
    if to_match:
        data['recipient'] = to_match.group(1).strip()
    return data

# --- ฟังก์ชันหลัก (ตัวจัดการ/Router - เหมือนเดิม) ---

def parse_slip(text):
    final_data = {'date': 'N/A', 'amount': 'N/A', 'recipient': 'N/A', 'account': 'N/A'}

    date_match = re.search(r'(\d{1,2})\s+(ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)\s+(\d{2,4})', text)
    if date_match:
        final_data['date'] = normalize_date(date_match.group(1), date_match.group(2), date_match.group(3))

    final_data['amount'] = find_amount(text)

    specific_data = {}
    if "K+" in text or "กสิกรไทย" in text:
        specific_data = _parse_kbank_slip(text)
    elif "SCB" in text:
        specific_data = _parse_scb_slip(text)
    elif "Bangkok Bank" in text:
        specific_data = _parse_bbl_slip(text)
    
    final_data.update(specific_data)

    for key, value in final_data.items():
        if value is None:
            final_data[key] = 'N/A'
            
    return final_data