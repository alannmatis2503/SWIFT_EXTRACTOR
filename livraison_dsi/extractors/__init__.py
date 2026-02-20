"""
=============================================================================
 Package « extractors »
=============================================================================
 Ce package regroupe les modules d'extraction spécialisés pour chaque type
 de message SWIFT :

   - mt103.py   : Messages MT103 (virements clients)
   - mt202.py   : Messages MT202 (virements interbancaires)
   - mt910.py   : Messages MT910 (confirmations de crédit)
   - mt900.py   : Messages MT900 (confirmations de débit)
   - mt_multi.py : Dispatcher multi-messages (découpe un PDF contenant
                   plusieurs messages et répartit vers les extracteurs)
   - bic_utils.py : Utilitaires de résolution BIC → nom de banque

 Chaque extracteur expose une fonction `extract_block(text, source)` qui
 prend un bloc de texte brut issu d'un PDF et retourne un dictionnaire
 normalisé contenant les champs extraits (référence, montant, devise, etc.).
=============================================================================
"""
