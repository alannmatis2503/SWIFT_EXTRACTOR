# backend/app/extractors/mt910.py
"""
Extractor for SWIFT-like MT910 (confirmations/report outputs).
Rules (as requested):
 - For MT910, donor = sender, beneficiary = receiver.
 - Do NOT consult external BIC mapping for MT910.
 - Extract sender code (exactly 11 chars, A-Z0-9) from Sender Institution block.
 - Extract receiver code (exactly 11 chars) from Receiver Institution block.
 - Extract expansion name from "Expansion:" if present (same block) and produce "[CODE]/[NAME]".
 - If name missing, fall back to a readable line from the block.
 - Always set sender_bic and receiver_bic to the raw 11-char code (if found).
 - Ensure reference extraction still uses header "Transaction Reference" or block4 :20:.
"""

import re
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# reuse helpers from mt202 for consistent text extraction and small utilities
from backend.app.extractors.mt202 import (
    extract_text_from_pdf,
    parse_amount,
    parse_date_YYMMDD,
    detect_country_from_text,
    BIC_RE,
    get_field_block,
)

# ---------- helpers ----------

# strict sender/receiver code: uppercase letters/digits exactly 11 chars
CODE11_RE = re.compile(r'\b([A-Z0-9]{11})\b')

def _parse_block4(text: str) -> Optional[str]:
    m = re.search(r'(?si)Block\s*4(.*?)(?:Block\s*5|Message Text|End of report|End of Message|$)', text)
    return m.group(1).strip() if m else None

def _extract_tag_from_block4(block4: Optional[str], tag: str) -> Optional[str]:
    if not block4:
        return None
    pat = re.compile(r'(?m)^:' + re.escape(tag) + r':\s*(.*)$')
    m = pat.search(block4)
    if m:
        return m.group(1).strip()
    m2 = re.search(r':' + re.escape(tag) + r':\s*([^\r\n]+)', block4)
    return m2.group(1).strip() if m2 else None

def _extract_sender_receiver_header(text: str):
    """
    Extract the raw 'Sender Institution' and 'Receiver Institution' blocks.
    Returns (sender_block_text, receiver_block_text) or (None, None).
    """
    sender = None
    receiver = None
    m_sender = re.search(r'(?si)Sender Institution\s*[:\s]*([^\n].*?)(?=Receiver Institution\s*:|Message Text|Block 4|$)', text)
    if m_sender:
        sender = m_sender.group(1).strip()
    m_receiver = re.search(r'(?si)Receiver Institution\s*[:\s]*([^\n].*?)(?=Message Text|Block 4|$)', text)
    if m_receiver:
        receiver = m_receiver.group(1).strip()
    return sender, receiver

def _compact_whitespace(s: Optional[str]) -> Optional[str]:
    return re.sub(r'\s+', ' ', s).strip() if s else s

def _extract_expansion_name(block_text: str) -> Optional[str]:
    """
    Get the first 'Expansion:' value inside the header block (if present).
    Accepts 'Expansion: NAME' on same line.
    """
    if not block_text:
        return None
    m = re.search(r'(?i)Expansion\s*[:\s]\s*([^\r\n]+)', block_text)
    if m:
        return m.group(1).strip()
    # sometimes "Expansion: <name>" may be followed on next lines; try capture across small window
    m2 = re.search(r'(?i)Expansion\s*[:\s]\s*([^\n]{1,120})', block_text)
    if m2:
        return m2.group(1).strip()
    return None

def _find_code11_in_block(block_text: Optional[str]) -> Optional[str]:
    """
    Find the first token matching exactly 11 alnum (A-Z0-9) in the block.
    Uppercase result returned.
    """
    if not block_text:
        return None
    # search for 11-char token (prefer one containing letters)
    toks = re.findall(r'\b([A-Z0-9]{11})\b', block_text, flags=re.I)
    if not toks:
        return None
    # prefer token that contains a letter (likely BIC-type)
    for t in toks:
        if re.search(r'[A-Z]', t, flags=re.I):
            return t.upper()
    # else return first
    return toks[0].upper()

def _format_code_and_name(code: Optional[str], name: Optional[str]) -> Optional[str]:
    if not code and not name:
        return None
    if code:
        code_u = code.strip().upper()
        if name:
            return f"{code_u}/{_compact_whitespace(name)}"
        return code_u
    # no code but name present
    return _compact_whitespace(name)

# ---------- main extractor ----------

