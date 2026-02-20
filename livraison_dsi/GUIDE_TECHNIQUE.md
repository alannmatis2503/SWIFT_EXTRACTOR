# Guide Technique — PDF SWIFT Extractor

**Version** : 3.0  
**Date** : 20 juin 2025  
**Destinataires** : Direction des Systèmes d'Information (DSI)  
**Classification** : Document interne — Usage technique

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Architecture applicative](#2-architecture-applicative)
3. [Stack technologique](#3-stack-technologique)
4. [Arborescence du projet](#4-arborescence-du-projet)
5. [Description des modules](#5-description-des-modules)
6. [Pipeline d'extraction](#6-pipeline-dextraction)
7. [Formats de messages SWIFT traités](#7-formats-de-messages-swift-traités)
8. [Règles métier implémentées](#8-règles-métier-implémentées)
9. [Référentiel BIC (bic_codes.xlsx)](#9-référentiel-bic-bic_codesxlsx)
10. [Structure Excel de sortie](#10-structure-excel-de-sortie)
11. [Installation et lancement en local](#11-installation-et-lancement-en-local)
12. [Déploiement Docker](#12-déploiement-docker)
13. [Configuration et variables d'environnement](#13-configuration-et-variables-denvironnement)
14. [Maintenance et mise à jour](#14-maintenance-et-mise-à-jour)
15. [Dépannage](#15-dépannage)
16. [Diagrammes](#16-diagrammes)
17. [Changelog v3.0](#17-changelog-v30)

---

## 1. Vue d'ensemble

**PDF SWIFT Extractor** est une application web interne qui automatise l'extraction de données à partir de fichiers PDF contenant des messages SWIFT (MT103, MT202, MT910, MT900).

L'utilisateur dépose des fichiers PDF via une interface web, et l'application :
- Identifie automatiquement les types de messages SWIFT
- Extrait les données structurées (référence, montant, devise, donneur d'ordre, bénéficiaire…)
- Résout les codes BIC SWIFT en noms de banques grâce à un référentiel Excel
- Génère un fichier Excel multi-feuilles prêt à l'emploi

### Cas d'usage

| Mode | Description | Entrée | Sortie |
|------|-------------|--------|--------|
| Messages entrants | Extraction des MT202, MT103, MT910 reçus | PDF | Excel |
| Messages sortants | Extraction des MT202, MT103 émis | PDF | Excel |
| Analyse transferts | Rapprochement MT900 ↔ MT103/MT202 | 2× PDF | Excel |

---

## 2. Architecture applicative

L'application suit une architecture **3 couches** :

```
┌─────────────────────────────────────────────────────────────┐
│                    COUCHE PRÉSENTATION                       │
│                         app.py                              │
│              (Interface Streamlit — port 8501)              │
│                                                             │
│    Upload PDF │ Sélection mode │ Filtre dates │ Download    │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    COUCHE MÉTIER                             │
│                  extractor_manager.py                        │
│                                                             │
│    Dispatching │ Workbook Excel │ Matching MT900 │ BIC      │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    COUCHE EXTRACTION                         │
│                     extractors/                              │
│                                                             │
│  mt_multi.py │ mt202.py │ mt103.py │ mt910.py │ mt900.py   │
│                    bic_utils.py                              │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    COUCHE DONNÉES                            │
│                  data/bic_codes.xlsx                         │
│           (Référentiel BIC — annuaire des banques)          │
└─────────────────────────────────────────────────────────────┘
```

### Flux de données

```
PDF uploadé
    │
    ▼
[PyMuPDF / pdfplumber] ─── Extraction texte brut
    │
    ▼
[mt_multi._split_messages()] ─── Découpage en blocs
    │
    ▼
[mt_multi._detect_mt_type()] ─── Identification du type MT
    │
    ├── MT202 ──► mt202.extract_block()
    ├── MT103 ──► mt103.extract_block()
    ├── MT910 ──► mt910.extract_block()
    └── MT900 ──► mt900.extract_block()
    │
    ▼
[Post-traitement] ─── Résolution BIC, pays, exceptions
    │
    ▼
[create_workbook()] ─── Génération Excel
    │
    ▼
Fichier .xlsx téléchargeable
```

---

## 3. Stack technologique

### Langage
- **Python 3.10+** (testé avec Python 3.13)

### Dépendances principales

| Package | Version | Rôle |
|---------|---------|------|
| `streamlit` | 1.52.2 | Framework web (interface utilisateur) |
| `pandas` | 2.3.3 | Manipulation de données tabulaires |
| `openpyxl` | 3.1.5 | Lecture/écriture de fichiers Excel (.xlsx) |
| `pdfplumber` | 0.11.8 | Extraction de texte depuis PDF (fallback) |
| `pdfminer.six` | 20251107 | Moteur bas niveau d'extraction PDF |
| `PyMuPDF` | ≥1.23.0 | Extraction PDF haute performance (~50× plus rapide) |
| `python-dateutil` | 2.9.0 | Parsing flexible de dates |
| `pillow` | 12.0.0 | Manipulation d'images (dépendance indirecte) |

### Conteneurisation
- **Docker** (image de base `python:3.13-slim`)

---

## 4. Arborescence du projet

```
livraison_dsi/
├── app.py                    ← Point d'entrée (interface Streamlit)
├── extractor_manager.py      ← Chef d'orchestre (dispatching + Excel)
├── utils.py                  ← Logging applicatif
├── requirements.txt          ← Dépendances Python
├── Dockerfile                ← Conteneurisation Docker
├── data/
│   └── bic_codes.xlsx        ← Référentiel BIC (annuaire des banques)
├── extractors/
│   ├── __init__.py           ← Déclaration du package
│   ├── bic_utils.py          ← Résolution BIC → nom de banque
│   ├── mt103.py              ← Extracteur MT103 (virements clients)
│   ├── mt202.py              ← Extracteur MT202 (virements interbancaires)
│   ├── mt900.py              ← Extracteur MT900 (confirmations de débit)
│   ├── mt910.py              ← Extracteur MT910 (confirmations de crédit)
│   └── mt_multi.py           ← Dispatcher multi-messages
├── GUIDE_TECHNIQUE.md        ← Ce document
└── GUIDE_UTILISATEUR.md      ← Guide pour les utilisateurs finaux
```

---

## 5. Description des modules

### `app.py` — Interface utilisateur

| Responsabilité | Description |
|----------------|-------------|
| Upload fichiers | Zone de drag & drop pour fichiers PDF |
| Sélection mode | Radio buttons verticaux : entrants / sortants / analyse |
| Filtrage dates | Plage de dates de début et fin |
| Affichage résultats | Tableau interactif Streamlit + prévisualisation multi-feuilles |
| Téléchargement | Bouton de téléchargement Excel |
| Session state | Persistance des résultats après rechargement |
| Clear All | Bouton de réinitialisation de tous les fichiers chargés |
| Déduplication | Correction automatique des en-têtes dupliqués dans la prévisualisation |

### `extractor_manager.py` — Couche métier

| Fonction | Signature | Description |
|----------|-----------|-------------|
| `extract_dispatch` | `(pdf_path, direction) → tuple[6]` | Dispatch intelligent, retourne 6 listes (rows, beaccmcx091, exceptions_323201, autres_exceptions, banque_de_france, missing_codes) |
| `extract_single` | `(pdf_path, direction) → dict` | Extraction d’un PDF à message unique |
| `create_workbook` | `(rows, out_dir, ..., banque_de_france_rows) → Path` | Génération Excel multi-feuilles avec hyperliens bidirectionnels |
| `create_transfer_analysis_workbook` | `(...) → Path` | Excel spécifique analyse transferts |
| `match_mt900_with_transfers` | `(mt900_rows, transfer_rows) → tuple` | Matching par références avec copie du correspondant |
| `load_bic_mapping` | `(xlsx_path) → Dict` | Chargement référentiel BIC |
| `detect_message_type` | `(text) → str` | Détection type MT par regex |

### `extractors/mt202.py` — Extracteur MT202

Fournit les utilitaires de base réutilisés par tous les extracteurs :
- `get_field_block(text, label)` : Extrait un bloc de champ SWIFT
- `parse_amount(s)` : Convertit un montant texte → float
- `parse_date_YYMMDD(s)` : Convertit une date YYMMDD → ISO
- `extract_text_from_pdf(path)` : Extraction texte (PyMuPDF → pdfplumber fallback)
- `extract_transaction_reference(text, block4)` : Référence robuste multi-formats

### `extractors/mt103.py` — Extracteur MT103

Spécificités :
- Extraction du donneur d'ordre depuis F50F/F50K (clients, pas des banques)
- Gestion des codes Trésor (1001, 2001…) et codes CCF
- Champ F70 pour les commentaires (au lieu de F72)

### `extractors/mt910.py` — Extracteur MT910

Spécificités :
- Donneur d'ordre = bénéficiaire (même institution)
- Extraction des codes BIC à 11 caractères depuis les blocs header
- Champ « Expansion: » pour le nom de l'institution

### `extractors/mt900.py` — Extracteur MT900

Spécificités :
- Utilisé uniquement en mode « analyse des transferts »
- F21 (Related Reference) est la clé de matching avec les MT103/MT202
- Extraction du `sender_bic` (BIC de l’expéditeur) utilisé comme `correspondant`
- Lors du matching, le correspondant est remplacé par celui du MT202/MT103 associé

### `extractors/mt_multi.py` — Dispatcher multi-messages

Le module le plus complexe (~1700 lignes) :
- Découpage intelligent de PDF multi-messages (6 heuristiques)
- Routage vers l’extracteur spécialisé par type MT
- Post-traitement complet (BIC, pays, exceptions, commentaires, correspondant)
- Gestion de toutes les règles métier
- Retourne 6 valeurs : `(rows, beaccmcx091_rows, exception_323201_rows, other_exceptions_rows, banque_de_france_rows, missing_codes)`
- Annulation des exceptions BC/BEACCMCX091 pour receivers SCBLGB2LXXX et CITIGB2LXXX
- Collecte des MT103 USD BANQUE DE FRANCE dans une liste dédiée (au lieu de rejet)

### `extractors/bic_utils.py` — Résolution BIC

- Cache LRU pour performance (chargement Excel unique)
- 6 dictionnaires en mémoire : BIC→nom, BIC→pays, règlement, CCF, etc.
- Détection de codes BIC dans les champs SWIFT (F52A, F50F…)

---

## 6. Pipeline d'extraction

```
                        ┌──────────────────────┐
                        │   app.py reçoit le    │
                        │     fichier PDF       │
                        └──────────┬───────────┘
                                   │
                        ┌──────────▼───────────┐
                        │  extract_dispatch()   │
                        │  (extractor_manager)  │
                        └──────────┬───────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │                              │
            ┌───────▼────────┐            ┌───────▼────────┐
            │  1 seul message │            │ Multi-messages  │
            │                 │            │   (mt_multi)    │
            └───────┬────────┘            └───────┬────────┘
                    │                              │
            ┌───────▼────────┐            ┌───────▼────────┐
            │ extract_single()│            │ _split_messages()│
            └───────┬────────┘            └───────┬────────┘
                    │                              │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │  Post-traitement :           │
                    │  • Résolution BIC → nom      │
                    │  • Extraction bénéficiaire    │
                    │  • Détection exceptions       │
                    │  • Remplissage pays ISO3      │
                    │  • Extraction commentaires    │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │   create_workbook()          │
                    │   Génération du fichier Excel│
                    └─────────────────────────────┘
```

---

## 7. Formats de messages SWIFT traités

### MT202 — Virement interbancaire

| Champ SWIFT | Description | Mapping interne |
|-------------|-------------|-----------------|
| F20 | Transaction Reference Number | `reference` |
| F21 | Related Reference | `reference_origine` |
| F32A | Value Date / Currency / Amount | `date_reference`, `devise`, `montant` |
| F52A | Ordering Institution | `code_donneur_dordre`, `donneur_dordre` |
| F58A | Beneficiary Institution | `beneficiaire` (sortants) |
| F72 | Sender to Receiver Information | `commentaires` (entrants et sortants) |

### MT103 — Virement client

| Champ SWIFT | Description | Mapping interne |
|-------------|-------------|-----------------|
| F20 | Transaction Reference Number | `reference` |
| F32A | Value Date / Currency / Amount | `date_reference`, `devise`, `montant` |
| F50F/F50K | Ordering Customer | `donneur_dordre` |
| F52A | Ordering Institution | `code_donneur_dordre` |
| F59 | Beneficiary Customer | `beneficiaire` |
| F70 | Remittance Information | `commentaires` (entrants et sortants) |

### MT910 — Confirmation de crédit

| Champ SWIFT | Description | Mapping interne |
|-------------|-------------|-----------------|
| F20 | Transaction Reference Number | `reference` |
| F21 | Related Reference | `reference_origine` |
| F25P | Account Identification | (filtrage nivellement) |
| F32A | Value Date / Currency / Amount | `date_reference`, `devise`, `montant` |
| F52A | Ordering Institution | `donneur_dordre` = `beneficiaire` |

### MT900 — Confirmation de débit

| Champ SWIFT | Description | Mapping interne |
|-------------|-------------|-----------------|
| F20 | Transaction Reference Number | `reference` |
| F21 | Related Reference (clé de matching) | `related_reference` |
| F32A | Value Date / Currency / Amount | `date_reference`, `devise`, `montant` |
| Header | Sender BIC (expéditeur) | `correspondant` (par défaut) |

---

## 8. Règles métier implémentées

### Filtrage et classification

| Règle | Condition | Action |
|-------|-----------|--------|
| Types valides | Seuls MT202, MT103, MT910 | Autres types ignorés |
| BEACCMCX091 | Code dans F50A ou F52A | Stocké dans onglet séparé |
| BEACCMCX091 annulation | Sender BEACCMCX + receiver SCBLGB2LXXX ou CITIGB2LXXX | Exception annulée → traitement normal |
| 323201 dans F58A | MT202 entrant avec F58A contenant 323201 | Onglet `Exceptions_323201` |
| MT103 USD BANQUE DE FRANCE | F53A/F54A/F57A contient « BANQUE DE FRANCE » ou « FW021083459 » | Onglet dédié `BANQUE DE FRANCE` |
| EUR + T2PI | Devise EUR et T2PI dans référence | Exception « intérêts » |
| EUR + T2RM | Devise EUR et T2RM dans référence (entrant) | Exception « remboursement » |
| EUR + T2PL | Devise EUR et T2PL dans référence (sortant) | Exception « placement » |
| Nivellement | NIVLT dans référence ou 5175 dans F25P | Exception « nivellement » |
| Salle des marchés | BC dans F21 d'un MT202 sortant | Exception « salle des marchés » |
| Salle des marchés annulation | Receiver = SCBLGB2LXXX ou CITIGB2LXXX | Exception annulée → traitement normal |
| Doublons | MT910 et MT202 avec même référence et montant | Onglet `Doublons_potentiels` |
| Correspondant | MT202/MT103 → receiver_bic ; MT900 → sender_bic | Champ `correspondant` rempli automatiquement |

### Extraction du donneur d'ordre (par priorité)

**MT202 / MT103 sortants :**
1. F52A : Code BIC → mapping bic_codes.xlsx
2. F52D : Code à 4 chiffres (règlement) → mapping Excel
3. F52D : Code BIC → mapping Excel
4. F50F : Code Trésor (1001, 2001…) → mapping Excel
5. F50F : Code CCF → mapping Excel
6. F50F : Nom après « Details: Détails: »

**MT103 entrants :**
1. F50K/F50F : Code BIC → mapping Excel
2. F50F : Nom après « Details: Détails: »

---

## 9. Référentiel BIC (bic_codes.xlsx)

Le fichier `data/bic_codes.xlsx` est le référentiel central de l'application. Il contient l'annuaire des institutions financières.

### Colonnes attendues

| Colonne | Obligatoire | Description | Exemple |
|---------|:-----------:|-------------|---------|
| Code BIC | ✅ | Code SWIFT 8 ou 11 caractères | `ECOCMLCX` |
| Noms | ✅ | Nom complet de l'institution | `ECOBANK CAMEROUN S.A.` |
| Pays | ✅ | Code pays ISO3 | `CMR` |
| Reglement | ❌ | Code de règlement à 4 chiffres | `8101` |
| CCF | ❌ | Code CCF long (XX.XXXXXX.X.XXXX…) | `10.311301.0.1401.0.0.0.0.0` |

### Mise à jour

Pour ajouter une nouvelle banque au référentiel :
1. Ouvrir `data/bic_codes.xlsx` dans Excel
2. Ajouter une ligne avec les colonnes Code BIC, Noms, Pays
3. Sauvegarder et redémarrer l'application

> ⚠️ Le fichier est chargé en mémoire au démarrage. Tout ajout nécessite un redémarrage.

---

## 10. Structure Excel de sortie

### Mode standard (entrants / sortants)

| Feuille | Contenu |
|---------|---------|
| `summary` | Tous les messages extraits (vue principale) |
| `BEACCMCX091` | Messages BEACCMCX091 isolés |
| `Exceptions_323201` | Messages avec 323201 dans F58A |
| `Autres_Exceptions` | Exceptions EUR, nivellement, salle des marchés |
| `BANQUE DE FRANCE` | MT103 USD avec BANQUE DE FRANCE / FW021083459 |
| `Doublons_potentiels` | Doublons MT910/MT202 (même réf. + montant) |
| `CMR`, `GAB`, `TCD`… | Feuilles par pays ISO3 (triées par code_banque) |
| `nom_fichier.pdf` | Détail clé/valeur par fichier source (avec lien « ⬅ Retour au summary ») |

### Mode analyse des transferts

| Feuille | Contenu |
|---------|---------|
| `Transferts_Executes` | MT900 matchés avec infos MT103/MT202 (correspondant inclus) |
| `MT900_non_rapproches` | MT900 sans correspondant trouvé |
| `Suspens` | MT103/MT202 sans confirmation MT900 |
| `Exceptions` | MT900 en exception (T2PL, nivellement) |

### Colonnes du summary

| Colonne | Description |
|---------|-------------|
| `code_banque` | Code BIC de l'institution |
| `date_reference` | Date valeur (format DD/MM/YYYY dans Excel) |
| `reference` | Référence de la transaction (F20) |
| `reference_origine` | Référence d'origine (F21) |
| `type_MT` | Type de message (fin.202, fin.103, fin.910…) |
| `pays_iso3` | Code pays ISO3 (CMR, GAB…) |
| `Code du donneur d'ordre` | Code BIC du donneur d'ordre |
| `donneur d'ordre` | Nom du donneur d'ordre |
| `Bénéficiaire` | Nom du bénéficiaire |
| `correspondant` | BIC du correspondant (sender/receiver) |
| `montant` | Montant numérique |
| `devise` | Code devise ISO 4217 (XAF, USD, EUR…) |
| `commentaires` | Commentaires métier (F70/F72 ou exceptions) |
| `source_pdf` | Nom du fichier PDF source |

---

## 11. Installation et lancement en local

### Prérequis

- **Python** 3.10 ou supérieur ([python.org](https://www.python.org/downloads/))
- **pip** (gestionnaire de packages Python, inclus avec Python)
- **git** (optionnel, pour le versionnement)

### Étape 1 : Créer un environnement virtuel

```bash
# Se placer dans le répertoire du projet
cd livraison_dsi/

# Créer l'environnement virtuel
python -m venv venv

# Activer l'environnement virtuel
# Sur Linux / macOS :
source venv/bin/activate
# Sur Windows :
venv\Scripts\activate
```

### Étape 2 : Installer les dépendances

```bash
pip install -r requirements.txt
```

### Étape 3 : Vérifier le référentiel BIC

S'assurer que le fichier `data/bic_codes.xlsx` est présent et contient les codes BIC nécessaires.

### Étape 4 : Lancer l'application

```bash
streamlit run app.py
```

L'application démarre et est accessible à l'adresse :
```
http://localhost:8501
```

### Arrêter l'application

Appuyer sur `Ctrl+C` dans le terminal.

---

## 12. Déploiement Docker

### Construction de l'image

```bash
cd livraison_dsi/
docker build -t pdf-swift-extractor .
```

### Exécution du conteneur

```bash
docker run -d \
  --name pdf-extractor \
  -p 8501:8501 \
  --restart unless-stopped \
  pdf-swift-extractor
```

L'application est accessible à `http://<IP_SERVEUR>:8501`.

### Commandes utiles

```bash
# Voir les logs
docker logs -f pdf-extractor

# Arrêter le conteneur
docker stop pdf-extractor

# Redémarrer
docker restart pdf-extractor

# Supprimer le conteneur
docker rm -f pdf-extractor

# Reconstruire après mise à jour du code
docker build -t pdf-swift-extractor . && docker rm -f pdf-extractor && \
docker run -d --name pdf-extractor -p 8501:8501 --restart unless-stopped pdf-swift-extractor
```

### Monter un volume pour les données BIC

Pour pouvoir mettre à jour le fichier `bic_codes.xlsx` sans reconstruire l'image :

```bash
docker run -d \
  --name pdf-extractor \
  -p 8501:8501 \
  -v /chemin/sur/serveur/data:/app/data \
  --restart unless-stopped \
  pdf-swift-extractor
```

---

## 13. Configuration et variables d'environnement

| Variable | Description | Valeur par défaut |
|----------|-------------|-------------------|
| `PDF_SWIFT_DATA_DIR` | Répertoire contenant `bic_codes.xlsx` | `data/` (relatif) |
| `STREAMLIT_SERVER_PORT` | Port d'écoute Streamlit | `8501` |

L'application fonctionne sans aucune configuration. Les valeurs par défaut conviennent à la plupart des déploiements.

---

## 14. Maintenance et mise à jour

### Mise à jour du référentiel BIC

1. Modifier le fichier `data/bic_codes.xlsx`
2. Redémarrer l'application (ou le conteneur Docker)

### Mise à jour du code

1. Remplacer les fichiers sources modifiés
2. Réinstaller les dépendances si `requirements.txt` a changé :
   ```bash
   pip install -r requirements.txt
   ```
3. Redémarrer l'application

### Logs

Les logs sont écrits dans :
- **Fichier** : `logs/app.log`
- **Console** : sortie standard

Format : `2026-02-10 14:30:05,123 [INFO] Message`

---

## 15. Dépannage

| Symptôme | Cause probable | Solution |
|----------|---------------|----------|
| « Impossible d'importer l'extracteur backend » | Dépendances manquantes | `pip install -r requirements.txt` |
| « Aucun fichier Excel trouvé » | `data/bic_codes.xlsx` absent | Vérifier la présence du fichier |
| Montants à 0 ou None | Format PDF inhabituel | Vérifier le PDF source manuellement |
| Pays ISO3 vide | Code BIC absent du référentiel | Ajouter le code à `bic_codes.xlsx` |
| Erreur PyMuPDF au lancement | Bibliothèque C non compilée | `pip install --force-reinstall PyMuPDF` |
| Port 8501 déjà utilisé | Autre instance active | `streamlit run app.py --server.port=8502` |

---

## 16. Diagrammes

### Diagramme de séquence — Extraction standard

```
Utilisateur        app.py        extractor_manager    mt_multi        bic_utils
    │                │                  │                │                │
    │── Upload PDF ──►│                  │                │                │
    │── Clic Extraire►│                  │                │                │
    │                │── extract_dispatch►│                │                │
    │                │                  │── split_messages►│                │
    │                │                  │◄── blocs ──────┤                │
    │                │                  │   pour chaque bloc :            │
    │                │                  │── extract_block──►│                │
    │                │                  │◄── row dict ────┤                │
    │                │                  │── load_bic_mapping─────────────►│
    │                │                  │◄── mapping ─────────────────────┤
    │                │◄── rows ─────────┤                │                │
    │                │── create_workbook►│                │                │
    │                │◄── fichier.xlsx ──┤                │                │
    │◄── Download ───┤                  │                │                │
```

### Diagramme de séquence — Analyse des transferts

```
Utilisateur        app.py        extractor_manager    mt_multi
    │                │                  │                │
    │── Upload MT900 ►│                  │                │
    │── Upload MT103 ►│                  │                │
    │── Clic Extraire►│                  │                │
    │                │                  │                │
    │                │── extract_mt900_only──────────────►│
    │                │◄── mt900_rows ────────────────────┤
    │                │                  │                │
    │                │── extract_dispatch(outgoing) ────►│
    │                │◄── transfer_rows ────────────────┤
    │                │                  │                │
    │                │── match_mt900_with_transfers ────►│
    │                │   (F21 MT900 = F20 MT103/202)    │
    │                │◄── matched, suspens, exceptions ─┤
    │                │                  │                │
    │                │── create_transfer_analysis_workbook►│
    │                │◄── fichier.xlsx ──┤                │
    │◄── Download ───┤                  │                │
```

---

## 17. Changelog v3.0

### Nouvelles fonctionnalités

| Fonctionnalité | Description |
|----------------|-------------|
| **Feuille BANQUE DE FRANCE** | Les MT103 USD contenant « BANQUE DE FRANCE » ou « FW021083459 » dans F53A/F54A/F57A sont désormais collectés dans un onglet dédié au lieu d'être rejetés silencieusement |
| **Correspondant** | Nouveau champ `correspondant` : receiver_bic pour MT202/MT103, sender_bic pour MT900. Copié automatiquement dans Transferts_Executes lors du matching |
| **Commentaires sortants** | Les commentaires (F72 pour MT202/MT910, F70 pour MT103) sont désormais extraits aussi en mode sortant (pas seulement entrant) |
| **Tri des feuilles pays** | Les feuilles par pays (CMR, GAB, etc.) sont triées par code_banque puis donneur_dordre |
| **Bouton Clear All** | Bouton « 🗑️ Clear All » pour effacer tous les fichiers chargés d'un seul clic |
| **Disposition verticale** | Les radio buttons de sélection du mode sont affichés verticalement dans la zone principale (et non plus en sidebar) |
| **Hyperliens bidirectionnels** | Chaque cellule `source_pdf` dans le summary est cliquable et pointe vers la feuille de détail du fichier. Chaque feuille de détail contient un lien « ⬅ Retour au summary » |
| **Prévisualisation multi-feuilles** | L'interface affiche un aperçu de chaque feuille Excel avec sélecteur d'onglet |

### Corrections de bugs

| Bug | Correction |
|-----|------------|
| **En-têtes dupliqués** | La prévisualisation multi-feuilles pouvait générer des colonnes `['', '']` à cause de lignes vides après le lien « Retour au summary ». Corrigé par filtrage des lignes vides et dédoublement des en-têtes |

### Changements de règles métier

| Règle | Modification |
|-------|-------------|
| **MT103 USD BANQUE DE FRANCE** | Anciennement : message rejeté (ignoré). Nouveau : collecté dans un onglet dédié `BANQUE DE FRANCE` |
| **Exception salle des marchés (BC dans F21)** | Annulée si le receiver est SCBLGB2LXXX (Standard Chartered) ou CITIGB2LXXX (Citibank). Le message est alors traité normalement |
| **Exception BEACCMCX091** | Annulée si le sender commence par BEACCMCX et le receiver est SCBLGB2LXXX ou CITIGB2LXXX. Le message est alors traité normalement |
| **Renommage MT900_non_matches** | La feuille `MT900_non_matches` est renommée en `MT900_non_rapproches` |

### Changements techniques

| Composant | Modification |
|-----------|-------------|
| `extract_dispatch()` | Retourne désormais 6 valeurs (ajout de `banque_de_france_rows`) |
| `create_workbook()` | Nouveau paramètre `banque_de_france_rows` |
| `extract_messages_from_pdf()` | Retourne 6 valeurs, gère les annulations d'exceptions |
| `mt_multi.py` | ~1700 lignes (anciennement ~1600) |

---

*Document généré le 20 juin 2025 — PDF SWIFT Extractor v3.0*
