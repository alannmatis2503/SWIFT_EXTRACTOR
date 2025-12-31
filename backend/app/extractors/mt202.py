# backend/app/extractors/mt202.py
"""
Extracteur MT202 (text-level). Fournit :
- helpers utilitaires (get_field_block, parse_amount, parse_date_YYMMDD, etc.)
- parse_f32a, extract_transaction_reference (robuste)
- expose aussi parse_reference pour compatibilité avec mt103
- utilise bic_utils.get_donneur_from_f52 pour la valeur donneur_dordre (CODE/NAME)
"""

import re
from datetime import datetime
from typing import Optional
from dateutil import parser as dateparser
import pdfplumber

# bic helper (may return "CODE/Name" or "CODE")
try:
    from backend.app.extractors.bic_utils import get_donneur_from_f52, map_code_to_name
except Exception:
    # fallback: define no-op functions if bic_utils missing
    def get_donneur_from_f52(*a, **k):
        return None
    def map_code_to_name(*a, **k):
        return None

# regex / constants
BIC_RE = re.compile(r'\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b')
MT_RE = re.compile(r'\b(?:MT|FIN)[\s\-_]*(\d{3})\b', re.I)

# Codes ISO 4217 valides
VALID_CURRENCIES = {
    'USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD',
    'CNY', 'INR', 'RUB', 'BRL', 'MXN', 'SGD', 'HKD', 'KRW',
    'XAF', 'XOF', 'XPF', 'CFA',
    'ZAR', 'NGN', 'KES', 'EGP',
    'TND', 'MAD', 'AED', 'SAR', 'ILS',
    'THB', 'MYR', 'PHP', 'IDR', 'VND',
    'PKR', 'BDT', 'LKR',
}

CEMAC_MAP = {
    "CM": "CMR", "CMR": "CMR", "CAMEROON": "CMR",
    "GA": "GAB", "GAB": "GAB", "GABON": "GAB",
    "TD": "TCD", "TCD": "TCD", "CHAD": "TCD",
    "CG": "COG", "COG": "COG", "CONGO": "COG",
    "GQ": "GNQ", "GNQ": "GNQ", "EQUATORIAL GUINEA": "GNQ",
    "CF": "CAF", "CAF": "CAF", "CENTRAL AFRICAN REPUBLIC": "CAF"
}

# Pre-compiled regex patterns for performance optimization
_COUNTRY_CODE_PATTERN = re.compile(r'\b[A-Z]{2}\b')
_COUNTRY_CODES_SET = frozenset(CEMAC_MAP.keys())
_COUNTRY_CODES_LONG = frozenset(k for k in CEMAC_MAP if len(k) > 2)

# ---------- PDF text extractor ----------
def extract_text_from_pdf(path):
    txt = ""
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            txt += "\n" + (page.extract_text() or "")
    # minor normalization
    txt = re.sub(r'(?mi)^\s*page\s+\d+\s*(?:of\s*\d+)?\s*$', '', txt, flags=re.M)
    txt = re.sub(r'\r', '\n', txt)
    # collapse excessive blank lines but keep paragraph separation
    txt = re.sub(r'\n{3,}', '\n\n', txt)
    return txt

# ---------- low-level helpers ----------
def get_field_block(text: str, field_label: str) -> Optional[str]:
    """
    Return the multiline text belonging to a tag Fxx (e.g. 'F52A' or 'F20') inside `text`.
    """
    if not text:
        return None
    # Try label with optional trailing colon/description and capture following lines until next Fxx or end
    pattern = re.compile(r'(?si)(' + re.escape(field_label) + r'[:\s]*)(.*?)(?=\nF\d{2}[A-Z]?:|\nF\d{2}\b|$)')
    m = pattern.search(text)
    return m.group(2).strip() if m else None

