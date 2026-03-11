#!/usr/bin/env python3
"""
Script d'analyse des transferts exécutés — dossier Test 2
=========================================================
Reproduit exactement le flux de l'application (mode "Analyse Transferts Exécutés") :
  1. Extrait les MT900 depuis MT900.pdf
  2. Extrait les MT103 et MT202 depuis MT103.pdf et MT202.pdf (direction=outgoing)
  3. Matche les MT900 avec les MT103/MT202 via la référence F21=F20
  4. Analyse détaillée des MT900 non matchés
  5. Produit le fichier Excel identique à l'application
"""

import sys
import os
from pathlib import Path

# Ajouter hf_spaces au path
HF_SPACES_DIR = Path(__file__).resolve().parent.parent / "hf_spaces"
sys.path.insert(0, str(HF_SPACES_DIR))
os.chdir(str(HF_SPACES_DIR))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger(__name__)

from extractor_manager import (
    extract_mt900_only,
    extract_dispatch,
    match_mt900_with_transfers,
    create_transfer_analysis_workbook,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "Test 2"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "output_test2"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def main():
    print("=" * 80)
    print("ANALYSE DES TRANSFERTS EXÉCUTÉS — Dossier Test 2")
    print("=" * 80)

    # ===================== Étape 1 : Extraire les MT900 =====================
    print("\n[1/3] Extraction des MT900 depuis MT900.pdf...")
    mt900_path = DATA_DIR / "MT900.pdf"
    mt900_rows, mt900_missing = extract_mt900_only(mt900_path)
    print(f"   → {len(mt900_rows)} MT900 extraits")
    if mt900_missing.get("unmapped"):
        print(f"   ⚠ Codes BIC non mappés : {mt900_missing['unmapped']}")

    # ===================== Étape 2 : Extraire les MT103/MT202 =====================
    print("\n[2/3] Extraction des MT103 et MT202 (direction=outgoing)...")
    all_transfer_rows = []
    for pdf_name in ["MT103.pdf", "MT202.pdf"]:
        pdf_path = DATA_DIR / pdf_name
        print(f"   → Traitement de {pdf_name}...")
        new_rows, new_beac, new_exc, new_other_exc, new_bdf, new_forex, missing = extract_dispatch(pdf_path, direction="outgoing")
        # Filtrer pour ne garder que MT103 et MT202
        transfer_rows = [r for r in new_rows if r.get("type_MT") and ("103" in r.get("type_MT") or "202" in r.get("type_MT"))]
        all_transfer_rows.extend(transfer_rows)
        print(f"     {len(transfer_rows)} messages de transfert extraits depuis {pdf_name}")
        if missing.get("unmapped"):
            print(f"     ⚠ Codes BIC non mappés : {missing['unmapped']}")
    
    print(f"   → Total transferts : {len(all_transfer_rows)} messages (MT103 + MT202)")

    # ===================== Étape 3 : Matching MT900 ↔ MT103/MT202 =====================
    print("\n[3/3] Matching des MT900 avec les MT103/MT202...")
    matched_rows, suspens_rows, exception_mt900_rows, unmatched_mt900_rows = match_mt900_with_transfers(
        mt900_rows, all_transfer_rows
    )

    print(f"\n{'=' * 60}")
    print(f"RÉSUMÉ DU MATCHING")
    print(f"{'=' * 60}")
    print(f"  MT900 matchés avec MT103/MT202 : {len(matched_rows)}")
    print(f"  MT900 NON matchés              : {len(unmatched_mt900_rows)}")
    print(f"  MT900 en exception (T2PL/NIVL) : {len(exception_mt900_rows)}")
    print(f"  MT103/MT202 en suspens         : {len(suspens_rows)}")
    print(f"{'=' * 60}")

    # ===================== Analyse détaillée des MT900 non matchés =====================
    print(f"\n{'=' * 60}")
    print(f"ANALYSE DÉTAILLÉE DES MT900 NON MATCHÉS ({len(unmatched_mt900_rows)})")
    print(f"{'=' * 60}")

    if unmatched_mt900_rows:
        # Construire un index des MT103 par référence pour vérification croisée
        mt103_refs = {}
        mt202_refs = {}
        for r in all_transfer_rows:
            ref = str(r.get("reference") or "").strip().upper()
            mt_type = r.get("type_MT") or ""
            if "103" in mt_type:
                mt103_refs[ref] = r
            elif "202" in mt_type:
                mt202_refs[ref] = r

        mt900_with_mt103_match = 0
        mt900_with_mt202_match = 0
        mt900_ref_not_found = 0
        mt900_no_related_ref = 0

        print(f"\n  Détail des {len(unmatched_mt900_rows)} MT900 non matchés :")
        print(f"  {'Réf MT900 (F20)':<30} {'Réf Origine (F21)':<30} {'Montant':>15} {'Devise':<6} {'Date':>12} {'Correspondance?'}")
        print(f"  {'-'*130}")

        for i, r in enumerate(unmatched_mt900_rows):
            ref = r.get("reference") or "?"
            related_ref = str(r.get("related_reference") or "").strip()
            related_ref_upper = related_ref.upper()
            montant = r.get("montant") or "?"
            devise = r.get("devise") or "?"
            date_ref = r.get("date_reference") or "?"

            correspondance = "❌ Aucune"
            if not related_ref:
                mt900_no_related_ref += 1
                correspondance = "⚠ Pas de F21"
            elif related_ref_upper in mt103_refs:
                mt900_with_mt103_match += 1
                correspondance = "✅ MT103 trouvé"
            elif related_ref_upper in mt202_refs:
                mt900_with_mt202_match += 1
                correspondance = "✅ MT202 trouvé"
            else:
                # Chercher par préfixe (correspondance partielle)
                found_partial = False
                for mref in mt103_refs:
                    if mref and (mref.startswith(related_ref_upper) or related_ref_upper.startswith(mref)):
                        mt900_with_mt103_match += 1
                        correspondance = f"≈ MT103 (partiel: {mref})"
                        found_partial = True
                        break
                if not found_partial:
                    for mref in mt202_refs:
                        if mref and (mref.startswith(related_ref_upper) or related_ref_upper.startswith(mref)):
                            mt900_with_mt202_match += 1
                            correspondance = f"≈ MT202 (partiel: {mref})"
                            found_partial = True
                            break
                if not found_partial:
                    mt900_ref_not_found += 1

            if i < 50 or i >= len(unmatched_mt900_rows) - 5:  # Afficher les 50 premiers et les 5 derniers
                print(f"  {str(ref):<30} {related_ref:<30} {str(montant):>15} {str(devise):<6} {str(date_ref):>12} {correspondance}")
            elif i == 50:
                print(f"  ... ({len(unmatched_mt900_rows) - 55} lignes supplémentaires) ...")

        print(f"\n  {'=' * 60}")
        print(f"  RÉSUMÉ DE L'ANALYSE DES MT900 NON MATCHÉS")
        print(f"  {'=' * 60}")
        print(f"  MT900 dont F21 → trouvé en MT103 (exact ou partiel) : {mt900_with_mt103_match}")
        print(f"  MT900 dont F21 → trouvé en MT202 (exact ou partiel) : {mt900_with_mt202_match}")
        print(f"  MT900 dont F21 → introuvable (ni MT103 ni MT202)    : {mt900_ref_not_found}")
        print(f"  MT900 sans F21 (référence origine absente)          : {mt900_no_related_ref}")
        total_found = mt900_with_mt103_match + mt900_with_mt202_match
        pct = (total_found / len(unmatched_mt900_rows) * 100) if unmatched_mt900_rows else 0
        print(f"  → {total_found}/{len(unmatched_mt900_rows)} ({pct:.1f}%) ont une correspondance potentielle")
        print()

        # Analyse: pourquoi le matching principal n'a pas fonctionné malgré la présence de la référence?
        if mt900_with_mt103_match > 0 or mt900_with_mt202_match > 0:
            print(f"  ⚠ DIAGNOSTIC : {total_found} MT900 ont une correspondance de référence")
            print(f"    mais ne sont pas matchés par la logique de matching standard.")
            print(f"    Raisons possibles :")
            print(f"    - Le MT103/MT202 correspondant a déjà été matché avec un autre MT900")
            print(f"    - Correspondance par préfixe seulement (pas exact)")
            print()
    else:
        print("  ✅ Tous les MT900 non-exception ont été matchés avec un MT103/MT202 !")

    # ===================== Analyse des exceptions =====================
    if exception_mt900_rows:
        print(f"\n  EXCEPTIONS MT900 ({len(exception_mt900_rows)}) :")
        from collections import Counter
        exc_types = Counter(r.get("commentaires") for r in exception_mt900_rows)
        for exc_type, count in exc_types.most_common():
            print(f"    {exc_type}: {count}")

    # ===================== Analyse des suspens =====================
    if suspens_rows:
        print(f"\n  MT103/MT202 EN SUSPENS ({len(suspens_rows)}) :")
        from collections import Counter
        susp_types = Counter(r.get("type_MT") for r in suspens_rows)
        for mt_type, count in susp_types.most_common():
            print(f"    {mt_type}: {count}")

    # ===================== Génération du fichier Excel =====================
    print(f"\n{'=' * 60}")
    print("GÉNÉRATION DU FICHIER EXCEL...")
    print(f"{'=' * 60}")

    out_path = create_transfer_analysis_workbook(
        matched_rows,
        suspens_rows,
        exception_mt900_rows,
        OUTPUT_DIR,
        unmatched_mt900_rows=unmatched_mt900_rows,
    )
    print(f"  → Fichier Excel créé : {out_path}")
    print(f"  → Emplacement : {out_path.resolve()}")

    # ===================== Résumé final par types de MT matchés =====================
    print(f"\n{'=' * 60}")
    print("STATISTIQUES DÉTAILLÉES DES MATCHÉS")
    print(f"{'=' * 60}")
    from collections import Counter
    matched_types = Counter(r.get("matched_type") for r in matched_rows)
    for mt_type, count in matched_types.most_common():
        print(f"  MT900 matchés avec {mt_type}: {count}")

    # Ventilation par devise
    devises_matched = Counter(r.get("devise") for r in matched_rows)
    print(f"\n  Devises (matchés) :")
    for dev, count in devises_matched.most_common():
        print(f"    {dev}: {count}")

    devises_unmatched = Counter(r.get("devise") for r in unmatched_mt900_rows)
    if devises_unmatched:
        print(f"\n  Devises (non matchés) :")
        for dev, count in devises_unmatched.most_common():
            print(f"    {dev}: {count}")

    print(f"\n{'=' * 80}")
    print("ANALYSE TERMINÉE")
    print(f"{'=' * 80}")

if __name__ == "__main__":
    main()
