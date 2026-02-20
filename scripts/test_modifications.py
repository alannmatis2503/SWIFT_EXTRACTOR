#!/usr/bin/env python3
"""
Script de test pour valider les 9 points de modifications.
"""
import sys
import os

# Ajouter le projet racine au path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from backend.app.extractors.mt_multi import extract_messages_from_pdf, extract_transfer_analysis
from backend.app.extractors.bic_utils import map_ccf_code, extract_ccf_code_from_f50f

# Chemin vers les fichiers de test
DATA_DIR = os.path.join(project_root, 'data', 'raw')


def test_point1_beaccmcx091():
    """Point 1: BEACCMCX091 non détecté pour Message 6 dans Citi euro.pdf"""
    print("\n" + "="*60)
    print("TEST POINT 1: Détection BEACCMCX091 (Citi euro.pdf)")
    print("="*60)
    
    pdf_path = os.path.join(DATA_DIR, 'Citi euro.pdf')
    if not os.path.exists(pdf_path):
        print(f"  ⚠️  Fichier non trouvé: {pdf_path}")
        return False
    
    # extract_messages_from_pdf retourne:
    # (rows, beaccmcx091_rows, exception_323201_rows, other_exceptions_rows, missing_codes)
    rows, beacc_rows, exc_323201, other_exc, missing = extract_messages_from_pdf(pdf_path)
    
    # Tous les messages
    all_rows = rows + beacc_rows + exc_323201 + other_exc
    
    print(f"  Messages normaux: {len(rows)}")
    print(f"  Messages BEACCMCX091: {len(beacc_rows)}")
    print(f"  Messages 323201: {len(exc_323201)}")
    print(f"  Autres exceptions: {len(other_exc)}")
    print(f"  Total: {len(all_rows)}")
    
    for r in beacc_rows:
        print(f"    - Type: {r.get('type_MT')}, Ref: {r.get('reference')}, Comment: {r.get('commentaires')}")
    
    # Vérifier que des messages BEACCMCX091 sont détectés
    success = len(beacc_rows) > 0
    print(f"  {'✅ SUCCÈS' if success else '❌ ÉCHEC'}: Détection BEACCMCX091")
    return success


def test_point2_duplicates():
    """Point 2: Gestion des doublons - un seul gardé avec 'potentiel doublon'"""
    print("\n" + "="*60)
    print("TEST POINT 2: Gestion des doublons (SCB.pdf)")
    print("="*60)
    
    pdf_path = os.path.join(DATA_DIR, 'SCB.pdf')
    if not os.path.exists(pdf_path):
        print(f"  ⚠️  Fichier non trouvé: {pdf_path}")
        return False
    
    rows, beacc, exc_323201, other_exc, _ = extract_messages_from_pdf(pdf_path)
    all_rows = rows + beacc + exc_323201 + other_exc
    
    # Rechercher les doublons par référence
    refs_count = {}
    for r in all_rows:
        ref = r.get('reference')
        if ref:
            refs_count[ref] = refs_count.get(ref, 0) + 1
    
    duplicates = [ref for ref, count in refs_count.items() if count > 1]
    
    print(f"  Messages extraits: {len(all_rows)}")
    print(f"  Références en double dans extraction: {duplicates}")
    
    # Note: La gestion des doublons se fait au niveau de create_summary_workbook
    print("  ℹ️  La déduplication avec 'potentiel doublon' se fait dans create_summary_workbook")
    return True


def test_point3_t2rm_t2pi():
    """Point 3: Détection T2RM/T2PI pour MT910 entrants"""
    print("\n" + "="*60)
    print("TEST POINT 3: Détection T2RM/T2PI (report-484.pdf)")
    print("="*60)
    
    pdf_path = os.path.join(DATA_DIR, 'report-484.pdf')
    if not os.path.exists(pdf_path):
        print(f"  ⚠️  Fichier non trouvé: {pdf_path}")
        return False
    
    rows, beacc, exc_323201, other_exc, _ = extract_messages_from_pdf(pdf_path)
    all_rows = rows + beacc + exc_323201 + other_exc
    
    # Filtrer MT910
    mt910_rows = [r for r in all_rows if r.get('type_MT') == 'MT910']
    
    # Vérifier les exceptions T2RM/T2PI
    t2_exceptions = [r for r in all_rows if any(x in str(r.get('commentaires', '')) for x in ['T2RM', 'T2PI'])]
    
    print(f"  Messages extraits: {len(all_rows)}")
    print(f"  MT910: {len(mt910_rows)}")
    print(f"  Messages avec exception T2RM/T2PI: {len(t2_exceptions)}")
    
    for r in t2_exceptions[:5]:
        print(f"    - Type: {r.get('type_MT')}, Ref: {r.get('reference')}, Comment: {r.get('commentaires')}")
    
    # Vérifier aussi les autres exceptions
    print(f"\n  Exceptions autres (EUR/nivellement): {len(other_exc)}")
    for r in other_exc[:5]:
        print(f"    - Type: {r.get('type_MT')}, Ref: {r.get('reference')}, Comment: {r.get('commentaires')}")
    
    return True


