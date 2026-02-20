#!/usr/bin/env python3
"""
Script pour déboguer l'extraction du message N°08 (MT103 sortant avec code 1401).
"""
import sys
import re
from pathlib import Path

# Add hf_spaces to path
sys.path.insert(0, str(Path(__file__).parent.parent / "hf_spaces"))

from extractors.bic_utils import (
    extract_tresor_code_from_f50f,
    extract_ccf_4digit_code_from_f50f,
    load_bic_mapping,
    _CCF_MAP,
    _REGLEMENT_MAP,
)

# Import get_field_block only (avoid pdfplumber import)
def get_field_block(text, field_label):
    """Simple extraction of field block."""
    patterns = [
        rf'{field_label}[:\s]+(.+?)(?=\nF[0-9]+[A-Z]?:|$)',
        rf'#{field_label}#(.+?)(?=#F|$)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None

# Contenu du message 8 (extrait du PDF)
MESSAGE_8_TEXT = """
Message 8
Message Identifier
Message Preparation
Application:
Unique Message Identifier:

Alliance Message Management
I CITIUS33XXX 103 9910/217/1401/26 (suffix 26012825609200)

Message Header
Status:
Format:
Identifier:

Message Modified
Deletable
Swift
fin.103

Sub-Format:
Expansion:

Application
FIN
Nature:
Sender:
BEACCMCX100
LT:
Receiver:
CITIUS33XXX
LT:
Transaction Reference:
9910/217/1401/26
Priority:
Normal
Monitoring:
None
UETR:
d10171b6-9671-4988-a75c-ee1ab61b131f
Amount:
10.078.300,9
Currency:

Input
Single Customer Credit Transfer
Financial
A
X

USD

Value Date:

30/01/26

Message Text
Block 4
F20: Référence de l'émetteur
9910/217/1401/26
F23B: Code de l'opération bancaire
CRED
F32A: Date val/dvs/mnt règl interbq
Date:
260130
2026 Jan 30
Currency: Devise:
USD
US DOLLAR
Amount: Montant:
10078300,90
#10.078.300,90#
F50F: Client donneur d'ordre - Identifiant de partie - Numéro/Nom et adresse
PartyIdentifier: Identifiant de partie:
Code - Code pays - Identifiant
Code:
CUST/
CountryCode: Code pays:
CM/
Identifier: Identifiant:
BEAC/1401.
NameAndAddress: Numéro/Nom et adresse
Number: Numéro:
1/
Details: Détails:
CAISSE AUTONOME D'AMORTISSEMENT
Number: Numéro:
1/
Details: Détails:
DU CAMEROUN
Number: Numéro:
2/
Details: Détails:
BOULEVARD DU 20 MAI
Number: Numéro:
3/
Details: Détails:
CM/YAOUNDE, BP 7167
F53B: Correspondant de l'émetteur - Identifiant de partie - Lieu
PartyIdentifier: Identifiant de partie:
/D/36357124
F57A: Inst gestionnaire compte - Identifiant de partie - Code d'identifiant
IdentifierCode: Code d'identifiant:
BKCHUS33
BANK OF CHINA
NEW YORK,NY US
F59F: Client bénéficiaire - Compte - Numéro/Nom et adresse
Account: Compte:
/01000130
NumberNameAndAddressDetails: Numéro/Nom et adresse
Number: Numéro:
1/
Details: Détails:
THE EXPORT IMPORT BANK OF CHINA
"""

def main():
    print("="*80)
    print("DÉBOGAGE EXTRACTION MESSAGE N°08")
    print("="*80)
    print()
    
    # Charger le mapping BIC
    print("1. Chargement du fichier bic_codes.xlsx...")
    bic_mapping = load_bic_mapping()
    print(f"   ✓ {len(bic_mapping)} codes BIC chargés")
    
    # Vérifier les mappings CCF et Reglement
    print()
    print("2. Vérification des mappings CCF et Reglement...")
    if _CCF_MAP:
        print(f"   ✓ {len(_CCF_MAP)} codes CCF chargés")
        # Afficher quelques exemples
        for i, (code, info) in enumerate(list(_CCF_MAP.items())[:5]):
            print(f"     - {code}: {info['name'][:50]}")
    else:
        print("   ✗ Aucun code CCF chargé")
    
    if _REGLEMENT_MAP:
        print(f"   ✓ {len(_REGLEMENT_MAP)} codes Reglement chargés")
        # Chercher spécifiquement le code 1401
        if '1401' in _REGLEMENT_MAP:
            print(f"     - Code 1401 trouvé: {_REGLEMENT_MAP['1401']}")
        else:
            print(f"     ✗ Code 1401 NON trouvé dans _REGLEMENT_MAP")
            print(f"     Codes disponibles: {list(_REGLEMENT_MAP.keys())}")
    else:
        print("   ✗ Aucun code Reglement chargé")
    
    # Extraire le champ F50F
    print()
    print("3. Extraction du champ F50F...")
    f50f_block = get_field_block(MESSAGE_8_TEXT, 'F50F')
    if f50f_block:
        print("   ✓ Champ F50F trouvé:")
        print("   " + "-"*70)
        for line in f50f_block.split('\n')[:15]:
            print(f"   {line}")
        print("   " + "-"*70)
    else:
        print("   ✗ Champ F50F non trouvé")
        return
    
    # Chercher le code 1401 dans F50F
    print()
    print("4. Recherche du code 1401 dans F50F...")
    import re
    matches = re.findall(r'\b1401\b', f50f_block)
    print(f"   Matches trouvés: {matches}")
    if matches:
        print("   ✓ Code 1401 présent dans F50F")
    else:
        print("   ✗ Code 1401 NON trouvé dans F50F")
    
    # Test de extract_tresor_code_from_f50f
    print()
    print("5. Test de extract_tresor_code_from_f50f()...")
    tresor_info = extract_tresor_code_from_f50f(f50f_block)
    if tresor_info:
        print("   ✓ Code Trésor extrait:")
        print(f"     - Code: {tresor_info.get('code')}")
        print(f"     - Nom: {tresor_info.get('name')}")
        print(f"     - Pays: {tresor_info.get('country')}")
        print(f"     - BIC: {tresor_info.get('bic')}")
    else:
        print("   ✗ Aucun code Trésor extrait")
    
    # Test de extract_ccf_4digit_code_from_f50f
    print()
    print("6. Test de extract_ccf_4digit_code_from_f50f()...")
    ccf_info = extract_ccf_4digit_code_from_f50f(f50f_block)
    if ccf_info:
        print("   ✓ Code CCF extrait:")
        print(f"     - Code: {ccf_info.get('code')}")
        print(f"     - Nom: {ccf_info.get('name')}")
        print(f"     - Pays: {ccf_info.get('country')}")
        print(f"     - BIC: {ccf_info.get('bic')}")
    else:
        print("   ✗ Aucun code CCF extrait")
    
    # Test de parse_f52a_or_f50f_institution
    print()
    print("7. Test de extraction manuelle du nom depuis F50F...")
    # Extraire manuellement le nom depuis F50F
    name_lines = []
    for line in f50f_block.split('\n'):
        if 'CAISSE' in line or 'AMORTISSEMENT' in line or 'CAMEROUN' in line:
            name_lines.append(line.strip())
    if name_lines:
        print(f"   ✓ Lignes de nom trouvées: {name_lines}")
    else:
        print("   ✗ Aucune ligne de nom trouvée")
    
    print()
    print("="*80)
    print("ANALYSE DU PROBLÈME")
    print("="*80)
    print()
    
    # Analyse du problème
    if not tresor_info and not ccf_info:
        print("❌ PROBLÈME IDENTIFIÉ:")
        print("   Le code 1401 n'est pas extrait correctement.")
        print()
        print("CAUSES POSSIBLES:")
        print("   1. Le code 1401 n'est pas dans _REGLEMENT_MAP")
        print("   2. Le format du code dans F50F ne correspond pas aux patterns de recherche")
        print("   3. Le fichier bic_codes.xlsx ne contient pas le code 1401 dans la colonne CCF ou Reglement")
        print()
        print("RECOMMANDATIONS:")
        print("   1. Vérifier que le fichier bic_codes.xlsx contient une ligne avec 1401")
        print("   2. Vérifier que la colonne CCF ou Reglement contient bien le code 1401")
        print("   3. Adapter les patterns de recherche dans extract_tresor_code_from_f50f()")
    else:
        print("✓ Le code 1401 a été extrait correctement")
        if tresor_info:
            print(f"  Via extract_tresor_code_from_f50f: {tresor_info.get('name')}")
        if ccf_info:
            print(f"  Via extract_ccf_4digit_code_from_f50f: {ccf_info.get('name')}")

if __name__ == "__main__":
    main()
