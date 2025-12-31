# backend/app/extractors/bic_utils.py
"""
Utilities to extract the IdentifierCode from F52A and to map the first-8-chars
to a human-readable bank name using an Excel table (data/bic_codes.xlsx).

Provides:
 - load_bic_mapping(xlsx_path: Optional[str]) -> Dict[str, str]
 - map_code_to_name(code: str, xlsx_path: Optional[str]) -> Optional[str]
 - get_name_for_code(code: str, xlsx_path: Optional[str]) -> Optional[str]  # alias for compatibility
 - get_donneur_from_f52(f52_text, message_text=None, xlsx_path=None) -> Optional[str]
"""
from pathlib import Path
import re
from functools import lru_cache
from typing import Optional, Dict

try:
    import pandas as pd
except Exception as e:
    raise RuntimeError("pandas is required by bic_utils.py (pip install pandas)") from e

# exact label to find (case-insensitive)
_LABEL_TEXT = "IdentifierCode: Code d'identifiant:"
_LABEL_RE = re.compile(re.escape(_LABEL_TEXT), re.I)

# strict code rule: only uppercase letters, length 8..11
_STRICT_TOKEN_RE = re.compile(r'^[A-Z]{8,11}$')

# fallback token pattern (alphanumeric 8..11) - used only if strict not found
_FALLBACK_TOKEN_RE = re.compile(r'\b([A-Z0-9]{8,11})\b', re.I)

# module-level cache
_BIC_MAP_CACHE: Optional[Dict[str, str]] = None
_BIC_FULLKEY_MAP: Optional[Dict[str, str]] = None
_BIC_COUNTRY_MAP: Optional[Dict[str, str]] = None  # Code BIC -> Pays (ISO3)


def _find_strict_identifier_in_f52(f52_text: str) -> Optional[str]:
    """
    Apply the strict rule:
      - find exact label (case-insensitive) inside f52_text
      - inspect: same line after label, next line, next-next line
      - return the first token matching ^[A-Z]{8,11}$
    """
    if not f52_text:
        return None

    lines = [ln.rstrip() for ln in f52_text.splitlines()]
    label_idx = None
    label_span_end = None
    for i, ln in enumerate(lines):
        m = _LABEL_RE.search(ln)
        if m:
            label_idx = i
            label_span_end = m.end()
            break

    if label_idx is None:
        return None

    candidates = []

    # same line after label
    same_line_after = lines[label_idx][label_span_end:].strip()
    if same_line_after:
        candidates.append(same_line_after)

    # next two lines (allow blank lines)
    for j in (label_idx + 1, label_idx + 2):
        if j < len(lines):
            candidates.append(lines[j].strip())

    # evaluate candidates strictly: only A-Z length 8..11
    for cand in candidates:
        if not cand:
            continue
        # split candidate into tokens of letters only (strip punctuation)
        toks = re.findall(r'[A-Z]+', cand.upper())
        for t in toks:
            if _STRICT_TOKEN_RE.match(t):
                return t
    return None


@lru_cache(maxsize=1)
def load_bic_mapping(xlsx_path: Optional[str] = None) -> Dict[str, str]:
    """
    Load the BIC mapping Excel file and return a dict mapping 8-char key -> bank name.
    Also populate a full-key map for 11-char exact matches and a country map.
    Default path: data/bic_codes.xlsx

    Expected columns: try to detect columns for code and name (flexible).
    """
    global _BIC_MAP_CACHE, _BIC_FULLKEY_MAP, _BIC_COUNTRY_MAP
    if _BIC_MAP_CACHE is not None and _BIC_FULLKEY_MAP is not None:
        return _BIC_MAP_CACHE

    fp = Path(xlsx_path) if xlsx_path else Path("data/bic_codes.xlsx")
    mapping: Dict[str, str] = {}
    mapping_full: Dict[str, str] = {}
    country_map: Dict[str, str] = {}

    if not fp.exists():
        _BIC_MAP_CACHE = {}
        _BIC_FULLKEY_MAP = {}
        _BIC_COUNTRY_MAP = {}
        return _BIC_MAP_CACHE

    df = pd.read_excel(fp, dtype=str)
    # normalize column names
    cols = [c for c in df.columns]

    # heuristics to find code and name columns
    code_col = None
    name_col = None
    country_col = None
    col_upper = [c.strip().upper() for c in cols]

    # common name candidates
    for i, cu in enumerate(col_upper):
        if cu in ("CODE", "BIC", "CODE BIC", "BIC_CODE", "BIC8", "CODE8", "CODE_BIC", "CODEBIC"):
            code_col = cols[i]
            break
    if not code_col:
        # fallback: pick first column whose values look like BICs/8..11 alnum
        for c in cols:
            sample = df[c].dropna().astype(str)
            if not sample.empty and sample.str.match(r'^[A-Z0-9]{6,12}$', case=False).sum() > 0:
                code_col = c
                break

    for i, cu in enumerate(col_upper):
        if cu in ("NOMS", "NOM", "NAME", "BANK", "INSTITUTION", "NOMINSTITUTION"):
            name_col = cols[i]
            break
    if not name_col:
        # fallback: choose the first non-code column
        for c in cols:
            if c != code_col:
                name_col = c
                break

    # look for country column
    for i, cu in enumerate(col_upper):
        if cu in ("PAYS", "COUNTRY", "COUNTRY_CODE", "ISO3", "ISO_COUNTRY"):
            country_col = cols[i]
            break

    if not code_col:
        _BIC_MAP_CACHE = {}
        _BIC_FULLKEY_MAP = {}
        _BIC_COUNTRY_MAP = {}
        return _BIC_MAP_CACHE

    # build mapping
    for _, row in df.iterrows():
        raw_code = str(row.get(code_col) or "").strip()
        if not raw_code:
            continue
        raw_code = raw_code.replace(" ", "").upper()
        raw_name = ""
        if name_col:
            raw_name = str(row.get(name_col) or "").strip()
        raw_country = ""
        if country_col:
            raw_country = str(row.get(country_col) or "").strip().upper()
        
        # map first 8 chars -> name
        key8 = raw_code[:8]
        if key8:
            if raw_name:
                mapping[key8] = raw_name
            else:
                # if no name column found, use the raw_code as fallback value (rare)
                mapping.setdefault(key8, raw_code)
            # also store country for this 8-char key
            if raw_country:
                country_map[key8] = raw_country
        
        # also keep full mapping for exact 11-char keys (useful for BEACCMCX100)
        if len(raw_code) >= 8:
            mapping_full[raw_code] = raw_name or mapping.get(raw_code[:8], "")

    _BIC_MAP_CACHE = mapping
    _BIC_FULLKEY_MAP = mapping_full
    _BIC_COUNTRY_MAP = country_map
    return _BIC_MAP_CACHE


