#!/usr/bin/env python3
"""
Test complet de l'extraction de tous les messages du fichier PDF.
"""
import sys
from pathlib import Path
from pprint import pprint

# Add hf_spaces to path
sys.path.insert(0, str(Path(__file__).parent.parent / "hf_spaces"))

import pdfplumber
from extractors.mt_multi import extract_messages_from_pdf

def test_pdf(pdf_path, direction="outgoing"):
    print("="*100)
    print(f"EXTRACTION COMPLÈTE - {pdf_path.name}")
    print(f"Direction: {direction}")
    print("="*100)
    print()
    
    if not pdf_path.exists():
        print(f"❌ Fichier PDF non trouvé: {pdf_path}")
        return
    
    print(f"📂 Fichier: {pdf_path}")
    print()
    
    # Extraction avec mt_multi
    mt202_rows, mt103_rows, mt910_rows, rejected_rows, rejected_refs = extract_messages_from_pdf(
        pdf_path, direction=direction
    )
    
    # Combiner tous les résultats
    results = mt202_rows + mt103_rows + mt910_rows
    
    print(f"📊 {len(results)} messages extraits (MT202: {len(mt202_rows)}, MT103: {len(mt103_rows)}, MT910: {len(mt910_rows)})")
    print(f"   {len(rejected_rows)} messages rejetés")
    print()
    
    # Afficher tous les résultats en tableau
    print("-"*120)
    print(f"{'No':<4} {'Type':<10} {'Reference':<25} {'Montant':<18} {'Devise':<6} {'Donneur ordre':<50}")
    print("-"*120)
    
    for i, row in enumerate(results, 1):
        type_mt = row.get('type_MT', 'N/A')
        ref = row.get('reference', 'N/A')
        montant = row.get('montant', 'N/A')
        devise = row.get('devise', 'N/A')
        donneur = row.get('donneur_dordre', 'N/A')
        
        # Tronquer le donneur si trop long
        if donneur and len(str(donneur)) > 48:
            donneur = str(donneur)[:45] + "..."
        
        print(f"{i:<4} {str(type_mt):<10} {str(ref):<25} {str(montant):<18} {str(devise):<6} {str(donneur):<50}")
    
    print("-"*120)
    return results

def main():
    # Test 1: MT103.pdf
    pdf1 = Path(__file__).parent.parent / "data" / "raw" / "Analyse des transferts exécutés" / "MT103.pdf"
    results1 = test_pdf(pdf1, direction="outgoing")
    
    print("\n\n")
    
    # Test 2: Messages transferts sortants.pdf
    pdf2 = Path(__file__).parent.parent / "data" / "raw" / "Test_29_01_2026" / "Messages transferts sortants.pdf"
    results2 = test_pdf(pdf2, direction="outgoing")
    
    # Vérifications spécifiques
    print("\n")
    print("="*100)
    print("VÉRIFICATIONS SPÉCIFIQUES")
    print("="*100)
    
    # Vérifier message 1 de MT103.pdf -> ONDELE MARCEL
    if results1:
        msg1 = results1[0] if results1 else None
        if msg1:
            donneur1 = msg1.get('donneur_dordre', '')
            expected1 = "ONDELE MARCEL"
            status1 = "✅" if expected1 in str(donneur1) else "❌"
            print(f"{status1} MT103.pdf Message 1: donneur_dordre = '{donneur1}' (attendu: '{expected1}')")
        
        # Vérifier message 3 de MT103.pdf -> BANQUE AFRICAINE D IMPORT- EXPORT
        msg3 = results1[2] if len(results1) > 2 else None
        if msg3:
            donneur3 = msg3.get('donneur_dordre', '')
            expected3 = "BANQUE AFRICAINE D IMPORT- EXPORT"
            status3 = "✅" if expected3 in str(donneur3) else "❌"
            print(f"{status3} MT103.pdf Message 3: donneur_dordre = '{donneur3}' (attendu: '{expected3}')")
    
    # Vérifier message 8 de Messages transferts sortants.pdf -> Caisse Autonome d'Amortissement Cameroun
    if results2:
        msg8 = None
        for row in results2:
            if '1401' in str(row.get('reference', '')):
                msg8 = row
                break
        if msg8:
            donneur8 = msg8.get('donneur_dordre', '')
            expected8 = "Caisse Autonome d'Amortissement Cameroun"
            status8 = "✅" if expected8 in str(donneur8) else "❌"
            print(f"{status8} Messages sortants Message 8: donneur_dordre = '{donneur8}' (attendu: '{expected8}')")

if __name__ == "__main__":
    main()
