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
from datetime import datetime
import pdfplumber
import logging

logger = logging.getLogger(__name__)

# specialized extractors (block-level API: extract_block(block_text, source=...))
from extractors import mt202, mt103, mt910

# Import MT900 for transfer analysis
try:
    from extractors import mt900
    HAS_MT900 = True
except Exception:
    mt900 = None
    HAS_MT900 = False

# optional bic mapping utilities (used only for 202/103 postprocessing)
try:
    from extractors import bic_utils
    from extractors.bic_utils import map_reglement_code, extract_4digit_code_from_f52d, extract_tresor_code_from_f50f, extract_ccf_4digit_code_from_f50f, load_forex_codes
    HAS_BIC_UTILS = True
except Exception:
    bic_utils = None
    map_reglement_code = None
    extract_4digit_code_from_f52d = None
    extract_tresor_code_from_f50f = None
    extract_ccf_4digit_code_from_f50f = None
    load_forex_codes = None
    HAS_BIC_UTILS = False

# Import MT103 specific functions
try:
    from extractors.mt103 import extract_donneur_from_f50, extract_donneur_outgoing_mt103, extract_name_from_f50f_details
    HAS_MT103_DONNEUR = True
except Exception:
    extract_donneur_from_f50 = None
    extract_donneur_outgoing_mt103 = None
    extract_name_from_f50f_details = None
    HAS_MT103_DONNEUR = False

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

# BdF (Banque de France) BIC codes for special handling
BDF_BICS = frozenset(["BDFEFRPPCCT", "BDFEFRPPSRD"])

# small helper to get F52A from a block (try to reuse mt202 helper if present)
try:
    from extractors.mt202 import get_field_block
except Exception:
    def get_field_block(text: str, field_label: str) -> Optional[str]:
        # crude fallback: find occurrences of the label and return following lines until next F.. or blank
        pat = re.compile(r'(?si)(' + re.escape(field_label) + r'[:\s]*)(.*?)(?=\nF\d{2}[A-Z]?:|\nF\d{2}\b|$)')
        m = pat.search(text)
        return m.group(2).strip() if m else None


# Try to import PyMuPDF for faster PDF extraction (~50x faster than pdfplumber)
try:
    import fitz as pymupdf
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False


def _safe_text_extract(pdf_path: Path) -> str:
    """
    Extract text reliably from pdf using PyMuPDF (fast) or pdfplumber (fallback).
    Keep some whitespace structure (double newlines) but remove excessive blank runs.
    
    Performance: PyMuPDF is ~50x faster than pdfplumber for text extraction.
    """
    text = ""
    
    # Try PyMuPDF first (much faster)
    if HAS_PYMUPDF:
        try:
            doc = pymupdf.open(str(pdf_path))
            for page in doc:
                text += "\n" + page.get_text()
            doc.close()
        except Exception as e:
            logger.debug("PyMuPDF extraction failed, falling back to pdfplumber: %s", e)
            text = ""
    
    # Fallback to pdfplumber if PyMuPDF failed or not available
    if not text:
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


def _extract_f72_comment(block_text: str) -> Optional[str]:
    """
    Extraire le commentaire du champ F72 pour les 202 entrants.
    On prend tout ce qui vient après « texte descriptif » et qui soit situé
    sur la même ligne que « texte descriptif ». On sépare par des /.
    """
    f72_block = get_field_block(block_text, 'F72')
    if not f72_block:
        return None
    
    # Chercher toutes les occurrences de "Texte descriptif:" suivies de contenu sur la même ligne
    matches = re.findall(r'(?i)Texte descriptif\s*:\s*(.+?)\s*$', f72_block, re.MULTILINE)
    # Filtrer les valeurs vides ou whitespace-only
    values = [m.strip() for m in matches if m.strip()]
    if values:
        return ' / '.join(values)
    return None


def _extract_f70_comment(block_text: str) -> Optional[str]:
    """
    Extraire le commentaire du champ F70 pour les 103 entrants.
    Prend TOUT le contenu du champ F70.
    """
    f70_block = get_field_block(block_text, 'F70')
    if not f70_block:
        return None
    
    # Prendre tout le contenu du champ F70
    comment = f70_block.strip()
    # Nettoyer les espaces multiples et retours à la ligne
    comment = re.sub(r'\s+', ' ', comment).strip()
    return comment if comment else None


def _extract_f72_comment_mt910(block_text: str) -> Optional[str]:
    """
    Extraire le commentaire du champ F72 pour les 910 entrants.
    On prend ce qui vient après « Info émetteur – destinataire » dans le champ F72.
    Le contenu est lu jusqu'à « Block 5 » ou la fin du bloc, et les lignes sont jointes par /.
    """
    f72_block = get_field_block(block_text, 'F72')
    if not f72_block:
        return None
    
    # Chercher le contenu après 'Info émetteur - destinataire' (tiret normal ou tiret long)
    m = re.search(r'(?i)Info\s+[eé]metteur\s*[-–—]\s*destinataire\s*\n(.*?)(?:\nBlock\s+5|\Z)', f72_block, re.DOTALL)
    if m:
        content = m.group(1).strip()
        # Joindre les lignes non vides avec /
        lines = [l.strip() for l in content.split('\n') if l.strip()]
        if lines:
            return ' / '.join(lines)
    return None


def _extract_f21_reference_origine(block_text: str) -> Optional[str]:
    """
    Extraire la référence d'origine depuis le champ F21.
    Le format est: F21: Référence d'origine\n          VALUE
    """
    f21_block = get_field_block(block_text, 'F21')
    if not f21_block:
        return None
    
    lines = f21_block.strip().split('\n')
    for line in lines:
        line = line.strip()
        # Skip the label line (contains 'Référence' or 'origine')
        if line and not re.match(r'(?i)r[eé]f[eé]rence', line):
            return line
    return None


def _check_f52a_beaccmcx091_mt202_outgoing(block_text: str) -> bool:
    """
    Pour les MT202 sortants: vérifier si BEACCMCX091 est dans le champ F52A.
    Retourne True si trouvé (message à mettre en exception).
    """
    f52a_block = get_field_block(block_text, 'F52A')
    if not f52a_block:
        return False
    return 'BEACCMCX091' in f52a_block.upper()


def _check_f21_bc_mt202_outgoing(block_text: str) -> bool:
    """
    Pour les MT202 sortants: vérifier si « BC » est dans le champ F21 (référence d'origine).
    Retourne True si trouvé (message à mettre en exception avec commentaire 'opération salle des marchés').
    """
    ref_origine = _extract_f21_reference_origine(block_text)
    if not ref_origine:
        return False
    return 'BC' in ref_origine.upper()


def _extract_donneur_from_f50f_details(block_text: str) -> Optional[str]:
    """
    Extraire le nom du donneur d'ordre depuis F50F pour MT103 entrants.
    Cherche dans le sous-champ "NameAndAddress: Numéro/Nom et adresse",
    puis prend la ligne qui vient après "Number: Numéro: 1/Details: Détails:".
    
    Exemples:
    - "AUDIBLE LIMITED"
    - "PAIERIE GENERALE AUX ARMEES (PGA)"
    """
    f50f_block = get_field_block(block_text, 'F50F')
    if not f50f_block:
        return None
    
    # Chercher "Number: Numéro: 1/" puis "Details: Détails:" suivi du texte
    # La structure peut être sur plusieurs lignes
    # Pattern flexible pour capturer le texte après "Details: Détails:" jusqu'à la fin de ligne
    pattern = r'Number:\s*Numéro:\s*1/\s*Details:\s*Détails:\s*(.+?)(?:\s*Number:\s*Numéro:\s*\d+/|$)'
    m = re.search(pattern, f50f_block, re.IGNORECASE | re.DOTALL)
    if m:
        donneur = m.group(1).strip()
        # Prendre seulement la première ligne (avant le premier retour à la ligne)
        donneur = donneur.split('\n')[0].strip()
        # Nettoyer les espaces multiples
        donneur = re.sub(r'\s+', ' ', donneur).strip()
        return donneur if donneur else None
    
    return None


def _extract_sender_bic(block_text: str) -> Optional[str]:
    """
    Extraire le BIC du sender depuis l'en-tête du message.
    """
    # Chercher dans "Sender:" ou "Sender Institution:"
    m = re.search(r'(?i)Sender(?:\s+Institution)?\s*[:\s]*.*?([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)', block_text, re.DOTALL)
    if m:
        return m.group(1).upper()
    return None


def _extract_receiver_bic(block_text: str) -> Optional[str]:
    """
    Extraire le BIC du receiver depuis l'en-tête du message.
    """
    # Chercher dans "Receiver:" ou "Receiver Institution:"
    m = re.search(r'(?i)Receiver(?:\s+Institution)?\s*[:\s]*.*?([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)', block_text, re.DOTALL)
    if m:
        return m.group(1).upper()
    return None


def _check_f58a_323201(block_text: str) -> bool:
    """
    Vérifier si le champ F58A contient la séquence 323201.
    Retourne True si trouvé (message à mettre en exception).
    La séquence peut être dans le PartyIdentifier ou ailleurs dans F58A.
    """
    f58a_block = get_field_block(block_text, 'F58A')
    if not f58a_block:
        return False
    
    # Chercher la séquence 323201 (peut être dans un numéro de compte comme /203232018206001)
    if '323201' in f58a_block:
        return True
    return False