def _extract_from_text(text: str, source: str = None) -> dict:
    row = {
        "type_MT": "fin.910",
        "code_banque": None,
        "sender_bic": None,
        "receiver_bic": None,
        "reference": None,
        "date_reference": None,
        "devise": None,
        "montant": None,
        "donneur_dordre": None,      # legacy key expected by app
        "institution_name": None,    # alias for donneur
        "beneficiaire": None,
        "pays_iso3": None,
        "source_pdf": source,
        "related_reference": None,
        "sender_account": None
    }

    # 0) reference: prefer header "Transaction Reference" (works cross-block) then :20:
    m_tr = re.search(r'(?i)Transaction Reference\s*[:\s]*([A-Z0-9\-\_]+)', text)
    if m_tr:
        row["reference"] = m_tr.group(1).strip()

    # 1) header sender/receiver
    sender_blk, receiver_blk = _extract_sender_receiver_header(text)

    # sender
    if sender_blk:
        code = _find_code11_in_block(sender_blk)
        expansion = _extract_expansion_name(sender_blk)
        formatted = _format_code_and_name(code, expansion)
        if formatted:
            row["donneur_dordre"] = formatted
            row["institution_name"] = formatted
        else:
            # fallback: pick a readable line
            lines = [ln.strip() for ln in sender_blk.splitlines() if ln.strip()]
            if lines:
                # if first token looks like code, remove it from the name
                first = lines[0]
                # if first contains code at start, drop it for name
                mcode = CODE11_RE.search(first)
                if mcode:
                    candidate_name = " / ".join(lines[1:3]) if len(lines) > 1 else first
                else:
                    candidate_name = " / ".join(lines[:2])
                row["donneur_dordre"] = _compact_whitespace(candidate_name)
        # also set sender_bic as code if found
        if sender_blk:
            c11 = _find_code11_in_block(sender_blk)
            if c11:
                row["sender_bic"] = c11
                # set code_banque if not set
                if not row.get("code_banque"):
                    row["code_banque"] = c11

    # receiver
    if receiver_blk:
        code_r = _find_code11_in_block(receiver_blk)
        expansion_r = _extract_expansion_name(receiver_blk)
        formatted_r = _format_code_and_name(code_r, expansion_r)
        if formatted_r:
            row["beneficiaire"] = formatted_r
        else:
            lines = [ln.strip() for ln in receiver_blk.splitlines() if ln.strip()]
            if lines:
                # similar fallback
                first = lines[0]
                mcode = CODE11_RE.search(first)
                if mcode:
                    candidate_name = " / ".join(lines[1:3]) if len(lines) > 1 else first
                else:
                    candidate_name = " / ".join(lines[:2])
                row["beneficiaire"] = _compact_whitespace(candidate_name)
        if code_r:
            row["receiver_bic"] = code_r
            # if code_banque not set prefer receiver
            if not row.get("code_banque"):
                row["code_banque"] = code_r

    # 2) block4 tags: prefer :20: for reference, :32A: for date/currency/amount, :25P for account
    block4 = _parse_block4(text)
    if block4:
        tag20 = _extract_tag_from_block4(block4, '20')
        if tag20:
            row["reference"] = tag20.strip()
        tag21 = _extract_tag_from_block4(block4, '21')
        if tag21:
            row["related_reference"] = tag21.strip()
        tag25 = _extract_tag_from_block4(block4, '25P') or _extract_tag_from_block4(block4, '25')
        if tag25:
            row["sender_account"] = tag25.strip()
        tag32 = _extract_tag_from_block4(block4, '32A')
        if tag32:
            # reuse small parser from mt202: try simple inline parse
            m = re.match(r'^\s*(\d{6})\s*([A-Z]{3})\s*([0-9\.,]+)\s*$', tag32)
            if m:
                date_iso = parse_date_YYMMDD(m.group(1))
                cur = m.group(2).upper()
                try:
                    amt = float(m.group(3).replace('.', '').replace(',', '.'))
                except Exception:
                    amt = None
            else:
                # fallback: find currency token and number
                m2 = re.search(r'([A-Z]{3})\s*([0-9\.,]+)', tag32)
                if m2:
                    cur = m2.group(1).upper()
                    try:
                        amt = float(m2.group(2).replace('.', '').replace(',', '.'))
                    except Exception:
                        amt = None
                else:
                    date_iso = None
                    cur = None
                    amt = None
            if date_iso:
                row["date_reference"] = date_iso
            if cur:
                row["devise"] = cur
            if amt is not None:
                row["montant"] = amt

    # 3) fallback free text amount/date
    if row.get("montant") is None:
        m_amt = re.search(r'(?i)Amount[:\s]*([0-9\.,\s]+)\s*(?:Currency[:\s]*([A-Z]{3}))?', text)
        if m_amt:
            s = m_amt.group(1)
            cur = m_amt.group(2)
            try:
                val = float(s.replace('.', '').replace(',', '.'))
            except Exception:
                val = None
            row["montant"] = val
            if cur:
                row["devise"] = cur.upper()

    if not row.get("date_reference"):
        m_val = re.search(r'(?i)Value Date[:\s]*([0-3]?\d)[\/\-]([01]?\d)[\/\-]([0-9]{2,4})', text)
        if m_val:
            d, mth, y = m_val.group(1), m_val.group(2), m_val.group(3)
            if len(y) == 2:
                y = '20' + y
            try:
                row["date_reference"] = f"{int(y):04d}-{int(mth):02d}-{int(d):02d}"
            except Exception:
                pass

    # 4) country detection
    row["pays_iso3"] = detect_country_from_text(text)

    # normalization: uppercase currency
    if row.get("devise"):
        row["devise"] = row["devise"].upper()

    # cast montant to float if possible (already done)
    try:
        if row.get("montant") is not None:
            row["montant"] = float(row["montant"])
    except Exception:
        row["montant"] = None

    return row

# Public API
def extract_block(block_text: str, source: str = None) -> dict:
    return _extract_from_text(block_text, source=source)

def extract_for_mt910(pdf_path):
    p = Path(pdf_path)
    txt = extract_text_from_pdf(p)
    return _extract_from_text(txt, source=getattr(p, "name", str(p)))

if __name__ == "__main__":
    import sys
    from pprint import pprint
    if len(sys.argv) < 2:
        print("Usage: python3 mt910.py path/to/910.pdf")
        raise SystemExit(1)
    pprint(extract_for_mt910(sys.argv[1]))
