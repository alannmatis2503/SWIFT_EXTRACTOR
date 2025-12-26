# backend/app/extractors/mt103.py
"""
Extracteur MT103 — wrapper/variant spécifique.
Uses bic_utils.get_donneur_from_f52 for donor mapping.
"""

import re
from pathlib import Path
from typing import Optional
import pdfplumber

from backend.app.extractors.mt202 import (
    get_field_block,
    parse_amount,
    parse_date_YYMMDD,
    detect_country_from_text,
    extract_receiver_bic,
    parse_reference as parse_reference_mt202,
)
from backend.app.extractors.bic_utils import get_donneur_from_f52  # NEW

def parse_f32a_103(text: str) -> dict:
    blk = get_field_block(text, 'F32A') or text
    blk_clean = re.sub(r'#.*?#', '', blk, flags=re.S)
    result = {'date_reference': None, 'devise': None, 'montant': None}
    m_date = re.search(r'(?i)\bDate[:\s]*([0-9]{6})\b', blk_clean)
    if m_date:
        result['date_reference'] = parse_date_YYMMDD(m_date.group(1))
    else:
        m_date2 = re.search(r'(\d{6})', blk_clean)
        if m_date2:
            result['date_reference'] = parse_date_YYMMDD(m_date2.group(1))
    m_cur = re.search(r'(?i)\bDevise[:\s]*([A-Z]{3})\b', blk_clean)
    if m_cur:
        result['devise'] = m_cur.group(1)
    else:
        m_cur2 = re.search(r'(?i)Currency[:\s\S]{0,80}?([A-Z]{3})\b', blk_clean)
        if m_cur2:
            result['devise'] = m_cur2.group(1)
        else:
            m_cur3 = re.search(r'\b([A-Z]{3})\b', blk_clean)
            if m_cur3:
                result['devise'] = m_cur3.group(1)
    candidate = None
    m_line = re.search(r'(?im)^\s*(?:Montant|Amount)\s*[:\-]\s*(.*)$', blk_clean, flags=re.M)
    if m_line:
        line = m_line.group(1).strip()
        nums = re.findall(r'([0-9]+(?:[.,\s][0-9]{1,3})*(?:[.,][0-9]{1,2})?)', line)
        if nums:
            def digits_len(s): return len(re.sub(r'[^0-9]', '', s))
            candidate = max(nums, key=digits_len)
    if not candidate:
        nums_all = re.findall(r'([0-9]+(?:[.,\s][0-9]{1,3})*(?:[.,][0-9]{1,2})?)', blk_clean)
        if nums_all:
            def digits_len(s): return len(re.sub(r'[^0-9]', '', s))
            candidate = max(nums_all, key=digits_len)
    if candidate:
        result['montant'] = parse_amount(candidate)
    return result

def parse_f59_account(text: str) -> Optional[str]:
    blk = get_field_block(text, 'F59') or get_field_block(text, 'F59:')
    if not blk:
        return None
    blk_clean = re.sub(r'#.*?#', '', blk, flags=re.S)
    m = re.search(r'(?m)^\s*\/?([A-Z]{2}[0-9A-Z]{8,34})\b', blk_clean)
    if not m:
        m = re.search(r'\/([A-Z]{2}[0-9A-Z]{8,34})', blk_clean)
    if not m:
        m = re.search(r'([A-Z]{2}[0-9A-Z]{8,34})', blk_clean)
    if not m:
        return None
    candidate = m.group(1)
    candidate_norm = re.sub(r'\s+', '', candidate).upper()
    return candidate_norm

