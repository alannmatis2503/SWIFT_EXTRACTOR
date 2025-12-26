# scripts/debug_extract_f52_codes.py
# Usage: python scripts/debug_extract_f52_codes.py path/to/all.pdf
import sys
from pathlib import Path
import re
from backend.app.extractors import mt_multi as mtm
from backend.app.extractors.mt202 import get_field_block, parse_reference

# Robust extractor: find 8-11 alnum token after "IdentifierCode" label, allowing newline
IDENTIFIER_AFTER_LABEL_RE = re.compile(
    r"(?i)(?:IdentifierCode|Identifier Code|Identifiercode|Code d'identifiant|Code d identifiant|IDENTIFIERCODE)\s*[:\-\s]*\n?\s*([A-Z0-9]{8,11})",
    re.M
)

# labels that are false-positives (11 letters) we want to avoid
_BAD_LABEL_PREFIXES = ("IDENTIF", "PARTYIDENT", "PARTYIDENTI")

def extract_raw_identifier_from_block(f52_text: str, message_text: str = None):
    """
    Retourne le token brut (8..11 alnum) associé à IdentifierCode si trouvé.
    Recherche dans f52_text d'abord, puis dans message_text (cross-page).
    Filtre tokens qui ressemblent à des étiquettes (IDENTIFIERC, PARTYIDENTI...).
    """
    if not f52_text and not message_text:
        return None

    txt_f52 = (f52_text or "").replace('\r', '\n')
    # 1) recherche stricte dans F52A (label + token possibly next line)
    m = IDENTIFIER_AFTER_LABEL_RE.search(txt_f52)
    if m:
        tok = m.group(1).upper()
        # reject obvious label-like tokens
        if not any(tok.startswith(pref) for pref in _BAD_LABEL_PREFIXES):
            return tok

    # 2) recherche dans le message complet (permet cross-page)
    if message_text:
        full = message_text.replace('\r', '\n')
        m2 = IDENTIFIER_AFTER_LABEL_RE.search(full)
        if m2:
            tok = m2.group(1).upper()
            if not any(tok.startswith(pref) for pref in _BAD_LABEL_PREFIXES):
                return tok
        # sometimes label occurs alone and token is on next non-empty line
        m_label = re.search(r"(?i)(?:IdentifierCode|Code d'identifiant|Identifiercode)\s*[:\-\s]*\n?", full)
        if m_label:
            tail = full[m_label.end(): m_label.end() + 500]  # lookahead window
            m3 = re.search(r"\b([A-Z0-9]{8,11})\b", tail)
            if m3:
                tok = m3.group(1).upper()
                if not any(tok.startswith(pref) for pref in _BAD_LABEL_PREFIXES):
                    return tok

    # 3) final fallback: any 8-11 token inside F52A (but avoid label-like)
    if txt_f52:
        m4 = re.search(r"\b([A-Z0-9]{8,11})\b", txt_f52)
        if m4:
            tok = m4.group(1).upper()
            if not any(tok.startswith(pref) for pref in _BAD_LABEL_PREFIXES):
                return tok

    return None

def main(pdf_path: Path):
    if not pdf_path.exists():
        print("File not found:", pdf_path)
        return

    # read whole text and split messages using existing logic
    text = mtm._safe_text_extract(pdf_path)
    blocks = mtm._split_messages(text)
    print(f"Messages detected: {len(blocks)}\n")

    for i, blk in enumerate(blocks, start=1):
        # reference (best-effort)
        try:
            ref = parse_reference(blk) or "(no-ref)"
        except Exception:
            ref = "(no-ref)"
        # extract raw F52A block using helper (reuse mt202.get_field_block)
        f52 = get_field_block(blk, 'F52A') or get_field_block(blk, 'F52A:')
        # also attempt generic mt_multi fallback if present
        if not f52:
            try:
                f52 = mtm._extract_field_block('F52A', blk)
            except Exception:
                f52 = None

        raw_code = extract_raw_identifier_from_block(f52, message_text=blk)
        print(f"Message #{i} | ref={ref} | raw_identifier={raw_code}")
        print("----- F52A (first 5 lines) -----")
        if f52:
            for ln in (f52.splitlines()[:5]):
                print("  ", ln)
        else:
            print("   <no F52A found>")
        print("\n")
    print("Done.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/debug_extract_f52_codes.py path/to/all.pdf")
        raise SystemExit(1)
    pdf = Path(sys.argv[1])
    main(pdf)
