#!/usr/bin/env python3
"""
Script de benchmark pour mesurer les performances d'extraction.
Usage: python scripts/benchmark_performance.py
"""

import time
import sys
from pathlib import Path

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.app.extractors.mt_multi import extract_messages_from_pdf


def benchmark_file(pdf_path: Path, direction: str = "incoming", runs: int = 3):
    """Benchmark extraction performance on a PDF file."""
    print(f"\n{'='*60}")
    print(f"Fichier: {pdf_path.name}")
    print(f"Direction: {direction}")
    print(f"{'='*60}")
    
    times = []
    row_count = 0
    
    for i in range(runs):
        start = time.time()
        rows, missing = extract_messages_from_pdf(pdf_path, direction=direction)
        elapsed = time.time() - start
        times.append(elapsed)
        row_count = len(rows)
        print(f"  Run {i+1}/{runs}: {elapsed:.3f}s - {row_count} messages")
    
    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)
    
    print(f"\n  Résultats:")
    print(f"    Temps moyen: {avg_time:.3f}s")
    print(f"    Min: {min_time:.3f}s | Max: {max_time:.3f}s")
    print(f"    Messages/seconde: {row_count/avg_time:.1f}")
    print(f"    Messages extraits: {row_count}")
    
    return avg_time, row_count


def main():
    """Run benchmarks on available PDF files."""
    data_dir = Path(__file__).parent.parent / "data" / "raw"
    
    test_configs = [
        ("all.pdf", "incoming"),
        ("all2.pdf", "incoming"),
        ("out.pdf", "outgoing"),
    ]
    
    total_time = 0
    total_messages = 0
    
    print("\n" + "="*60)
    print("BENCHMARK DE PERFORMANCE - Extraction SWIFT")
    print("="*60)
    
    for filename, direction in test_configs:
        pdf_path = data_dir / filename
        if not pdf_path.exists():
            print(f"\n⚠ Fichier non trouvé: {filename}")
            continue
        
        avg_time, msg_count = benchmark_file(pdf_path, direction, runs=3)
        total_time += avg_time
        total_messages += msg_count
    
    print(f"\n{'='*60}")
    print("RÉSUMÉ GLOBAL")
    print(f"{'='*60}")
    print(f"  Temps total: {total_time:.3f}s")
    print(f"  Messages totaux: {total_messages}")
    print(f"  Débit moyen: {total_messages/total_time:.1f} messages/seconde")
    print(f"\n✓ Optimisations appliquées:")
    print(f"  • Regex pré-compilés au niveau module")
    print(f"  • frozenset pour lookups O(1)")
    print(f"  • Imports optimisés au top level")
    print(f"  • Patterns regex optimisés")
    print(f"  • Réduction des recherches répétées")


if __name__ == "__main__":
    main()
