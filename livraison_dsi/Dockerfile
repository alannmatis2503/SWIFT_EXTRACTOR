# =============================================================================
# Dockerfile — Conteneurisation de l'application PDF SWIFT Extractor
# =============================================================================
# Ce fichier décrit l'image Docker nécessaire pour exécuter l'application
# dans un conteneur isolé. Compatible avec Docker, Podman, ou tout
# orchestrateur Kubernetes.
#
# Construction :
#   docker build -t pdf-swift-extractor .
#
# Exécution :
#   docker run -p 8501:8501 pdf-swift-extractor
#
# L'application sera accessible à http://localhost:8501
# =============================================================================

# ── Image de base ───────────────────────────────────────────────────────────
# Python 3.13 version « slim » (image légère basée sur Debian)
FROM python:3.13-slim

# ── Répertoire de travail dans le conteneur ─────────────────────────────────
WORKDIR /app

# ── Installation des dépendances système ────────────────────────────────────
# build-essential : nécessaire pour compiler certaines extensions C (ex: fitz)
# curl            : pour le healthcheck
# git             : pour certaines installations pip depuis GitHub
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# ── Installation des dépendances Python ─────────────────────────────────────
# On copie d'abord requirements.txt seul pour tirer parti du cache Docker
# (les dépendances ne sont réinstallées que si requirements.txt change)
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

# ── Copie de tout le code applicatif ────────────────────────────────────────
COPY app.py ./
COPY utils.py ./
COPY extractor_manager.py ./
COPY extractors/ ./extractors/
COPY data/ ./data/

# ── Création du répertoire de logs ──────────────────────────────────────────
RUN mkdir -p /app/logs

# ── Port exposé ─────────────────────────────────────────────────────────────
# Streamlit écoute par défaut sur le port 8501
EXPOSE 8501

# ── Healthcheck ─────────────────────────────────────────────────────────────
# Vérifie toutes les 30 secondes que l'application répond
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# ── Commande de démarrage ───────────────────────────────────────────────────
# Lance Streamlit en mode serveur, accessible depuis toutes les interfaces
ENTRYPOINT ["streamlit", "run", "app.py", \
    "--server.port=8501", \
    "--server.address=0.0.0.0", \
    "--server.headless=true"]