def _citius33_f58a_fallback(row: Dict, block_text: str, xlsx_path: Optional[str] = None) -> Dict:
    """
    Règle CITIUS33 : pour les MT910 et MT202 entrants dont le correspondant (sender_bic)
    commence par CITIUS33, si le code donneur d'ordre extrait de F52A n'est pas mappé
    dans bic_codes.xlsx, on tente un fallback sur F58A.

    Dans F58A, on cherche :
      1. Un code sous-participant à 4 chiffres → résolution via colonne Reglement
      2. Un code BIC standard → résolution via map_code_to_name
      3. Un code CCF → résolution via _CCF_MAP

    Si un mapping est trouvé, on remplace code_donneur_dordre / donneur_dordre.
    Pour MT910, on met aussi à jour le bénéficiaire.
    """
    if not HAS_BIC_UTILS:
        return row

    code_current = row.get("code_donneur_dordre")

    # Le fallback ne s'applique que si F52A a fourni un code NON mappé.
    # Si aucun code n'a été trouvé du tout, on ne tente pas F58A.
    if not code_current:
        return row

    # Vérifier que le code F52A actuel n'est PAS mappé dans bic_codes.xlsx
    # (si déjà mappé, pas besoin de fallback)
    name_from_mapping = bic_utils.map_code_to_name(code_current, xlsx_path=xlsx_path)
    if name_from_mapping:
        # Code déjà résolu → pas de fallback nécessaire
        return row

    # Récupérer le bloc F58A
    f58a_block = get_field_block(block_text, 'F58A')
    f58d_block = get_field_block(block_text, 'F58D')
    target_block = f58a_block or f58d_block

    if not target_block:
        return row

    code_name = None
    code_only = None

    # 1. Chercher un code sous-participant à 4 chiffres dans F58A/F58D
    if extract_4digit_code_from_f52d:
        code_4 = extract_4digit_code_from_f52d(target_block)
        if code_4:
            reglement_info = map_reglement_code(code_4, xlsx_path=xlsx_path)
            if reglement_info:
                bic_from_reglement = reglement_info.get('bic', code_4)
                code_name = f"{bic_from_reglement}/{reglement_info.get('name', '')}"
                if reglement_info.get('country') and not row.get('pays_iso3'):
                    row['pays_iso3'] = reglement_info['country']
                logger.debug("mt_multi: CITIUS33 fallback F58A → code sous-participant %s → %s", code_4, code_name)

    # 2. Chercher un code BIC dans F58A
    if not code_name:
        bic_result = bic_utils.get_donneur_from_f52(target_block, message_text=None, xlsx_path=xlsx_path)
        if bic_result:
            # Vérifier que le BIC est bien mappé
            if '/' in str(bic_result):
                code_name = bic_result
                logger.debug("mt_multi: CITIUS33 fallback F58A → BIC mappé %s", code_name)

    # 3. Chercher un code CCF dans F58A
    if not code_name and hasattr(bic_utils, 'extract_ccf_4digit_code_from_f50f'):
        ccf_result = bic_utils.extract_ccf_4digit_code_from_f50f(target_block, xlsx_path=xlsx_path)
        if ccf_result:
            bic_ccf = ccf_result.get('bic', '')
            name_ccf = ccf_result.get('name', '')
            if bic_ccf:
                code_name = f"{bic_ccf}/{name_ccf}"
            elif name_ccf:
                code_name = name_ccf
            if ccf_result.get('country') and not row.get('pays_iso3'):
                row['pays_iso3'] = ccf_result['country']
            logger.debug("mt_multi: CITIUS33 fallback F58A → CCF %s", code_name)

    if not code_name:
        return row

    # Mettre à jour le donneur d'ordre
    if '/' in str(code_name):
        code_only, name_only = str(code_name).split('/', 1)
    else:
        code_only = str(code_name)
        name_only = None

    row["code_donneur_dordre"] = code_only
    row["donneur_dordre"] = name_only if name_only else code_only
    row["institution_name"] = name_only if name_only else code_only

    if not row.get("code_banque"):
        row["code_banque"] = code_only

    # Pour MT910 : bénéficiaire = donneur d'ordre
    mt_type_val = row.get("type_MT", "")
    if mt_type_val and mt_type_val.startswith("fin.910"):
        row["beneficiaire"] = row["donneur_dordre"]

    # Remplir pays depuis le nouveau code BIC
    row = _fill_country_from_code_force(row, xlsx_path=xlsx_path)

    return row


def _bdf_f58a_fallback(row: Dict, block_text: str, xlsx_path: Optional[str] = None) -> Dict:
    """
    Règle BdF : pour les MT202 entrants dont le correspondant (sender_bic)
    est BDFEFRPPCCT ou BDFEFRPPSRD, si le code donneur d'ordre extrait de F52A
    n'est pas mappé dans bic_codes.xlsx, on tente un fallback sur F58A.

    Dans F58A, on cherche :
      1. Un code BIC strict → résolution via map_code_to_name (excluant faux BIC)
      2. Un code sous-participant à 4 chiffres → résolution via colonne Reglement

    Si un mapping est trouvé, on remplace code_donneur_dordre / donneur_dordre.
    """
    if not HAS_BIC_UTILS:
        return row

    code_current = row.get("code_donneur_dordre")

    # Le fallback ne s'applique que si F52A a fourni un code NON mappé.
    if not code_current:
        return row

    # Vérifier que le code F52A actuel n'est PAS mappé dans bic_codes.xlsx
    name_from_mapping = bic_utils.map_code_to_name(code_current, xlsx_path=xlsx_path)
    if name_from_mapping:
        return row  # Déjà résolu, pas de fallback

    # Récupérer le bloc F58A
    f58a_block = get_field_block(block_text, 'F58A')
    f58d_block = get_field_block(block_text, 'F58D')
    target_block = f58a_block or f58d_block

    if not target_block:
        return row

    code_name = None

    # 1. Chercher un code BIC strict dans F58A (excluant faux BIC)
    bic_result = bic_utils.get_donneur_from_f52(target_block, message_text=None, xlsx_path=xlsx_path)
    if bic_result:
        if '/' in str(bic_result):
            code_name = bic_result
            logger.debug("mt_multi: BdF fallback F58A → BIC mappé %s", code_name)

    # 2. Chercher un code sous-participant à 4 chiffres dans F58A
    if not code_name and extract_4digit_code_from_f52d:
        code_4 = extract_4digit_code_from_f52d(target_block)
        if code_4:
            reglement_info = map_reglement_code(code_4, xlsx_path=xlsx_path)
            if reglement_info:
                bic_from_reglement = reglement_info.get('bic', code_4)
                code_name = f"{bic_from_reglement}/{reglement_info.get('name', '')}"
                if reglement_info.get('country') and not row.get('pays_iso3'):
                    row['pays_iso3'] = reglement_info['country']
                logger.debug("mt_multi: BdF fallback F58A → code sous-participant %s → %s", code_4, code_name)

    if not code_name:
        return row

    # Mettre à jour le donneur d'ordre
    if '/' in str(code_name):
        code_only, name_only = str(code_name).split('/', 1)
    else:
        code_only = str(code_name)
        name_only = None

    row["code_donneur_dordre"] = code_only
    row["donneur_dordre"] = name_only if name_only else code_only
    row["institution_name"] = name_only if name_only else code_only

    if not row.get("code_banque"):
        row["code_banque"] = code_only

    # Remplir pays depuis le nouveau code BIC
    row = _fill_country_from_code_force(row, xlsx_path=xlsx_path)

    return row


def _check_mt202_outgoing_corr_exception(block_text: str, receiver_bic: str) -> Optional[str]:
    """
    Vérifier si un MT202 sortant doit être mis en exception correspondant.

    Règles :
    - CITIGB2LXXX : F57A contient BDFEFRPPCCT ou BDFEFRPPSRD
    - SCBLGB2LXXX : F57A contient CITIUS33XXX ET F58A contient 36357124

    Returns:
        Le commentaire d'exception ou None si pas d'exception
    """
    if not receiver_bic:
        return None

    recv = receiver_bic.upper()

    if recv == "CITIGB2LXXX":
        f57a = get_field_block(block_text, 'F57A')
        if f57a:
            f57a_upper = f57a.upper()
            for bdf_bic in BDF_BICS:
                if bdf_bic in f57a_upper:
                    return f"Banque de France ({bdf_bic}) dans F57A"

    elif recv == "SCBLGB2LXXX":
        f57a = get_field_block(block_text, 'F57A')
        f58a = get_field_block(block_text, 'F58A')
        if f57a and f58a:
            if "CITIUS33" in f57a.upper() and "36357124" in f58a:
                return "CITIUS33 dans F57A + 36357124 dans F58A"

    return None


