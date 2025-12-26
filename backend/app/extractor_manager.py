# backend/app/extractor_manager.py
import os
import sys
from pathlib import Path
import re
import logging
from datetime import datetime
from typing import List, Dict, Optional

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from .utils import logger

# import extractors (primary helpers)
from .extractors.mt202 import extract_for_mt202, extract_text_from_pdf as extract_text_mt202
try:
    from .extractors.mt103 import extract_for_mt103
    HAS_MT103 = True
except Exception:
    HAS_MT103 = False
    logger.info("mt103 extractor not available at import time; you can add it to EXTRACTOR_MAP later.")

try:
    from .extractors.mt910 import extract_for_mt910
    HAS_MT910 = True
except Exception:
    HAS_MT910 = False
    logger.info("mt910 extractor not available at import time.")

# try to import the multi-message extractor (optional)
try:
    from .extractors import mt_multi as mt_multi_module
    HAS_MT_MULTI = True
except Exception:
    mt_multi_module = None
    HAS_MT_MULTI = False
    logger.info("mt_multi extractor not available at import time; multi-file detection will fall back to single extractors.")

# regex to detect MT/FIN code
MT_DETECT = re.compile(r'\b(?:MT|FIN)[\s\-\_\.:\/]*(\d{3})\b', re.I)
IDENTIFIER_FIN_RE = re.compile(r'Identifier[:\s]*fin[\.:\s\-\/]*(\d{3})', re.I)

# map MT number -> extractor callable
EXTRACTOR_MAP = {
    "202": extract_for_mt202,
}
if HAS_MT103:
    EXTRACTOR_MAP["103"] = extract_for_mt103
if HAS_MT910:
    EXTRACTOR_MAP["910"] = extract_for_mt910

# -----------------------------
# BIC mapping / donor logic
# -----------------------------
# caching globals
_cached_mapping: Optional[Dict[str, str]] = None
_cached_mapping_path: Optional[str] = None

# heuristics: possible default file locations
_DEFAULT_XLS_PATHS = [
    "data/bic_codes.xlsx",
    "data/bic.xlsx",
    "bic_codes.xlsx",
    "bic.xlsx",
    "data/bfde98b8-0a94-4ba1-ab8a-eae27357cc7e.xlsx"  # the uploaded name you used earlier (kept as candidate)
]

def _find_columns(df):
    """
    Find likely code_col and name_col heuristically from DataFrame columns.
    """
    cols = list(df.columns)
    code_col = None
    name_col = None
    for c in cols:
        cu = c.upper()
        if ('BIC' in cu) or (('CODE' in cu) and ('BIC' in cu or 'SWIFT' in cu)):
            code_col = c
            break
    if not code_col:
        for c in cols:
            cu = c.upper()
            if 'CODE' in cu:
                code_col = c
                break

    for c in cols:
        cu = c.upper()
        if 'NOM' in cu or 'NAME' in cu or 'NOMS' in cu:
            name_col = c
            break
    if not name_col:
        candidate = None
        best_alpha = 0.0
        for c in cols:
            sample = ' '.join([str(x) for x in df[c].dropna().astype(str).head(20).tolist()])
            if not sample:
                continue
            alpha_frac = sum(ch.isalpha() for ch in sample) / max(1, len(sample))
            if alpha_frac > best_alpha:
                best_alpha = alpha_frac
                candidate = c
        name_col = candidate
    return code_col, name_col


