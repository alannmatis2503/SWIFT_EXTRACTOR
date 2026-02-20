"""
=============================================================================
 PDF SWIFT Extractor — Utilitaires (Logging)
=============================================================================
 Ce module configure le système de journalisation (logging) de l'application.

 Deux handlers sont créés :
   1. FileHandler   → écrit dans logs/app.log (persistant, encodé UTF-8)
   2. StreamHandler → affiche dans la console/terminal

 Le logger est nommé "pdf_extractor" et est réutilisé par tous les modules
 de l'application via :
     from utils import logger
     logger.info("Message de log")

 Niveau par défaut : INFO
 Format des messages : « 2026-02-10 14:30:05,123 [INFO] Message »
=============================================================================
"""

import logging
from pathlib import Path

# ── Répertoire des logs ─────────────────────────────────────────────────────
# Le dossier « logs/ » est créé automatiquement deux niveaux au-dessus de ce
# fichier si besoin (compatible avec la structure Hugging Face ou locale).
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Fichier de log principal
LOG_FILE = LOG_DIR / "app.log"


def setup_logger():
    """
    Initialise et retourne le logger principal de l'application.

    - Vérifie d'abord si des handlers existent déjà (évite les doublons
      lors de rechargements Streamlit).
    - Configure un FileHandler et un StreamHandler avec le même format.

    Returns:
        logging.Logger : instance du logger « pdf_extractor »
    """
    logger = logging.getLogger("pdf_extractor")

    # Éviter d'ajouter des handlers en double
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    # ── Handler fichier ─────────────────────────────────────────────────
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # ── Handler console ─────────────────────────────────────────────────
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    return logger


# Instanciation immédiate — tous les modules importent directement `logger`
logger = setup_logger()