def _check_eur_exception(row: Dict, direction: str) -> Optional[str]:
    """
    Vérifier si le message doit être mis en exception pour devise EUR.
    Règles:
    - T2PI dans la référence (entrant ou sortant) -> "intérêts"
    - T2RM dans la référence (entrant seulement) -> "remboursement"
    - T2PL dans la référence (sortant seulement) -> "placement"
    
    Returns:
        Le commentaire d'exception ou None si pas d'exception
    """
    devise = row.get("devise")
    reference = row.get("reference") or ""
    
    # Vérifier si devise est EUR
    if not devise or devise.upper() != "EUR":
        return None
    
    reference_upper = reference.upper()
    
    # T2PI -> intérêts (entrant ET sortant)
    if "T2PI" in reference_upper:
        return "intérêts"
    
    # T2RM -> remboursement (entrant seulement)
    if direction == "incoming" and "T2RM" in reference_upper:
        return "remboursement"
    
    # T2PL -> placement (sortant seulement)
    if direction == "outgoing" and "T2PL" in reference_upper:
        return "placement"
    
    return None


def _check_nivellement_exception(row: Dict, block_text: str, direction: str) -> Optional[str]:
    """
    Vérifier si un message (MT910 ou MT202 sortant) doit être mis en exception pour nivellement.
    Règles:
    - NIVLT dans la référence (F20) ou la référence d'origine (F21) -> nivellement (tous types)
    - MT910 Entrants: 5175 dans F25P (numéro de compte)
    - MT910 Sortants: 5175 dans F53B ou F58A (numéro de compte)
    - MT202 Sortants: NIVLT dans la référence ou la référence d'origine
    
    Returns:
        "nivellement" si exception, None sinon
    """
    mt_type = row.get("type_MT") or ""
    is_910 = mt_type.startswith("fin.910")
    is_202 = mt_type.startswith("fin.202")
    
    if not is_910 and not is_202:
        return None
    
    reference = (row.get("reference") or "").upper()
    reference_origine = (row.get("reference_origine") or "").upper()
    
    # NIVLT dans la référence ou la référence d'origine -> nivellement (tous types applicables)
    if "NIVLT" in reference or "NIVLT" in reference_origine:
        return "nivellement"
    
    # Règles 5175 spécifiques au MT910
    if is_910:
        if direction == "incoming":
            # Vérifier 5175 dans F25P
            f25p_block = get_field_block(block_text, 'F25P') or get_field_block(block_text, 'F25')
            if f25p_block and "5175" in f25p_block:
                return "nivellement"
        
        elif direction == "outgoing":
            # Vérifier 5175 dans F53B
            f53b_block = get_field_block(block_text, 'F53B') or get_field_block(block_text, 'F53')
            if f53b_block and "5175" in f53b_block:
                return "nivellement"
            
            # Vérifier 5175 dans F58A
            f58a_block = get_field_block(block_text, 'F58A') or get_field_block(block_text, 'F58')
            if f58a_block and "5175" in f58a_block:
                return "nivellement"
    
    return None


# IBAN salle des marchés (MT910 entrants)
_SALLE_DES_MARCHES_IBAN = "FR7630001000640000005169558"


def _check_salle_des_marches_exception(row: Dict, block_text: str, direction: str) -> Optional[str]:
    """
    Vérifier si un MT910 entrant doit être routé vers opérations_salle des marchés.
    Règle: si FR7630001000640000005169558 est détecté dans le champ F25P.
    
    Cette règle est MINORITAIRE sur toutes les autres règles d'exception.
    Elle ne s'applique que si aucune autre exception n'a été détectée.
    
    Returns:
        "opération salle des marchés" si détecté, None sinon
    """
    if direction != "incoming":
        return None
    
    mt_type = row.get("type_MT") or ""
    if "910" not in mt_type:
        return None
    
    f25p_block = get_field_block(block_text, 'F25P') or get_field_block(block_text, 'F25')
    if f25p_block and _SALLE_DES_MARCHES_IBAN in f25p_block:
        return "opération salle des marchés"
    
    return None


def _should_reject_mt103(row: Dict) -> bool:
    """
    RÈGLE 3: Pour MT103 en USD, rejeter si F53A, F54A ou F57A contient:
    - "BANQUE DE FRANCE"
    - "FW021083459"
    """
    if not row or not row.get("type_MT"):
        return False
    
    if not row.get("type_MT").startswith("fin.103"):
        return False
    
    # Vérifier que la devise est USD
    devise = row.get("devise")
    if not devise or devise.upper() != "USD":
        return False  # Ne rejeter que les messages en USD
    
    forbidden_patterns = ["BANQUE DE FRANCE", "FW021083459"]
    fields_to_check = ["f53a_raw", "f54a_raw", "f57a_raw"]
    
    for field_name in fields_to_check:
        field_value = row.get(field_name)
        if field_value:
            field_upper = field_value.upper()
            for pattern in forbidden_patterns:
                if pattern.upper() in field_upper:
                    logger.debug("mt_multi: MT103 USD rejeté - Pattern '%s' trouvé dans %s", pattern, field_name)
                    return True  # Rejeter ce message
    
    return False  # Ne pas rejeter


