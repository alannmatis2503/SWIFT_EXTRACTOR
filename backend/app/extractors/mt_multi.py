"""
Dispatcher / découpeur de messages SWIFT.
- Lit un PDF (pdfplumber)
- Découpe en messages
- Détecte le type MT (202, 103, 910, 202.COV, ...)
- Appelle l'extracteur spécialisé (mt202, mt103, mt910)
- Pour 202.COV : utilise la même extraction que 202 pour tous les champs,
  mais met type_MT = "fin.202.COV" (d'après "Identifier: fin.202.COV" du header)
- Post-traitement: pour MT202/MT103 (et variantes), tente d'extraire le token strict
  depuis F52A et de formater "CODE/Bank Name" via bic_utils si disponible.
Returns list[dict] standardisés.
"""

from pathlib import Path
import re
from typing import List, Dict, Optional
import pdfplumber
import logging

logger = logging.getLogger(__name__)

# specialized extractors (block-level API: extract_block(block_text, source=...))
from backend.app.extractors import mt202, mt103, mt910

# optional bic mapping utilities (used only for 202/103 postprocessing)
try:
    from backend.app.extractors import bic_utils
    HAS_BIC_UTILS = True
except Exception:
    bic_utils = None
    HAS_BIC_UTILS = False

# ---------- patterns ----------
# try to capture "Identifier: fin.202.COV" (we will extract the tail e.g. "202" or "202.COV")
IDENTIFIER_FIN_FULL_RE = re.compile(r'(?i)Identifier\s*[:\s]*\s*fin\.(\d{3}(?:\.[A-Z0-9]+)?)')
# fallback simpler inline MT tokens
MT_INLINE_RE = re.compile(r'\b(?:FIN|MT)[\s\-\._:\/]*(\d{3})\b', re.I)

# Pre-compiled patterns for performance
_MESSAGE_N_RE = re.compile(r'(?m)^\s*Message\s+\d+\b')
_IDENTIFIER_RE = re.compile(r'(?mi)Identifier\s*[:\s]*fin\.\d{3}(?:\.[A-Z0-9]+)?')
_UMI_RE = re.compile(r'(?m)^(?:Unique Message Identifier|Message Identifier)\b', re.M)
_F20_TOKEN_RE = re.compile(r'(?mi)(:20:|\bF20[:\s])')
_SEPARATOR_RE = re.compile(r'(?m)^\s*(\*{3,}|-{3,})\s*$')
_UNDERSCORE_RE = re.compile(r'(?m)^\s*_{5,}\s*$')
_SENDER_RE = re.compile(r'(?m)^Sender\s*:')
_LABEL_SEARCH_RE = re.compile(r'(?i)(?:IdentifierCode|Identifier Code|Code d\'identifiant|Code d identifiant|Identifier code)\s*[:\-\s]*')
_TOKEN_SEARCH_RE = re.compile(r'\b([A-Z0-9]{8,11})\b', re.I)

# small helper to get F52A from a block (try to reuse mt202 helper if present)
try:
    from backend.app.extractors.mt202 import get_field_block
except Exception:
    def get_field_block(text: str, field_label: str) -> Optional[str]:
        # crude fallback: find occurrences of the label and return following lines until next F.. or blank
        pat = re.compile(r'(?si)(' + re.escape(field_label) + r'[:\s]*)(.*?)(?=\nF\d{2}[A-Z]?:|\nF\d{2}\b|$)')
        m = pat.search(text)
        return m.group(2).strip() if m else None


def _safe_text_extract(pdf_path: Path) -> str:
    """
    Extract text reliably from pdf using pdfplumber and normalize newlines.
    Keep some whitespace structure (double newlines) but remove excessive blank runs.
    """
    text = ""
    with pdfplumber.open(str(pdf_path)) as pdf:
        for p in pdf.pages:
            text += "\n" + (p.extract_text() or "")
    # normalize
    text = text.replace('\r', '\n')
    # remove "page X of Y" lines often injected
    text = re.sub(r'(?mi)^\s*page\s+\d+\s*(?:of\s*\d+)?\s*$', '', text, flags=re.M)
    # collapse long empty runs to two newlines (keep paragraph separation)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