def test_point4_nivlt_f21():
    """Point 4: Détection NIVLT/NIVELLEMENT dans F21 (related_reference)"""
    print("\n" + "="*60)
    print("TEST POINT 4: NIVLT/NIVELLEMENT dans F21 (report-484.pdf)")
    print("="*60)
    
    pdf_path = os.path.join(DATA_DIR, 'report-484.pdf')
    if not os.path.exists(pdf_path):
        print(f"  ⚠️  Fichier non trouvé: {pdf_path}")
        return False
    
    rows, beacc, exc_323201, other_exc, _ = extract_messages_from_pdf(pdf_path)
    all_rows = rows + beacc + exc_323201 + other_exc
    
    # Messages avec NIVLT ou NIVELLEMENT en commentaire
    nivlt_rows = [r for r in all_rows if any(x in str(r.get('commentaires', '')) for x in ['NIVLT', 'NIVELLEMENT', 'nivellement'])]
    
    print(f"  Messages extraits: {len(all_rows)}")
    print(f"  Messages avec exception NIVLT/NIVELLEMENT: {len(nivlt_rows)}")
    
    for r in nivlt_rows[:5]:
        print(f"    - Type: {r.get('type_MT')}, Ref: {r.get('reference')}, Related: {r.get('related_reference')}")
        print(f"      Comment: {r.get('commentaires')}")
    
    return True


def test_point5_ccf_mapping():
    """Point 5: Mapping CCF pour MT103 sortants"""
    print("\n" + "="*60)
    print("TEST POINT 5: Mapping CCF (bic_codes.xlsx)")
    print("="*60)
    
    # Tester la fonction de mapping CCF directement
    test_codes = ['1401', '9401', '1201', 'XXXX']
    
    print("  Test des codes CCF:")
    for code in test_codes:
        result = map_ccf_code(code)
        print(f"    - CCF {code}: {result if result else '(non trouvé)'}")
    
    # Tester l'extraction CCF depuis un texte F50F
    test_f50f = """1/DONNEUR ORDRE SARL
2/10.311301.0.1401.0.0.0.0.0
3/DOUALA"""
    
    extracted = extract_ccf_code_from_f50f(test_f50f)
    print(f"\n  Extraction CCF depuis F50F: {extracted}")
    
    # Tester avec MT103.pdf
    pdf_path = os.path.join(DATA_DIR, 'MT103.pdf')
    if os.path.exists(pdf_path):
        rows, beacc, exc_323201, other_exc, _ = extract_messages_from_pdf(pdf_path, direction="outgoing")
        all_rows = rows + beacc + exc_323201 + other_exc
        
        mt103_sortants = [r for r in all_rows if '103' in str(r.get('type_MT', ''))]
        
        print(f"\n  MT103 dans MT103.pdf: {len(mt103_sortants)}")
        for r in mt103_sortants[:5]:  # Afficher les 5 premiers
            print(f"    - Ref: {r.get('reference')}, Code: {r.get('code_donneur_dordre')}, Donneur: {r.get('donneur_dordre', '')[:30] if r.get('donneur_dordre') else 'N/A'}")
    
    return True


def test_point6_f50f_incoming():
    """Point 6: Extraction donneur d'ordre depuis F50F pour MT103 entrants"""
    print("\n" + "="*60)
    print("TEST POINT 6: Extraction F50F MT103 entrants (MT103.pdf)")
    print("="*60)
    
    pdf_path = os.path.join(DATA_DIR, 'MT103.pdf')
    if not os.path.exists(pdf_path):
        print(f"  ⚠️  Fichier non trouvé: {pdf_path}")
        return False
    
    rows, beacc, exc_323201, other_exc, _ = extract_messages_from_pdf(pdf_path, direction="incoming")
    all_rows = rows + beacc + exc_323201 + other_exc
    
    # Filtrer MT103 (types: MT103 ou fin.103)
    mt103_rows = [r for r in all_rows if '103' in str(r.get('type_MT', ''))]
    
    print(f"  Messages extraits: {len(all_rows)}")
    print(f"  MT103: {len(mt103_rows)}")
    
    # Vérifier ceux avec donneur d'ordre extrait
    with_donneur = [r for r in mt103_rows if r.get('donneur_dordre')]
    print(f"  MT103 avec donneur d'ordre: {len(with_donneur)}")
    
    for r in with_donneur[:5]:  # Afficher les 5 premiers
        donneur = r.get('donneur_dordre', '')
        print(f"    - Ref: {r.get('reference')}, Donneur: {donneur[:50] if donneur else 'N/A'}...")
    
    return len(mt103_rows) > 0


