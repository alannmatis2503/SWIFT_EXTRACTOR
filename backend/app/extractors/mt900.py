# backend/app/extractors/mt900.py
"""
Extractor for SWIFT MT900 (Confirmation of Debit).
Used for "Analyse des transferts sortants exécutés" feature.

Extracts:
- F20: Reference (Transaction Reference)
- F21: Related Reference (Original Reference) - used to match with 202/103
- F32A: Value Date, Currency, Amount
"""

import re
from pathlib import Path
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)

# reuse helpers from mt202
from backend.app.extractors.mt202 import (
    get_field_block,
    parse_amount,
    parse_date_YYMMDD,
    BIC_RE,
)


def _extract_tag_from_block4(text: str, tag: str) -> Optional[str]:
    """Extract a specific tag value from block 4 format."""
    if not text:
        return None
    # Pattern: :TAG:value
    pat = re.compile(r'(?m)^:' + re.escape(tag) + r':\s*(.*)$')
    m = pat.search(text)
    if m:
        return m.group(1).strip()
    # Fallback: inline format
    m2 = re.search(r':' + re.escape(tag) + r':\s*([^\r\n]+)', text)
    return m2.group(1).strip() if m2 else None


def _parse_block4(text: str) -> Optional[str]:
    """Extract Block 4 content."""
    m = re.search(r'(?si)Block\s*4(.*?)(?:Block\s*5|Message Text|End of report|End of Message|$)', text)
    return m.group(1).strip() if m else None


def extract_from_text(text: str, source: str = None) -> Dict:
    """
    Extract MT900 fields from text.
    
    Returns dict with:
    - type_MT: "fin.900"
    - reference: F20 (Transaction Reference)
    - related_reference: F21 (Related Reference / Original Reference)
    - date_reference: From F32A
    - devise: From F32A
    - montant: From F32A
    - source_pdf
    """
    row = {
        "type_MT": "fin.900",
        "code_banque": None,
        "sender_bic": None,
        "receiver_bic": None,
        "reference": None,
        "related_reference": None,  # F21 - clé pour matcher avec 202/103
        "date_reference": None,
        "devise": None,
        "montant": None,
        "donneur_dordre": None,
        "code_donneur_dordre": None,
        "beneficiaire": None,
        "pays_iso3": None,
        "source_pdf": source,
        "commentaires": None,
        "correspondant": None,
    }
    
    # Try to extract from Block 4 format first
    block4 = _parse_block4(text)
    
    # F20: Transaction Reference
    if block4:
        ref = _extract_tag_from_block4(block4, '20')
        if ref:
            row["reference"] = ref.strip()
    
    if not row["reference"]:
        # Fallback: look for F20 field
        f20 = get_field_block(text, 'F20')
        if f20:
            # Take first non-empty line
            for line in f20.splitlines():
                line = line.strip()
                if line and not line.lower().startswith('transaction'):
                    row["reference"] = line
                    break
    
    # F21: Related Reference (Original Reference)
    if block4:
        rel_ref = _extract_tag_from_block4(block4, '21')
        if rel_ref:
            row["related_reference"] = rel_ref.strip()
    
    if not row["related_reference"]:
        # Fallback: look for F21 field
        f21 = get_field_block(text, 'F21')
        if f21:
            for line in f21.splitlines():
                line = line.strip()
                if line and not line.lower().startswith('related'):
                    row["related_reference"] = line
                    break
    
    # Also try "Related Reference:" pattern in text
    if not row["related_reference"]:
        m = re.search(r'(?i)Related\s+Reference\s*[:\s]*([A-Z0-9\-\_/]+)', text)
        if m:
            row["related_reference"] = m.group(1).strip()
    
    # F32A: Value Date, Currency, Amount
    if block4:
        tag32 = _extract_tag_from_block4(block4, '32A')
        if tag32:
            # Format: YYMMDDCCCAMOUNT (e.g., 260112EUR1000,00)
            m = re.match(r'^\s*(\d{6})\s*([A-Z]{3})\s*([0-9\.,]+)\s*$', tag32)
            if m:
                row["date_reference"] = parse_date_YYMMDD(m.group(1))
                row["devise"] = m.group(2).upper()
                try:
                    row["montant"] = float(m.group(3).replace('.', '').replace(',', '.'))
                except Exception:
                    row["montant"] = parse_amount(m.group(3))
            else:
                # Try to extract parts separately
                m_date = re.search(r'(\d{6})', tag32)
                if m_date:
                    row["date_reference"] = parse_date_YYMMDD(m_date.group(1))
                m_cur = re.search(r'([A-Z]{3})', tag32)
                if m_cur:
                    row["devise"] = m_cur.group(1).upper()
                m_amt = re.search(r'([0-9\.,]+)\s*$', tag32)
                if m_amt:
                    row["montant"] = parse_amount(m_amt.group(1))
    
    # Fallback: F32A field
    if not row["montant"]:
        f32a = get_field_block(text, 'F32A')
        if f32a:
            # Look for amount pattern
            m_amt = re.search(r'(?i)(?:Amount|Montant)\s*[:\s]*([0-9\.,\s]+)', f32a)
            if m_amt:
                row["montant"] = parse_amount(m_amt.group(1))
            
            # Look for currency
            m_cur = re.search(r'(?i)(?:Currency|Devise)\s*[:\s]*([A-Z]{3})', f32a)
            if m_cur and not row["devise"]:
                row["devise"] = m_cur.group(1).upper()
            
            # Look for date
            m_date = re.search(r'(?i)(?:Date|Value)\s*[:\s]*(\d{6})', f32a)
            if m_date and not row["date_reference"]:
                row["date_reference"] = parse_date_YYMMDD(m_date.group(1))
    
    # Extract BICs from header
    m_sender = re.search(r'(?i)Sender(?:\s+Institution)?\s*[:\s]*.*?([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)', text, re.DOTALL)
    if m_sender:
        row["sender_bic"] = m_sender.group(1).upper()
    
    m_receiver = re.search(r'(?i)Receiver(?:\s+Institution)?\s*[:\s]*.*?([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)', text, re.DOTALL)
    if m_receiver:
        row["receiver_bic"] = m_receiver.group(1).upper()
        row["correspondant"] = row["receiver_bic"]
    
    return row


def extract_block(block_text: str, source: str = None) -> Dict:
    """Extract from a text block (same as extract_from_text)."""
    return extract_from_text(block_text, source=source)


if __name__ == "__main__":
    import sys
    from pprint import pprint
    if len(sys.argv) < 2:
        print("Usage: python mt900.py path/to/900.pdf")
        raise SystemExit(1)
    
    import pdfplumber
    txt = ""
    with pdfplumber.open(sys.argv[1]) as pdf:
        for page in pdf.pages:
            txt += "\n" + (page.extract_text() or "")
    
    pprint(extract_from_text(txt, source=sys.argv[1]))