def _split_messages(text: str) -> List[str]:
    """
    Robust splitting into messages. Try multiple heuristics because pdf text extraction
    can vary a lot between files.
    Returns list of message blocks (stripped).
    """
    if not text:
        return []

    txt = text.replace('\r', '\n')
    # keep original to use slices by index
    norm = txt

    # 1) 'Message N' headings (common in many dumps)
    msgs = list(_MESSAGE_N_RE.finditer(norm))
    if len(msgs) >= 2:
        positions = [m.start() for m in msgs] + [len(norm)]
        blocks = [norm[positions[i]:positions[i+1]].strip() for i in range(len(positions)-1)]
        # filter out empty
        return [b for b in blocks if b]

    # 2) 'Identifier: fin.XXX' header occurrences (covers fin.202.COV etc.)
    idents = list(_IDENTIFIER_RE.finditer(norm))
    if len(idents) >= 2:
        positions = [m.start() for m in idents] + [len(norm)]
        blocks = [norm[positions[i]:positions[i+1]].strip() for i in range(len(positions)-1)]
        return [b for b in blocks if b]

    # 2b) 'Sender:' header (particularité des messages sortants - outgoing messages)
    senders = list(_SENDER_RE.finditer(norm))
    if len(senders) >= 2:
        positions = [m.start() for m in senders] + [len(norm)]
        blocks = [norm[positions[i]:positions[i+1]].strip() for i in range(len(positions)-1)]
        return [b for b in blocks if b]

    # 3) 'Unique Message Identifier' / 'Message Identifier' headings
    umi = list(_UMI_RE.finditer(norm))
    if len(umi) >= 2:
        positions = [m.start() for m in umi] + [len(norm)]
        blocks = [norm[positions[i]:positions[i+1]].strip() for i in range(len(positions)-1)]
        return [b for b in blocks if b]

    # 4) split by :20: / F20 tokens (more tolerant: any occurrence of :20: or F20: or F20)
    tokens = list(_F20_TOKEN_RE.finditer(norm))
    if tokens:
        positions = [m.start() for m in tokens]
        if positions and positions[0] != 0:
            positions = [0] + positions
        positions.append(len(norm))
        blocks = [norm[positions[i]:positions[i+1]].strip() for i in range(len(positions)-1)]
        # sometimes splitting on :20: yields an initial tiny prefix; drop very small blocks
        blocks = [b for b in blocks if len(b) > 10]
        if len(blocks) >= 2:
            return blocks

    # 5) visual separators like lines with '***' or '---'
    sep_matches = list(_SEPARATOR_RE.finditer(norm))
    if sep_matches:
        positions = []
        # collect segment between separators
        prev = 0
        blocks = []
        for m in sep_matches:
            s = norm[prev:m.start()].strip()
            if s:
                blocks.append(s)
            prev = m.end()
        tail = norm[prev:].strip()
        if tail:
            blocks.append(tail)
        if len(blocks) >= 2:
            return blocks

    # 6) fallback: try splitting by large page-like separators (multiple underscores)
    page_like = _UNDERSCORE_RE.split(norm)
    if len(page_like) >= 2:
        blocks = [p.strip() for p in page_like if p.strip()]
        if len(blocks) >= 2:
            return blocks

    # final fallback: whole text as single block
    return [norm.strip()]


def _detect_mt_type(block_text: str) -> Optional[str]:
    """
    Detect specific MT type string:
      - prefer Identifier header form -> returns e.g. '202', '202.COV', '910'
      - else fallback to inline MT/FIN token -> returns digits like '202'
    """
    if not block_text:
        return None
    m = IDENTIFIER_FIN_FULL_RE.search(block_text)
    if m:
        return m.group(1)  # e.g. "202" or "202.COV"
    m2 = MT_INLINE_RE.search(block_text)
    if m2:
        return m2.group(1)
    return None


