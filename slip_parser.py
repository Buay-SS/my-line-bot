import re
from datetime import datetime

# --- พจนานุกรมและฟังก์ชันช่วยเหลือ (เหมือนเดิม แต่ปรับปรุง) ---
THAI_MONTH_MAP = {
    'ม.ค.': 1, 'ก.พ.': 2, 'มี.ค.': 3, 'เม.ย.': 4, 'พ.ค.': 5, 'มิ.ย.': 6,
    'ก.ค.': 7, 'ส.ค.': 8, 'ก.ย.': 9, 'ต.ค.': 10, 'พ.ย.': 11, 'ธ.ค.': 12
}

def normalize_date(day_str, month_str, year_str):
    try:
        day = int(day_str)
        month = THAI_MONTH_MAP.get(month_str.replace('.', '').strip())
        year = int(year_str)

        if year < 100:  # กรณีปีเป็น 2 หลัก เช่น 68
            year += 2500
        
        if year > 2500: # ถ้าเป็น พ.ศ. ให้แปลงเป็น ค.ศ.
            year -= 543
        
        # ส่งคืนเป็น YYYY-MM-DD ซึ่งเป็นรูปแบบมาตรฐาน
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, TypeError, AttributeError):
        return 'N/A'

def find_amount(text):
    # Regex ที่ดีขึ้น: มองหา "จำนวน" หรือ "Amount" แล้วตามด้วยตัวเลข
    # หรือมองหาตัวเลขที่มี .00 THB ต่อท้าย
    patterns = [
        r'(?:จำนวน|Amount)[\s:]*([,\d]+\.\d{2})',
        r'([,\d]+\.\d{2})\s*THB'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(',', ''))
    
    # Fallback: ถ้าไม่เจอจากคำสำคัญ ให้หาตัวเลข .00 ที่ใหญ่ที่สุด
    all_amounts = [float(amount.replace(',', '')) for amount in re.findall(r'(\d{1,3}(?:,\d{3})*\.\d{2})', text)]
    if all_amounts:
        return max(all_amounts)
        
    return 'N/A'

# --- Parser เฉพาะสำหรับแต่ละธนาคาร ---

def _parse_kbank_slip(text):
    """Parser สำหรับสลิปจาก K+/KBank โดยเฉพาะ"""
    data = {'date': 'N/A', 'amount': 'N/A', 'recipient': 'N/A', 'account': 'N/A'}

    # ผู้โอน: มองหา "น.ส./นาย" แล้วเอาเฉพาะชื่อข้างหลัง
    account_match = re.search(r'(?:น\.ส\.|นาย)\s+(.*?)\n', text)
    if account_match:
        data['account'] = account_match.group(1).strip()

    # ผู้รับ: ตรวจสอบจาก Keyword ที่ชัดเจนก่อน
    if "TrueMoney Wallet" in text:
        data['recipient'] = "TrueMoney Wallet"
    elif "ShopeePay" in text or "รี Shopee" in text:
        data['recipient'] = "ShopeePay"
    else:
        # ถ้าไม่ใช่ e-wallet ให้หาจาก PromptPay
        recipient_match = re.search(r'Prompt\s*Pay\s*\n(.*?)\n', text, re.MULTILINE)
        if recipient_match:
            data['recipient'] = recipient_match.group(1).strip()
            
    return data

def _parse_scb_slip(text):
    """Parser สำหรับสลิปจาก SCB โดยเฉพาะ"""
    data = {'date': 'N/A', 'amount': 'N/A', 'recipient': 'N/A', 'account': 'N/A'}

    from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
    if from_match:
        data['account'] = from_match.group(1).strip()

    to_match = re.search(r'ไปยัง\s*\n(.*?)\n', text, re.MULTILINE)
    if to_match:
        data['recipient'] = to_match.group(1).strip()
        
    return data

def _parse_bbl_slip(text):
    """Parser สำหรับสลิปจาก Bangkok Bank โดยเฉพาะ"""
    data = {'date': 'N/A', 'amount': 'N/A', 'recipient': 'N/A', 'account': 'N/A'}

    from_match = re.search(r'จาก\s*\n(.*?)\n', text, re.MULTILINE)
    if from_match:
        data['account'] = from_match.group(1).strip()

    to_match = re.search(r'ไปที่\s*\n(.*?)\n', text, re.MULTILINE)
    if to_match:
        data['recipient'] = to_match.group(1).strip()
        
    return data


# --- ฟังก์ชันหลัก (ตัวจัดการ/Router) ---

def parse_slip(text):
    """
    ฟังก์ชันหลักที่จะวิเคราะห์ข้อความดิบ, ตรวจสอบว่าเป็นสลิปจากธนาคารไหน,
    แล้วเรียกใช้ Parser เฉพาะทางที่เหมาะสม
    """
    # 1. หาข้อมูลพื้นฐานที่มักจะเหมือนกันในทุกสลิป
    base_data = {}
    
    # ค้นหาวันที่: รูปแบบ "dd เดือน yy"
    date_match = re.search(r'(\d{1,2})\s+(ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)\s+(\d{2,4})', text)
    if date_match:
        base_data['date'] = normalize_date(date_match.group(1), date_match.group(2), date_match.group(3))

    # ค้นหาจำนวนเงิน
    base_data['amount'] = find_amount(text)
    
    # 2. ตรวจสอบว่าเป็นสลิปของธนาคารไหน แล้วเรียกใช้ Parser เฉพาะทาง
    specific_data = {}
    if "K+" in text or "กสิกรไทย" in text:
        specific_data = _parse_kbank_slip(text)
    elif "SCB" in text:
        specific_data = _parse_scb_slip(text)
    elif "Bangkok Bank" in text:
        specific_data = _parse_bbl_slip(text)
    # (คุณสามารถเพิ่ม elif สำหรับธนาคารอื่นๆ ได้ที่นี่ในอนาคต)
    else:
        # ถ้าไม่สามารถระบุธนาคารได้
        print("Could not identify the bank from the slip.")

    # 3. รวมผลลัพธ์จากข้อมูลพื้นฐานและข้อมูลเฉพาะทางเข้าด้วยกัน
    # โดยจะยึดข้อมูลจาก Parser เฉพาะทางเป็นหลักถ้ามี
    final_data = {**base_data, **specific_data}

    # 4. ตรวจสอบและทำความสะอาดข้อมูลครั้งสุดท้าย
    for key, value in final_data.items():
        if value is None or value == '':
            final_data[key] = 'N/A' # N/A = Not Available
            
    return final_data