#!/usr/bin/env python3
"""
Script pour extraire le message 201 (pages 845-847).
"""
import sys
from pathlib import Path
from PyPDF2 import PdfReader, PdfWriter

def extract_pages(input_pdf: Path, output_pdf: Path, page_ranges: list):
    """Extraire des pages spécifiques."""
    reader = PdfReader(input_pdf)
    writer = PdfWriter()
    
    for start, end in page_ranges:
        for page_num in range(start - 1, end):
            if 0 <= page_num < len(reader.pages):
                writer.add_page(reader.pages[page_num])
    
    with open(output_pdf, 'wb') as f:
        writer.write(f)
    
    print(f"✓ Pages extraites: {output_pdf}")

if __name__ == "__main__":
    base_dir = Path(__file__).parent.parent
    input_pdf = base_dir / "data/raw/report-484.pdf"
    output_pdf = base_dir / "data/raw/report-484-msg201.pdf"
    
    # Message 201: pages 845-847
    page_ranges = [(845, 847)]
    
    extract_pages(input_pdf, output_pdf, page_ranges)