def _should_reject_mt103(row: Dict) -> bool:
    """
    RÈGLE 3: Pour MT103, rejeter si F53A, F54A ou F57A contient:
    - "BANQUE DE FRANCE"
    - "FW021083459"
    """
    if not row or not row.get("type_MT"):
        return False
    
    if not row.get("type_MT").startswith("fin.103"):
        return False
    
    forbidden_patterns = ["BANQUE DE FRANCE", "FW021083459"]
    fields_to_check = ["f53a_raw", "f54a_raw", "f57a_raw"]
    
    for field_name in fields_to_check:
        field_value = row.get(field_name)
        if field_value:
            field_upper = field_value.upper()
            for pattern in forbidden_patterns:
                if pattern.upper() in field_upper:
                    logger.debug("mt_multi: MT103 rejeté - Pattern '%s' trouvé dans %s", pattern, field_name)
                    return True  # Rejeter ce message
    
    return False  # Ne pas rejeter


def _fill_country_from_code(row: Dict, xlsx_path: Optional[str] = None) -> Dict:
    """
    If pays_iso3 is empty and code_donneur_dordre is present, try to fill pays_iso3
    by looking up the code in the BIC mapping.
    """
    if row.get("pays_iso3"):
        # Already has a country, don't override
        return row
    
    code = row.get("code_donneur_dordre")
    if not code or not HAS_BIC_UTILS:
        return row
    
    try:
        country = bic_utils.map_code_to_country(code, xlsx_path=xlsx_path)
        if country:
            row["pays_iso3"] = country
    except Exception as e:
        logger.debug("mt_multi: map_code_to_country failed for code %s: %s", code, e)
    
    return row


def _fill_country_from_code_force(row: Dict, xlsx_path: Optional[str] = None) -> Dict:
    """
    For MT910: FORCE fill pays_iso3 from BIC code, overriding any existing value.
    This is necessary because detect_country_from_text may pick up false positives
    from the document text. BIC mapping is authoritative.
    """
    code = row.get("code_donneur_dordre")
    if not code or not HAS_BIC_UTILS:
        return row
    
    try:
        country = bic_utils.map_code_to_country(code, xlsx_path=xlsx_path)
        if country:
            row["pays_iso3"] = country  # FORCE override, don't check existing value
    except Exception as e:
        logger.debug("mt_multi: _fill_country_from_code_force failed for code %s: %s", code, e)
    
    return row


def _extract_f58a_beneficiary(row: Dict, block_text: str, xlsx_path: Optional[str] = None) -> Dict:
    """
    For MT202 outgoing: extract F58A (Beneficiary Institution) to populate beneficiaire.
    Extract BIC code from F58A and match against bic_codes.xlsx to get name.
    If name not found, use code and track as unmapped.
    
    Returns:
        Updated row with beneficiaire field populated
    """
    try:
        f58_block = get_field_block(block_text, 'F58A')
    except Exception:
        f58_block = None

    code_name = None
    code_only = None

    if HAS_BIC_UTILS and f58_block:
        try:
            # Try to extract code from F58A using similar logic as F52A
            # Look for BIC pattern in F58A
            m_bic = re.search(r'\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b', f58_block)
            if m_bic:
                code_only = m_bic.group(1).upper()
                try:
                    name = bic_utils.map_code_to_name(code_only, xlsx_path=xlsx_path)
                    if name:
                        code_name = f"{code_only}/{name}"
                    else:
                        code_name = code_only
                except Exception as e:
                    logger.debug("mt_multi: map_code_to_name failed for F58A code %s: %s", code_only, e)
                    code_name = code_only
        except Exception as e:
            logger.debug("mt_multi: F58A extraction error: %s", e)
            code_name = None

    if not code_name and f58_block:
        # Fallback: search for any BIC-like pattern in F58A block
        m_bic = re.search(r'\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b', f58_block)
        if m_bic:
            code_only = m_bic.group(1).upper()
            code_name = code_only

    if code_name:
        # Set beneficiaire to name if available, otherwise code
        if '/' in code_name:
            code_only, name_only = code_name.split('/', 1)
            row["beneficiaire"] = name_only if name_only else code_only
        else:
            row["beneficiaire"] = code_name
    else:
        row["beneficiaire"] = None
    
    return row


