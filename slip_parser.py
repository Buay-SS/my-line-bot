import re
from datetime import datetime

# --- พจนานุกรมและฟังก์ชันช่วยเหลือ ---
# ... (ส่วนนี้เหมือนเดิม) ...
THAI_MONTH_MAP = {
    'ม.ค.': 1, 'ก.พ.': 2, 'มี.ค.': 3, 'เม.ย.': 4, 'พ.ค.': 5, 'มิ.ย.': 6,
    'ก.ค.': 7, 'ส.ค.': 8, 'ก.ย.': 9, 'ต.ค.': 10, 'พ.ย.': 11, 'ธ.ค.': 12
}

def normalize_date(day_str, month_str, year_str):
    try:
        day = int(day_str)
        month = THAI_MONTH_MAP.get(month_str.strip())
        year = int(year_str)
        if year < 100: year += 2500
        if year > 2500: year -= 543
        return f"{year:04d}-{month:02d}-{day:02d}"
    except: return None

def find_amount(text):
    # ... (ส่วนนี้เหมือนเดิม) ...
    patterns = [
        r'(?:จำนวน|Amount)[\s:]*([,\d]+\.\d{2})',
        r'([,\d]+\.\d{2})\s*THB'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match: return float(match.group(1).replace(',', ''))
    all_amounts = [float(amount.replace(',', '')) for amount in re.findall(r'(\d{1,3}(?:,\d{3})*\.\d{2})', text)]
    return max(all_amounts) if all_amounts else None

# =========================================================
#  **ฟังก์ชันใหม่สำหรับหารหัสอ้างอิง**
# =========================================================
def find_reference_id(text):
    """พยายามค้นหารหัสอ้างอิง/เลขที่รายการที่มีความยาวและไม่ซ้ำใคร"""
    # Regex มองหาตัวอักษรและตัวเลขผสมกันที่มีความยาวมากๆ (15 ตัวอักษรขึ้นไป)
    # หรือคำว่า "เลขที่รายการ/รหัสอ้างอิง" แล้วตามด้วยตัวเลข/อักษร
    patterns = [
        r'เลขที่รายการ[:\s]*([a-zA-Z0-9]{15,})',
        r'รหัสอ้างอิง[:\s]*([a-zA-Z0-9]{15,})',
        r'เลขที่อ้างอิง[:\s]*([a-zA-Z0-9]{15,})',
        # มองหาเลขยาวๆ ที่มีโอกาสเป็น ID เฉพาะตัว
        r'\b([a-zA-Z0-9]{20,})\b'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None # ถ้าหาไม่เจอจริงๆ

# --- Parser เฉพาะสำหรับแต่ละธนาคาร (ไม่ต้องแก้ไข) ---
# เราจะเรียก find_reference_id ในฟังก์ชันหลักแทน
def _parse_kbank_slip(text):
    # ... (ส่วนนี้เหมือนเดิม) ...
    data = {}
    account_match = re.search(r'(?:น\.ส\.|นาย)\s+(.*?)\n', text)
    if account_match: data['account'] = account_match.group(1).strip()
    if "TrueMoney Wallet" in text: data['recipient'] = "TrueMoney Wallet"
    elif "ShopeePay" in text or "รี Shopee" in text: data['recipient'] = "ShopeePay"
    else:
        recipient_match = re.search(r'Prompt\s*Pay\s*\n(.*?)\n', text, re.MULTILINE)
        if recipient_match: data['recipient'] = recipient_match.group(1).strip()
    return data

def _parse_scb_slip(text):
    # ... (ส่วนนี้เหมือนเดิม) ...
    data = {}
    from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
    if from_match: data['account'] = from_match.group(1).strip()
    to_match = re.search(r'ไปยัง\s*\n(.*?)\n', text, re.MULTILINE)
    if to_match: data['recipient'] = to_match.group(1).strip()
    return data

def _parse_bbl_slip(text):
    # ... (ส่วนนี้เหมือนเดิม) ...
    data = {}
    try:
        from_keyword_index = text.find('จาก')
        to_keyword_index = text.find('ไปที่')
        name_pattern = re.compile(r'(นาย|นาง|น\.ส\.)\s+[^\n]+')
        all_names = [(match.group(0).strip(), match.start()) for match in name_pattern.finditer(text)]
        sender_name, recipient_name = None, None
        if from_keyword_index != -1:
            # ... (โค้ดหาผู้โอน) ...
            min_dist = float('inf')
            sender_candidate = None
            for name, name_index in all_names:
                if name_index > from_keyword_index:
                    dist = name_index - from_keyword_index
                    if dist < min_dist:
                        min_dist = dist
                        sender_candidate = (name, name_index)
            if sender_candidate:
                sender_name = sender_candidate[0]
                all_names.remove(sender_candidate)
        if to_keyword_index != -1:
            # ... (โค้ดหาผู้รับ) ...
            min_dist = float('inf')
            recipient_candidate = None
            for name, name_index in all_names:
                if name_index > to_keyword_index:
                    dist = name_index - to_keyword_index
                    if dist < min_dist:
                        min_dist = dist
                        recipient_candidate = (name, name_index)
            if recipient_candidate:
                recipient_name = recipient_candidate[0]
        data['account'] = sender_name
        data['recipient'] = recipient_name
    except Exception as e: print(f"BBL Parser (exclusive logic) failed: {e}")
    return data

# --- ฟังก์ชันหลัก (ตัวจัดการ/Router - อัปเกรด!) ---
def parse_slip(text):
    # <-- เปลี่ยน: เพิ่ม ref_id เข้าไป
    final_data = {'date': 'N/A', 'amount': 'N/A', 'recipient': 'N/A', 'account': 'N/A', 'ref_id': 'N/A'}

    # 1. หาข้อมูลพื้นฐาน
    date_match = re.search(r'(\d{1,2})\s+(ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)\s+(\d{2,4})', text)
    if date_match:
        final_data['date'] = normalize_date(date_match.group(1), date_match.group(2), date_match.group(3))
    final_data['amount'] = find_amount(text)
    final_data['ref_id'] = find_reference_id(text) # <-- เรียกใช้ฟังก์ชันใหม่

    # 2. หาข้อมูลเฉพาะทาง
    specific_data = {}
    if "K+" in text or "กสิกรไทย" in text:
        specific_data = _parse_kbank_slip(text)
    elif "SCB" in text:
        specific_data = _parse_scb_slip(text)
    elif "Bangkok Bank" in text:
        specific_data = _parse_bbl_slip(text)
    
    # 3. รวมข้อมูล
    final_data.update(specific_data)

    # 4. ทำความสะอาด
    for key, value in final_data.items():
        if value is None:
            final_data[key] = 'N/A'
            
    return final_data