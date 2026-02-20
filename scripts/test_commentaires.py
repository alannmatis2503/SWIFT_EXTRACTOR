#!/usr/bin/env python3
"""
Script de test pour l'extraction des commentaires F70 (MT103) et F72 (MT202).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hf_spaces"))

from extractors.mt_multi import extract_messages_from_pdf

def test_commentaires():
    """Tester l'extraction des commentaires."""
    pdf_path = Path(__file__).parent.parent / "data/raw/report-484-test.pdf"
    bic_xlsx = Path(__file__).parent.parent / "hf_spaces/data/bic_codes.xlsx"
    
    print(f"📄 Test d'extraction des commentaires\n")
    print(f"Fichier: {pdf_path.name}\n")
    print("=" * 80)
    
    rows, beac_rows, exc_323201, other_exc, missing = extract_messages_from_pdf(
        pdf_path,
        bic_xlsx=str(bic_xlsx),
        direction="incoming"
    )
    
    print(f"\n✅ Messages extraits: {len(rows)}\n")
    
    for i, row in enumerate(rows, 1):
        msg_type = row.get("type_MT", "N/A")
        reference = row.get("reference", "N/A")
        commentaires = row.get("commentaires", "")
        donneur = row.get("donneur_dordre", "N/A")
        
        print(f"\nMessage {i}:")
        print(f"  Type: {msg_type}")
        print(f"  Référence: {reference}")
        print(f"  Donneur: {donneur}")
        print(f"  Commentaires: {commentaires if commentaires else '(vide)'}")
        
        if commentaires:
            print(f"    Longueur: {len(commentaires)} caractères")
    
    print("\n" + "=" * 80)
    print("RÉSUMÉ:")
    print("=" * 80)
    
    with_comments = [r for r in rows if r.get("commentaires")]
    without_comments = [r for r in rows if not r.get("commentaires")]
    
    print(f"Messages avec commentaires: {len(with_comments)}")
    print(f"Messages sans commentaires: {len(without_comments)}")

if __name__ == "__main__":
    test_commentaires()