# ---------- postprocessing for 202/103: F52A -> CODE/Name ----------
# Import helpers at module level for performance
try:
    from backend.app.extractors.mt202 import extract_name_from_f52d, extract_name_from_f50f
    HAS_NAME_EXTRACTORS = True
except Exception:
    HAS_NAME_EXTRACTORS = False

# Constants for invalid words check (set for O(1) lookup)
_INVALID_DONNEUR_WORDS = frozenset(['IDENTIFIANT', 'INSTITUTION', 'IDENTIFIER', 'CODE', 'NAMEANDADDRESS', 'PARTY'])

def _postprocess_row_for_202_103(row: Dict, block_text: str, xlsx_path: Optional[str] = None) -> Dict:
    """
    For MT202 / MT103 and variants (like 202.COV) : attempt to extract a strict Identifier
    token from F52A (or message text) using bic_utils.get_donneur_from_f52 (if available).
    Cas particulier messages sortants: essayer F52D si F52A absent, et si le résultat est un mot invalide, extraire le nom réel.
    If a CODE or CODE/Name is found, fill row['code_donneur_dordre'] (the code) and 
    row['donneur_dordre'] (the name only).
    """
    try:
        f52_block = get_field_block(block_text, 'F52A')
        f52d_block = None
        f50f_block = None
        # Cas particulier messages sortants: essayer F52D si F52A absent
        if not f52_block:
            f52_block = get_field_block(block_text, 'F52D')
            f52d_block = f52_block
        # Cas particulier MT103 sortants: essayer F50F
        if not f52_block:
            f52_block = get_field_block(block_text, 'F50F')
            f50f_block = f52_block
    except Exception:
        f52_block = None
        f52d_block = None
        f50f_block = None

    code_name = None
    code_only = None

    if HAS_BIC_UTILS:
        try:
            # bic_utils.get_donneur_from_f52 returns "CODE/Name" or CODE or None
            code_name = bic_utils.get_donneur_from_f52(f52_block, message_text=block_text, xlsx_path=xlsx_path)
            
            # Cas particulier: si code_name est un mot label invalide et on a F52D ou F50F, extraire le vrai nom
            if code_name and (f52d_block or f50f_block) and HAS_NAME_EXTRACTORS:
                code_name_upper = code_name.upper()
                # Use set membership for O(1) lookup
                if any(word in code_name_upper for word in _INVALID_DONNEUR_WORDS):
                    name_from_field = None
                    if f52d_block:
                        name_from_field = extract_name_from_f52d(f52d_block)
                    elif f50f_block:
                        name_from_field = extract_name_from_f50f(f50f_block)
                    
                    if name_from_field:
                        code_name = name_from_field
        except Exception as e:
            logger.debug("mt_multi: bic_utils.get_donneur_from_f52 error: %s", e)
            code_name = None

    if not code_name:
        # fallback naive search near label if bic_utils absent or returned None
        m_label = re.search(r'(?i)(?:IdentifierCode|Identifier Code|Code d\'identifiant|Code d identifiant|Identifier code)\s*[:\-\s]*', block_text)
        if m_label:
            tail = block_text[m_label.end(): m_label.end() + 800]
            m_tok = re.search(r'\b([A-Z0-9]{8,11})\b', tail, flags=re.I)
            if m_tok:
                code_only = m_tok.group(1).upper()
                if HAS_BIC_UTILS:
                    try:
                        name = bic_utils.map_code_to_name(code_only, xlsx_path=xlsx_path)
                    except Exception:
                        name = None
                    code_name = f"{code_only}/{name}" if name else code_only
                else:
                    code_name = code_only

    if code_name:
        # Extract code and name separately
        if '/' in code_name:
            code_only, name_only = code_name.split('/', 1)
        else:
            code_only = code_name
            name_only = None
        
        row["code_donneur_dordre"] = code_only
        row["donneur_dordre"] = name_only if name_only else code_only
        row["institution_name"] = name_only if name_only else code_only
        if not row.get("code_banque"):
            row["code_banque"] = code_only
    
    return row


