#!/usr/bin/env python3
"""
Script pour extraire des pages spécifiques d'un PDF volumineux
pour créer un fichier de test allégé.
"""
import sys
from pathlib import Path
from PyPDF2 import PdfReader, PdfWriter

def extract_pages(input_pdf: Path, output_pdf: Path, page_ranges: list):
    """
    Extraire des pages spécifiques d'un PDF.
    
    Args:
        input_pdf: Chemin du PDF source
        output_pdf: Chemin du PDF de sortie
        page_ranges: Liste de tuples (start, end) - numéros de pages 1-based
    """
    reader = PdfReader(input_pdf)
    writer = PdfWriter()
    
    total_pages = len(reader.pages)
    print(f"PDF source: {input_pdf.name}")
    print(f"Total pages: {total_pages}")
    print(f"Extraction des pages: {page_ranges}")
    
    for start, end in page_ranges:
        # Conversion 1-based vers 0-based
        for page_num in range(start - 1, end):
            if 0 <= page_num < total_pages:
                writer.add_page(reader.pages[page_num])
                print(f"  Page {page_num + 1} ajoutée")
            else:
                print(f"  ATTENTION: Page {page_num + 1} hors limites (max: {total_pages})")
    
    # Sauvegarder
    with open(output_pdf, 'wb') as f:
        writer.write(f)
    
    print(f"\n✓ PDF de test créé: {output_pdf}")
    print(f"  Taille: {output_pdf.stat().st_size / 1024:.1f} KB")

if __name__ == "__main__":
    # Chemins
    base_dir = Path(__file__).parent.parent
    input_pdf = base_dir / "data/raw/report-484.pdf"
    output_pdf = base_dir / "data/raw/report-484-test.pdf"
    
    # Pages à extraire
    # Message 167: pages 776-777
    # Message 429: pages 1302-1303
    page_ranges = [
        (776, 777),
        (1302, 1303)
    ]
    
    if not input_pdf.exists():
        print(f"ERREUR: Fichier introuvable: {input_pdf}")
        sys.exit(1)
    
    extract_pages(input_pdf, output_pdf, page_ranges)