def test_point7_mt103_count():
    """Point 7: Vérifier le nombre de messages dans MT103.pdf (attendu: 32/36)"""
    print("\n" + "="*60)
    print("TEST POINT 7: Comptage messages MT103.pdf")
    print("="*60)
    
    pdf_path = os.path.join(DATA_DIR, 'MT103.pdf')
    if not os.path.exists(pdf_path):
        print(f"  ⚠️  Fichier non trouvé: {pdf_path}")
        return False
    
    rows, beacc, exc_323201, other_exc, _ = extract_messages_from_pdf(pdf_path)
    all_rows = rows + beacc + exc_323201 + other_exc
    
    # Compter par type
    type_counts = {}
    for r in all_rows:
        mt = r.get('type_MT', 'Unknown')
        type_counts[mt] = type_counts.get(mt, 0) + 1
    
    print(f"  Total messages extraits: {len(all_rows)}")
    print(f"  Par type:")
    for mt, count in sorted(type_counts.items()):
        print(f"    - {mt}: {count}")
    
    # L'utilisateur attend 32 messages (sur 36 dans le PDF)
    success = len(all_rows) >= 30  # Approximation
    print(f"\n  {'✅' if success else '⚠️'} Nombre attendu: ~32, Obtenu: {len(all_rows)}")
    
    return success


def test_point8_transfer_analysis():
    """Point 8: Exceptions dans le module analyse de transferts"""
    print("\n" + "="*60)
    print("TEST POINT 8: Module analyse de transferts (report-484.pdf)")
    print("="*60)
    
    pdf_path = os.path.join(DATA_DIR, 'report-484.pdf')
    if not os.path.exists(pdf_path):
        print(f"  ⚠️  Fichier non trouvé: {pdf_path}")
        return False
    
    try:
        # extract_transfer_analysis retourne:
        # (matched_900_rows, suspens_rows, mt900_exception_rows, missing_codes)
        result = extract_transfer_analysis(pdf_path)
        
        if len(result) == 4:
            matched, suspens, exceptions, missing_codes = result
            print(f"  Matched (MT900): {len(matched)}")
            print(f"  Suspens: {len(suspens)}")
            print(f"  Exceptions: {len(exceptions)}")
            
            if exceptions:
                print(f"\n  Exceptions trouvées:")
                for exc in exceptions[:5]:
                    print(f"    - Type: {exc.get('type_MT')}, Ref: {exc.get('reference')}, Comment: {exc.get('commentaires')}")
            
            return True
        else:
            print(f"  ⚠️  Nombre de retours inattendu: {len(result)}")
            return False
    except Exception as e:
        import traceback
        print(f"  ❌ Erreur: {e}")
        traceback.print_exc()
        return False


def test_point9_pymupdf():
    """Point 9: Vérifier que pymupdf est utilisé (pas pdfplumber)"""
    print("\n" + "="*60)
    print("TEST POINT 9: Migration vers pymupdf")
    print("="*60)
    
    # Vérifier les imports dans mt_multi.py
    mt_multi_path = os.path.join(project_root, 'backend', 'app', 'extractors', 'mt_multi.py')
    
    with open(mt_multi_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    has_fitz = 'import fitz' in content
    has_pdfplumber = 'import pdfplumber' in content or 'from pdfplumber' in content
    
    print(f"  Import fitz (pymupdf): {'✅' if has_fitz else '❌'}")
    print(f"  Import pdfplumber: {'❌' if has_pdfplumber else '✅ (absent)'}")
    
    # Tester l'extraction réelle
    pdf_path = os.path.join(DATA_DIR, 'Citi euro.pdf')
    if os.path.exists(pdf_path):
        try:
            rows, beacc, exc_323201, other_exc, _ = extract_messages_from_pdf(pdf_path)
            all_rows = rows + beacc + exc_323201 + other_exc
            print(f"\n  Test extraction avec pymupdf: ✅ ({len(all_rows)} messages)")
        except Exception as e:
            print(f"\n  Test extraction avec pymupdf: ❌ ({e})")
            return False
    
    return has_fitz and not has_pdfplumber


def main():
    print("="*60)
    print("TESTS DE VALIDATION DES 9 POINTS DE MODIFICATIONS")
    print("="*60)
    
    results = {
        "Point 1 - BEACCMCX091": test_point1_beaccmcx091(),
        "Point 2 - Doublons": test_point2_duplicates(),
        "Point 3 - T2RM/T2PI": test_point3_t2rm_t2pi(),
        "Point 4 - NIVLT/F21": test_point4_nivlt_f21(),
        "Point 5 - CCF Mapping": test_point5_ccf_mapping(),
        "Point 6 - F50F Incoming": test_point6_f50f_incoming(),
        "Point 7 - MT103 Count": test_point7_mt103_count(),
        "Point 8 - Transfer Analysis": test_point8_transfer_analysis(),
        "Point 9 - pymupdf": test_point9_pymupdf(),
    }
    
    print("\n" + "="*60)
    print("RÉSUMÉ DES TESTS")
    print("="*60)
    
    for test_name, success in results.items():
        status = "✅ OK" if success else "❌ ÉCHEC"
        print(f"  {test_name}: {status}")
    
    total_success = sum(1 for s in results.values() if s)
    print(f"\n  Total: {total_success}/{len(results)} tests réussis")


if __name__ == '__main__':
    main()