def _extract_f52a_for_mt910(row: Dict, block_text: str, xlsx_path: Optional[str] = None) -> Dict:
    """
    For MT910: extract F52A (Beneficiary) to populate code_donneur_dordre and donneur_dordre.
    This replaces the original receiver-based extraction with a proper F52A extraction.
    Follows the same logic as MT202/103 F52A processing.
    
    For MT910, F52A is BOTH donneur_dordre AND beneficiaire (they are the same).
    Also retrieves country code from BIC mapping.
    """
    try:
        f52_block = get_field_block(block_text, 'F52A')
    except Exception:
        f52_block = None

    code_name = None
    code_only = None

    if HAS_BIC_UTILS:
        try:
            # bic_utils.get_donneur_from_f52 returns "CODE/Name" or CODE or None
            code_name = bic_utils.get_donneur_from_f52(f52_block, message_text=block_text, xlsx_path=xlsx_path)
        except Exception as e:
            logger.debug("mt_multi: bic_utils.get_donneur_from_f52 error in MT910: %s", e)
            code_name = None

    if not code_name:
        # fallback naive search near label if bic_utils absent or returned None
        m_label = _LABEL_SEARCH_RE.search(block_text)
        if m_label:
            tail = block_text[m_label.end(): m_label.end() + 800]
            m_tok = _TOKEN_SEARCH_RE.search(tail)
            if m_tok:
                code_only = m_tok.group(1).upper()
                if HAS_BIC_UTILS:
                    try:
                        name = bic_utils.map_code_to_name(code_only, xlsx_path=xlsx_path)
                    except Exception:
                        name = None
                    code_name = f"{code_only}/{name}" if name else code_only
                else:
                    code_name = code_only

    if code_name:
        # Extract code and name separately
        if '/' in code_name:
            code_only, name_only = code_name.split('/', 1)
        else:
            code_only = code_name
            name_only = None
        
        row["code_donneur_dordre"] = code_only
        row["donneur_dordre"] = name_only if name_only else code_only
        row["institution_name"] = name_only if name_only else code_only
        
        # For MT910: beneficiaire is the same as donneur_dordre (F52A is both donor and beneficiary)
        row["beneficiaire"] = row["donneur_dordre"]
        
        if not row.get("code_banque"):
            row["code_banque"] = code_only
        
        # Fill country from BIC code for MT910 - FORCE override detect_country_from_text results
        # BIC mapping is authoritative, not the heuristic text detection
        row = _fill_country_from_code_force(row, xlsx_path=xlsx_path)
    
    return row