def bundled_base_path() -> Path:
    """
    Retourne le chemin racine d'où lire les fichiers "embarqués".
    - Si l'app est packagée par PyInstaller (--onefile), les resources sont extraites
      temporairement dans sys._MEIPASS.
    - Sinon, retourne la racine du projet (deux niveaux au-dessus de ce fichier).
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    # adjust parents count depending on file location; this file is backend/app/extractor_manager.py
    # parents[2] points to repo root (pdf-extractor/)
    return Path(__file__).resolve().parents[2]

def _user_override_bic_paths() -> List[Path]:
    """
    Emplacements où un admin/utilisateur peut déposer un bic_codes.xlsx modifiable.
    Ordre de priorité (testé dans load_bic_mapping) :
      1) variable d'environnement PDF_SWIFT_DATA_DIR si définie
      2) dossier commun ProgramData (Windows) -> {PROGRAMDATA}/PDF_Swift_Extractor/data
      3) dossier local de l'utilisateur -> %LOCALAPPDATA%/PDF_Swift_Extractor/data ou ~/ .pdf_swift_extractor/data
    """
    paths = []
    env = os.getenv("PDF_SWIFT_DATA_DIR")
    if env:
        paths.append(Path(env))

    # Windows common appdata (ProgramData)
    programdata = os.getenv("PROGRAMDATA")
    if programdata:
        paths.append(Path(programdata) / "PDF_Swift_Extractor" / "data")

    # Windows local appdata or cross-platform user dir
    localappdata = os.getenv("LOCALAPPDATA") or os.getenv("XDG_DATA_HOME")
    if localappdata:
        paths.append(Path(localappdata) / "PDF_Swift_Extractor" / "data")

    # fallback to user home hidden dir
    paths.append(Path.home() / ".pdf_swift_extractor" / "data")

    return paths

def load_bic_mapping(xlsx_path: Optional[str] = None, sheet_name: Optional[str] = 0) -> Dict[str, str]:
    """
    Charge (et met en cache) la table BIC -> nom de banque depuis un fichier Excel.
    Logique améliorée pour permettre une mise à jour manuelle après installation :
      - si xlsx_path explicit fourni -> utilisé (existing behaviour)
      - sinon : on cherche d'abord dans des emplacements externes éditables (ProgramData, user dir,
        variable d'env PDF_SWIFT_DATA_DIR)
      - sinon : on cherche dans le bundle embarqué (bundled_base_path()/data)
      - sinon : on retombe sur les chemins _DEFAULT_XLS_PATHS comme avant
    """
    global _cached_mapping, _cached_mapping_path
    import pandas as pd  # lazy import

    # 1) if explicit path provided, prefer it
    if xlsx_path:
        p = Path(xlsx_path)
        if not p.exists():
            raise FileNotFoundError(f"Provided xlsx_path not found: {xlsx_path}")
    else:
        # 2) check user-writable override locations (ProgramData, LOCALAPPDATA, env var)
        p = None
        for base in _user_override_bic_paths():
            candidate = base / "bic_codes.xlsx"
            if candidate.exists():
                p = candidate
                break
            # also accept alternative names
            alt = base / "bic.xlsx"
            if alt.exists():
                p = alt
                break

        # 3) check bundled data dir (this will work for --onedir and for files added with --add-data)
        if p is None:
            bundled = bundled_base_path() / "data"
            for name in ("bic_codes.xlsx", "bic.xlsx"):
                cand = bundled / name
                if cand.exists():
                    p = cand
                    break

        # 4) fallback to original candidate list (relative to current working dir)
        if p is None:
            for cand in _DEFAULT_XLS_PATHS:
                if Path(cand).exists():
                    p = Path(cand)
                    break

        if p is None:
            raise FileNotFoundError(
                "Aucun fichier Excel trouvé. Place your Excel mapping in one of: "
                + ", ".join(_DEFAULT_XLS_PATHS)
                + " or a writable location like %PROGRAMDATA%\\PDF_Swift_Extractor\\data\\bic_codes.xlsx "
                + "or set environment variable PDF_SWIFT_DATA_DIR to a folder containing bic_codes.xlsx"
            )

    # cache check
    pstr = str(Path(p).resolve())
    if _cached_mapping is not None and _cached_mapping_path == pstr:
        return _cached_mapping

    df = pd.read_excel(pstr, sheet_name=sheet_name, dtype=str)
    df = df.dropna(axis=1, how='all')

    code_col, name_col = _find_columns(df)
    if not code_col:
        raise ValueError(f"Impossible de détecter la colonne code BIC dans {pstr}. Colonnes: {list(df.columns)}")
    if not name_col:
        logger.warning("load_bic_mapping: impossible de détecter colonne 'nom' ; les valeurs de nom seront vides.")

    mapping: Dict[str, str] = {}
    for _, row in df.iterrows():
        code_val = (str(row.get(code_col) or "")).strip()
        name_val = (str(row.get(name_col) or "")).strip() if name_col else ""
        if not code_val or code_val.lower() in ("nan", "none"):
            continue
        code_clean = re.sub(r'\s+', '', code_val).upper()
        key = code_clean[:8]
        if not key:
            continue
        mapping[key] = name_val

    _cached_mapping = mapping
    _cached_mapping_path = pstr
    logger.info("load_bic_mapping: loaded %d entries from %s", len(mapping), pstr)
    return mapping


FALLBACK_11_RE = re.compile(r'\b([A-Z0-9]{11})\b')

# remplace la fonction get_donneur_from_f52 existante par ceci
IDENTIFIER_RE_AFTER_LABEL = re.compile(
    r"(?i)(?:IdentifierCode|Identifier Code|Identifiercode|Code d'identifiant|Code d identifiant|IDENTIFIERCODE)\s*[:\-\s]*\n?\s*([A-Z0-9]{11})"
)

# words that look like labels and should NOT be accepted as code
_BAD_LABEL_TOKENS = {
    "IDENTIFIER", "IDENTIFIERC", "PARTYIDENTI", "PARTYIDENT", "IDENTIFIANT", "IDENTIFIERCODE",
    "PARTY", "PARTYIDENTIFIER"
}


# label regex (variantes FR/EN)
_LABEL_RE = re.compile(
    r"(?i)(?:IdentifierCode|Identifier Code|Identifiercode|Code d'identifiant|Code d identifiant|identifiant de partie|IDENTIFIERCODE)\s*[:\-\s]*",
    re.M
)

_BAD_LABEL_PREFIXES = ("IDENTIF", "PARTYIDENT", "PARTY", "IDENTIFIANT")

def _find_identifier_after_label(text: str, lookahead_chars: int = 600) -> Optional[str]:
    """
    Cherche après un label 'IdentifierCode' un token alphanumérique 6..11 caractères,
    autorise lignes vides entre label et token, privilégie tokens contenant des lettres.
    """
    if not text:
        return None
    txt = text.replace('\r', '\n')
    m = _LABEL_RE.search(txt)
    if not m:
        return None
    start = m.end()
    tail = txt[start: start + lookahead_chars]
    # find candidates 6..11 chars
    toks = re.findall(r'\b([A-Z0-9]{6,11})\b', tail, flags=re.I)
    toks = [t.upper() for t in toks]
    # filter label-like tokens
    toks = [t for t in toks if not any(t.startswith(pref) for pref in _BAD_LABEL_PREFIXES)]
    if not toks:
        return None
    # prefer token with a letter (likely BIC), otherwise return first
    for t in toks:
        if re.search(r'[A-Z]', t):
            return t
    return toks[0]

def get_donneur_from_f52(f52_text: Optional[str], message_text: Optional[str] = None, xlsx_path: Optional[str] = None) -> Optional[str]:
    """
    Robust extract:
     - try inside F52A block: find label then next token (6..11 chars), preferring alpha tokens
     - if not found, try search on the whole message (cross-page)
     - if still not found, attempt a last-resort token in F52A that contains letters
     - then map first 8 chars to bank name using load_bic_mapping (if available)
    Returns: "CODE11/BANK NAME" if mapping found, else CODE (or None).
    """
    # normalize and remove xml-like tags
    def _norm(s):
        return re.sub(r'<[^>]+>', ' ', (s or "")).replace('\r', '\n')

    f52 = _norm(f52_text)
    full = _norm(message_text) if message_text else None

    # 1) try strictly in F52A block
    code = _find_identifier_after_label(f52, lookahead_chars=800)
    # 2) if not found, try whole message (cross-page)
    if not code and full:
        code = _find_identifier_after_label(full, lookahead_chars=1200)
    # 3) last-resort: try to find first alnum token with letters in F52A
    if not code and f52:
        m = re.search(r'\b([A-Z][A-Z0-9]{5,10})\b', f52, flags=re.I)
        if m:
            tok = m.group(1).upper()
            if not any(tok.startswith(pref) for pref in _BAD_LABEL_PREFIXES):
                code = tok

    if not code:
        return None

    # attempt mapping to bank name (uses cached loader)
    try:
        mapping = load_bic_mapping(xlsx_path=xlsx_path)
    except Exception:
        mapping = {}

    key8 = code[:8].upper()
    bank = mapping.get(key8)
    if bank:
        bank_clean = re.sub(r'\s{2,}', ' ', bank).strip()
        return f"{code}/{bank_clean}"
    return code


# -----------------------------
# Dispatcher / workbook logic (existing)
# -----------------------------
def detect_message_type(text: str) -> Optional[str]:
    """
    Detect the MT type (e.g. "202", "103", "910") from extracted text.
    Returns the numeric string (e.g. "202") or None.
    """
    if not text:
        return None

    m = MT_DETECT.search(text)
    if m:
        mt = m.group(1)
        logger.debug("detect_message_type: primary MT_DETECT matched -> %s", mt)
        return mt

    m2 = IDENTIFIER_FIN_RE.search(text)
    if m2:
        mt = m2.group(1)
        logger.debug("detect_message_type: IDENTIFIER_FIN_RE matched -> %s", mt)
        return mt

    logger.debug("detect_message_type: no MT type matched")
    return None


def extract_dispatch(pdf_path: Path) -> List[Dict]:
    """
    Dispatcher intelligent :
      - si le PDF contient plusieurs messages -> utilise mt_multi.extract_messages_from_pdf
      - sinon -> utilise extract_single (retourne [row])
    Retourne toujours une LISTE de rows.
    """
    p = Path(pdf_path)
    # quick text extraction using existing helper
    text = ""
    try:
        text = extract_text_mt202(p)
    except Exception as e:
        logger.debug("extract_dispatch: extract_text_mt202 failed (%s), falling back to pdfplumber", e)
        try:
            import pdfplumber
            s = ""
            with pdfplumber.open(str(p)) as pdf:
                for page in pdf.pages[:2]:
                    s += "\n" + (page.extract_text() or "")
            text = s
        except Exception as e2:
            logger.warning("extract_dispatch: quick pdfplumber fallback failed for %s: %s", p.name, e2)
            text = ""

    # If multi-message extractor available, use its split logic to decide
    if HAS_MT_MULTI and mt_multi_module:
        try:
            blocks = mt_multi_module._split_messages(text)
            if blocks and len(blocks) > 1:
                logger.info("%s: detected %d messages (using mt_multi).", p.name, len(blocks))
                rows = mt_multi_module.extract_messages_from_pdf(p)
                # ensure backward compatibility: set institution_name from donneur_dordre if missing
                for r in rows:
                    if "institution_name" not in r or not r.get("institution_name"):
                        r["institution_name"] = r.get("donneur_dordre") or r.get("donneur d'ordre") or None
                    for k in ["code_banque", "date_reference", "reference", "type_MT", "pays_iso3", "beneficiaire", "montant", "devise", "source_pdf"]:
                        if k not in r:
                            r[k] = None
                return rows
        except Exception as e:
            logger.exception("extract_dispatch: mt_multi detection/extraction failed for %s: %s", p.name, e)
            # fall through to single extractor

    # fallback: treat as single message
    single_row = extract_single(p)
    return [single_row]


def _ensure_minimal_row(p: Path, mt_type: Optional[str] = None) -> Dict:
    """Return a minimal row template used when extraction not performed or failed."""
    return {
        "code_banque": None,
        "date_reference": None,
        "reference": None,
        "type_MT": f"fin.{mt_type}" if mt_type else None,
        "pays_iso3": None,
        "institution_name": None,
        "beneficiaire": None,
        "montant": None,
        "devise": None,
        "source_pdf": p.name
    }


def extract_single(pdf_path: Path) -> Dict:
    """
    Dispatch extraction for a single pdf_path (Path or str).
    Returns a dict with fields (internal keys). The create_workbook function maps
    'institution_name' -> "donneur d'ordre" when writing the summary sheet.
    """
    p = Path(pdf_path)
    if not p.exists():
        logger.error("extract_single: file not found: %s", p)
        return _ensure_minimal_row(p)

    # read text (use helper from mt202 for consistent behavior)
    try:
        text = extract_text_mt202(p)
    except Exception as e:
        logger.exception("extract_single: extract_text_mt202 failed for %s: %s", p.name, e)
        # fallback quick text extraction
        try:
            import pdfplumber
            s = ""
            with pdfplumber.open(str(p)) as pdf:
                for page in pdf.pages[:2]:
                    s += "\n" + (page.extract_text() or "")
            text = s
        except Exception as e2:
            logger.exception("extract_single: fallback pdfplumber failed for %s: %s", p.name, e2)
            return _ensure_minimal_row(p)

    mt = detect_message_type(text)
    if not mt:
        logger.info("%s: MT type not found in text", p.name)
        row = _ensure_minimal_row(p, mt_type=None)
        row["source_pdf"] = p.name
        return row

    extractor = EXTRACTOR_MAP.get(mt)
    if not extractor:
        logger.info("%s: type detected -> %s but no extractor implemented", p.name, mt)
        row = _ensure_minimal_row(p, mt_type=mt)
        row["source_pdf"] = p.name
        return row

    try:
        row = extractor(p)
        if not isinstance(row, dict):
            logger.error("%s: extractor returned non-dict result: %r", p.name, row)
            row = _ensure_minimal_row(p, mt_type=mt)
        else:
            required = ["code_banque", "date_reference", "reference", "type_MT", "pays_iso3",
                        "institution_name", "beneficiaire", "montant", "devise", "source_pdf"]
            for k in required:
                if k not in row:
                    row[k] = None
            if not row.get("institution_name") and row.get("donneur_dordre"):
                row["institution_name"] = row.get("donneur_dordre")
            if not row.get("type_MT"):
                row["type_MT"] = f"fin.{mt}"
            if not row.get("source_pdf"):
                row["source_pdf"] = p.name
        logger.info("%s: extracted via MT%s", p.name, mt)
        return row
    except Exception as e:
        logger.exception("Extraction failed for %s (MT%s): %s", p.name, mt, e)
        row = _ensure_minimal_row(p, mt_type=mt)
        row["error"] = str(e)
        return row


def _sanitize_sheet_title(name: str, max_len: int = 31) -> str:
    """Make a safe Excel sheet name (no invalid chars, limited length)."""
    if not name:
        name = "sheet"
    sanitized = re.sub(r'[:\\\/\?\*\[\]]+', '_', name)
    sanitized = sanitized.strip()
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len]
    if not sanitized:
        sanitized = "sheet"
    return sanitized


def create_workbook(rows: List[Dict], out_dir: Path) -> Path:
    """
    Create an Excel workbook with:
      - a 'summary' sheet containing one row per extracted file (display headers in French)
      - one additional sheet per file with key/value pairs (debug-friendly)
    Returns the Path to the saved workbook.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"swift_extraction_{ts}.xlsx"

    wb = Workbook()
    summary = wb.active
    summary.title = "summary"

    # summary headers (user-facing)
    display_headers = [
        "code_banque",
        "date_reference",
        "reference",
        "type_MT",
        "pays_iso3",
        "donneur d'ordre",
        "Bénéficiaire",
        "montant",
        "devise",
        "source_pdf"
    ]
    summary.append(display_headers)

    # write summary rows (map internal keys -> display)
    for r in rows:
        # prefer institution_name, else new key donneur_dordre
        donneur = r.get("institution_name") or r.get("donneur_dordre") or r.get("donneur d'ordre") or None
        beneficiaire = r.get("beneficiaire") or None
        summary.append([
            r.get("code_banque"),
            r.get("date_reference"),
            r.get("reference"),
            r.get("type_MT"),
            r.get("pays_iso3"),
            donneur,
            beneficiaire,
            r.get("montant"),
            r.get("devise"),
            r.get("source_pdf")
        ])

    # create per-file sheets (key/value)
    used_names = set()
    for r in rows:
        base = r.get("source_pdf", "sheet")
        title = _sanitize_sheet_title(str(base))
        original = title
        i = 1
        while title in used_names or title in wb.sheetnames:
            suffix = f"_{i}"
            max_base_len = 31 - len(suffix)
            title = (original[:max_base_len] + suffix) if len(original) > max_base_len else (original + suffix)
            i += 1
        used_names.add(title)
        ws = wb.create_sheet(title=title)

        ordered_keys = [
            "code_banque", "date_reference", "reference", "type_MT", "pays_iso3",
            "institution_name", "beneficiaire", "montant", "devise", "source_pdf"
        ]
        written = set()
        for k in ordered_keys:
            if k in r:
                label = "donneur d'ordre" if k == "institution_name" else ("Bénéficiaire" if k == "beneficiaire" else k)
                ws.append([label, r.get(k)])
                written.add(k)
        for k, v in r.items():
            if k in written:
                continue
            label = "donneur d'ordre" if k == "institution_name" else ("Bénéficiaire" if k == "beneficiaire" else k)
            ws.append([label, v])

        # adjust column widths heuristically
        try:
            max_len_col1 = max((len(str(row[0])) for row in ws.values if row[0] is not None), default=10)
            max_len_col2 = max((len(str(row[1])) for row in ws.values if len(row) > 1 and row[1] is not None), default=10)
            ws.column_dimensions[get_column_letter(1)].width = min(60, max(12, max_len_col1 + 2))
            ws.column_dimensions[get_column_letter(2)].width = min(80, max(12, max_len_col2 + 8))
        except Exception:
            pass

    wb.save(out_path)
    logger.info("Workbook created: %s", out_path)
    return out_path
