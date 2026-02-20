"""
=============================================================================
 Extracteur MT900 — Confirmations de débit
=============================================================================
 Ce module extrait les données structurées depuis un message SWIFT MT900
 (confirmation qu'un débit a été porté au compte d'une institution).

 Rôle dans l'application :
   Le MT900 est utilisé exclusivement dans le mode « Analyse des transferts
   exécutés ». Il confirme qu'un virement sortant (MT202/MT103) a bien été
   exécuté par la banque correspondante.

 Champs SWIFT spécifiques au MT900 :
   - F20  : Référence de la transaction MT900
   - F21  : Référence d'origine — c'est la CLÉ DE MATCHING :
            F21 du MT900 = F20 du MT202/MT103 correspondant
   - F32A : Date valeur + Devise + Montant

 Règles de matching :
   1. Le MT900 est rapproché du MT103/MT202 via F21 ↔ F20
   2. Les informations manquantes du MT900 (donneur, bénéficiaire, pays)
      sont complétées par celles du message matché
   3. Les MT900 avec T2PL dans F20 → exception « T2PL »
   4. Les MT900 avec NIVLT dans F21 → exception « nivellement »

 Dépendances :
   - Réutilise get_field_block, parse_amount, parse_date_YYMMDD de mt202.py
============================================================================="""

import re
from pathlib import Path
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)

# reuse helpers from mt202
from extractors.mt202 import (
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
            # Format: première ligne = description, deuxième ligne = valeur
            lines = [l.strip() for l in f20.splitlines() if l.strip()]
            for line in lines:
                # Ignorer les lignes descriptives
                if line.lower().startswith(('transaction', 'numéro', 'reference')):
                    continue
                # Ignorer les lignes trop courtes ou qui ressemblent à des headers
                if len(line) > 3 and not line.startswith('Page '):
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
            # Format: première ligne = description, deuxième ligne = valeur
            lines = [l.strip() for l in f21.splitlines() if l.strip()]
            for line in lines:
                # Ignorer les lignes descriptives
                if line.lower().startswith(('related', 'référence', 'origine')):
                    continue
                # Ignorer les lignes trop courtes
                if len(line) > 3 and not line.startswith('Page '):
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
    
    # Fallback direct depuis le header si disponible - à faire en premier car format plus fiable
    # Format header: "Amount: 35.000, Currency: USD"
    m_header = re.search(r'Amount:\s*([0-9\.,]+)\s*,?\s*Currency:\s*([A-Z]{3})', text)
    if m_header:
        if not row["montant"]:
            amt_str = m_header.group(1).replace('.', '').replace(',', '.')
            try:
                row["montant"] = float(amt_str)
            except Exception:
                pass
        if not row["devise"]:
            row["devise"] = m_header.group(2).upper()
    
    # Date depuis le header: "Value Date: 13/01/26"
    if not row["date_reference"]:
        m_vd = re.search(r'Value\s+Date:\s*(\d{2})/(\d{2})/(\d{2})', text)
        if m_vd:
            day, month, year = m_vd.groups()
            row["date_reference"] = f"20{year}-{month}-{day}"
    
    # Fallback: F32A field si header n'a pas donné les infos
    if not row["montant"] or not row["devise"]:
        f32a = get_field_block(text, 'F32A')
        if f32a:
            # Look for amount pattern - format: Amount: Montant: 35000, #35.000,#
            if not row["montant"]:
                m_amt = re.search(r'(?i)(?:Amount|Montant)\s*[:\s]*([0-9\.,\s#]+)', f32a)
                if m_amt:
                    amt_str = m_amt.group(1).strip()
                    # Nettoyer: enlever espaces, # et garder le premier nombre
                    amt_str = re.sub(r'[#\s]', '', amt_str)
                    if ',' in amt_str and '.' not in amt_str:
                        # Format européen: 35000,00
                        amt_str = amt_str.replace(',', '.')
                    elif '.' in amt_str and ',' in amt_str:
                        # Format mixte: 35.000,00 -> 35000.00
                        amt_str = amt_str.replace('.', '').replace(',', '.')
                    # Prendre le premier nombre valide
                    m_num = re.match(r'^([0-9\.]+)', amt_str)
                    if m_num:
                        try:
                            row["montant"] = float(m_num.group(1))
                        except Exception:
                            row["montant"] = parse_amount(m_num.group(1))
            
            # Look for currency - format spécifique: "Currency: Devise: USD US DOLLAR"
            # Doit être un code ISO 3 lettres après tous les ":"
            if not row["devise"]:
                # Pattern spécifique pour ce format
                m_cur = re.search(r'Currency:\s*Devise:\s*([A-Z]{3})\s', f32a)
                if m_cur:
                    row["devise"] = m_cur.group(1).upper()
            
            # Look for date - format: Date: 260113 2026 Jan 13
            if not row["date_reference"]:
                m_date = re.search(r'(?:Date|Value)\s*[:\s]*(\d{6})\s', f32a)
                if m_date:
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
