import re
from datetime import datetime

# (โค้ดทั้งหมดในไฟล์นี้เหมือนเดิมกับขั้นตอนที่แล้ว)
THAI_MONTH_MAP = {
    'ม.ค.': 1, 'ก.พ.': 2, 'มี.ค.': 3, 'เม.ย.': 4, 'พ.ค.': 5, 'มิ.ย.': 6,
    'ก.ค.': 7, 'ส.ค.': 8, 'ก.ย.': 9, 'ต.ค.': 10, 'พ.ย.': 11, 'ธ.ค.': 12
}

def normalize_date(day, month_str, year_str):
    try:
        day = int(day)
        month = THAI_MONTH_MAP.get(month_str.replace(' ', ''))
        year = int(year_str)

        if year < 100: # ถ้าปีเป็น 2 หลัก เช่น 68
            year += 2500
        
        if year > 2500: # ถ้าเป็น พ.ศ. ให้แปลงเป็น ค.ศ.
            year -= 543
        
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, TypeError, AttributeError):
        return 'N/A'

def find_amount(text):
    amount_pattern = re.compile(r'(\d{1,3}(?:,\d{3})*\.\d{2})')
    
    keywords = ["จำนวนเงิน", "จำนวน:", "Amount"]
    for keyword in keywords:
        match = re.search(f"{keyword}[\\s:]*([\\d,]+\\.\\d{{2}})", text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(',', ''))
            
    all_amounts = [float(amount.replace(',', '')) for amount in amount_pattern.findall(text)]
    if all_amounts:
        return max(all_amounts)
        
    return 'N/A'

def parse_slip(text):
    data = {'date': 'N/A', 'amount': 'N/A', 'recipient': 'N/A', 'account': 'N/A'}

    # 1. ค้นหาวันที่
    date_match = re.search(r'(\d{1,2})\s+(ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)\s+(\d{2,4})', text)
    if date_match:
        data['date'] = normalize_date(date_match.group(1), date_match.group(2), date_match.group(3))

    # 2. ค้นหาจำนวนเงิน
    data['amount'] = find_amount(text)

    # 3. ค้นหาผู้รับและผู้โอน
    if "K+" in text or "กสิกรไทย" in text:
        account_match = re.search(r'(น\.[สส]\.|นาย)\s(.*?)\n', text)
        if account_match: data['account'] = account_match.group(0).strip()
        
        recipient_match = re.search(r'Prompt\s*Pay\s*\n(.*?)\n', text, re.MULTILINE) or \
                          re.search(r'TrueMoney Wallet\s*\n(.*?)\n', text, re.MULTILINE) or \
                          re.search(r'รี\s*(Shopee\s*Pay)', text)
        if recipient_match:
            data['recipient'] = (recipient_match.group(1) or recipient_match.group(2)).strip()

    elif "SCB" in text:
        from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
        to_match = re.search(r'ไปยัง\s*\n(.*?)\n', text, re.MULTILINE)
        if from_match: data['account'] = from_match.group(1).strip()
        if to_match: data['recipient'] = to_match.group(1).strip()
            
    elif "Bangkok Bank" in text:
        from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
        to_match = re.search(r'ไปที่\s*\n(.*?)\n', text, re.MULTILINE)
        if from_match: data['account'] = from_match.group(1).strip()
        if to_match: data['recipient'] = to_match.group(1).strip()
    
    return data