def _check_mt900_exception(row: Dict) -> Optional[str]:
    """
    RÈGLE: Pour MT900, mettre en exception si:
    - "T2PL" dans F20 (référence) -> exception "T2PL"
    - "NIVLT" ou "NIVELLEMENT" dans F21 (référence d'origine) -> exception "nivellement"
    
    Returns:
        Le commentaire d'exception ou None si pas d'exception
    """
    if not row or not row.get("type_MT"):
        return None
    
    if not row.get("type_MT").startswith("fin.900"):
        return None
    
    # Vérifier F20 (référence)
    reference = row.get("reference") or ""
    reference_upper = reference.upper()
    
    if "T2PL" in reference_upper:
        return "T2PL"
    
    # Vérifier F21 (référence d'origine)
    related_reference = row.get("related_reference") or ""
    related_ref_upper = related_reference.upper()
    
    if "NIVLT" in related_ref_upper or "NIVELLEMENT" in related_ref_upper:
        return "nivellement"
    
    return None


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
    For MT202 outgoing: extract F58A or F58D (Beneficiary Institution) to populate beneficiaire.
    
    Priority:
    1. F58A: Extract BIC code and match against bic_codes.xlsx to get name
    2. F58D (fallback): Extract BIC code, or if no BIC found, extract name from NameAndAddress
    
    Returns:
        Updated row with beneficiaire field populated
    """
    try:
        f58a_block = get_field_block(block_text, 'F58A')
    except Exception:
        f58a_block = None
    
    try:
        f58d_block = get_field_block(block_text, 'F58D')
    except Exception:
        f58d_block = None

    code_name = None
    code_only = None

    # --- Étape 1: Essayer F58A (prioritaire) ---
    if HAS_BIC_UTILS and f58a_block:
        try:
            # Try to extract code from F58A using similar logic as F52A
            # Look for BIC pattern in F58A
            m_bic = re.search(r'\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b', f58a_block)
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

    if not code_name and f58a_block:
        # Fallback: search for any BIC-like pattern in F58A block
        m_bic = re.search(r'\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b', f58a_block)
        if m_bic:
            code_only = m_bic.group(1).upper()
            code_name = code_only

    # --- Étape 2: Si rien trouvé dans F58A, essayer F58D ---
    if not code_name and f58d_block:
        logger.debug("mt_multi: F58A empty or no BIC found, trying F58D")
        
        # Séparer le bloc en deux parties: avant et après NameAndAddress
        # Le BIC doit être cherché AVANT NameAndAddress pour éviter les faux positifs
        parts = re.split(r'NameAndAddress:', f58d_block, maxsplit=1)
        before_nameaddr = parts[0] if parts else f58d_block
        after_nameaddr = parts[1] if len(parts) > 1 else ""
        
        # D'abord chercher un code BIC AVANT la section NameAndAddress
        m_bic = re.search(r'\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b', before_nameaddr)
        if m_bic:
            potential_bic = m_bic.group(1).upper()
            # Vérifier que ce n'est pas un faux positif
            if potential_bic not in _FALSE_BIC_WORDS:
                code_only = potential_bic
                if HAS_BIC_UTILS:
                    try:
                        name = bic_utils.map_code_to_name(code_only, xlsx_path=xlsx_path)
                        if name:
                            code_name = f"{code_only}/{name}"
                        else:
                            code_name = code_only
                    except Exception as e:
                        logger.debug("mt_multi: map_code_to_name failed for F58D code %s: %s", code_only, e)
                        code_name = code_only
                else:
                    code_name = code_only
        
        # Si pas de BIC avant NameAndAddress, chercher dans le NameAndAddress
        if not code_name and after_nameaddr:
            # Extraire le contenu après "Nom et adresse:"
            m_name = re.search(r'Nom\s+et\s+adresse:\s*\n?\s*(.+)', after_nameaddr, re.DOTALL)
            if m_name:
                nameaddr_content = m_name.group(1).strip()
                lines = nameaddr_content.split('\n')
                
                # Chercher d'abord un BIC dans les lignes du NameAndAddress
                found_bic_in_nameaddr = False
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    # Chercher un pattern BIC (8-12 caractères: 4 lettres + 2 lettres + 2-6 alphanum)
                    # Standard: 8 ou 11 chars, mais certains systèmes utilisent 12 (ex: CORITDNDXXXX)
                    m_bic_line = re.search(r'^([A-Z]{4}[A-Z]{2}[A-Z0-9]{2,6})$', line)
                    if m_bic_line:
                        potential_bic = m_bic_line.group(1).upper()
                        if potential_bic not in _FALSE_BIC_WORDS:
                            code_only = potential_bic
                            if HAS_BIC_UTILS:
                                try:
                                    name = bic_utils.map_code_to_name(code_only, xlsx_path=xlsx_path)
                                    if name:
                                        code_name = f"{code_only}/{name}"
                                    else:
                                        code_name = code_only
                                except Exception:
                                    code_name = code_only
                            else:
                                code_name = code_only
                            found_bic_in_nameaddr = True
                            break
                
                # Si pas de BIC trouvé, prendre le texte complet du NameAndAddress
                if not found_bic_in_nameaddr:
                    for line in lines:
                        line = line.strip()
                        # Ignorer les lignes qui ressemblent à des IBAN ou sont trop courtes
                        if line and len(line) > 3 and not re.match(r'^[A-Z]{2}\d{2}', line) and not re.match(r'^\d+$', line):
                            # Pour le nom, on peut prendre plusieurs lignes si c'est le nom d'une banque
                            # Mais on s'arrête au premier retour à la ligne significatif
                            code_name = line
                            logger.debug("mt_multi: F58D NameAndAddress extracted: %s", code_name)
                            break

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
    from extractors.mt202 import extract_name_from_f52d, extract_name_from_f50f
    HAS_NAME_EXTRACTORS = True
except Exception:
    HAS_NAME_EXTRACTORS = False

# Constants for invalid words check (set for O(1) lookup)
_INVALID_DONNEUR_WORDS = frozenset(['IDENTIFIANT', 'INSTITUTION', 'IDENTIFIER', 'CODE', 'NAMEANDADDRESS', 'PARTY'])

# Mots courants qui peuvent matcher le pattern BIC mais ne sont pas des BICs
_FALSE_BIC_WORDS = frozenset([
    'CAMEROON', 'CAMEROUN', 'GABON', 'CONGO', 'TCHAD', 'CENTRAFRICAINE', 
    'GUINEE', 'EQUATORIALE', 'DOUALA', 'YAOUNDE', 'MALABO', 'LIBREVILLE',
    'BRAZZAVILLE', 'BANGUI', 'NDJAMENA', 'INSTITUTION', 'IDENTIFIANT',
    'IDENTIFIER', 'BENEFICIAIRE', 'DONNEUR', 'ORDRE', 'PARTIE',
    'TRANSFER', 'PAYMENT', 'CREDIT', 'DEBIT', 'COMPTE', 'ACCOUNT',
    'FINANCES', 'MINISTERE', 'TRESOR', 'BANQUE', 'FRANCE', 'AFRIQUE'
])

def _postprocess_row_for_202_103(row: Dict, block_text: str, xlsx_path: Optional[str] = None) -> Dict:
    """
    For MT202 / MT103 and variants (like 202.COV) : attempt to extract a strict Identifier
    token from F52A (or message text) using bic_utils.get_donneur_from_f52 (if available).
    
    Pour MT202: Si F52A n'a pas de code BIC, on prend F52D et on check:
    - D'abord un code BIC
    - Sinon un code à 4 chiffres (matcher avec colonne Reglement)
    Si rien n'est trouvé, laisser les champs vides.
    
    If a CODE or CODE/Name is found, fill row['code_donneur_dordre'] (the code) and 
    row['donneur_dordre'] (the name only).
    """
    try:
        f52a_block = get_field_block(block_text, 'F52A')
        f52d_block = get_field_block(block_text, 'F52D')
        f50f_block = get_field_block(block_text, 'F50F')
    except Exception:
        f52a_block = None
        f52d_block = None
        f50f_block = None

    code_name = None
    code_only = None
    use_f52d = False

    # D'abord essayer F52A avec le code BIC
    if HAS_BIC_UTILS and f52a_block:
        try:
            # bic_utils.get_donneur_from_f52 returns "CODE/Name" or CODE or None
            code_name = bic_utils.get_donneur_from_f52(f52a_block, message_text=None, xlsx_path=xlsx_path)  # Ne pas chercher dans tout le message
            
            # Vérifier si c'est un mot label invalide
            if code_name:
                code_name_upper = code_name.upper()
                if any(word in code_name_upper for word in _INVALID_DONNEUR_WORDS):
                    code_name = None  # Invalide, continuer vers F52D
        except Exception as e:
            logger.debug("mt_multi: bic_utils.get_donneur_from_f52 error for F52A: %s", e)
            code_name = None

    # Si pas de résultat avec F52A, essayer F52D
    if not code_name and f52d_block:
        use_f52d = True
        
        # D'ABORD chercher un code 4 chiffres (après "PartyIdentifier: Identifiant de partie:")
        if HAS_BIC_UTILS and extract_4digit_code_from_f52d:
            code_4 = extract_4digit_code_from_f52d(f52d_block)
            if code_4:
                # Mapper avec la colonne Reglement
                reglement_info = map_reglement_code(code_4, xlsx_path=xlsx_path)
                if reglement_info:
                    # Utiliser le code BIC correspondant au lieu du code trésor 4 chiffres
                    bic_from_reglement = reglement_info.get('bic', code_4)
                    code_name = f"{bic_from_reglement}/{reglement_info.get('name', '')}"
                    # Stocker aussi le pays si trouvé
                    if reglement_info.get('country') and not row.get('pays_iso3'):
                        row['pays_iso3'] = reglement_info['country']
                else:
                    # Code 4 chiffres trouvé mais pas de mapping
                    code_name = code_4
        
        # Si pas de code 4 chiffres, chercher un code BIC dans F52D
        if not code_name:
            bic_match = re.search(r'\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b', f52d_block)
            if bic_match:
                bic_code = bic_match.group(1).upper()
                # Vérifier que ce n'est pas un faux positif (mot courant qui ressemble à un BIC)
                if bic_code not in _FALSE_BIC_WORDS:
                    if HAS_BIC_UTILS:
                        try:
                            name = bic_utils.map_code_to_name(bic_code, xlsx_path=xlsx_path)
                            code_name = f"{bic_code}/{name}" if name else bic_code
                        except Exception:
                            code_name = bic_code
                    else:
                        code_name = bic_code
        
        # Fallback: extraire le nom de F52D si disponible
        if not code_name and HAS_NAME_EXTRACTORS:
            name_from_f52d = extract_name_from_f52d(f52d_block)
            if name_from_f52d:
                code_name = name_from_f52d

    # Fallback vers F50F pour MT103
    if not code_name and f50f_block and HAS_NAME_EXTRACTORS:
        name_from_f50f = extract_name_from_f50f(f50f_block)
        if name_from_f50f:
            code_name = name_from_f50f

    if not code_name:
        # Si toujours rien, laisser les champs vides (ne pas chercher ailleurs)
        row["code_donneur_dordre"] = None
        row["donneur_dordre"] = None
        row["institution_name"] = None
        return row

    # Extract code and name separately from code_name
    if '/' in str(code_name):
        code_only, name_only = str(code_name).split('/', 1)
    else:
        code_only = str(code_name)
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


def _extract_donneur_mt910_incoming(row: Dict, block_text: str, xlsx_path: Optional[str] = None) -> Dict:
    """
    Pour MT910 entrants: extraction du donneur d'ordre avec les mêmes règles
    et le même ordre de priorité que les MT202 entrants.
    
    Priorités:
    1. F52A: Code BIC + mapping via bic_utils
    2. F52D: Code à 4 chiffres (Reglement) puis BIC
    3. F50F: Nom depuis details
    
    Pour MT910: beneficiaire = donneur_dordre
    """
    try:
        f52a_block = get_field_block(block_text, 'F52A')
        f52d_block = get_field_block(block_text, 'F52D')
    except Exception:
        f52a_block = None
        f52d_block = None
    
    code_name = None
    code_only = None
    
    # Priorité 1: F52A avec code BIC
    if HAS_BIC_UTILS and f52a_block:
        try:
            code_name = bic_utils.get_donneur_from_f52(f52a_block, message_text=None, xlsx_path=xlsx_path)
            if code_name:
                code_name_upper = code_name.upper()
                if any(word in code_name_upper for word in _INVALID_DONNEUR_WORDS):
                    code_name = None
        except Exception as e:
            logger.debug("mt_multi: bic_utils.get_donneur_from_f52 error for MT910 F52A: %s", e)
            code_name = None

    # Priorité 2: F52D - code 4 chiffres, puis BIC
    if not code_name and f52d_block:
        # D'abord code 4 chiffres
        if HAS_BIC_UTILS and extract_4digit_code_from_f52d:
            code_4 = extract_4digit_code_from_f52d(f52d_block)
            if code_4:
                reglement_info = map_reglement_code(code_4, xlsx_path=xlsx_path)
                if reglement_info:
                    # Utiliser le code BIC correspondant au lieu du code trésor 4 chiffres
                    bic_from_reglement = reglement_info.get('bic', code_4)
                    code_name = f"{bic_from_reglement}/{reglement_info.get('name', '')}"
                    if reglement_info.get('country') and not row.get('pays_iso3'):
                        row['pays_iso3'] = reglement_info['country']
                else:
                    code_name = code_4
        
        # Si pas de code 4 chiffres, chercher un BIC dans F52D
        if not code_name:
            bic_match = re.search(r'\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b', f52d_block)
            if bic_match:
                bic_code = bic_match.group(1).upper()
                if bic_code not in _FALSE_BIC_WORDS:
                    if HAS_BIC_UTILS:
                        try:
                            name = bic_utils.map_code_to_name(bic_code, xlsx_path=xlsx_path)
                            code_name = f"{bic_code}/{name}" if name else bic_code
                        except Exception:
                            code_name = bic_code
                    else:
                        code_name = bic_code
        
        # Fallback: nom depuis F52D
        if not code_name and HAS_NAME_EXTRACTORS:
            name_from_f52d = extract_name_from_f52d(f52d_block)
            if name_from_f52d:
                code_name = name_from_f52d

    if not code_name:
        row["code_donneur_dordre"] = None
        row["donneur_dordre"] = None
        row["institution_name"] = None
        return row

    # Séparer code et nom
    if '/' in str(code_name):
        code_only, name_only = str(code_name).split('/', 1)
    else:
        code_only = str(code_name)
        name_only = None
    
    row["code_donneur_dordre"] = code_only
    row["donneur_dordre"] = name_only if name_only else code_only
    row["institution_name"] = name_only if name_only else code_only
    
    # Pour MT910: beneficiaire = donneur_dordre
    row["beneficiaire"] = row["donneur_dordre"]
    
    if not row.get("code_banque"):
        row["code_banque"] = code_only
    
    # Remplir pays depuis BIC - FORCE override
    row = _fill_country_from_code_force(row, xlsx_path=xlsx_path)
    
    return row


def extract_messages_from_pdf(pdf_path: Path, bic_xlsx: Optional[str] = None, direction: str = "incoming", preloaded_text: Optional[str] = None) -> tuple[List[Dict], List[Dict], List[Dict], List[Dict], List[Dict], Dict[str, set]]:
    """
    Main entrypoint: read pdf_path, split into messages, dispatch to extractors.
    bic_xlsx: optional path forwarded to bic_utils when used in postprocessing.
    direction: "incoming" or "outgoing" - determines beneficiary extraction logic
    preloaded_text: optional pre-extracted text to avoid re-reading the PDF (optimization)
    
    Returns:
        tuple: (list of extracted rows, list of BEACCMCX091 rows, list of 323201 exception rows, 
                list of other exceptions (EUR/nivellement), list of BANQUE DE FRANCE rows,
                list of forex rows (MT910 entrants dont le donneur est dans la feuille forex),
                dict with 'unmapped' and 'empty' code sets)
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

    # Charger les codes forex pour classification MT910 entrants
    forex_codes = set()
    if HAS_BIC_UTILS and load_forex_codes:
        try:
            forex_codes = load_forex_codes(bic_xlsx)
        except Exception as e:
            logger.debug("mt_multi: forex codes load failed: %s", e)

    # Use preloaded text if available, otherwise extract from PDF
    text = preloaded_text if preloaded_text else _safe_text_extract(pdf_path)
    blocks = _split_messages(text)
    multi = len(blocks) > 1
    rows: List[Dict] = []
    beaccmcx091_rows: List[Dict] = []  # Messages BEACCMCX091 séparés
    exception_323201_rows: List[Dict] = []  # Messages 202 entrants avec 323201 dans F58A
    other_exceptions_rows: List[Dict] = []  # Autres exceptions (EUR T2PI/T2RM/T2PL, nivellement)
    banque_de_france_rows: List[Dict] = []  # MT103 USD avec BANQUE DE FRANCE / FW021083459
    forex_rows: List[Dict] = []  # MT910 entrants dont le code donneur est dans la feuille forex
    bdf_corr_exception_rows: List[Dict] = []  # MT202 sortants avec exceptions BdF correspondant
    missing_codes: Dict[str, set] = {
        "unmapped": set(),  # codes found but no name mapping
        "empty": set()      # no code found at all
    }
    
    # RÈGLE 1: Types valides à accepter
    # MT900 exclus des modes incoming/outgoing - inclus uniquement en mode transfer_analysis
    VALID_BASE_TYPES = {'202', '103', '910'}

    for i, blk in enumerate(blocks, start=1):
        # Format: "voir message N°X du fichier filename.pdf" (multi) or just "filename.pdf" (single)
        if multi:
            source_label = f"voir message N°{i} du fichier {pdf_path.name}"
        else:
            source_label = pdf_path.name
        
        # RÈGLE: Filtrer les messages NAK (sortants uniquement)
        # Si "NAK" apparaît dans l'en-tête du message, on l'ignore
        # NB: fenêtre réduite à 30 chars pour éviter le faux positif
        # du label "ACK/NAK Reception Date/Time" qui apparaît vers pos 550-600
        if direction == "outgoing":
            header_zone = blk[:30]  # En-tête = tout début du bloc
            if re.search(r'\bNAK\b', header_zone):
                logger.debug("mt_multi: Message %s rejeté (NAK détecté dans l'en-tête, sortant)", source_label)
                continue

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

                    # MT202 sortants CITI/SCB: donneur d'ordre = bénéficiaire
                    _recv_202 = _extract_receiver_bic(blk) or row.get("receiver_bic") or ""
                    if _recv_202.upper() in ("SCBLGB2LXXX", "CITIGB2LXXX"):
                        row["donneur_dordre"] = row.get("beneficiaire")
                        row["institution_name"] = row.get("beneficiaire")
                        # Extraire le code BIC du bénéficiaire depuis F58A pour code_donneur_dordre
                        _f58a = get_field_block(blk, 'F58A')
                        if _f58a:
                            _m_bic = re.search(r'\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b', _f58a)
                            if _m_bic and _m_bic.group(1).upper() not in _FALSE_BIC_WORDS:
                                row["code_donneur_dordre"] = _m_bic.group(1).upper()

                # if variant .COV present, force type_MT accordingly
                if '.' in mt_type_token:
                    # example: mt_type_token == '202.COV' -> type_MT 'fin.202.COV'
                    row['type_MT'] = f"fin.{mt_type_token}"
                else:
                    row.setdefault('type_MT', 'fin.202')

            elif mt_type_token == '103':
                row = mt103.extract_block(blk, source=source_label)
                
                # Traitement spécifique du donneur d'ordre pour MT103
                if HAS_MT103_DONNEUR:
                    if direction == "incoming":
                        # Déterminer si le sender est Banque de France
                        _sender_early = _extract_sender_bic(blk) or row.get("sender_bic")
                        _is_bdf_incoming = _sender_early and _sender_early.upper() in BDF_BICS

                        # PRIORITÉ 0 (BdF uniquement): F57A pour le donneur d'ordre
                        _f57a_resolved = False
                        if _is_bdf_incoming and HAS_BIC_UTILS:
                            f57a_block = get_field_block(blk, 'F57A')
                            if f57a_block:
                                f57a_code = bic_utils.get_donneur_from_f52(f57a_block, message_text=None, xlsx_path=bic_xlsx)
                                if f57a_code and '/' in str(f57a_code):
                                    _code, _name = str(f57a_code).split('/', 1)
                                    row["code_donneur_dordre"] = _code
                                    row["donneur_dordre"] = _name
                                    row["institution_name"] = _name
                                    if not row.get("code_banque"):
                                        row["code_banque"] = _code
                                    # Remplir le pays depuis le BIC F57A
                                    _country_f57 = bic_utils.map_code_to_country(_code, xlsx_path=bic_xlsx)
                                    if _country_f57:
                                        row["pays_iso3"] = _country_f57
                                    _f57a_resolved = True
                                    logger.debug("mt_multi: MT103 entrant BdF - donneur d'ordre depuis F57A: %s", f57a_code)

                        if not _f57a_resolved:
                            # Entrants: Priorités pour extraction du donneur d'ordre
                            # Priorité 1: Code BIC depuis F50K ou F50F
                            code_bic, nom_donneur_fallback = extract_donneur_from_f50(blk)
                            
                            if code_bic:
                                # Priorité 1: Code BIC trouvé
                                row["code_donneur_dordre"] = code_bic
                                if HAS_BIC_UTILS:
                                    name = bic_utils.map_code_to_name(code_bic, xlsx_path=bic_xlsx)
                                    row["donneur_dordre"] = name if name else nom_donneur_fallback
                                else:
                                    row["donneur_dordre"] = nom_donneur_fallback
                            else:
                                # Pas de code BIC, chercher alternatives
                                # Priorité 2: Extraction depuis F50F "Number: Numéro: 1/Details: Détails:"
                                donneur_f50f_details = _extract_donneur_from_f50f_details(blk)
                                
                                if donneur_f50f_details:
                                    # Priorité 2: Nom extrait depuis F50F details
                                    row["donneur_dordre"] = donneur_f50f_details
                                    row["code_donneur_dordre"] = None
                                elif nom_donneur_fallback:
                                    # Priorité 3: Fallback vers le nom extrait par extract_donneur_from_f50
                                    row["donneur_dordre"] = nom_donneur_fallback
                                    row["code_donneur_dordre"] = None
                                else:
                                    # Aucune donnée trouvée
                                    row["donneur_dordre"] = None
                                    row["code_donneur_dordre"] = None
                        
                        # MT103 entrants BdF: remplir PAYS depuis RECEIVER BIC
                        # si le correspondant (sender) est Banque de France et pays pas déjà rempli
                        if _is_bdf_incoming:
                            if not row.get("pays_iso3"):
                                _recv_bic = _extract_receiver_bic(blk) or row.get("receiver_bic")
                                if _recv_bic and HAS_BIC_UTILS:
                                    _country = bic_utils.map_code_to_country(_recv_bic, xlsx_path=bic_xlsx)
                                    if _country:
                                        row["pays_iso3"] = _country
                    else:
                        # Sortants: Nouvelle logique avec priorités
                        # Priorité 1: Codes Trésor (1001-6001) dans F50F
                        f50f_block = get_field_block(blk, 'F50F')
                        tresor_info = None
                        
                        if f50f_block and HAS_BIC_UTILS and extract_tresor_code_from_f50f:
                            tresor_info = extract_tresor_code_from_f50f(f50f_block, xlsx_path=bic_xlsx)
                        
                        if tresor_info:
                            # Code Trésor trouvé - utiliser le BIC correspondant
                            row["code_donneur_dordre"] = tresor_info.get("bic") or tresor_info["code"]
                            row["donneur_dordre"] = tresor_info["name"]
                            if tresor_info.get("country") and not row.get("pays_iso3"):
                                row["pays_iso3"] = tresor_info["country"]
                        else:
                            # Déterminer si le correspondant est BdF (pour l'ordre des priorités)
                            _recv = _extract_receiver_bic(blk) or row.get("receiver_bic")
                            _is_bdf = _recv and _recv.upper() in BDF_BICS

                            # Pré-extraire F52A et F50F Details
                            code_bic, nom_donneur = extract_donneur_outgoing_mt103(blk)
                            nom_from_details = None
                            if f50f_block and extract_name_from_f50f_details:
                                nom_from_details = extract_name_from_f50f_details(f50f_block)

                            if _is_bdf:
                                # BdF: P2=Details (nom F50F), P3=CCF, P4=F52A BIC
                                if nom_from_details:
                                    row["donneur_dordre"] = nom_from_details
                                    row["code_donneur_dordre"] = None
                                else:
                                    ccf_info = None
                                    if f50f_block and HAS_BIC_UTILS and extract_ccf_4digit_code_from_f50f:
                                        ccf_info = extract_ccf_4digit_code_from_f50f(f50f_block, xlsx_path=bic_xlsx)
                                    if ccf_info:
                                        row["code_donneur_dordre"] = ccf_info["code"]
                                        row["donneur_dordre"] = ccf_info["name"]
                                        if ccf_info.get("country") and not row.get("pays_iso3"):
                                            row["pays_iso3"] = ccf_info["country"]
                                    elif code_bic:
                                        row["code_donneur_dordre"] = code_bic
                                        if HAS_BIC_UTILS:
                                            name = bic_utils.map_code_to_name(code_bic, xlsx_path=bic_xlsx)
                                            row["donneur_dordre"] = name if name else nom_donneur
                                        else:
                                            row["donneur_dordre"] = nom_donneur
                                    elif nom_donneur:
                                        row["donneur_dordre"] = nom_donneur
                                        row["code_donneur_dordre"] = None
                            else:
                                # Non-BdF: P2=F52A BIC, P3=CCF, P4=Details (ordre original)
                                if code_bic:
                                    row["code_donneur_dordre"] = code_bic
                                    if HAS_BIC_UTILS:
                                        name = bic_utils.map_code_to_name(code_bic, xlsx_path=bic_xlsx)
                                        row["donneur_dordre"] = name if name else nom_donneur
                                    else:
                                        row["donneur_dordre"] = nom_donneur
                                else:
                                    ccf_info = None
                                    if f50f_block and HAS_BIC_UTILS and extract_ccf_4digit_code_from_f50f:
                                        ccf_info = extract_ccf_4digit_code_from_f50f(f50f_block, xlsx_path=bic_xlsx)
                                    if ccf_info:
                                        row["code_donneur_dordre"] = ccf_info["code"]
                                        row["donneur_dordre"] = ccf_info["name"]
                                        if ccf_info.get("country") and not row.get("pays_iso3"):
                                            row["pays_iso3"] = ccf_info["country"]
                                    elif nom_from_details:
                                        row["donneur_dordre"] = nom_from_details
                                        row["code_donneur_dordre"] = None
                                    elif nom_donneur:
                                        row["donneur_dordre"] = nom_donneur
                                        row["code_donneur_dordre"] = None
                else:
                    # Fallback: utiliser l'ancienne logique
                    row = _postprocess_row_for_202_103(row, blk, xlsx_path=bic_xlsx)
                
                # Pour MT103 sortants: bénéficiaire peut être laissé vide
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
                    "devise","montant","code_donneur_dordre","donneur_dordre","beneficiaire","pays_iso3","source_pdf","reference_origine"]
        for k in expected:
            if k not in row:
                row[k] = None
        if not row.get("source_pdf"):
            row["source_pdf"] = source_label

        # NOUVELLE RÈGLE: Extraire la référence d'origine (F21) pour MT202, MT910
        if mt_type_token and (mt_type_token.startswith('202') or mt_type_token == '910'):
            row["reference_origine"] = _extract_f21_reference_origine(blk)
        else:
            row["reference_origine"] = None

        # RÈGLE: Pour MT202 sortants, exception si BEACCMCX091 dans F52A
        is_beaccmcx091_mt202_outgoing = False
        if direction == "outgoing" and mt_type_token and mt_type_token.startswith('202'):
            if _check_f52a_beaccmcx091_mt202_outgoing(blk):
                logger.debug("mt_multi: MT202 sortant %s identifié avec BEACCMCX091 dans F52A (exception)", source_label)
                is_beaccmcx091_mt202_outgoing = True

        # RÈGLE: Pour MT202 sortants, exception si BC dans F21
        is_bc_exception = False
        if direction == "outgoing" and mt_type_token and mt_type_token.startswith('202'):
            if _check_f21_bc_mt202_outgoing(blk):
                logger.debug("mt_multi: MT202 sortant %s identifié avec BC dans F21 (exception salle des marchés)", source_label)
                row["commentaires"] = "opération salle des marchés"
                is_bc_exception = True

        # RÈGLE 2: Pour MT910, séparer les BEACCMCX091 (ne plus les rejeter, les stocker séparément)
        is_beaccmcx091 = False
        if row.get("type_MT") and row.get("type_MT").startswith("fin.910"):
            # Chercher d'abord dans F50A
            f50a_block = get_field_block(blk, 'F50A')
            if f50a_block:
                # Chercher le code d'identifiant dans F50A après la ligne "IdentifierCode: Code d'identifiant:"
                m = re.search(r'(?i)IdentifierCode.*?Code d[\'`]identifiant:?\s+([A-Z0-9]{8,11})', f50a_block, re.DOTALL)
                if m:
                    code = m.group(1).strip().upper()
                    if code == "BEACCMCX091":
                        logger.debug("mt_multi: Message %s identifié comme BEACCMCX091 dans F50A (stocké séparément)", source_label)
                        is_beaccmcx091 = True
            
            # Chercher également dans F52A
            if not is_beaccmcx091:
                f52a_block = get_field_block(blk, 'F52A')
                if f52a_block:
                    # Chercher le code d'identifiant dans F52A
                    m = re.search(r'(?i)IdentifierCode.*?Code d[\'`]identifiant:?\s+([A-Z0-9]{8,11})', f52a_block, re.DOTALL)
                    if m:
                        code = m.group(1).strip().upper()
                        if code == "BEACCMCX091":
                            logger.debug("mt_multi: Message %s identifié comme BEACCMCX091 dans F52A (stocké séparément)", source_label)
                            is_beaccmcx091 = True
            
            # MT910: Extract F52A for beneficiary/donneur_dordre (même pour BEACCMCX091)
            # Pour MT910 entrants: appliquer les mêmes règles que MT202 entrants pour le donneur d'ordre
            if direction == "incoming":
                row = _extract_donneur_mt910_incoming(row, blk, xlsx_path=bic_xlsx)
            else:
                row = _extract_f52a_for_mt910(row, blk, xlsx_path=bic_xlsx)
        
        # RÈGLE 3: Pour MT103 USD, déplacer vers feuille BANQUE DE FRANCE si F53A/F54A/F57A contient patterns interdits
        is_banque_de_france = _should_reject_mt103(row)

        # NOUVELLE RÈGLE: Bénéficiaire vide pour MT103 entrants
        mt_type = row.get("type_MT")
        if direction == "incoming" and mt_type and mt_type.startswith("fin.103"):
            row["beneficiaire"] = None

        # NOUVELLE RÈGLE: Extraire commentaires (F72 pour 202 et 910, F70 pour 103)
        if direction == "incoming":
            if mt_type and mt_type.startswith("fin.202"):
                row["commentaires"] = _extract_f72_comment(blk)
            elif mt_type and mt_type.startswith("fin.103"):
                row["commentaires"] = _extract_f70_comment(blk)
            elif mt_type and mt_type.startswith("fin.910"):
                row["commentaires"] = _extract_f72_comment_mt910(blk)
            else:
                row["commentaires"] = None
        else:
            # Pour les sortants: extraire les commentaires (même logique que les entrants)
            # mais ne pas écraser un commentaire déjà défini (ex: "opération salle des marchés")
            if not row.get("commentaires"):
                if mt_type and mt_type.startswith("fin.202"):
                    row["commentaires"] = _extract_f72_comment(blk)
                elif mt_type and mt_type.startswith("fin.103"):
                    row["commentaires"] = _extract_f70_comment(blk)
                elif mt_type and mt_type.startswith("fin.910"):
                    row["commentaires"] = _extract_f72_comment_mt910(blk)
                else:
                    row["commentaires"] = None

        # NOUVELLE RÈGLE: Extraire le correspondant (receiver pour sortants, sender pour entrants)
        sender_bic = _extract_sender_bic(blk) or row.get("sender_bic")
        receiver_bic = _extract_receiver_bic(blk) or row.get("receiver_bic")
        row["sender_bic"] = sender_bic
        row["receiver_bic"] = receiver_bic
        
        if direction == "incoming":
            row["correspondant"] = sender_bic
        else:
            row["correspondant"] = receiver_bic

        # RÈGLE: Annuler l'exception BEACCMCX091 pour MT202 sortants
        # si sender = BEACCMCX091 ET receiver = SCBLGB2LXXX ou CITIGB2LXXX
        # Dans ce cas, le message va dans les rows normales (=> feuille Operations_BEAC)
        if is_beaccmcx091_mt202_outgoing and sender_bic and receiver_bic:
            sender_up = sender_bic.upper()
            receiver_up = receiver_bic.upper()
            if sender_up.startswith("BEACCMCX") and receiver_up in ("SCBLGB2LXXX", "CITIGB2LXXX"):
                logger.debug("mt_multi: MT202 sortant %s - exception BEACCMCX091 annulée (sender=%s, receiver=%s)", source_label, sender_up, receiver_up)
                is_beaccmcx091_mt202_outgoing = False

        # RÈGLE: Annuler l'exception BC pour MT202 sortants
        # si receiver = SCBLGB2LXXX ou CITIGB2LXXX
        if is_bc_exception and receiver_bic:
            receiver_up = receiver_bic.upper()
            if receiver_up in ("SCBLGB2LXXX", "CITIGB2LXXX"):
                logger.debug("mt_multi: MT202 sortant %s - exception BC annulée (receiver=%s)", source_label, receiver_up)
                is_bc_exception = False
                row["commentaires"] = None  # Retirer le commentaire 'opération salle des marchés'

        # NOUVELLE RÈGLE: Exception pour 202 entrants avec 323201 dans F58A
        is_exception_323201 = False
        if direction == "incoming" and mt_type and mt_type.startswith("fin.202"):
            if _check_f58a_323201(blk):
                logger.debug("mt_multi: Message %s identifié avec 323201 dans F58A (exception)", source_label)
                is_exception_323201 = True

        # NOUVELLE RÈGLE: Exceptions EUR (T2PI, T2RM, T2PL)
        is_eur_exception = False
        eur_comment = _check_eur_exception(row, direction)
        if eur_comment:
            logger.debug("mt_multi: Message %s identifié comme exception EUR (%s)", source_label, eur_comment)
            row["commentaires"] = eur_comment
            is_eur_exception = True
        
        # RÈGLE: Exceptions nivellement pour MT910 et MT202 sortants
        is_nivellement_exception = False
        nivellement_comment = _check_nivellement_exception(row, blk, direction)
        if nivellement_comment:
            logger.debug("mt_multi: Message %s identifié comme exception nivellement", source_label)
            row["commentaires"] = nivellement_comment
            is_nivellement_exception = True

        # RÈGLE CITIUS33: Pour MT910/MT202 entrants depuis CITIUS33,
        # si F52A contient un BIC non mappé dans bic_codes.xlsx,
        # fallback sur F58A pour chercher un code sous-participant ou CCF
        if direction == "incoming" and sender_bic and sender_bic.upper().startswith("CITIUS33"):
            mt_type_val = row.get("type_MT", "")
            if mt_type_val and (mt_type_val.startswith("fin.910") or mt_type_val.startswith("fin.202")):
                row = _citius33_f58a_fallback(row, blk, xlsx_path=bic_xlsx)

        # RÈGLE BdF F58A: Pour MT202 entrants depuis Banque de France,
        # si F52A contient un BIC non mappé, fallback sur F58A
        if direction == "incoming" and sender_bic and sender_bic.upper() in BDF_BICS:
            mt_type_val = row.get("type_MT", "")
            if mt_type_val and mt_type_val.startswith("fin.202"):
                row = _bdf_f58a_fallback(row, blk, xlsx_path=bic_xlsx)

        # RÈGLE BdF-CORRESPONDANT: Pour MT202 sortants vers CITI/SCB,
        # vérifier si le message doit aller en exception correspondant
        is_bdf_corr_exception = False
        if direction == "outgoing" and mt_type and mt_type.startswith("fin.202"):
            _recv_exc = receiver_bic or row.get("receiver_bic") or ""
            bdf_exc_comment = _check_mt202_outgoing_corr_exception(blk, _recv_exc)
            if bdf_exc_comment:
                logger.debug("mt_multi: Message %s identifié comme exception correspondant BdF (%s)", source_label, bdf_exc_comment)
                row["commentaires"] = bdf_exc_comment
                is_bdf_corr_exception = True

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
        if direction == "outgoing" and mt_type and mt_type.startswith("fin.202"):
            beneficiary = row.get("beneficiaire")
            # Check if beneficiaire looks like a BIC code (all uppercase, no spaces, 8-11 chars)
            if beneficiary and len(beneficiary) >= 8 and len(beneficiary) <= 11 and beneficiary.isupper() and ' ' not in beneficiary:
                # This is likely an unmapped BIC code
                missing_codes["unmapped"].add(beneficiary)

        # Ajouter le message à la liste appropriée
        if is_banque_de_france:
            banque_de_france_rows.append(row)
        elif is_bdf_corr_exception:
            bdf_corr_exception_rows.append(row)
        elif is_beaccmcx091_mt202_outgoing:
            other_exceptions_rows.append(row)
        elif is_bc_exception:
            other_exceptions_rows.append(row)
        elif is_exception_323201:
            exception_323201_rows.append(row)
        elif is_eur_exception or is_nivellement_exception:
            other_exceptions_rows.append(row)
        elif is_beaccmcx091:
            beaccmcx091_rows.append(row)
        else:
            # Vérifier si MT910 entrant avec code donneur dans la feuille forex
            is_forex = False
            if direction == "incoming" and forex_codes:
                mt = row.get("type_MT") or ""
                if "910" in mt:
                    # Règle 1 : code_donneur_dordre (8 premiers chars) dans la liste forex
                    code_donneur = row.get("code_donneur_dordre") or ""
                    code_donneur_8 = code_donneur.strip().upper()[:8] if code_donneur else ""
                    if code_donneur_8 and code_donneur_8 in forex_codes:
                        is_forex = True
                        row["commentaires"] = (row.get("commentaires") or "") + " forex" if row.get("commentaires") else "forex"
                        logger.debug("mt_multi: Message %s identifié comme forex (code donneur %s)", source_label, code_donneur_8)

                    # Règle 2 (CITIUS33 + USD) : chercher un code forex dans F50A
                    if not is_forex:
                        corr = (row.get("correspondant") or "").upper()
                        devise = (row.get("devise") or "").upper()
                        if corr.startswith("CITIUS33") and devise == "USD":
                            f50a_block = get_field_block(blk, 'F50A')
                            if f50a_block:
                                f50a_upper = f50a_block.upper()
                                for fx_code in forex_codes:
                                    if fx_code in f50a_upper:
                                        is_forex = True
                                        row["commentaires"] = (row.get("commentaires") or "") + " forex" if row.get("commentaires") else "forex"
                                        logger.debug("mt_multi: Message %s identifié comme forex via F50A (code %s, correspondant CITIUS33, USD)", source_label, fx_code)
                                        break
            
            if is_forex:
                forex_rows.append(row)
            else:
                # Vérifier salle des marchés (règle MINORITAIRE - plus basse priorité)
                salle_des_marches_comment = _check_salle_des_marches_exception(row, blk, direction)
                if salle_des_marches_comment:
                    row["commentaires"] = salle_des_marches_comment
                    logger.debug("mt_multi: Message %s identifié comme salle des marchés", source_label)
                    other_exceptions_rows.append(row)
                else:
                    rows.append(row)

    return rows, beaccmcx091_rows, exception_323201_rows, other_exceptions_rows, banque_de_france_rows, forex_rows, bdf_corr_exception_rows, missing_codes


