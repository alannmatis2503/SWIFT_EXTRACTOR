#!/usr/bin/env python3
"""Test script for CITIUS33 F58A fallback rule."""
import sys, os

hf_spaces_dir = os.path.join(os.path.dirname(__file__), '..', 'hf_spaces')
sys.path.insert(0, hf_spaces_dir)

from extractors.mt_multi import _extract_sender_bic, _detect_mt_type, _safe_text_extract, _split_messages
from extractors.mt202 import get_field_block
from extractors.bic_utils import get_donneur_from_f52, map_code_to_name
from pathlib import Path

pdf_path = Path(os.path.join(os.path.dirname(__file__), '..', 'data', 'raw', 'Test', 'Entrants.pdf'))
bic_xlsx = os.path.join(hf_spaces_dir, 'data', 'bic_codes.xlsx')

text = _safe_text_extract(pdf_path)
blocks = _split_messages(text)

print(f"Total messages: {len(blocks)}")
print()

for i, blk in enumerate(blocks):
    sender_bic = _extract_sender_bic(blk)
    if not sender_bic or not sender_bic.upper().startswith('CITIUS33'):
        continue

    mt_type = _detect_mt_type(blk)
    f52a = get_field_block(blk, 'F52A')
    f58a = get_field_block(blk, 'F58A')
    f58d = get_field_block(blk, 'F58D')

    if not f52a:
        continue

    code_name = get_donneur_from_f52(f52a, xlsx_path=bic_xlsx)
    if not code_name:
        continue

    code_part = code_name.split('/')[0] if '/' in code_name else code_name
    name_mapped = map_code_to_name(code_part, xlsx_path=bic_xlsx)

    if not name_mapped:
        print(f"--- Message {i+1} (MT{mt_type}, sender={sender_bic}) ---")
        print(f"  F52A code (unmapped): {code_name}")
        if f58a:
            print(f"  F58A: {repr(f58a[:300])}")
        else:
            print(f"  F58A: None")
        if f58d:
            print(f"  F58D: {repr(f58d[:300])}")
        else:
            print(f"  F58D: None")
        print()
