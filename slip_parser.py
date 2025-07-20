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
        month = THAI_MONTH_MAP.get(month_str.strip())
        year = int(year_str)
        if year < 100: year += 2500
        if year > 2500: year -= 543
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, TypeError, AttributeError):
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

# --- Parser เฉพาะสำหรับแต่ละธนาคาร ---

def _parse_kbank_slip(text):
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
    # (ตรรกะนี้อาจจะต้องปรับปรุงในอนาคต แต่เก็บไว้ก่อน)
    data = {}
    from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
    if from_match: data['account'] = from_match.group(1).strip()
    to_match = re.search(r'ไปยัง\s*\n(.*?)\n', text, re.MULTILINE)
    if to_match: data['recipient'] = to_match.group(1).strip()
    return data

def _parse_bbl_slip(text):
    """Parser สำหรับ BBL ที่ใช้ 'ตรรกะตามลำดับตำแหน่ง' (Proximity Logic)"""
    data = {}
    try:
        # 1. หาตำแหน่งของ Keywords
        from_keyword_index = text.find('จาก')
        to_keyword_index = text.find('ไปที่')
        
        # 2. ค้นหา "ชื่อ" ทั้งหมดพร้อมตำแหน่ง
        name_pattern = re.compile(r'(นาย|นาง|น\.ส\.)\s+[^\n]+')
        all_names = [(match.group(0).strip(), match.start()) for match in name_pattern.finditer(text)]

        # 3. คำนวณหาชื่อที่อยู่ใกล้ที่สุด
        closest_from_name, closest_to_name = None, None
        min_from_dist, min_to_dist = float('inf'), float('inf')

        for name, name_index in all_names:
            # หาชื่อที่ใกล้ "จาก" ที่สุด
            if from_keyword_index != -1 and name_index > from_keyword_index:
                dist = name_index - from_keyword_index
                if dist < min_from_dist:
                    min_from_dist = dist
                    closest_from_name = name
            
            # หาชื่อที่ใกล้ "ไปที่" ที่สุด
            if to_keyword_index != -1 and name_index > to_keyword_index:
                dist = name_index - to_keyword_index
                if dist < min_to_dist:
                    min_to_dist = dist
                    closest_to_name = name

        data['account'] = closest_from_name
        data['recipient'] = closest_to_name
    except Exception as e:
        print(f"BBL Parser (proximity logic) failed: {e}")
        
    return data

# --- ฟังก์ชันหลัก (ตัวจัดการ/Router - ไม่ต้องแก้ไข) ---
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