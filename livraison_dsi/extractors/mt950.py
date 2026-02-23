"""
Extracteur MT950 (Statement Message) — parsing des écritures F61.

Lit un PDF contenant un ou plusieurs messages fin.950.
Pour chaque message, extrait les champs d'en-tête (F20, F25, F28C, F60, F62)
et toutes les écritures F61 avec :
  - ValueDate, DebitCreditMark (C/D), Amount
  - ReferenceForTheAccountOwner  (RefOwner)
  - ReferenceOfTheAccountServicingInstitution  (RefServ, après //)
  - IdentificationCode (202, 910, etc.)
  - SupplementaryDetails
"""

import re
import logging
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ---------- text extraction ----------
try:
    import fitz as pymupdf
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False


def _extract_text(pdf_path: Path) -> str:
    """Extract text from PDF, preferring PyMuPDF for speed."""
    if HAS_PYMUPDF:
        try:
            doc = pymupdf.open(str(pdf_path))
            pages = [doc[i].get_text() for i in range(len(doc))]
            doc.close()
            return "\n".join(pages)
        except Exception:
            pass
    if HAS_PDFPLUMBER:
        with pdfplumber.open(str(pdf_path)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    raise RuntimeError("Ni PyMuPDF ni pdfplumber n'est disponible pour lire le PDF.")


# ---------- amount normalisation ----------
def normalize_amount(raw: str) -> Optional[float]:
    """
    Normalise un montant au format européen (1.234.567,89) vers float.
    Gère les formats tronqués comme '303.100,' ou '57.006,6'.
    """
    if not raw:
        return None
    s = raw.replace("#", "").strip()
    # European format: dots as thousands sep, comma as decimal sep
    s = s.replace(".", "").replace(",", ".")
    # trailing dot from missing decimals
    if s.endswith("."):
        s = s[:-1]
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------- F61 parsing ----------
# Pre-compiled patterns
_F61_SPLIT_RE = re.compile(r"(?=F61:\s*Ecriture)")
_CD_RE = re.compile(r"DebitCreditMark.*?:\s*([CD])")
_AMT_RE = re.compile(r"Amount:.*?#([\d.,]+)#")
_IDC_RE = re.compile(r"IdentificationCode:.*?:\s*(\d+)")
_REF_OWNER_RE = re.compile(
    r"ReferenceForTheAccountOwner:.*?compte:\s*\n?\s*(.+?)(?:\n|$)"
)
_REF_SERV_RE = re.compile(r"compte:\s*//(.+?)(?:\n|$)")
_SUPPL_RE = re.compile(
    r"SupplementaryDetails:.*?complémentaires:\s*\n?\s*(.+?)(?:\n|$)"
)
_VALUE_DATE_RE = re.compile(
    r"ValueDate:.*?valeur:\s*(\d{6})"
)


def parse_f61_entries(text: str) -> List[Dict]:
    """
    Parse all F61 entries from the combined text of a MT950 PDF.
    
    Returns a list of dicts with keys:
      cd, amount_raw, amount, ref_owner, ref_serv, identification_code,
      supplementary_details, value_date
    """
    blocks = _F61_SPLIT_RE.split(text)
    blocks = [b for b in blocks if b.startswith("F61:")]

    entries: List[Dict] = []
    for idx, b in enumerate(blocks):
        cd_m = _CD_RE.search(b)
        amt_m = _AMT_RE.search(b)
        idc_m = _IDC_RE.search(b)
        ro_m = _REF_OWNER_RE.search(b)
        rs_m = _REF_SERV_RE.search(b)
        sup_m = _SUPPL_RE.search(b)
        vd_m = _VALUE_DATE_RE.search(b)

        amount_raw = amt_m.group(1) if amt_m else None

        entries.append({
            "f61_index": idx + 1,  # 1-based position in MT950
            "cd": cd_m.group(1) if cd_m else None,
            "amount_raw": amount_raw,
            "amount": normalize_amount(amount_raw),
            "ref_owner": ro_m.group(1).strip() if ro_m else None,
            "ref_serv": rs_m.group(1).strip() if rs_m else None,
            "identification_code": idc_m.group(1) if idc_m else None,
            "supplementary_details": sup_m.group(1).strip() if sup_m else None,
            "value_date": vd_m.group(1) if vd_m else None,
        })

    return entries


# ---------- Match helpers ----------

def _refs_match(f61_ref: str, msg_ref: str) -> bool:
    """
    Check if two references match.
    Uses prefix matching: one reference starts with the other.
    """
    if not f61_ref or not msg_ref:
        return False
    a = f61_ref.strip()
    b = msg_ref.strip()
    return a == b or b.startswith(a) or a.startswith(b)


def match_f61_with_messages(
    f61_entries: List[Dict],
    message_rows: List[Dict],
    sub_mode: str = "entrants",
) -> tuple[List[Dict], List[Dict], List[Dict]]:
    """
    Rapprocher les écritures F61 avec les messages extraits.

    Stratégie de matching :
    - Entrants (C) : F61.ref_serv == message.reference  (match exact)
    - Sortants (D) : F61.ref_owner est préfixe de message.reference (prefix match)
    - Dans tous les cas :
      * le montant normalisé doit correspondre
      * le code d'identification F61 doit correspondre au type MT du message
      * la date valeur F61 (YYMMDD) doit correspondre à la date référence du message
    
    Args:
        f61_entries: parsed F61 dicts
        message_rows: extracted message dicts (from extract_dispatch)
        sub_mode: "entrants" or "sortants"
        
    Returns:
        (rapproches, non_rapproches_messages, non_rapproches_f61)
        - rapproches: list of dicts with MT950 + message info side-by-side
        - non_rapproches_messages: messages without a matching F61
        - non_rapproches_f61: F61 entries without a matching message
    """
    # Filter F61 entries by C/D based on sub_mode
    target_cd = "C" if sub_mode == "entrants" else "D"
    filtered_f61 = [e for e in f61_entries if e.get("cd") == target_cd]

    used_msg_indices: set = set()
    rapproches: List[Dict] = []
    non_rapproches_f61: List[Dict] = []

    for f61 in filtered_f61:
        f61_amt = f61.get("amount")
        matched = False

        for idx, msg in enumerate(message_rows):
            if idx in used_msg_indices:
                continue

            msg_ref = msg.get("reference")
            msg_amt = msg.get("montant")

            # Normalise message amount if string
            if isinstance(msg_amt, str):
                msg_amt_f = normalize_amount(msg_amt)
            else:
                msg_amt_f = float(msg_amt) if msg_amt is not None else None

            # Amount must match
            if f61_amt is None or msg_amt_f is None:
                continue
            if abs(f61_amt - msg_amt_f) > 0.011:
                continue

            # Identification code must match message type
            f61_idc = f61.get("identification_code") or ""
            msg_type = msg.get("type_MT") or ""
            if f61_idc and f61_idc not in msg_type:
                continue

            # Date valeur must match date référence
            # F61 value_date is YYMMDD, message date_reference is YYYY-MM-DD
            f61_vd = f61.get("value_date") or ""
            msg_date = str(msg.get("date_reference") or "")
            if f61_vd and msg_date:
                # Normalize F61 YYMMDD → YYYY-MM-DD
                if len(f61_vd) == 6:
                    f61_date_norm = f"20{f61_vd[0:2]}-{f61_vd[2:4]}-{f61_vd[4:6]}"
                else:
                    f61_date_norm = f61_vd
                # Normalize msg date (may be YYYY-MM-DD or datetime)
                msg_date_norm = msg_date[:10]  # take first 10 chars
                if f61_date_norm != msg_date_norm:
                    continue

            # Reference matching depends on sub_mode
            if sub_mode == "entrants":
                # Use ref_serv (after //) — exact match with F20
                ref_ok = (f61.get("ref_serv") or "") == (msg_ref or "")
            else:
                # Use ref_owner — prefix match with F20
                ref_ok = _refs_match(f61.get("ref_owner"), msg_ref)

            if ref_ok:
                rapproches.append({
                    # F61 traceback
                    "f61_index": f61.get("f61_index"),
                    # MT950 side
                    "mt950_cd": f61.get("cd"),
                    "mt950_ref_owner": f61.get("ref_owner"),
                    "mt950_ref_serv": f61.get("ref_serv"),
                    "mt950_supplementary_details": f61.get("supplementary_details"),
                    "mt950_value_date": f61.get("value_date"),
                    # Message side
                    "msg_type_MT": msg.get("type_MT"),
                    "msg_reference": msg.get("reference"),
                    "msg_date_reference": msg.get("date_reference"),
                    "msg_montant": msg.get("montant"),
                    "msg_devise": msg.get("devise"),
                    "msg_donneur_dordre": msg.get("donneur_dordre") or msg.get("institution_name"),
                    "msg_code_donneur_dordre": msg.get("code_donneur_dordre"),
                    "msg_beneficiaire": msg.get("beneficiaire"),
                    "msg_pays_iso3": msg.get("pays_iso3"),
                    "msg_correspondant": msg.get("correspondant"),
                    "msg_commentaires": msg.get("commentaires"),
                    "msg_source_pdf": msg.get("source_pdf"),
                })
                used_msg_indices.add(idx)
                matched = True
                break

        if not matched:
            non_rapproches_f61.append(f61)

    # Messages not matched
    non_rapproches_messages = [
        msg for idx, msg in enumerate(message_rows)
        if idx not in used_msg_indices
    ]

    logger.info(
        "match_f61_with_messages (sub_mode=%s): %d rapprochés, %d F61 non rapprochés, %d messages non rapprochés",
        sub_mode, len(rapproches), len(non_rapproches_f61), len(non_rapproches_messages),
    )

    return rapproches, non_rapproches_messages, non_rapproches_f61


def extract_mt950_entries(pdf_path: Path) -> List[Dict]:
    """
    Main entry point: extract all F61 entries from a MT950 PDF.
    """
    text = _extract_text(Path(pdf_path))
    return parse_f61_entries(text)
