#!/usr/bin/env python3
"""
Script pour afficher le contenu brut du champ F50F.
"""
import sys
from pathlib import Path
import pdfplumber

def extract_f50f_raw():
    """Extraire et afficher le texte brut des champs F50F."""
    pdf_path = Path(__file__).parent.parent / "data/raw/report-484-test.pdf"
    
    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += page.extract_text() or ""
        
    # Chercher tous les blocs F50F
    import re
    pattern = r':50F:(.*?)(?=:\d{2}[A-Z]:|$)'
    matches = re.findall(pattern, full_text, re.DOTALL)
    
    print(f"📄 Fichier: {pdf_path.name}")
    print(f"📊 Blocs F50F trouvés: {len(matches)}\n")
    print("=" * 80)
    
    for i, match in enumerate(matches, 1):
        print(f"\nBLOC F50F #{i}:")
        print("-" * 80)
        print(match[:500])  # Premiers 500 caractères
        print("-" * 80)

if __name__ == "__main__":
    extract_f50f_raw()