def parse_f52a_or_f50f_institution(text: str) -> Optional[str]:
    """
    Prefer F52A (donor) processed by bic_utils.get_donneur_from_f52.
    If absent, fallback to previous heuristics (F50F/F50).
    """
    # try F52A using strict bic_utils
    f52 = get_field_block(text, 'F52A')
    # If get_donneur_from_f52 returns code/name, use it
    donneur = None
    if f52:
        donneur = get_donneur_from_f52(f52, message_text=text)
        if donneur:
            return donneur

    # fallback: try to get a human-friendly name from F52A (previous logic)
    if f52:
        lines = [l.strip() for l in re.sub(r'<[^>]+>', ' ', f52).splitlines() if l.strip()]
        name_lines = []
        for ln in lines:
            up = ln.upper()
            if up.startswith("IDENTIFIER") or up.startswith("IDENTIFIERCODE") or up.startswith("CODE") or up.startswith("PARTYIDENTIFIER") or up.startswith("IDENTIFIANT"):
                continue
            if re.match(r'^\/[A-Z0-9\/\-]+', ln):
                continue
            if re.fullmatch(r'[A-Z0-9]{6,11}', ln.replace(' ', '')):
                continue
            if len(ln) > 1:
                name_lines.append(ln)
        if name_lines:
            for i, ln in enumerate(name_lines):
                up = ln.upper()
                if 'BANK' in up or 'BANQUE' in up or 'ORABANK' in up:
                    out = ln
                    if i+1 < len(name_lines) and len(name_lines[i+1]) < 40:
                        out = f"{out} / {name_lines[i+1]}"
                    return out.strip()
            out = ' '.join(name_lines[:2]).strip()
            return out

    # fallback to F50F / F50 (client giver)
    blk50 = get_field_block(text, 'F50F') or get_field_block(text, 'F50')
    if blk50:
        lines = [l.strip() for l in blk50.splitlines() if l.strip()]
        name_candidates = []
        for ln in lines:
            up = ln.upper()
            if up.startswith("NAMEANDADDRESS") or up.startswith("DETAILS") or re.search(r'[A-Za-z]', ln):
                if up.startswith("NUMBER") or up.startswith("PARTYIDENTIFIER") or up.startswith("COMPTE"):
                    continue
                if len(ln) >= 4 and re.search(r'[A-Za-z]', ln):
                    name_candidates.append(ln)
        if name_candidates:
            out = ' '.join(name_candidates[:2]).strip()
            return out

    return None

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
    m_type = re.search(r'\b(?:MT|FIN)[\s\-_]*(\d{3})\b', text, re.I)
    if m_type:
        row["type_MT"] = f"fin.{m_type.group(1)}".lower()
    else:
        row["type_MT"] = "fin.103"
    rb = extract_receiver_bic(text)
    row["code_banque"] = rb
    row["receiver_bic"] = rb
    try:
        ref = parse_reference_mt202(text)
        row["reference"] = ref
    except Exception:
        blk20 = get_field_block(text, 'F20')
        if blk20:
            for ln in blk20.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                if re.search(r'\d+\/\d+|\w+\/\w+|\d{2,}', ln):
                    row["reference"] = ln
                    break
            if not row["reference"]:
                row["reference"] = blk20.splitlines()[0].strip()
    f32 = parse_f32a_103(text)
    row["date_reference"] = f32.get("date_reference")
    row["devise"] = f32.get("devise")
    row["montant"] = f32.get("montant")

    # F52A or fallback F50F
    inst = parse_f52a_or_f50f_institution(text)
    row["donneur_dordre"] = inst

    # bénéficiaire
    row["beneficiaire"] = parse_f59_account(text)

    # country detection
    row["pays_iso3"] = detect_country_from_text(text)
    return row

def extract_block(block_text: str, source: str = None) -> dict:
    return extract_from_text(block_text, source=source)

def extract_for_mt103(pdf_path):
    txt = ""
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            txt += "\n" + (page.extract_text() or "")
    return extract_from_text(txt, source=getattr(pdf_path, "name", str(pdf_path)))

if __name__ == "__main__":
    import sys
    from pprint import pprint
    if len(sys.argv) < 2:
        print("Usage: python mt103.py path/to/103.pdf")
        raise SystemExit(1)
    pprint(extract_for_mt103(sys.argv[1]))
