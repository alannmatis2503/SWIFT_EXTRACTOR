#!/usr/bin/env python3
"""
Script de test pour l'extraction du donneur d'ordre MT103 entrants.
"""
import sys
from pathlib import Path

# Ajouter le chemin des extractors
sys.path.insert(0, str(Path(__file__).parent.parent / "hf_spaces"))

from extractors.mt_multi import extract_messages_from_pdf

def test_extraction():
    """Tester l'extraction avec le PDF de test."""
    pdf_path = Path(__file__).parent.parent / "data/raw/report-484-test.pdf"
    bic_xlsx = Path(__file__).parent.parent / "hf_spaces/data/bic_codes.xlsx"
    
    if not pdf_path.exists():
        print(f"ERREUR: Fichier introuvable: {pdf_path}")
        return
    
    print(f"📄 Test d'extraction: {pdf_path.name}")
    print(f"📊 Fichier BIC: {bic_xlsx.name}\n")
    
    # Extraction en mode incoming
    rows, beac_rows, exc_323201, other_exc, missing = extract_messages_from_pdf(
        pdf_path,
        bic_xlsx=str(bic_xlsx),
        direction="incoming"
    )
    
    print(f"✅ Messages extraits: {len(rows)}")
    print(f"   BEACCMCX091: {len(beac_rows)}")
    print(f"   Exceptions 323201: {len(exc_323201)}")
    print(f"   Autres exceptions: {len(other_exc)}\n")
    
    # Afficher les résultats pour chaque message
    for i, row in enumerate(rows, 1):
        msg_type = row.get("type_MT", "N/A")
        reference = row.get("reference", "N/A")
        code_donneur = row.get("code_donneur_dordre", "")
        nom_donneur = row.get("donneur_dordre", "N/A")
        source = row.get("source", "N/A")
        
        print(f"Message {i}:")
        print(f"  Type: {msg_type}")
        print(f"  Source: {source}")
        print(f"  Référence: {reference}")
        print(f"  Code donneur: {code_donneur if code_donneur else '(vide)'}")
        print(f"  Nom donneur: {nom_donneur}")
        print()
    
    # Vérifier les résultats attendus
    print("=" * 60)
    print("VÉRIFICATION DES RÉSULTATS ATTENDUS:")
    print("=" * 60)
    
    # Message 167 (pages 776-777): doit renvoyer "AUDIBLE LIMITED"
    msg_167 = next((r for r in rows if "167" in r.get("source", "")), None)
    if msg_167:
        donneur_167 = msg_167.get("donneur_dordre", "")
        expected_167 = "AUDIBLE LIMITED"
        if donneur_167 == expected_167:
            print(f"✅ Message 167: CORRECT - '{donneur_167}'")
        else:
            print(f"❌ Message 167: INCORRECT")
            print(f"   Attendu: '{expected_167}'")
            print(f"   Obtenu:  '{donneur_167}'")
    else:
        print("❌ Message 167: NON TROUVÉ")
    
    # Message 429 (pages 1302-1303): doit renvoyer "PAIERIE GENERALE AUX ARMEES (PGA)"
    msg_429 = next((r for r in rows if "429" in r.get("source", "")), None)
    if msg_429:
        donneur_429 = msg_429.get("donneur_dordre", "")
        expected_429 = "PAIERIE GENERALE AUX ARMEES (PGA)"
        if donneur_429 == expected_429:
            print(f"✅ Message 429: CORRECT - '{donneur_429}'")
        else:
            print(f"❌ Message 429: INCORRECT")
            print(f"   Attendu: '{expected_429}'")
            print(f"   Obtenu:  '{donneur_429}'")
    else:
        print("❌ Message 429: NON TROUVÉ")

if __name__ == "__main__":
    test_extraction()
