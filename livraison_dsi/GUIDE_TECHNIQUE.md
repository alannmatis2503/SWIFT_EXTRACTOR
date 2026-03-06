# Guide Technique — PDF SWIFT Extractor

**Version** : 5.0  
**Date** : 6 mars 2026  
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
7. [Découpage des messages (split)](#7-découpage-des-messages-split)
8. [Formats de messages SWIFT traités](#8-formats-de-messages-swift-traités)
9. [Règles métier — Filtrage initial](#9-règles-métier--filtrage-initial)
10. [Règles métier — Classification et exceptions](#10-règles-métier--classification-et-exceptions)
11. [Règles métier — Annulation des exceptions](#11-règles-métier--annulation-des-exceptions)
12. [Arbre de décision du routage](#12-arbre-de-décision-du-routage)
13. [Extraction du donneur d'ordre](#13-extraction-du-donneur-dordre)
14. [Extraction du bénéficiaire](#14-extraction-du-bénéficiaire)
15. [Extraction des commentaires](#15-extraction-des-commentaires)
16. [Extraction du correspondant](#16-extraction-du-correspondant)
17. [Détection des doublons](#17-détection-des-doublons)
18. [Mode Analyse des Transferts Exécutés](#18-mode-analyse-des-transferts-exécutés)
19. [Mode Rapprochement MT950](#19-mode-rapprochement-mt950)
20. [Référentiel BIC (bic_codes.xlsx)](#20-référentiel-bic-bic_codesxlsx)
21. [Structure Excel de sortie](#21-structure-excel-de-sortie)
22. [Installation et lancement en local](#22-installation-et-lancement-en-local)
23. [Déploiement Docker](#23-déploiement-docker)
24. [Configuration et variables d'environnement](#24-configuration-et-variables-denvironnement)
25. [Maintenance et mise à jour](#25-maintenance-et-mise-à-jour)
26. [Dépannage](#26-dépannage)

---

## 1. Vue d'ensemble

**PDF SWIFT Extractor** est une application web interne qui automatise l'extraction de données à partir de fichiers PDF contenant des messages SWIFT (MT103, MT202, MT910, MT900, MT950).

L'utilisateur dépose des fichiers PDF via une interface web, et l'application :
- Identifie automatiquement les types de messages SWIFT
- Extrait les données structurées (référence, montant, devise, donneur d'ordre, bénéficiaire…)
- Résout les codes BIC SWIFT en noms de banques grâce à un référentiel Excel
- Applique des règles métier de classification et de filtrage
- Génère un fichier Excel multi-feuilles prêt à l'emploi

### Modes de fonctionnement

| Mode | Description | Entrée | Sortie |
|------|-------------|--------|--------|
| **Messages entrants** | Extraction des MT202, MT103, MT910 reçus | 1+ PDF | Excel multi-feuilles |
| **Messages sortants** | Extraction des MT202, MT103 émis | 1+ PDF | Excel multi-feuilles |
| **Analyse transferts** | Rapprochement MT900 ↔ MT103/MT202 | 2× PDF | Excel multi-feuilles |
| **Rapprochement MT950** | Rapprochement écritures F61 ↔ messages | 2× PDF | Excel multi-feuilles |

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
│  Dispatching │ Workbook Excel │ Matching MT900/MT950 │ BIC  │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    COUCHE EXTRACTION                         │
│                     extractors/                              │
│                                                             │
│  mt_multi │ mt202 │ mt103 │ mt910 │ mt900 │ mt950 │ bic    │
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
[mt_multi._split_messages()] ─── Découpage en blocs-messages
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
[Post-traitement] ─── Résolution BIC, pays, exceptions, commentaires
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
- **Python 3.10+** (testé avec Python 3.12/3.13)

### Dépendances principales

| Package | Version | Rôle |
|---------|---------|------|
| `streamlit` | ≥1.52 | Framework web (interface utilisateur) |
| `pandas` | ≥2.3 | Manipulation de données tabulaires |
| `openpyxl` | ≥3.1 | Lecture/écriture de fichiers Excel (.xlsx) |
| `pdfplumber` | ≥0.11 | Extraction de texte depuis PDF (fallback) |
| `pdfminer.six` | ≥20251107 | Moteur bas niveau d'extraction PDF |
| `PyMuPDF` | ≥1.23 | Extraction PDF haute performance (~50× plus rapide) |
| `python-dateutil` | ≥2.9 | Parsing flexible de dates |
| `pillow` | ≥12.0 | Manipulation d'images (dépendance indirecte) |

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
│   ├── mt950.py              ← Parseur MT950 + rapprochement F61
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
| Sélection mode | Radio buttons : entrants / sortants / analyse / rapprochement MT950 |
| Filtrage dates | Plage de dates de début et fin (modes standard uniquement) |
| Affichage résultats | Tableau interactif Streamlit + prévisualisation multi-feuilles |
| Téléchargement | Bouton de téléchargement Excel |
| Session state | Persistance des résultats après rechargement |
| Clear All | Bouton de réinitialisation de tous les fichiers chargés |
| Déduplication en-têtes | Correction automatique des en-têtes dupliqués dans la prévisualisation |

### `extractor_manager.py` — Couche métier

| Fonction | Description |
|----------|-------------|
| `extract_dispatch(pdf_path, direction)` | Dispatch intelligent, retourne 7 valeurs (voir §6) |
| `extract_single(pdf_path, direction)` | Extraction d'un PDF à message unique |
| `create_workbook(...)` | Génération Excel multi-feuilles avec hyperliens bidirectionnels |
| `create_transfer_analysis_workbook(...)` | Excel spécifique analyse transferts (voir §18) |
| `create_mt950_reconciliation_workbook(...)` | Excel spécifique rapprochement MT950 (voir §19) |
| `match_mt900_with_transfers(...)` | Matching MT900 ↔ MT103/MT202 par références |
| `load_bic_mapping(xlsx_path)` | Chargement référentiel BIC en mémoire |

### `extractors/mt_multi.py` — Dispatcher multi-messages

Le module le plus complexe (~1750 lignes). Il assure :
- Le découpage de PDF multi-messages (6 heuristiques de split)
- Le routage vers l'extracteur spécialisé par type MT
- Le post-traitement complet (BIC, pays, exceptions, commentaires, correspondant)
- L'application de toutes les règles métier
- Le retour de 7 listes classifiées

### `extractors/mt202.py` — Extracteur MT202

Fournit les utilitaires de base réutilisés par tous les extracteurs :
- `get_field_block(text, label)` : extraction d'un bloc de champ SWIFT
- `parse_amount(s)` : conversion montant texte → float
- `parse_date_YYMMDD(s)` : conversion date YYMMDD → ISO
- `extract_text_from_pdf(path)` : extraction texte (PyMuPDF → pdfplumber fallback)
- `extract_transaction_reference(text, block4)` : référence robuste multi-formats

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
- Extraction du `sender_bic` utilisé comme `correspondant`

### `extractors/mt950.py` — Parseur MT950 et rapprochement

- Parsing des écritures F61 depuis les PDF MT950
- Normalisation des montants (format européen → float)
- Rapprochement F61 ↔ messages extraits (4 clés de match)
- Détail complet en §19

### `extractors/bic_utils.py` — Résolution BIC

- Cache LRU pour performance (chargement Excel unique)
- 6 dictionnaires en mémoire : BIC→nom, BIC→pays, règlement, CCF, forex
- Détection de codes BIC dans les champs SWIFT (F52A, F50F…)

---

## 6. Pipeline d'extraction

### `extract_dispatch()` — Point d'entrée

Signature :
```
extract_dispatch(pdf_path, direction="incoming")
    → (rows, beaccmcx091_rows, exception_323201_rows,
       other_exceptions_rows, banque_de_france_rows,
       forex_rows, missing_codes)
```

**7 valeurs retournées** :

| # | Variable | Contenu |
|---|----------|---------|
| 1 | `rows` | Messages normaux (feuille summary) |
| 2 | `beaccmcx091_rows` | Messages BEACCMCX091 (MT910) |
| 3 | `exception_323201_rows` | Exceptions 323201 (MT202 entrant, F58A) |
| 4 | `other_exceptions_rows` | EUR/nivellement/salle des marchés/BC/BEACCMCX091-MT202 |
| 5 | `banque_de_france_rows` | MT103 USD BANQUE DE FRANCE |
| 6 | `forex_rows` | MT910 entrants forex |
| 7 | `missing_codes` | `{"unmapped": set(), "empty": set()}` — codes BIC non résolus |

**Logique de dispatch** : Si `_split_messages()` retourne ≥ 2 blocs → `mt_multi.extract_messages_from_pdf()`. Sinon, fallback vers `extract_single()`.

---

## 7. Découpage des messages (split)

La fonction `_split_messages()` tente 6 heuristiques en cascade pour découper le texte brut du PDF en blocs-messages individuels :

| Priorité | Heuristique | Pattern regex | Condition de succès |
|:--------:|-------------|---------------|---------------------|
| 1 | En-têtes `Message N` | `^\s*Message\s+\d+\b` | ≥ 2 correspondances |
| 2a | Identifiant `fin.XXX` | `Identifier\s*[:\s]*fin\.\d{3}` | ≥ 2 correspondances |
| 2b | En-tête `Sender:` | `^Sender\s*:` | ≥ 2 correspondances |
| 3 | `Unique Message Identifier` | `^(?:Unique Message Identifier\|Message Identifier)\b` | ≥ 2 correspondances |
| 4 | Tokens `:20:` / `F20` | `(:20:\|\bF20[:\s])` | ≥ 2 blocs > 10 caractères |
| 5 | Séparateurs visuels | `^\s*(\*{3,}\|-{3,})\s*$` | ≥ 2 blocs |
| 6 | Underscores | `^\s*_{5,}\s*$` | ≥ 2 blocs |

**Fallback** : Si aucune heuristique ne donne ≥ 2 blocs, le texte entier est traité comme un seul message.

---

## 8. Formats de messages SWIFT traités

### MT202 — Virement interbancaire

| Champ SWIFT | Description | Mapping interne |
|-------------|-------------|-----------------|
| F20 | Transaction Reference Number | `reference` |
| F21 | Related Reference | `reference_origine` |
| F32A | Value Date / Currency / Amount | `date_reference`, `devise`, `montant` |
| F52A/F52D | Ordering Institution | `code_donneur_dordre`, `donneur_dordre` |
| F58A/F58D | Beneficiary Institution | `beneficiaire` (sortants uniquement) |
| F72 | Sender to Receiver Information | `commentaires` |

### MT103 — Virement client

| Champ SWIFT | Description | Mapping interne |
|-------------|-------------|-----------------|
| F20 | Transaction Reference Number | `reference` |
| F32A | Value Date / Currency / Amount | `date_reference`, `devise`, `montant` |
| F50F/F50K | Ordering Customer | `donneur_dordre` |
| F52A | Ordering Institution | `code_donneur_dordre` |
| F53A/F54A/F57A | Intermediary/Account With Institution | (détection BDF) |
| F59 | Beneficiary Customer | `beneficiaire` |
| F70 | Remittance Information | `commentaires` |

### MT910 — Confirmation de crédit

| Champ SWIFT | Description | Mapping interne |
|-------------|-------------|-----------------|
| F20 | Transaction Reference Number | `reference` |
| F21 | Related Reference | `reference_origine` |
| F25P/F25 | Account Identification | (détection nivellement / salle marchés) |
| F32A | Value Date / Currency / Amount | `date_reference`, `devise`, `montant` |
| F52A/F52D | Ordering Institution | `donneur_dordre` = `beneficiaire` |
| F72 | Sender to Receiver Information | `commentaires` |

### MT900 — Confirmation de débit

| Champ SWIFT | Description | Mapping interne |
|-------------|-------------|-----------------|
| F20 | Transaction Reference Number | `reference` |
| F21 | Related Reference (clé de matching) | `related_reference` |
| F32A | Value Date / Currency / Amount | `date_reference`, `devise`, `montant` |
| Header | Sender BIC (expéditeur) | `correspondant` (par défaut) |

### MT950 — Relevé de compte (Statement Message)

| Champ F61 | Description | Mapping interne |
|-----------|-------------|-----------------|
| ValueDate | Date valeur (YYMMDD) | `value_date` |
| DebitCreditMark | C (crédit) ou D (débit) | `cd` |
| Amount | Montant entre `#...#` | `amount` |
| IdentificationCode | Type de message (202, 910…) | `identification_code` |
| ReferenceForTheAccountOwner | Référence propriétaire | `ref_owner` |
| ReferenceOfTheAccountServicingInstitution | Référence institution (après `//`) | `ref_serv` |
| SupplementaryDetails | Détails complémentaires | `supplementary_details` |

---

## 9. Règles métier — Filtrage initial

### Filtre par types valides

En modes entrant/sortant, seuls les types de base suivants sont conservés :

```
Types acceptés : {202, 103, 910}
```

Les types `900`, `950`, `210`, `199`, etc. sont silencieusement ignorés. Le type `202.COV` est accepté (le type de base `202` est extrait).

### Filtre NAK (sortants uniquement)

| Paramètre | Valeur |
|-----------|--------|
| **Direction** | `outgoing` uniquement |
| **Zone inspectée** | Les 600 premiers caractères du bloc-message |
| **Pattern** | Le mot `NAK` (word boundary) |
| **Action** | Le message est entièrement ignoré (skip) |

**Explication** : Un message portant la mention NAK (Negative Acknowledgement) est un message rejeté par le réseau SWIFT. Il ne doit pas être traité.

### Filtrage par plage de dates

L'utilisateur peut définir une date de début et une date de fin dans l'interface. Le filtre porte sur le champ `date_reference` (format `YYYY-MM-DD`), comparé sous forme de chaîne. Seuls les messages dont la date est comprise dans l'intervalle (bornes incluses) sont conservés.

Ce filtre s'applique **uniquement** à la liste `rows` (messages normaux du summary). Les listes d'exceptions, BEAC, BDF, forex et doublons ne sont **pas** filtrées par date.

Ce filtre ne s'applique **pas** en mode Rapprochement MT950.

---

## 10. Règles métier — Classification et exceptions

Chaque message extrait est soumis à une série de tests séquentiels qui déterminent dans quelle liste il sera classé. Les tests sont appliqués dans l'ordre décrit ci-dessous. **Le premier test positif l'emporte**, sauf pour l'exception « salle des marchés IBAN » qui est évaluée en dernier.

### 10.1 Exception BANQUE DE FRANCE (MT103 USD)

| Paramètre | Valeur |
|-----------|--------|
| **Type** | `fin.103` uniquement |
| **Devise** | `USD` uniquement |
| **Champs inspectés** | F53A, F54A, F57A |
| **Patterns recherchés** | `BANQUE DE FRANCE` ou `FW021083459` |
| **Action** | Message routé vers `banque_de_france_rows` → onglet **BANQUE DE FRANCE** |

### 10.2 Exception BEACCMCX091 — MT202 sortant

| Paramètre | Valeur |
|-----------|--------|
| **Type** | `fin.202` (y compris `202.COV`) |
| **Direction** | `outgoing` uniquement |
| **Champ inspecté** | F52A |
| **Pattern** | `BEACCMCX091` dans F52A |
| **Action** | Message routé vers `other_exceptions_rows` → onglet **Autres_Exceptions** |
| **Annulation possible** | Oui (voir §11) |

### 10.3 Exception BC — Salle des marchés (MT202 sortant)

| Paramètre | Valeur |
|-----------|--------|
| **Type** | `fin.202` |
| **Direction** | `outgoing` uniquement |
| **Champ inspecté** | F21 (référence d'origine) |
| **Pattern** | `BC` dans la référence d'origine |
| **Action** | Commentaire `"opération salle des marchés"` + message routé vers `other_exceptions_rows` |
| **Annulation possible** | Oui (voir §11) |

### 10.4 Exception 323201 (MT202 entrant)

| Paramètre | Valeur |
|-----------|--------|
| **Type** | `fin.202` (y compris `202.COV`) |
| **Direction** | `incoming` uniquement |
| **Champ inspecté** | F58A |
| **Pattern** | `323201` dans F58A |
| **Action** | Message routé vers `exception_323201_rows` → onglet **Exceptions_323201** |

### 10.5 Exceptions EUR — T2PI / T2RM / T2PL

Ces exceptions s'appliquent **uniquement si la devise est EUR**.

| Sous-règle | Pattern dans la référence (F20) | Direction | Commentaire forcé |
|-----------|-------------------------------|-----------|-------------------|
| T2PI | `T2PI` | Entrant **et** sortant | `"intérêts"` |
| T2RM | `T2RM` | Entrant uniquement | `"remboursement"` |
| T2PL | `T2PL` | Sortant uniquement | `"placement"` |

**Action** : Commentaire forcé + message routé vers `other_exceptions_rows` → onglet **Autres_Exceptions**.

### 10.6 Exception nivellement (MT910)

| Direction | Sous-règle | Champ inspecté | Pattern recherché |
|-----------|-----------|----------------|-------------------|
| Entrant | Règle 1 | Référence (F20) | `NIVLT` |
| Entrant | Règle 2 | F25P ou F25 | `5175` |
| Sortant | Règle 1 | F53B ou F53 | `5175` |
| Sortant | Règle 2 | F58A ou F58 | `5175` |

**Type** : `fin.910` uniquement.  
**Action** : Commentaire `"nivellement"` + message routé vers `other_exceptions_rows`.

### 10.7 Exception BEACCMCX091 — MT910

| Paramètre | Valeur |
|-----------|--------|
| **Type** | `fin.910` |
| **Champs inspectés** | F50A puis F52A |
| **Pattern** | Code BIC `BEACCMCX091` dans `IdentifierCode` |
| **Action** | Message routé vers `beaccmcx091_rows` → onglet **BEACCMCX091** |

### 10.8 Exception forex (MT910 entrant)

| Paramètre | Valeur |
|-----------|--------|
| **Type** | `fin.910` |
| **Direction** | `incoming` uniquement |
| **Condition (règle 1)** | Les 8 premiers caractères du `code_donneur_dordre` figurent dans la liste des codes forex |
| **Condition (règle 2)** | Si la règle 1 échoue ET que le correspondant commence par `CITIUS33` ET que la devise est `USD` : on cherche les codes forex dans le champ **F50A** du message |
| **Source des codes forex** | Feuille `forex` de `bic_codes.xlsx` (colonne A, à partir de la ligne 2) |
| **Action** | Commentaire `"forex"` + message routé vers `forex_rows` → onglet **forex** |

> **Détail règle 2 (CITIUS33 + USD)** : La correspondance est faite par recherche de sous-chaîne — chaque code forex (8 caractères) est cherché dans le texte brut du champ F50A. Si un code est trouvé, le message est routé vers l'onglet forex. Le donneur d'ordre extrait de F52A est conservé tel quel.

### 10.9 Exception salle des marchés — IBAN (MT910 entrant)

| Paramètre | Valeur |
|-----------|--------|
| **Type** | `fin.910` |
| **Direction** | `incoming` uniquement |
| **Champ inspecté** | F25P ou F25 |
| **Pattern** | IBAN `FR7630001000640000005169558` |
| **Action** | Commentaire `"opération salle des marchés"` + message routé vers `other_exceptions_rows` |

> **Priorité** : Cette exception est la dernière évaluée. Elle ne s'applique **que si aucune autre exception n'a été déclenchée** pour ce message.

---

## 11. Règles métier — Annulation des exceptions

Certaines exceptions peuvent être **annulées** lorsque le message est destiné à des correspondants particuliers. L'annulation est évaluée **avant** l'arbre de routage final.

### 11.1 Annulation BEACCMCX091 — MT202 sortant

| Paramètre | Valeur |
|-----------|--------|
| **Condition** | L'exception BEACCMCX091 MT202 sortant est active |
| **Annulation si** | `sender_bic` commence par `BEACCMCX` **ET** `receiver_bic` est `SCBLGB2LXXX` ou `CITIGB2LXXX` |
| **Effet** | Le message redevient normal → routé vers `rows` (summary) |

### 11.2 Annulation BC — Salle des marchés (MT202 sortant)

| Paramètre | Valeur |
|-----------|--------|
| **Condition** | L'exception BC (salle des marchés) est active |
| **Annulation si** | `receiver_bic` est `SCBLGB2LXXX` ou `CITIGB2LXXX` |
| **Effet** | Le message redevient normal, le commentaire `"opération salle des marchés"` est supprimé |

**Correspondants concernés** :
- `SCBLGB2LXXX` = Standard Chartered Bank (Londres)
- `CITIGB2LXXX` = Citibank (Londres)

---

## 12. Arbre de décision du routage

Voici l'ordre exact d'évaluation dans la fonction `extract_messages_from_pdf()`. **Le premier test positif détermine le routage** (sauf la règle salle des marchés IBAN qui est appliquée en dernier) :

```
Message extrait
    │
    ├─ [1] BANQUE DE FRANCE ?     → banque_de_france_rows    (onglet BANQUE DE FRANCE)
    │
    ├─ [1'] Exception correspondant BdF ? → bdf_corr_exception_rows (onglet Exceptions_Correspondants)
    │       (CITI: BdF dans F57A / SCB: CITIUS33+36357124)
    │
    ├─ [2] BEACCMCX091 MT202 ?    → other_exceptions_rows    (onglet Autres_Exceptions)
    │      (après test annulation)
    │
    ├─ [3] BC dans F21 ?          → other_exceptions_rows    (onglet Autres_Exceptions)
    │      (après test annulation)
    │
    ├─ [4] 323201 dans F58A ?     → exception_323201_rows    (onglet Exceptions_323201)
    │
    ├─ [5] EUR exception ?        → other_exceptions_rows    (onglet Autres_Exceptions)
    │      (T2PI / T2RM / T2PL)
    │
    ├─ [5'] Nivellement ?         → other_exceptions_rows    (onglet Autres_Exceptions)
    │
    ├─ [6] BEACCMCX091 MT910 ?    → beaccmcx091_rows         (onglet BEACCMCX091)
    │
    ├─ [7] Forex MT910 ?          → forex_rows               (onglet forex)
    │
    ├─ [8] Salle marchés IBAN ?   → other_exceptions_rows    (onglet Autres_Exceptions)
    │      (DERNIÈRE priorité)
    │
    └─ [9] Aucune exception       → rows                     (onglet summary)
```

---

## 13. Extraction du donneur d'ordre

L'extraction du donneur d'ordre suit un enchaînement de stratégies par priorité. La première stratégie qui retourne un résultat l'emporte.

### MT202 — Entrant et sortant

| Priorité | Source | Méthode |
|:--------:|--------|---------|
| 1 | F52A | Extraction du code BIC strict après `IdentifierCode:` → recherche dans `bic_codes.xlsx` |
| 2 | F52D | Code numérique à 4 chiffres (après `PartyIdentifier:`) → colonne `Reglement` de `bic_codes.xlsx` |
| 3 | F52D | Code BIC dans le texte (excluant les faux BIC) → recherche dans `bic_codes.xlsx` |
| 4 | F52D | Nom textuel extrait directement du champ |

### MT103 — Entrant

| Priorité | Source | Méthode |
|:--------:|--------|---------|
| 1 | F50K/F50F | Code BIC extrait → recherche dans `bic_codes.xlsx` |
| 2 | F50F | Nom après `Number: Numéro: 1/Details: Détails:` (ligne de détails) |
| 3 | F50F | Nom extrait par heuristique textuelle |

### MT103 — Sortant

| Priorité | Source | Méthode |
|:--------:|--------|---------|
| 1 | F50F | Code Trésor (1001–6001) → colonne `Reglement` de `bic_codes.xlsx` |
| 2 | F52A | Code BIC → recherche dans `bic_codes.xlsx` |
| 3 | F50F | Code CCF à 4 chiffres → colonne `CCF` de `bic_codes.xlsx` |
| 4 | F50F | Nom après `Details: Détails:` |
| 5 | - | Fallback : nom extrait par heuristique depuis F52A |

### MT910 — Entrant et sortant

Même logique que le MT202 (F52A → F52D), avec une spécificité importante :
- `beneficiaire` est **toujours identique** à `donneur_dordre` pour les MT910
- Le pays (`pays_iso3`) est **forcé** via `_fill_country_from_code_force()` (override du pays détecté par heuristique textuelle, car celle-ci produit des faux positifs pour les MT910)

### Règle spéciale CITIUS33 — Fallback F58A (MT202 et MT910 entrants)

Lorsque le correspondant (sender BIC) d'un message **entrant** commence par `CITIUS33` (Citibank USA), un mécanisme de fallback supplémentaire est activé :

| Condition | Description |
|-----------|-------------|
| **Type** | `fin.202` ou `fin.910` |
| **Direction** | `incoming` uniquement |
| **Correspondant** | Doit commencer par `CITIUS33` (8 premiers caractères) |
| **Déclenchement** | Le code BIC extrait de F52A **n'est pas** trouvé dans `bic_codes.xlsx` |

**Logique du fallback F58A/F58D** (par priorité) :

| Priorité | Source | Méthode |
|:--------:|--------|---------|
| 1 | F58A/F58D | Code sous-participant à 4 chiffres (après `PartyIdentifier:`) → colonne `Reglement` de `bic_codes.xlsx` |
| 2 | F58A/F58D | Code BIC standard → recherche dans `bic_codes.xlsx` |
| 3 | F58A/F58D | Code CCF à 4 chiffres → colonne `CCF` de `bic_codes.xlsx` |

Si un mapping est trouvé via ce fallback, il **remplace** le donneur d'ordre extrait de F52A.  
Pour les MT910, le bénéficiaire est également mis à jour (bénéficiaire = donneur d'ordre).

> **Explication métier** : Citibank USA agit comme banque correspondante. Le champ F52A peut contenir le BIC d'une banque locale qui n'est pas dans notre référentiel. Dans ce cas, F58A contient le code de la sous-participation (compte de règlement) ou un code CCF qui permet d'identifier le vrai donneur d'ordre.

### Règle spéciale Banque de France — Fallback F58A (MT202 entrants)

Lorsque le correspondant (sender BIC) d'un message **entrant** est un BIC Banque de France (`BDFEFRPPCCT` ou `BDFEFRPPSRD`), un mécanisme de fallback analogue à CITIUS33 est activé :

| Condition | Description |
|-----------|-------------|
| **Type** | `fin.202` |
| **Direction** | `incoming` uniquement |
| **Correspondant** | `BDFEFRPPCCT` ou `BDFEFRPPSRD` |
| **Déclenchement** | Le code BIC extrait de F52A **n'est pas** trouvé dans `bic_codes.xlsx` |

**Logique du fallback F58A/F58D** (par priorité) :

| Priorité | Source | Méthode |
|:--------:|--------|---------|
| 1 | F58A/F58D | Code BIC strict → recherche dans `bic_codes.xlsx` (excluant les faux BIC) |
| 2 | F58A/F58D | Code sous-participant à 4 chiffres → colonne `Reglement` de `bic_codes.xlsx` |

### Règle BdF — PAYS via RECEIVER (MT103 entrants)

Pour les MT103 entrants dont le correspondant (sender) est la Banque de France, la colonne PAYS est déterminée à partir du **BIC du RECEIVER** du message SWIFT (et non du donneur d'ordre).

| Condition | Description |
|-----------|-------------|
| **Type** | `fin.103` |
| **Direction** | `incoming` uniquement |
| **Correspondant** | `BDFEFRPPCCT` ou `BDFEFRPPSRD` |
| **Action** | `pays_iso3` rempli via le BIC RECEIVER → `bic_codes.xlsx` |

> **Explication métier** : Pour les MT103 entrants via BdF, le donneur d'ordre est souvent hors du référentiel BEAC. La BIC du Receiver (qui est une banque BEAC) est plus fiable pour déterminer le pays.

### Règle BdF — Inversion des priorités (MT103 sortants)

Pour les MT103 sortants dont le correspondant (receiver) est la Banque de France, l'ordre des priorités d'extraction du donneur d'ordre est modifié :

| Priorité | Source | Description |
|:--------:|--------|-------------|
| 1 | F50F | Codes Trésor (1001-6001) — identique au cas non-BdF |
| **2** | **F50F** | **Nom extrait de "Details: Détails:"** (normalement P4) |
| 3 | F50F | Codes CCF à 4 chiffres |
| **4** | **F52A** | **Code BIC** (normalement P2) |

> Pour les correspondants non-BdF, l'ordre reste : P1=Trésor, P2=F52A BIC, P3=CCF, P4=Details.

### Règle CITI/SCB — Donneur = Bénéficiaire (MT202 sortants)

Pour les MT202 sortants dont le receiver est `CITIGB2LXXX` ou `SCBLGB2LXXX`, le donneur d'ordre est forcé identique au bénéficiaire (extrait de F58A/F58D).

| Condition | Description |
|-----------|-------------|
| **Type** | `fin.202` |
| **Direction** | `outgoing` uniquement |
| **Receiver** | `CITIGB2LXXX` ou `SCBLGB2LXXX` |
| **Action** | `donneur_dordre = beneficiaire`, `code_donneur_dordre` = BIC du bénéficiaire |

### 10.10 Exception Correspondants BdF (MT202 sortant)

Des messages MT202 sortants vers CITI ou Standard Chartered sont routés vers la feuille **Exceptions_Correspondants** lorsque des conditions spécifiques sont remplies :

**Correspondant CITIGB2LXXX** :

| Paramètre | Valeur |
|-----------|--------|
| **Champ inspecté** | F57A |
| **Condition** | Contient un BIC Banque de France (`BDFEFRPPCCT` ou `BDFEFRPPSRD`) |
| **Action** | Commentaire + routage vers `bdf_corr_exception_rows` |

**Correspondant SCBLGB2LXXX** :

| Paramètre | Valeur |
|-----------|--------|
| **Champs inspectés** | F57A **ET** F58A |
| **Condition** | F57A contient `CITIUS33` **ET** F58A contient `36357124` (les deux conditions simultanées) |
| **Action** | Commentaire + routage vers `bdf_corr_exception_rows` |

### Filtrage des faux BIC

Les chaînes suivantes sont exclues de la détection BIC (faux positifs courants) : `CAMEROON`, `GABON`, `FRANCE`, `CENTRAL`, `INTERNATIONAL`, `COMMERCIAL`, `NATIONAL`, etc. Elles respectent la syntaxe d'un code BIC (8–11 lettres majuscules) mais n'en sont pas.

---

## 14. Extraction du bénéficiaire

| Type MT | Direction | Règle |
|---------|-----------|-------|
| `fin.202` | Entrant | **Toujours `None`** — pas de bénéficiaire exploitable |
| `fin.202` | Sortant | Extrait depuis **F58A** puis **F58D** : code BIC → `bic_codes.xlsx`, puis nom textuel (fallback) |
| `fin.103` | Entrant | **Toujours `None`** — le bénéficiaire client n'est pas pertinent |
| `fin.103` | Sortant | **Toujours `None`** |
| `fin.910` | Les deux | **Identique au donneur d'ordre** — pour un MT910, l'institution ordonnante (F52A) est à la fois le donneur et le bénéficiaire |

### Détail F58A/F58D (MT202 sortant)

1. **F58A** : Extraction du code BIC (`[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?`) → résolution via `bic_codes.xlsx`
2. **F58D** (fallback) :
   - Recherche d'un code BIC **avant** `NameAndAddress:`
   - Si absent, recherche d'un BIC dans les lignes `NameAndAddress:`
   - Si absent, prise du texte brut de `NameAndAddress:` (nom de la banque)
3. Filtrage des faux BIC (`_FALSE_BIC_WORDS`)

---

## 15. Extraction des commentaires

| Type MT | Champ source | Logique d'extraction |
|---------|-------------|---------------------|
| `fin.202` | **F72** | Toutes les occurrences de `Texte descriptif:` sur la même ligne, jointes par ` / ` |
| `fin.103` | **F70** | Contenu complet du champ F70, nettoyé (espaces multiples réduits à un seul) |
| `fin.910` | **F72** | Contenu après `Info émetteur – destinataire`, lu jusqu'à `Block 5` ou fin du bloc, lignes jointes par ` / ` |

**Règle de non-écrasement** : Si un commentaire a déjà été positionné par une règle d'exception (ex : `"opération salle des marchés"`, `"forex"`, `"nivellement"`, `"intérêts"`…), l'extraction F70/F72 ne l'écrase **pas**.

Les commentaires sont extraits aussi bien en mode **entrant** qu'en mode **sortant**.

---

## 16. Extraction du correspondant

Le champ `correspondant` identifie la contrepartie bancaire du message.

### Règle générale

| Direction | Valeur du correspondant |
|-----------|------------------------|
| `incoming` (entrant) | `sender_bic` — le BIC de l'expéditeur du message |
| `outgoing` (sortant) | `receiver_bic` — le BIC du destinataire du message |

### Extraction des BIC header

- **`sender_bic`** : regex sur `Sender` suivi d'un code BIC 8–11 caractères dans l'en-tête du message
- **`receiver_bic`** : regex sur `Receiver` suivi d'un code BIC 8–11 caractères dans l'en-tête du message

### Cas particulier MT900

En mode analyse des transferts, le `correspondant` du MT900 est initialement le `sender_bic`. Lors du matching avec un MT103/MT202, le correspondant du MT900 est **remplacé** par celui du MT103/MT202 associé (voir §18).

---

## 17. Détection des doublons

### Principe

Un MT910 (confirmation de crédit) peut être un doublon d'un MT202 (virement interbancaire) lorsque les deux portent la même référence et le même montant. L'application les détecte et les signale.

### Clé de déduplication

```
Clé = (reference.strip().upper(), float(montant))
```

- `reference` : champ F20, normalisé en majuscules sans espaces de début/fin
- `montant` : converti en float

### Condition de détection

Un doublon est détecté lorsque deux messages ont **la même clé** ET sont de **types croisés** :
- L'un est de type `fin.910` et l'autre de type `fin.202`, ou inversement

### Périmètre de recherche

La recherche de doublons s'effectue sur l'ensemble des messages, **toutes listes confondues** : `rows` + `beaccmcx091_rows` + `exception_323201_rows` + `other_exceptions_rows` + `banque_de_france_rows` + `forex_rows` + `bdf_corr_exception_rows`.

### Traitement

| Message | Action |
|---------|--------|
| **Premier** (déjà dans sa liste) | Reste dans sa liste d'origine. Son commentaire est préfixé de `"potentiel doublon"` |
| **Second** (le doublon détecté) | **Exclu** de la feuille summary. Reste dans l'onglet spécial auquel il appartient, le cas échéant |
| **Les deux** | Apparaissent ensemble dans l'onglet **Doublons_potentiels** |

---

## 18. Mode Analyse des Transferts Exécutés

### Principe

Ce mode permet de rapprocher les confirmations de débit (MT900) avec les virements émis (MT103/MT202) pour identifier les transferts exécutés, les suspens et les exceptions.

### Uploads

- **Zone 1** : Fichiers PDF contenant des MT900 (confirmations de débit)
- **Zone 2** : Fichiers PDF contenant des MT103/MT202 (virements sortants)

### Matching MT900 ↔ MT103/MT202

**Clé de matching** :
```
MT900.related_reference (F21) == MT103/MT202.reference (F20)
```
La comparaison est faite en **majuscules** (`.upper()`).

### Exceptions MT900

Avant le matching, certains MT900 sont écartés en exception :

| Pattern dans F20 | Pattern dans F21 | Exception |
|-------------------|-------------------|-----------|
| `T2PL` | — | `"T2PL"` (placement) |
| — | `NIVLT` ou `NIVELLEMENT` | `"nivellement"` |

### Résultat du matching

| Catégorie | Condition | Champs complétés |
|-----------|-----------|------------------|
| **Transferts exécutés** | MT900 rapproché avec un MT103/MT202 | `code_donneur_dordre`, `donneur_dordre`, `beneficiaire`, `pays_iso3`, `correspondant` copiés depuis le MT103/MT202 |
| **MT900 non rapprochés** | MT900 sans correspondant MT103/MT202 | Commentaire `"pas de correspondant MT103/MT202"`, correspondant = `sender_bic` |
| **Suspens** | MT103/MT202 sans confirmation MT900 | Commentaire `"suspens - pas de confirmation MT900"` |
| **Exceptions** | MT900 avec T2PL ou nivellement | Onglet séparé |

### Structure Excel

| Onglet | Contenu |
|--------|---------|
| `Transferts_Executes` | MT900 matchés avec les informations complétées du MT103/MT202, colonne supplémentaire « Message correspondant » |
| `MT900_non_rapproches` | MT900 sans correspondant trouvé |
| `Suspens` | MT103/MT202 sans confirmation MT900 |
| `Exceptions` | MT900 en exception (T2PL, nivellement) |

---

## 19. Mode Rapprochement MT950

### Principe

Ce mode rapproche les écritures F61 d'un relevé de compte MT950 avec les messages SWIFT (MT202, MT103, MT910) extraits de fichiers PDF séparés. Il permet de vérifier que chaque écriture du relevé correspond bien à un message et inversement.

### Uploads et sous-mode

- **Sous-mode** (sélecteur horizontal) :
  - **Entrants** : rapproche les écritures F61 de type **C** (crédit) avec les messages entrants
  - **Sortants** : rapproche les écritures F61 de type **D** (débit) avec les messages sortants
- **Zone 1** : Fichiers PDF MT950 (relevés de compte)
- **Zone 2** : Fichiers PDF messages (MT202/MT103/MT910)

### Parsing des écritures F61

Chaque écriture F61 est découpée depuis le texte brut par le pattern `F61: Ecriture`. Les champs suivants sont extraits par regex :

| Champ | Pattern | Exemple |
|-------|---------|---------|
| `cd` | `DebitCreditMark.*?:\s*([CD])` | `C` ou `D` |
| `amount` | `Amount:.*?#([\d.,]+)#` | `1.234.567,89` |
| `identification_code` | `IdentificationCode:.*?:\s*(\d+)` | `202`, `910` |
| `ref_owner` | `ReferenceForTheAccountOwner:.*?compte:\s*\n?\s*(.+?)` | Réf. propriétaire |
| `ref_serv` | `compte:\s*//(.+?)` | Réf. institution (après `//`) |
| `value_date` | `ValueDate:.*?valeur:\s*(\d{6})` | `260220` (YYMMDD) |
| `supplementary_details` | `SupplementaryDetails:.*?complémentaires:\s*\n?\s*(.+?)` | Texte libre |

Chaque écriture reçoit un **numéro séquentiel** (`f61_index`, 1-based) pour traçabilité.

### Normalisation des montants

Les montants au format européen sont convertis en float :
- Séparateur des milliers : point `.` → supprimé
- Séparateur décimal : virgule `,` → point `.`
- Montant tronqué (`303.100,`) → traité correctement
- Exemple : `1.234.567,89` → `1234567.89`

### Pool de messages pour le matching

**Tous** les messages extraits par `extract_dispatch()` sont combinés pour le matching, y compris ceux routés vers les onglets spéciaux (BEAC, forex, BDF, exceptions). Seuls les types `202`, `103`, `910` sont conservés (les MT900, MT950, MT210, etc. sont exclus).

### Clé de matching — 4 critères cumulatifs

Les 4 critères suivants doivent être **tous satisfaits simultanément** pour qu'un rapprochement soit établi :

| # | Critère | Condition | Tolérance |
|:-:|---------|-----------|-----------|
| 1 | **Montant** | `\|F61.amount − message.montant\|` | ≤ 0,011 |
| 2 | **Type / Code identification** | `F61.identification_code` contenu dans `message.type_MT` | Match partiel (ex : `202` dans `fin.202`) |
| 3 | **Date valeur** | `F61.value_date` (YYMMDD → `20YY-MM-DD`) = `message.date_reference` (premiers 10 caractères) | Exact |
| 4 | **Référence** | Dépend du sous-mode (voir ci-dessous) | Voir ci-dessous |

### Critère de référence : Entrants vs Sortants

| Sous-mode | Écritures | Référence F61 utilisée | Référence message | Type de comparaison |
|-----------|-----------|------------------------|-------------------|---------------------|
| **Entrants** | F61 `C` (crédit) | `ref_serv` (après `//`) | `reference` (F20) | **Match exact** |
| **Sortants** | F61 `D` (débit) | `ref_owner` | `reference` (F20) | **Match préfixe** : l'un commence par l'autre |

**Match préfixe** : La fonction `_refs_match(a, b)` retourne `True` si `a == b` ou `b.startswith(a)` ou `a.startswith(b)`. Cela couvre les cas où le relevé tronque la référence (ex : `8126/2412/` pour le message `8126/2412/CM`).

### Unicité du matching

Un message ne peut être rapproché qu'avec **une seule** écriture F61, et inversement. Les indices déjà utilisés sont suivis dans un set `used_msg_indices`.

### Structure Excel

| Onglet | Colonnes clés | Contenu |
|--------|--------------|---------|
| **Rapproches** | N° F61, MT950 C/D, Réf. Propriétaire, Réf. Institution, Date Valeur, Détails, Type MT, Référence, Date Référence, Montant, Devise, Code/Donneur d'ordre, Bénéficiaire, Pays ISO3, Correspondant, Commentaires, Source PDF | Écritures F61 matchées côte-à-côte avec les messages |
| **Msg_non_rapproches** | Colonnes standard messages | Messages sans écritures F61 correspondantes |
| **F61_non_rapproches** | N° F61, C/D, Réf. Propriétaire, Réf. Institution, Code Id., Montant, Date Valeur, Détails | Écritures F61 sans message correspondant |

### Hyperliens entre feuilles

| Onglet | Lien |
|--------|------|
| Rapproches | En-tête « Source PDF ➡ » pointe vers `Msg_non_rapproches` |
| Msg_non_rapproches | Dernière colonne « ⬅ Rapproches » pointe vers `Rapproches` |
| F61_non_rapproches | Dernière colonne « ⬅ Rapproches » pointe vers `Rapproches` |

---

## 20. Référentiel BIC (bic_codes.xlsx)

Le fichier `data/bic_codes.xlsx` est le référentiel central de l'application. Il contient l'annuaire des institutions financières et les listes de codes spéciaux.

### Feuille principale — Colonnes attendues

| Colonne | Obligatoire | Description | Exemple |
|---------|:-----------:|-------------|---------|
| Code BIC | ✅ | Code SWIFT 8 ou 11 caractères | `ECOCMLCX` |
| Noms | ✅ | Nom complet de l'institution | `ECOBANK CAMEROUN S.A.` |
| Pays | ✅ | Code pays ISO3 | `CMR` |
| Reglement | ❌ | Code de règlement à 4 chiffres | `8101` |
| CCF | ❌ | Code CCF long (XX.XXXXXX.X.XXXX…) | `10.311301.0.1401.0.0.0.0.0` |

### Feuille `forex`

| Colonne | Description | Exemple |
|---------|-------------|---------|
| A (à partir de la ligne 2) | Code BIC 8 caractères identifiant les correspondants forex | `ECOCGALI` |

### Dictionnaires construits au chargement

| Dictionnaire | Clé | Valeur | Usage |
|-------------|-----|--------|-------|
| `_BIC_MAP_CACHE` | BIC 8 chars | Nom institution | Résolution BIC → nom |
| `_BIC_FULLKEY_MAP` | BIC complet | Nom institution | Résolution BIC 11 chars |
| `_BIC_COUNTRY_MAP` | BIC 8 chars | Code pays ISO3 | Résolution BIC → pays |
| `_REGLEMENT_MAP` | Code 4 chiffres | `{name, country, bic}` | Donneur d'ordre F52D / Trésor |
| `_CCF_MAP` | CCF simplifié | `{name, country, bic, full_ccf}` | MT103 sortant codes CCF |
| `_FOREX_CODES` | — (set) | Code BIC 8 chars | Détection MT910 forex |

### Chaîne de recherche du fichier BIC

Le fichier est recherché dans cet ordre :
1. Chemin explicite passé en paramètre
2. Répertoires éditables (`PDF_SWIFT_DATA_DIR`, `PROGRAMDATA`, `LOCALAPPDATA`, `~/.pdf_swift_extractor/data`)
3. Bundle PyInstaller ou racine du projet (`*/data/`)
4. Chemins par défaut : `data/bic_codes.xlsx`, `data/bic.xlsx`, etc.

### Mise à jour

Pour ajouter une nouvelle banque au référentiel :
1. Ouvrir `data/bic_codes.xlsx` dans Excel
2. Ajouter une ligne avec les colonnes Code BIC, Noms, Pays
3. Sauvegarder et redémarrer l'application

> ⚠️ Le fichier est chargé en mémoire au démarrage. Tout ajout nécessite un redémarrage.

---

## 21. Structure Excel de sortie

### Mode standard (entrants / sortants)

| Onglet | Condition de création | Contenu |
|--------|----------------------|---------|
| `summary` | Toujours | Tous les messages normaux (excluant doublons secondaires) |
| `BEACCMCX091` | Si non vide | Messages BEACCMCX091 (MT910) |
| `Exceptions_323201` | Si non vide | Messages avec 323201 dans F58A |
| `Autres_Exceptions` | Si non vide | Exceptions EUR, nivellement, salle des marchés, BC, BEACCMCX091-MT202 |
| `BANQUE DE FRANCE` | Si non vide | MT103 USD avec BANQUE DE FRANCE / FW021083459 |
| `forex` | Si non vide | MT910 forex |
| `Exceptions_Correspondants` | Si non vide | MT202 sortants avec exceptions BdF correspondant (CITI/SCB) |
| `Doublons_potentiels` | Si doublons détectés | Paires MT910/MT202 même référence + montant |
| Par pays (`CMR`, `GAB`, `Operations_BEAC`…) | Si `pays_iso3` renseigné | Sous-ensemble par pays, trié par correspondant |
| Par message (nom du PDF) | Pour chaque message | Feuille clé/valeur détaillée (debug) |

> **Pays spécial** : Si le `donneur_dordre` contient `BEAC`, le `pays_iso3` est forcé à `"BEAC"` et la feuille pays est nommée `Operations_BEAC`.

### Colonnes du summary

| Colonne | Description |
|---------|-------------|
| `code_banque` | Code BIC de l'institution |
| `date_reference` | Date valeur (format DD/MM/YYYY dans Excel) |
| `reference` | Référence de la transaction (F20) |
| `reference_origine` | Référence d'origine (F21, MT202/MT910 uniquement) |
| `type_MT` | Type de message (`fin.202`, `fin.103`, `fin.910`…) |
| `pays_iso3` | Code pays ISO3 (`CMR`, `GAB`…) ou `BEAC` |
| `Code du donneur d'ordre` | Code BIC du donneur d'ordre |
| `donneur d'ordre` | Nom du donneur d'ordre |
| `Bénéficiaire` | Nom du bénéficiaire |
| `correspondant` | BIC du correspondant (sender ou receiver) |
| `montant` | Montant numérique |
| `devise` | Code devise ISO 4217 (`XAF`, `USD`, `EUR`…) |
| `commentaires` | Commentaires métier (F70/F72 ou exception) |
| `source_pdf` | Nom du fichier PDF source (lien cliquable vers la feuille détail) |

### Hyperliens bidirectionnels (mode standard)

- Chaque cellule `source_pdf` dans le summary est un lien cliquable vers la feuille de détail du fichier
- Chaque feuille de détail contient un lien « ⬅ Retour au summary » en première ligne
- Les feuilles d'exceptions contiennent aussi un lien retour vers le summary

### Feuilles par pays

Les feuilles par pays sont triées par `correspondant` puis `donneur_dordre`. Elles ne contiennent que les messages du summary ayant le `pays_iso3` correspondant.

---

## 22. Installation et lancement en local

### Prérequis

- **Python** 3.10 ou supérieur
- **pip** (inclus avec Python)

### Étape 1 : Créer un environnement virtuel

```bash
cd livraison_dsi/

python -m venv venv

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

## 23. Déploiement Docker

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

## 24. Configuration et variables d'environnement

| Variable | Description | Valeur par défaut |
|----------|-------------|-------------------|
| `PDF_SWIFT_DATA_DIR` | Répertoire contenant `bic_codes.xlsx` | `data/` (relatif) |
| `STREAMLIT_SERVER_PORT` | Port d'écoute Streamlit | `8501` |

L'application fonctionne sans aucune configuration. Les valeurs par défaut conviennent à la plupart des déploiements.

---

## 25. Maintenance et mise à jour

### Mise à jour du référentiel BIC

1. Modifier le fichier `data/bic_codes.xlsx`
2. Redémarrer l'application (ou le conteneur Docker)

### Mise à jour des codes forex

1. Ouvrir `data/bic_codes.xlsx`, feuille `forex`
2. Ajouter/retirer des codes BIC en colonne A
3. Redémarrer l'application

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

Format : `2026-02-23 14:30:05,123 [INFO] Message`

---

## 26. Dépannage

| Symptôme | Cause probable | Solution |
|----------|---------------|----------|
| « Impossible d'importer l'extracteur backend » | Dépendances manquantes | `pip install -r requirements.txt` |
| « Aucun fichier Excel trouvé » | `data/bic_codes.xlsx` absent | Vérifier la présence du fichier |
| Montants à 0 ou None | Format PDF inhabituel | Vérifier le PDF source manuellement |
| Pays ISO3 vide | Code BIC absent du référentiel | Ajouter le code à `bic_codes.xlsx` |
| Erreur PyMuPDF au lancement | Bibliothèque C non compilée | `pip install --force-reinstall PyMuPDF` |
| Port 8501 déjà utilisé | Autre instance active | `streamlit run app.py --server.port=8502` |
| Code BIC marqué « unmapped » | Code absent de `bic_codes.xlsx` | Ajouter le code dans le référentiel et redémarrer |
| MT210 ou MT950 non extraits | Types exclus en mode standard | Normal — seuls MT202/MT103/MT910 sont traités |
| Messages NAK dans les résultats | Mode entrant (NAK filtré sortant uniquement) | Normal — le filtre NAK n'opère qu'en mode sortant |
| Doublons inattendus | MT910 et MT202 avec même ref+montant | Vérifier l'onglet Doublons_potentiels |

---

*Document généré le 6 mars 2026 — PDF SWIFT Extractor v5.0*