def extract_messages_from_pdf(pdf_path: Path, bic_xlsx: Optional[str] = None, direction: str = "incoming") -> tuple[List[Dict], Dict[str, set]]:
    """
    Main entrypoint: read pdf_path, split into messages, dispatch to extractors.
    bic_xlsx: optional path forwarded to bic_utils when used in postprocessing.
    direction: "incoming" or "outgoing" - determines beneficiary extraction logic
    
    Returns:
        tuple: (list of extracted rows, dict with 'unmapped' and 'empty' code sets)
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    # if bic_utils available, try to preload mapping (best-effort)
    if HAS_BIC_UTILS:
        try:
            bic_utils.load_bic_mapping(bic_xlsx)
        except Exception as e:
            logger.debug("mt_multi: bic mapping preload failed: %s", e)

    text = _safe_text_extract(pdf_path)
    blocks = _split_messages(text)
    multi = len(blocks) > 1
    rows: List[Dict] = []
    missing_codes: Dict[str, set] = {
        "unmapped": set(),  # codes found but no name mapping
        "empty": set()      # no code found at all
    }
    
    # RÈGLE 1: Types valides à accepter
    VALID_BASE_TYPES = {'202', '103', '910'}

    for i, blk in enumerate(blocks, start=1):
        # Format: "voir message N°X du fichier filename.pdf" (multi) or just "filename.pdf" (single)
        if multi:
            source_label = f"voir message N°{i} du fichier {pdf_path.name}"
        else:
            source_label = pdf_path.name
        
        mt_type_token = _detect_mt_type(blk)  # e.g. '202', '202.COV', '910'
        row: Optional[Dict] = None
        
        # RÈGLE 1: Filtrer par type valide (202, 103, 910 et variantes)
        if mt_type_token:
            base_type = mt_type_token.split('.')[0]  # Extraire '202' de '202.COV'
            if base_type not in VALID_BASE_TYPES:
                logger.debug("mt_multi: Message %s rejeté (type invalide: %s)", source_label, mt_type_token)
                continue  # Passer au message suivant
        else:
            logger.debug("mt_multi: Message %s rejeté (type non détecté)", source_label)
            continue  # Passer au message suivant

        try:
            if mt_type_token and mt_type_token.startswith('202'):
                # includes '202' and variants like '202.COV'
                row = mt202.extract_block(blk, source=source_label)
                # postprocess like other 202/103
                row = _postprocess_row_for_202_103(row, blk, xlsx_path=bic_xlsx)

                # Beneficiary handling based on direction
                if direction == "incoming":
                    # FORCE beneficiary empty for 202 incoming (requirement)
                    try:
                        row["beneficiaire"] = None
                    except Exception:
                        row.update({"beneficiaire": None})
                elif direction == "outgoing":
                    # Extract F58A for outgoing MT202
                    row = _extract_f58a_beneficiary(row, blk, xlsx_path=bic_xlsx)

                # if variant .COV present, force type_MT accordingly
                if '.' in mt_type_token:
                    # example: mt_type_token == '202.COV' -> type_MT 'fin.202.COV'
                    row['type_MT'] = f"fin.{mt_type_token}"
                else:
                    row.setdefault('type_MT', 'fin.202')

            elif mt_type_token == '103':
                row = mt103.extract_block(blk, source=source_label)
                row = _postprocess_row_for_202_103(row, blk, xlsx_path=bic_xlsx)
                # Pour MT103 sortants: bénéficiaire vide (à implémenter plus tard)
                if direction == "outgoing":
                    row["beneficiaire"] = None
            elif mt_type_token == '910':
                # For 910 we do NOT use bic mapping in dispatcher; mt910 is responsible
                row = mt910.extract_block(blk, source=source_label)
            else:
                # unknown: try mt202 then mt103 then mt910 as fallbacks (keeps existing behavior)
                try:
                    row = mt202.extract_block(blk, source=source_label)
                    row = _postprocess_row_for_202_103(row, blk, xlsx_path=bic_xlsx)
                except Exception:
                    try:
                        row = mt103.extract_block(blk, source=source_label)
                        row = _postprocess_row_for_202_103(row, blk, xlsx_path=bic_xlsx)
                    except Exception:
                        try:
                            row = mt910.extract_block(blk, source=source_label)
                        except Exception:
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
                                "source_pdf": source_label
                            }
        except Exception as e:
            logger.exception("mt_multi: extractor failed for message %s (detected=%s): %s", source_label, mt_type_token, e)
            row = {
                "type_MT": f"fin.{mt_type_token}" if mt_type_token else None,
                "code_banque": None,
                "reference": None,
                "date_reference": None,
                "devise": None,
                "montant": None,
                "donneur_dordre": None,
                "beneficiaire": None,
                "pays_iso3": None,
                "source_pdf": source_label,
                "error": str(e)
            }

        # Safety check: ensure row is not None
        if row is None:
            logger.warning("mt_multi: row is None for message %s (type=%s), skipping", source_label, mt_type_token)
            continue

        # ensure expected keys present
        expected = ["type_MT","code_banque","sender_bic","receiver_bic","reference","date_reference",
                    "devise","montant","code_donneur_dordre","donneur_dordre","beneficiaire","pays_iso3","source_pdf"]
        for k in expected:
            if k not in row:
                row[k] = None
        if not row.get("source_pdf"):
            row["source_pdf"] = source_label

        # RÈGLE 2: Pour MT910, filtrer si F50A (Client donneur d'ordre) contient IdentifierCode == "BEACCMCX091"
        if row.get("type_MT") and row.get("type_MT").startswith("fin.910"):
            f50a_block = get_field_block(blk, 'F50A')
            if f50a_block:
                # Chercher le code d'identifiant dans F50A après la ligne "IdentifierCode: Code d'identifiant:"
                m = re.search(r'(?i)IdentifierCode.*?Code d[\'`]identifiant:?\s+([A-Z0-9]{8,11})', f50a_block, re.DOTALL)
                if m:
                    code = m.group(1).strip().upper()
                    if code == "BEACCMCX091":
                        logger.debug("mt_multi: Message %s rejeté (MT910 avec F50A=BEACCMCX091)", source_label)
                        continue  # Passer au message suivant (ne pas ajouter à rows)
            
            # MT910: Extract F52A for beneficiary/donneur_dordre (after RULE 2 check)
            row = _extract_f52a_for_mt910(row, blk, xlsx_path=bic_xlsx)
        
        # RÈGLE 3: Pour MT103, rejeter si F53A/F54A/F57A contient patterns interdits
        if _should_reject_mt103(row):
            logger.debug("mt_multi: Message %s rejeté (MT103 avec champs interdits)", source_label)
            continue  # Passer au message suivant (ne pas ajouter à rows)

        # Post-traitement: remplir pays_iso3 depuis code_donneur_dordre si absent
        row = _fill_country_from_code(row, xlsx_path=bic_xlsx)

        # Track missing codes for user feedback
        code = row.get("code_donneur_dordre")
        name = row.get("donneur_dordre")
        if not code:
            # Case 2: No code found at all
            missing_codes["empty"].add("(vide)")
        elif name == code:
            # Case 1: Code found but no name mapping (name == code means no mapping)
            missing_codes["unmapped"].add(code)
        
        # For outgoing MT202: also track beneficiary BIC if unmapped
        mt_type = row.get("type_MT")
        if direction == "outgoing" and mt_type and mt_type.startswith("fin.202"):
            beneficiary = row.get("beneficiaire")
            # Check if beneficiaire looks like a BIC code (all uppercase, no spaces, 8-11 chars)
            if beneficiary and len(beneficiary) >= 8 and len(beneficiary) <= 11 and beneficiary.isupper() and ' ' not in beneficiary:
                # This is likely an unmapped BIC code
                missing_codes["unmapped"].add(beneficiary)

        rows.append(row)

    return rows, missing_codes


# quick CLI for manual test
if __name__ == "__main__":
    import sys
    from pprint import pprint
    if len(sys.argv) < 2:
        print("Usage: python mt_multi.py path/to/all.pdf")
        raise SystemExit(1)
    path = Path(sys.argv[1])
    pprint(extract_messages_from_pdf(path))
