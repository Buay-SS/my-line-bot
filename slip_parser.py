import re
from datetime import datetime

# --- พจนานุกรมและฟังก์ชันช่วยเหลือ (เหมือนเดิม) ---
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
    patterns = [
        r'(?:จำนวน|Amount)[\s:]*([,\d]+\.\d{2})',
        r'([,\d]+\.\d{2})\s*THB'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match: return float(match.group(1).replace(',', ''))
    all_amounts = [float(amount.replace(',', '')) for amount in re.findall(r'(\d{1,3}(?:,\d{3})*\.\d{2})', text)]
    return max(all_amounts) if all_amounts else None

def find_reference_id(text):
    patterns = [
        r'เลขที่รายการ[:\s]*([a-zA-Z0-9]{15,})',
        r'รหัสอ้างอิง[:\s]*([a-zA-Z0-9]{15,})',
        r'เลขที่อ้างอิง[:\s]*([a-zA-Z0-9]{15,})',
        r'\b([a-zA-Z0-9]{20,})\b'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

# --- Parser เฉพาะสำหรับแต่ละธนาคาร (เหมือนเดิม) ---
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
    data = {}
    from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
    if from_match: data['account'] = from_match.group(1).strip()
    to_match = re.search(r'ไปยัง\s*\n(.*?)\n', text, re.MULTILINE)
    if to_match: data['recipient'] = to_match.group(1).strip()
    return data

def _parse_bbl_slip(text):
    data = {}
    try:
        from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
        if not from_match: from_match = re.search(r'จาก\s+(นาย|นาง|น\.ส\.)\s+([^\n]+)', text)
        if from_match: data['account'] = from_match.group(1).strip() if len(from_match.groups()) == 1 else " ".join(from_match.groups())

        to_match = re.search(r'ไปที่\s*\n(.*?)\n', text, re.MULTILINE)
        if not to_match: to_match = re.search(r'ไปยัง\s*\n(.*?)\n', text, re.MULTILINE)
        if to_match: data['recipient'] = to_match.group(1).strip()
    except Exception: pass
    return data

# --- ฟังก์ชันหลัก (อัปเกรด Rules Engine) ---
def parse_slip(text, rules):
    final_data = {'date': 'N/A', 'amount': 'N/A', 'recipient': 'N/A', 'account': 'N/A', 'ref_id': 'N/A'}
    
    # 1. หาข้อมูลพื้นฐาน
    date_match = re.search(r'(\d{1,2})\s+(ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)\s+(\d{2,4})', text)
    if date_match:
        final_data['date'] = normalize_date(date_match.group(1), date_match.group(2), date_match.group(3))
    final_data['amount'] = find_amount(text)
    final_data['ref_id'] = find_reference_id(text)
    
    # 2. *** Rules Engine ใหม่ ***
    for rule in rules:
        identifier = rule.get('IdentifierText', '')
        target_field = rule.get('TargetField')
        method = rule.get('SearchMethod')
        
        if final_data.get(target_field) == 'N/A' and (identifier in text or method == 'REGEX'):
            value_found = None
            if method == 'FIXED_VALUE':
                value_found = rule.get('FixedValue')
            elif method == 'REGEX':
                search_term = rule.get('SearchTerm')
                if not search_term: continue
                try:
                    # ใช้ re.DOTALL เพื่อให้ . แมทช์ newline ได้ด้วย
                    match = re.search(search_term, text, re.DOTALL)
                    if match:
                        value_found = match.group(1) if match.groups() else match.group(0)
                except re.error:
                    continue # ข้ามกฎที่มี Regex ผิด
            
            # --- ส่วนที่เพิ่มเข้ามา ---
            # ทำความสะอาดข้อมูลที่ได้จากกฎ
            if value_found:
                cleaned_value = value_found.replace('\n', ' ').strip()
                final_data[target_field] = ' '.join(cleaned_value.split()) # รวมเว้นวรรคหลายๆ อันเป็นอันเดียว
            # --- สิ้นสุดส่วนที่เพิ่ม ---

    # 3. *** Fallback Mechanism ***
    if final_data['recipient'] == 'N/A' or final_data['account'] == 'N/A':
        fallback_data = {}
        if "K+" in text or "กสิกรไทย" in text: fallback_data = _parse_kbank_slip(text)
        elif "SCB" in text: fallback_data = _parse_scb_slip(text)
        elif "Bangkok Bank" in text: fallback_data = _parse_bbl_slip(text)
        
        for key, value in fallback_data.items():
            if final_data.get(key) == 'N/A' and value:
                final_data[key] = value

    # 4. ทำความสะอาดข้อมูลครั้งสุดท้าย
    for key, value in final_data.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            final_data[key] = 'N/A'
            
    return final_data