def extract_transfer_analysis(pdf_path: Path, bic_xlsx: Optional[str] = None) -> tuple[List[Dict], List[Dict], Dict[str, set]]:
    """
    Analyse des transferts sortants exécutés.
    
    1. Extrait les fin.900 (confirmations de débit) en priorité
    2. Extrait les 202 et 103
    3. Match les 900 avec les 202/103 via la référence d'origine (F21)
    4. Remplit les infos manquantes dans les 900 à partir des 202/103 matchés
    5. Les 202/103 sans correspondant 900 vont dans "suspens"
    
    Args:
        pdf_path: Path to PDF file
        bic_xlsx: Optional path to BIC codes Excel
        
    Returns:
        tuple: (matched_900_rows, suspens_rows, missing_codes)
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    
    # Preload BIC mapping if available
    if HAS_BIC_UTILS:
        try:
            bic_utils.load_bic_mapping(bic_xlsx)
        except Exception as e:
            logger.debug("mt_multi: bic mapping preload failed: %s", e)
    
    text = _safe_text_extract(pdf_path)
    blocks = _split_messages(text)
    multi = len(blocks) > 1
    
    # Collections pour stocker les différents types
    mt900_rows: List[Dict] = []
    mt202_103_rows: List[Dict] = []
    missing_codes: Dict[str, set] = {"unmapped": set(), "empty": set()}
    
    for i, blk in enumerate(blocks, start=1):
        if multi:
            source_label = f"voir message N°{i} du fichier {pdf_path.name}"
        else:
            source_label = pdf_path.name
        
        mt_type_token = _detect_mt_type(blk)
        
        if not mt_type_token:
            continue
        
        try:
            if mt_type_token == '900' and HAS_MT900:
                # Extraire MT900
                row = mt900.extract_block(blk, source=source_label)
                row["source_pdf"] = source_label
                # Pour MT900, le correspondant est le sender BIC
                sender_bic_900 = _extract_sender_bic(blk) or row.get("sender_bic")
                if sender_bic_900:
                    row["correspondant"] = sender_bic_900
                    row["sender_bic"] = sender_bic_900
                mt900_rows.append(row)
                
            elif mt_type_token.startswith('202'):
                # Extraire MT202
                row = mt202.extract_block(blk, source=source_label)
                row = _postprocess_row_for_202_103(row, blk, xlsx_path=bic_xlsx)
                if '.' in mt_type_token:
                    row['type_MT'] = f"fin.{mt_type_token}"
                else:
                    row.setdefault('type_MT', 'fin.202')
                row["source_pdf"] = source_label
                # Correspondant = receiver BIC pour les transferts sortants
                receiver_bic_202 = _extract_receiver_bic(blk) or row.get("receiver_bic")
                if receiver_bic_202:
                    row["correspondant"] = receiver_bic_202
                mt202_103_rows.append(row)
                
            elif mt_type_token == '103':
                # Extraire MT103
                row = mt103.extract_block(blk, source=source_label)
                row = _postprocess_row_for_202_103(row, blk, xlsx_path=bic_xlsx)
                row["source_pdf"] = source_label
                # Correspondant = receiver BIC pour les transferts sortants
                receiver_bic_103 = _extract_receiver_bic(blk) or row.get("receiver_bic")
                if receiver_bic_103:
                    row["correspondant"] = receiver_bic_103
                mt202_103_rows.append(row)
                
        except Exception as e:
            logger.exception("mt_multi: transfer analysis extractor failed for %s: %s", source_label, e)
            continue
    
    # Créer un index des 202/103 par référence pour le matching rapide
    ref_index: Dict[str, Dict] = {}
    for row in mt202_103_rows:
        ref = row.get("reference")
        if ref:
            ref_key = str(ref).strip().upper()
            ref_index[ref_key] = row
    
    # Matcher les 900 avec les 202/103
    matched_900_rows: List[Dict] = []
    exception_900_rows: List[Dict] = []
    matched_refs: set = set()
    
    for mt900_row in mt900_rows:
        # Vérifier d'abord si le MT900 doit être mis en exception
        exception_comment = _check_mt900_exception(mt900_row)
        if exception_comment:
            mt900_row["commentaires"] = exception_comment
            exception_900_rows.append(mt900_row)
            continue  # Ne pas matcher ce message, il va directement en exception
        
        # La référence d'origine F21 est utilisée pour matcher
        related_ref = mt900_row.get("related_reference")
        
        if related_ref:
            ref_key = str(related_ref).strip().upper()
            
            if ref_key in ref_index:
                # Match trouvé ! Compléter les infos du 900 avec celles du 202/103
                matched_202_103 = ref_index[ref_key]
                
                # Copier les infos manquantes du 202/103 vers le 900
                fields_to_copy = [
                    "code_donneur_dordre", "donneur_dordre", "beneficiaire", 
                    "pays_iso3"
                ]
                for field in fields_to_copy:
                    if not mt900_row.get(field) and matched_202_103.get(field):
                        mt900_row[field] = matched_202_103[field]
                
                # Forcer le correspondant du MT202/MT103 (receiver BIC) sur le MT900 matché
                if matched_202_103.get("correspondant"):
                    mt900_row["correspondant"] = matched_202_103["correspondant"]
                
                # Si montant/devise manquant dans 900, prendre du 202/103
                if not mt900_row.get("montant") and matched_202_103.get("montant"):
                    mt900_row["montant"] = matched_202_103["montant"]
                if not mt900_row.get("devise") and matched_202_103.get("devise"):
                    mt900_row["devise"] = matched_202_103["devise"]
                
                # Ajouter info de matching
                mt900_row["matched_with"] = matched_202_103.get("reference")
                mt900_row["matched_type"] = matched_202_103.get("type_MT")
                # Ajouter la source du message matché pour la traçabilité
                mt900_row["matched_source"] = matched_202_103.get("source_pdf")
                
                matched_refs.add(ref_key)
        
        matched_900_rows.append(mt900_row)
    
    # Les 202/103 non matchés vont dans suspens
    suspens_rows: List[Dict] = []
    for row in mt202_103_rows:
        ref = row.get("reference")
        if ref:
            ref_key = str(ref).strip().upper()
            if ref_key not in matched_refs:
                row["status"] = "suspens - pas de confirmation 900"
                suspens_rows.append(row)
        else:
            # Pas de référence = suspens
            row["status"] = "suspens - référence manquante"
            suspens_rows.append(row)
    
    # Track missing codes
    for row in matched_900_rows + suspens_rows + exception_900_rows:
        code = row.get("code_donneur_dordre")
        name = row.get("donneur_dordre")
        if not code:
            missing_codes["empty"].add("(vide)")
        elif name == code:
            missing_codes["unmapped"].add(code)
    
    logger.info("Transfer analysis: %d MT900 total, %d matched, %d suspens, %d exceptions", 
                len(mt900_rows), len(matched_900_rows), len(suspens_rows), len(exception_900_rows))
    
    return matched_900_rows, suspens_rows, exception_900_rows, missing_codes


def extract_mt900_only(pdf_path: Path, bic_xlsx: Optional[str] = None) -> tuple[List[Dict], Dict[str, set]]:
    """
    Extraire uniquement les MT900 d'un fichier PDF.
    
    Args:
        pdf_path: Path to PDF file
        bic_xlsx: Optional path to BIC codes Excel
        
    Returns:
        tuple: (mt900_rows, missing_codes)
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    
    # Preload BIC mapping if available
    if HAS_BIC_UTILS:
        try:
            bic_utils.load_bic_mapping(bic_xlsx)
        except Exception as e:
            logger.debug("mt_multi: bic mapping preload failed: %s", e)
    
    text = _safe_text_extract(pdf_path)
    blocks = _split_messages(text)
    multi = len(blocks) > 1
    
    mt900_rows: List[Dict] = []
    missing_codes: Dict[str, set] = {"unmapped": set(), "empty": set()}
    
    for i, blk in enumerate(blocks, start=1):
        if multi:
            source_label = f"voir message N°{i} du fichier {pdf_path.name}"
        else:
            source_label = pdf_path.name
        
        mt_type_token = _detect_mt_type(blk)
        
        if not mt_type_token:
            continue
        
        # Extraire uniquement les MT900
        if mt_type_token == '900' and HAS_MT900:
            try:
                row = mt900.extract_block(blk, source=source_label)
                row["source_pdf"] = source_label
                mt900_rows.append(row)
            except Exception as e:
                logger.exception("mt_multi: MT900 extraction failed for %s: %s", source_label, e)
                continue
    
    logger.info("extract_mt900_only: %d MT900 extraits de %s", len(mt900_rows), pdf_path.name)
    
    return mt900_rows, missing_codes


# quick CLI for manual test
if __name__ == "__main__":
    import sys
    from pprint import pprint
    if len(sys.argv) < 2:
        print("Usage: python mt_multi.py path/to/all.pdf")
        raise SystemExit(1)
    path = Path(sys.argv[1])
    pprint(extract_messages_from_pdf(path))
