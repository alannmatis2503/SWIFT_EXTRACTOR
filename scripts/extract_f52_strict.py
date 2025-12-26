# scripts/extract_f52_strict.py
# Usage: python scripts/extract_f52_strict.py path/to/all.pdf
import sys
import re
from pathlib import Path

# import helpers from your project
try:
    from backend.app.extractors import mt_multi as mtm
    from backend.app.extractors.mt202 import get_field_block, parse_reference
except Exception as e:
    # fallback: try to import with project root on PYTHONPATH
    raise

LABEL = "IdentifierCode: Code d'identifiant:"  # match this (case-insensitive)
LABEL_RE = re.compile(re.escape(LABEL), re.I)

# token rule: only uppercase letters A-Z, length 8..11
TOKEN_RE = re.compile(r'^[A-Z]{8,11}$')

def find_strict_identifier_in_f52(f52_text: str):
    """
    Strict rule:
      - find line that contains the label text (case-insensitive)
      - examine same line (content after the label), next line, and next-next line
      - return the first token that matches ^[A-Z]{8,11}$
    """
    if not f52_text:
        return None

    lines = [ln.rstrip() for ln in f52_text.splitlines()]
    # normalize lines by stripping trailing/leading whitespace only for checks
    norm_lines = [ln for ln in lines]  # keep original for printing

    # find the index of line that contains the label
    label_idx = None
    label_span_pos = None
    for i, ln in enumerate(lines):
        if LABEL_RE.search(ln):
            label_idx = i
            # record position within line to consider "same line after label"
            m = LABEL_RE.search(ln)
            label_span_pos = m.end()
            break

    # if label not found inside block, return None
    if label_idx is None:
        return None

    # candidates: same line remainder, next line, next+1 line (in that order)
    candidates = []

    # same line after label: extract substring after label position
    same_line = lines[label_idx]
    after = same_line[label_span_pos:].strip()
    if after:
        candidates.append(after)

    # next lines (allow empty lines: we will skip empties when checking but must allow one empty line)
    for j in (label_idx + 1, label_idx + 2):
        if j < len(lines):
            candidates.append(lines[j].strip())

    # Evaluate candidates: we accept only tokens fully alphabetic uppercase, 8..11 chars
    for cand in candidates:
        if not cand:
            continue
        # sometimes lines contain other words; split tokens by whitespace/punctuation
        toks = re.findall(r'[A-Z0-9]+', cand.upper())
        for t in toks:
            if TOKEN_RE.match(t):
                return t  # strict: letters only length 8..11

    # nothing found
    return None

def main(pdf_path: Path):
    if not pdf_path.exists():
        print("File not found:", pdf_path)
        return

    # extract full text & split messages using your mt_multi helper
    text = mtm._safe_text_extract(pdf_path)
    blocks = mtm._split_messages(text)
    print(f"Messages detected: {len(blocks)}\n")

    for i, blk in enumerate(blocks, start=1):
        # try to get reference (F20) for display
        try:
            ref = parse_reference(blk) or "(no-ref)"
        except Exception:
            ref = "(no-ref)"

        # get F52A using mt202.get_field_block (fallback to mt_multi internal if needed)
        f52 = None
        try:
            f52 = get_field_block(blk, 'F52A')
        except Exception:
            pass
        if not f52:
            try:
                f52 = mtm._extract_field_block('F52A', blk)
            except Exception:
                f52 = None

        # show first lines and attempt strict extraction
        code = find_strict_identifier_in_f52(f52 or "")

        print(f"Message #{i} | ref={ref} | strict_code={code}")
        print("----- F52A (first 8 lines) -----")
        if f52:
            for ln in f52.splitlines()[:8]:
                print("  ", repr(ln))
        else:
            print("   <no F52A block found>")
        print("\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/extract_f52_strict.py path/to/all.pdf")
        raise SystemExit(1)
    main(Path(sys.argv[1]))