def parse_amount(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    s = s.strip()
    # keep digits, thousand separators, decimal separators, minus
    s = re.sub(r'[^\d,.\-\s]', '', s)
    s = s.replace('\xa0', ' ')
    # normalize: detect whether comma is decimal or dot is decimal
    if s.count(',') and s.count('.'):
        # decide by last separator position
        if s.rfind(',') > s.rfind('.'):
            # comma decimal -> remove dots (thousand), replace comma with dot
            s = s.replace('.', '').replace(',', '.')
        else:
            # dot decimal -> remove commas
            s = s.replace(',', '')
    else:
        if s.count(','):
            # comma may be decimal if last group length 1-2 digits
            idx = s.rfind(',')
            if len(s) - idx - 1 in (1, 2):
                s = s.replace('.', '').replace(',', '.')
            else:
                s = s.replace(',', '')
        else:
            # no comma, remove spaces
            s = s.replace(' ', '')
    try:
        return float(s)
    except Exception:
        return None

def parse_date_YYMMDD(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    if re.fullmatch(r'\d{6}', s):
        yy = int(s[:2]); mm = int(s[2:4]); dd = int(s[4:6])
        year = 2000 + yy
        try:
            return datetime(year, mm, dd).date().isoformat()
        except Exception:
            return None
    try:
        d = dateparser.parse(s, dayfirst=False)
        return d.date().isoformat() if d else None
    except Exception:
        return None

def detect_country_from_text(txt: str) -> Optional[str]:
    """
    Detect country code from text using CEMAC_MAP.
    OPTIMIZED: Check 2-letter codes first (fastest), then longer names.
    Pre-compiled regex for ~2x performance improvement.
    """
    if not txt:
        return None
    
    txtu = txt.upper()
    
    # OPTIMIZED: Check 2-letter codes FIRST (most common, fastest)
    # Using pre-compiled regex pattern
    for match in _COUNTRY_CODE_PATTERN.finditer(txtu):
        code = match.group()
        if code in CEMAC_MAP:
            return CEMAC_MAP[code]
    
    # Check longer country names only if 2-letter codes not found
    for key in _COUNTRY_CODES_LONG:
        if key in txtu:
            return CEMAC_MAP[key]
    
    return None

# ---------- helpers for reference robustness ----------
def _looks_like_amount(s: Optional[str]) -> bool:
    if not s:
        return False
    s_low = s.lower()
    if 'amount' in s_low or 'currency' in s_low or 'montant' in s_low:
        return True
    # detect numbers with thousand separators and decimal comma/dot
    if re.search(r'\b\d{1,3}(?:[.\s]\d{3})*(?:[.,]\d{1,2})\b', s):
        return True
    # detect patterns like "191.700,64" or "191700,64"
    if re.search(r'\d+[.,]\d{2}', s):
        return True
    return False

def extract_transaction_reference(full_text: str, block4_text: Optional[str]) -> Optional[str]:
    """
    Robust extraction of transaction reference.
    Priority:
      1) F20 / :20: inside block4 (handles value on next non-empty line)
      2) header 'Transaction Reference: <TOKEN>' (token = [A-Z0-9_-]{3,})
      3) safe fallback: small token search but avoid picking amounts
    Returns uppercase reference or None.
    """
    # 1) try block4 / F20
    b = block4_text or ""
    if b:
        # same-line pattern: "F20: S065..." or ":20:S065..."
        m = re.search(r'(?mi)^(?:\:20\:|F20[:\s]*)(.*)$', b, flags=re.M)
        if m:
            cand = m.group(1).strip()
            if not cand:
                # find next non-empty line after the matched line
                lines = b.splitlines()
                for i, ln in enumerate(lines):
                    if re.match(r'(?mi)^(?:\:20\:|F20[:\s]*)', ln):
                        j = i + 1
                        while j < len(lines) and not lines[j].strip():
                            j += 1
                        if j < len(lines):
                            cand = lines[j].strip()
                        break
            if cand and not _looks_like_amount(cand):
                tok = re.search(r'([A-Z0-9\-\_]{3,})', cand, flags=re.I)
                if tok:
                    return tok.group(1).upper()
        else:
            # handle label on its own line and value on next line:
            lines = b.splitlines()
            for i, ln in enumerate(lines):
                if re.match(r'(?mi)^\s*(?:F20[:\s]*|:20:)', ln):
                    # see if same-line value
                    same = re.sub(r'(?mi)^\s*(?:F20[:\s]*|:20:)\s*', '', ln).strip()
                    if same:
                        cand = same
                    else:
                        j = i + 1
                        while j < len(lines) and not lines[j].strip():
                            j += 1
                        cand = lines[j].strip() if j < len(lines) else ""
                    if cand and not _looks_like_amount(cand):
                        tok = re.search(r'([A-Z0-9\-\_]{3,})', cand, flags=re.I)
                        if tok:
                            return tok.group(1).upper()
                    break

    # 2) header "Transaction Reference: TOKEN"
    m2 = re.search(r'(?mi)Transaction\s+Reference\s*[:\s]*([A-Z0-9\-\_]{3,})', full_text)
    if m2:
        cand = m2.group(1).strip()
        if not _looks_like_amount(cand):
            return cand.upper()

    # 3) safe fallback: look for a line immediately after "F20" label anywhere in full_text
    m_label = re.search(r'(?mi)(?:F20[:\s]*|:20:)\s*$', full_text, flags=re.M)
    if m_label:
        # find the position, then take next non-empty line
        pos = m_label.end()
        tail = full_text[pos: pos + 400]
        lines = tail.splitlines()
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            if not _looks_like_amount(ln):
                tok = re.search(r'([A-Z0-9\-\_]{3,})', ln, flags=re.I)
                if tok:
                    return tok.group(1).upper()
            break

    # nothing reliable
    return None

# provide parse_reference wrapper for backwards compatibility
def parse_reference(text: str) -> Optional[str]:
    """
    Backwards-compatible wrapper expected by mt103: compute an appropriate block4
    (prefer F20 or Block 4) and call the robust extractor.
    """
    if not text:
        return None
    block4 = get_field_block(text, 'F20') or get_field_block(text, ':20') or get_field_block(text, 'Block 4') or get_field_block(text, 'Block4') or text
    return extract_transaction_reference(text, block4)

# ---------- field parsers ----------
def parse_f32a(text: str) -> dict:
    """
    Parse F32A block (or fallback to text) and return dict with:
    {'date_reference': iso-date or None, 'devise': 'USD'|'EUR'|..., 'montant': float or None}
    """
    blk = get_field_block(text, 'F32A') or text or ""
    blk_clean = re.sub(r'#.*?#', '', blk, flags=re.S)
    res = {'date_reference': None, 'devise': None, 'montant': None}

    # date: try explicit Date: 251222 or a 6-digit token
    m_date = re.search(r'(?i)\bDate[:\s]*([0-9]{6})\b', blk_clean)
    if m_date:
        res['date_reference'] = parse_date_YYMMDD(m_date.group(1))
    else:
        m_date2 = re.search(r'(\d{6})', blk_clean)
        if m_date2:
            res['date_reference'] = parse_date_YYMMDD(m_date2.group(1))

    # currency: try strict pattern "Currency: Devise: XXX" first (3 uppercase letters without spaces)
    m_cur_strict = re.search(r'(?i)Currency\s*[:\s]+Devise\s*[:\s]+([A-Z]{3})\b', blk_clean)
    if m_cur_strict:
        candidate = m_cur_strict.group(1).upper()
        # Vérifier si c'est un code devise valide
        if candidate in VALID_CURRENCIES:
            res['devise'] = candidate
    
    # Fallback: chercher "Devise:" ou "Currency:" avec 3 lettres
    if not res['devise']:
        m_cur = re.search(r'(?i)\b(?:Devise|Currency)[:\s\S]{0,40}?([A-Z]{3})\b', blk_clean)
        if m_cur:
            candidate = m_cur.group(1).upper()
            if candidate in VALID_CURRENCIES:
                res['devise'] = candidate
    
    # Final fallback: chercher n'importe quel code valide dans le bloc
    if not res['devise']:
        m_cur2 = re.search(r'\b([A-Z]{3})\b', blk_clean)
        if m_cur2:
            candidate = m_cur2.group(1).upper()
            if candidate in VALID_CURRENCIES:
                res['devise'] = candidate

    # amount: prefer explicit "Montant|Amount" line
    candidate = None
    m_line = re.search(r'(?im)^\s*(?:Montant|Amount)\s*[:\-]\s*(.*)$', blk_clean, flags=re.M)
    if m_line:
        line = m_line.group(1).strip()
        nums = re.findall(r'([0-9]+(?:[.,\s][0-9]{1,3})*(?:[.,][0-9]{1,2})?)', line)
        if nums:
            def digits_len(s): return len(re.sub(r'[^0-9]', '', s))
            candidate = max(nums, key=digits_len)
    if not candidate:
        # fallback: pick the longest numeric-looking token in block
        nums_all = re.findall(r'([0-9]+(?:[.,\s][0-9]{1,3})*(?:[.,][0-9]{1,2})?)', blk_clean)
        if nums_all:
            def digits_len(s): return len(re.sub(r'[^0-9]', '', s))
            candidate = max(nums_all, key=digits_len)
    if candidate:
        res['montant'] = parse_amount(candidate)
    return res

def extract_receiver_bic(text: str) -> Optional[str]:
    """
    Try to extract the receiver BIC from header 'Receiver:' or anywhere in the text.
    Returns first matched BIC-like token or None.
    """
    if not text:
        return None
    # try 'Receiver:' block
    m = re.search(r'(?i)Receiver\s*[:\-]?\s*(.*?)(?=\n[A-Z][a-z]|$)', text, re.S)
    if m:
        part = m.group(1)
        m2 = BIC_RE.search(part)
        if m2:
            return m2.group(0)
    # fallback: search nearby 'RECEIVER' text region
    idx = text.upper().find('RECEIVER')
    if idx >= 0:
        tail = text[idx: idx + 400]
        m2 = BIC_RE.search(tail)
        if m2:
            return m2.group(0)
    # final fallback: any BIC-looking token in document
    m_any = BIC_RE.findall(text)
    return m_any[0] if m_any else None

# ---------- main extractor for text-block ----------
def extract_from_text(text: str, source: str = None) -> dict:
    row = {
        "type_MT": None,
        "code_banque": None,
        "sender_bic": None,
        "receiver_bic": None,
        "reference": None,
        "date_reference": None,
        "devise": None,
        "montant": None,
        "donneur_dordre": None,
        "beneficiaire": None,
        "pays_iso3": None,
        "source_pdf": source
    }

    # type_MT detection
    m = MT_RE.search(text)
    if m:
        row["type_MT"] = f"fin.{m.group(1)}".lower()

    # receiver BIC (prefer header)
    rb = extract_receiver_bic(text)
    row["code_banque"] = rb
    row["receiver_bic"] = rb

    # robust reference extraction : prefer F20 inside block4 if present
    block4 = get_field_block(text, 'Block 4') or get_field_block(text, 'Block4') or text
    # also try F20 block explicitly
    f20_block = get_field_block(text, 'F20') or get_field_block(text, ':20') or None
    # choose block4_text as f20_block if present else block4
    block4_text = f20_block or block4
    row["reference"] = extract_transaction_reference(text, block4_text)

    # parse amount/date/currency from F32A or text
    f32 = parse_f32a(text)
    row["date_reference"] = f32.get('date_reference')
    row["devise"] = f32.get('devise')
    row["montant"] = f32.get('montant')

    # F52A: use bic_utils.get_donneur_from_f52 to produce CODE/NAME or CODE
    f52_block = get_field_block(text, 'F52A') or get_field_block(text, 'F52A:')
    try:
        donneur = get_donneur_from_f52(f52_block or "", message_text=text)
    except Exception:
        donneur = None
    row["donneur_dordre"] = donneur

    # payer/beneficiary names: try F59/F58 blocks if present (simple best-effort)
    f59 = get_field_block(text, 'F59') or get_field_block(text, 'F58') or None
    if f59:
        # pick first non-empty line as beneficiary readable text
        lines = [ln.strip() for ln in f59.splitlines() if ln.strip()]
        if lines:
            row["beneficiaire"] = lines[0] if not row.get("beneficiaire") else row.get("beneficiaire")

    # country detection will be done from BIC mapping in mt_multi post-processing
    # row["pays_iso3"] = detect_country_from_text(text)  # removed: use BIC mapping only

    return row

def extract_block(block_text: str, source: str = None) -> dict:
    return extract_from_text(block_text, source=source)

def extract_for_mt202(pdf_path):
    txt = extract_text_from_pdf(pdf_path)
    return extract_from_text(txt, source=getattr(pdf_path, "name", str(pdf_path)))
