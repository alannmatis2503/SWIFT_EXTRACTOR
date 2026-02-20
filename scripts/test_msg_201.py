#!/usr/bin/env python3
"""
Script de test pour le message 201.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hf_spaces"))

from extractors.mt_multi import extract_messages_from_pdf

def test_msg_201():
    """Tester l'extraction du message 201."""
    pdf_path = Path(__file__).parent.parent / "data/raw/report-484-msg201.pdf"
    bic_xlsx = Path(__file__).parent.parent / "hf_spaces/data/bic_codes.xlsx"
    
    print(f"📄 Test du message 201\n")
    
    rows, beac_rows, exc_323201, other_exc, missing = extract_messages_from_pdf(
        pdf_path,
        bic_xlsx=str(bic_xlsx),
        direction="incoming"
    )
    
    print(f"✅ Messages extraits: {len(rows)}\n")
    
    for i, row in enumerate(rows, 1):
        code_donneur = row.get("code_donneur_dordre", "")
        nom_donneur = row.get("donneur_dordre", "N/A")
        reference = row.get("reference", "N/A")
        
        print(f"Message {i}:")
        print(f"  Référence: {reference}")
        print(f"  Code donneur: '{code_donneur if code_donneur else '(vide)'}'")
        print(f"  Nom donneur: '{nom_donneur}'")
        print()
    
    # Vérification
    print("=" * 60)
    print("VÉRIFICATION:")
    print("=" * 60)
    
    if rows:
        row = rows[0]
        nom_donneur = row.get("donneur_dordre", "")
        code_donneur = row.get("code_donneur_dordre", "")
        expected_nom = "RECETTE GENERALE DES FINANCES"
        
        if nom_donneur == expected_nom:
            print(f"✅ Nom donneur: CORRECT - '{nom_donneur}'")
        else:
            print(f"❌ Nom donneur: INCORRECT")
            print(f"   Attendu: '{expected_nom}'")
            print(f"   Obtenu:  '{nom_donneur}'")
        
        if not code_donneur or code_donneur == "":
            print(f"✅ Code donneur: CORRECT - vide (pas de BIC valide)")
        else:
            print(f"⚠️  Code donneur: '{code_donneur}' (devrait être vide)")

if __name__ == "__main__":
    test_msg_201()