def map_code_to_name(code: str, xlsx_path: Optional[str] = None) -> Optional[str]:
    """
    Map a raw code (8..11 chars) to bank name using loaded mapping.
    Rules:
      - If code exactly matches an 11-char full key present in the sheet (e.g. BEACCMCX100),
        then prefer that exact mapping.
      - Otherwise map using the first 8 characters.
    Returns the bank name or None.
    """
    if not code:
        return None
    _ = load_bic_mapping(xlsx_path=xlsx_path)  # populate caches
    global _BIC_MAP_CACHE, _BIC_FULLKEY_MAP
    code_u = code.strip().upper()
    # prefer exact full key if present
    if _BIC_FULLKEY_MAP and code_u in _BIC_FULLKEY_MAP and _BIC_FULLKEY_MAP[code_u]:
        return _BIC_FULLKEY_MAP[code_u]
    key8 = code_u[:8]
    if _BIC_MAP_CACHE and key8 in _BIC_MAP_CACHE:
        return _BIC_MAP_CACHE[key8]
    return None


# Backwards-compatibility alias expected by older code
def get_name_for_code(code: str, xlsx_path: Optional[str] = None) -> Optional[str]:
    """
    Compatibility wrapper used by some older extractors.
    Returns same as map_code_to_name.
    """
    return map_code_to_name(code, xlsx_path=xlsx_path)


def get_donneur_from_f52(f52_text: Optional[str], message_text: Optional[str] = None, xlsx_path: Optional[str] = None) -> Optional[str]:
    """
    Public helper used by extractors:
     - find strict IdentifierCode token inside f52_text (or within message_text if absent)
     - map to bank name (first 8 chars) and return "CODE/Bank Name"
     - if no mapping, return CODE (the extracted token)
     - return None if no token found

    NOTE: token is expected to be letters A-Z only (8..11) based on your rule.
    """
    code = None
    if f52_text:
        code = _find_strict_identifier_in_f52(f52_text)
    if not code and message_text:
        # try in the full message (cross-page)
        code = _find_strict_identifier_in_f52(message_text)
    # final fallback: search for an 8..11 alpha token inside f52 block
    if not code and f52_text:
        m = _FALLBACK_TOKEN_RE.search(f52_text)
        if m:
            cand = m.group(1).upper()
            # accept only all-letters candidate (user requested only letters as valid code)
            if re.fullmatch(r'[A-Z]{8,11}', cand):
                code = cand

    if not code:
        return None

    name = map_code_to_name(code, xlsx_path=xlsx_path)
    if name:
        return f"{code}/{name}"
    return code


def map_code_to_country(code: str, xlsx_path: Optional[str] = None) -> Optional[str]:
    """
    Map a raw code (8..11 chars) to country ISO3 code using loaded mapping.
    Uses the first 8 characters of the code to look up the country.
    Returns the country ISO3 code (e.g., "CMR") or None if not found.
    """
    if not code:
        return None
    _ = load_bic_mapping(xlsx_path=xlsx_path)  # populate caches
    global _BIC_COUNTRY_MAP
    code_u = code.strip().upper()
    key8 = code_u[:8]
    if _BIC_COUNTRY_MAP and key8 in _BIC_COUNTRY_MAP:
        return _BIC_COUNTRY_MAP[key8]
    return None
