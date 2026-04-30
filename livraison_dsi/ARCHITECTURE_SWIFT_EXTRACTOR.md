**Architecture Technique\
Application SWIFT Extractor**

Version 6.1 --- 29 avril 2026

# Table des Matières

1.  1\. Vue d\'ensemble

2.  2\. Architecture en couches

3.  3\. Flux de traitement

4.  4\. Composants détaillés

5.  5\. Stack technique

6.  6\. Règles métier implémentées

7.  7\. Options de déploiement

8.  8\. Caractéristiques techniques

9.  9\. Points d\'attention pour le SI

10. 10\. Conclusion

# 1. Vue d\'ensemble

L\'application SWIFT Extractor est une solution web développée en Python
pour l\'extraction automatisée et l\'analyse de messages SWIFT depuis
des fichiers PDF. Elle fonctionne en 5 modes : messages entrants,
sortants, analyse des transferts exécutés, rapprochement MT950, et
extraction de relevés de compte Excel.

## Objectifs

-   Automatiser l\'extraction de données depuis les exports PDF SWIFT

-   Structurer les informations dans des fichiers Excel multi-feuilles

-   Classifier automatiquement les messages selon des règles métier

-   Faciliter l\'analyse et le rapprochement des opérations bancaires

-   Rapprocher les écritures MT950 avec les messages SWIFT

-   Traiter les relevés de compte Excel des correspondants (BDF, CITI,
    Standard Chartered)

## Types de messages traités

  -----------------------------------------------------------------------
  **Type**                **Description**         **Modes**
  ----------------------- ----------------------- -----------------------
  MT103                   Virement client         Entrants, Sortants,
                                                  Analyse, MT950

  MT202/COV               Virement interbancaire  Entrants, Sortants,
                                                  Analyse, MT950

  MT910                   Confirmation de crédit  Entrants, MT950

  MT900                   Confirmation de débit   Analyse transferts
                                                  uniquement

  MT950                   Relevé de compte        Rapprochement MT950
                                                  uniquement

  Excel                   Relevés de compte Excel Extraction Excel
  (BDF/CITI/Standard)     des correspondants      uniquement
  -----------------------------------------------------------------------

# 2. Architecture en couches

L\'application suit une architecture en couches classique pour garantir
la séparation des responsabilités et la maintenabilité du code.

## Diagramme d\'architecture

┌──────────────────────────────────────────────────────┐\
│ COUCHE PRÉSENTATION --- app.py │\
│ (Interface Streamlit --- port 8501) │\
│ Upload PDF │ Modes │ Filtre dates │ Download │\
└──────────────────────┬───────────────────────────────┘\
│\
┌──────────────────────▼───────────────────────────────┐\
│ COUCHE MÉTIER --- extractor_manager.py │\
│ Dispatching │ Workbook Excel │ Matching MT900/MT950 │\
└──────────────────────┬───────────────────────────────┘\
│\
┌──────────────────────▼───────────────────────────────┐\
│ COUCHE EXTRACTION --- extractors/ │\
│ mt_multi │ mt202 │ mt103 │ mt910 │ mt900 │ mt950 │ excel_extractor │\
└──────────────────────┬───────────────────────────────┘\
│\
┌──────────────────────▼───────────────────────────────┐\
│ COUCHE DONNÉES --- data/bic_codes.xlsx │\
│ (Référentiel BIC --- annuaire des banques) │\
└──────────────────────────────────────────────────────┘

## 2.1 Couche Présentation

**Fichier principal : app.py**

Technologies : Streamlit, Interface web responsive en français

**Fonctionnalités :**

-   Upload de fichiers PDF (drag & drop, multi-fichiers)

-   Sélection du mode de traitement (entrants / sortants / analyse
    transferts / rapprochement MT950 / extraction Excel)

-   Prévisualisation multi-onglets des résultats Excel

-   Filtrage par plage de dates (modes standard)

-   Téléchargement des fichiers générés

-   Affichage des statistiques de traitement

-   Bouton Clear All pour réinitialiser

-   Upload de fichiers Excel (.xlsx) en mode extraction Excel

## 2.2 Couche Métier

**A. Dispatcher Principal --- extractors/mt_multi.py (\~1900 lignes)**

Responsabilités :

-   Extraction du texte brut depuis les PDFs (PyMuPDF prioritaire,
    pdfplumber en fallback)

-   Découpage du PDF en messages individuels via 6 heuristiques regex

-   Détection automatique du type de message SWIFT

-   Routage vers l\'extracteur spécialisé approprié

-   Application des règles métier et exceptions (voir documentation
    technique)

-   Enrichissement des données (résolution BIC → noms)

-   Retour de 8 listes classifiées

**B. Extracteurs Spécialisés**

  -----------------------------------------------------------------------
  **Fichier**             **Lignes**              **Rôle**
  ----------------------- ----------------------- -----------------------
  mt202.py                \~400                   Extraction MT202 +
                                                  utilitaires de base
                                                  (champs, montants,
                                                  dates)

  mt103.py                \~350                   Extraction MT103
                                                  (donneur F50F/F50K,
                                                  codes Trésor/CCF)

  mt910.py                \~200                   Extraction MT910
                                                  (donneur =
                                                  bénéficiaire)

  mt900.py                \~150                   Extraction MT900 (F21 =
                                                  clé de matching)

  mt950.py                \~280                   Parsing F61 + matching
                                                  4 critères

  excel_extractor.py      \~1178                  Extraction relevés
                                                  Excel (BDF, CITI,
                                                  Standard) + règles de
                                                  routage
  -----------------------------------------------------------------------

**C. Générateur Excel --- extractor_manager.py (\~1774 lignes)**

-   Génération de fichiers Excel multi-feuilles :

<!-- -->

-   Feuille principale (messages normaux)

-   Exceptions BEACCMCX091

-   Exceptions 323201 (MT202 entrants)

-   Autres exceptions (BC, EUR, nivellement, salle marchés)

-   BANQUE DE FRANCE, forex, Exceptions Correspondants

-   Feuilles par pays

-   Doublons potentiels

-   Feuilles par fichier source (mode debug)

<!-- -->

-   Mise en forme automatique (colonnes, dates, montants)

-   Hyperliens bidirectionnels entre feuilles

## 2.3 Couche Traitement

**A. Mapping BIC --- extractors/bic_utils.py**

-   Résolution codes BIC (8-11 caractères) → Nom institution

-   Codes Trésor (1001-6001) → Nom pays

-   Codes CCF 4 chiffres → Nom institution

-   Codes forex → Détection MT910 forex

-   Cache LRU (chargement Excel unique)

-   Source de données : data/bic_codes.xlsx

## 2.4 Couche Données

**Entrées :**

-   PDFs SWIFT : Messages exportés depuis systèmes bancaires

-   bic_codes.xlsx : Référentiel des codes BIC et institutions

-   Fichiers Excel (.xlsx) : Relevés de compte des correspondants (BDF,
    CITI, Standard)

**Sorties :**

-   Fichiers Excel (.xlsx) : Résultats structurés multi-feuilles

-   Logs : Traçabilité du traitement (logs/app.log)

# 3. Flux de traitement

## Étapes détaillées

**1. Upload PDF :** L\'utilisateur upload un ou plusieurs fichiers PDF
et sélectionne le mode

**2. Initialisation :** L\'interface Streamlit appelle
extract_dispatch() ou les fonctions MT950

**3. Extraction texte :** PyMuPDF extrait le texte brut (\~50× plus
rapide, fallback pdfplumber)

**4. Découpage :** Le texte est découpé en messages individuels via 6
heuristiques regex en cascade

**5. Détection type :** Pour chaque message, détection du type via
\"Identifier: fin.XXX\"

**6. Extraction :** Routage vers l\'extracteur spécialisé
(mt202/mt103/mt910/mt900)

**7. Structuration :** L\'extracteur retourne un dictionnaire avec les
champs structurés

**8. Résolution BIC :** Les codes BIC sont résolus en noms
d\'institutions et pays via bic_codes.xlsx

**9. Règles métier :** Application des règles d\'exception (BEACCMCX091,
BC, 323201, EUR, nivellement, forex, BDF, salle marchés, correspondants
BdF)

**10. Classification :** Le message est classé (normal, exception,
doublon)

**11. Agrégation :** Tous les messages traités sont agrégés dans les 8
listes

**12. Génération Excel :** Le générateur crée le fichier Excel
multi-feuilles

**13. Mise en forme :** Application des styles, formats de colonnes,
ajustements, hyperliens

**14. Retour UI :** Le fichier est retourné à l\'interface (bytes en
mémoire)

**15. Présentation :** Prévisualisation multi-onglets + bouton
téléchargement

# 4. Composants détaillés

## 4.1 Structure des fichiers

livraison_dsi/\
├── app.py ← Interface Streamlit\
├── extractor_manager.py ← Dispatching + Excel\
├── utils.py ← Logging\
├── requirements.txt ← Dépendances Python\
├── Dockerfile ← Conteneurisation\
├── data/\
│ └── bic_codes.xlsx ← Référentiel BIC\
├── extractors/\
│ ├── \_\_init\_\_.py\
│ ├── bic_utils.py ← Résolution BIC\
│ ├── mt103.py ← Extracteur MT103\
│ ├── mt202.py ← Extracteur MT202\
│ ├── mt900.py ← Extracteur MT900\
│ ├── mt910.py ← Extracteur MT910\
│ ├── mt950.py ← Parseur MT950 + matching\
│ └── mt_multi.py ← Dispatcher multi-messages\
├── GUIDE_TECHNIQUE.md\
└── GUIDE_UTILISATEUR.md

## 4.2 Colonnes extraites

  -----------------------------------------------------------------------
  **Colonne**                         **Description**
  ----------------------------------- -----------------------------------
  code_banque                         Code BIC de l\'institution

  date_reference                      Date valeur (DD/MM/YYYY)

  reference                           Référence transaction (F20)

  reference_origine                   Référence d\'origine (F21)

  type_MT                             Type SWIFT (fin.202, fin.103...)

  pays_iso3                           Code pays ISO3 ou BEAC

  Code du donneur d\'ordre            Code BIC du donneur d\'ordre

  donneur d\'ordre                    Nom du donneur d\'ordre

  Bénéficiaire                        Nom du bénéficiaire

  correspondant                       BIC correspondant

  montant                             Montant numérique

  devise                              Code devise ISO

  commentaires                        Commentaires métier

  source_pdf                          PDF source (lien)
  -----------------------------------------------------------------------

# 5. Stack technique

## Environnement de développement

-   OS : Linux (Ubuntu 24.04 / WSL)

-   IDE : VS Code avec extensions Python

-   Gestion versions : Git + GitHub

-   Déploiement cloud : Hugging Face Spaces

## Dépendances principales

  -----------------------------------------------------------------------
  **Package**             **Version**             **Rôle**
  ----------------------- ----------------------- -----------------------
  streamlit               ≥1.52                   Framework web

  pandas                  ≥2.3                    Manipulation de données

  openpyxl                ≥3.1                    Fichiers Excel

  PyMuPDF                 ≥1.23                   Extraction PDF (\~50×
                                                  plus rapide)

  pdfplumber              ≥0.11                   Extraction PDF
                                                  (fallback)

  pdfminer.six            ≥20251107               Moteur bas niveau PDF

  python-dateutil         ≥2.9                    Parsing de dates
  -----------------------------------------------------------------------

# 6. Règles métier implémentées

L\'application intègre des règles métier spécifiques pour la
classification et le traitement. Voir la Documentation Technique
(sections 5.1 à 5.11) pour le détail exhaustif de chaque règle.

## 6.1 Exceptions MT202 sortants

-   BEACCMCX091 dans F52A → Exception (annulable si receiver =
    SCBLGB2LXXX/CITIGB2LXXX)

-   BC dans F21 → Exception salle des marchés (annulable)

## 6.2 Exceptions MT202 entrants

-   323201 dans F58A → Exception

## 6.3 Exceptions EUR

-   T2PI (TARGET2 Paiement Instantané) → intérêts

-   T2RM (TARGET2 Remboursement) → remboursement

-   T2PL (TARGET2 Prélèvement) → placement

## 6.4 Exceptions MT910

-   Nivellement (NIVLT/5175)

-   BEACCMCX091 dans F50A/F52A

-   Forex (code dans liste bic_codes.xlsx/forex ; fallback F50A si
    correspondant CITIUS33 + devise USD)

-   Salle des marchés IBAN (FR7630001000640000005169558)

-   **Exception BC — MT910 entrant Standard Chartered** (nouveauté v6.1) :
    pour les MT910 entrants depuis `SCBLGB2L*`, si la référence d'origine (F21)
    se termine par `BC`, le message est routé vers `Autres_Exceptions` avec le
    commentaire `"exception BC"`. Implanté dans `extractors/eastnet_extractor.py`
    (RJE Standard) ; logique équivalente déjà active en PDF via la règle
    salle des marchés.

## 6.5 Exception BANQUE DE FRANCE

-   MT103 USD avec `BANQUE DE FRANCE` ou `FW021083459` dans **F53A, F54A,
    F55A ou F57A** (F55A ajouté en v6.1 — Third Reimbursement Institution)

## 6.5bis Exception opérations DN (nouveauté v6.1)

-   Type : MT103 entrant uniquement

-   Correspondant : sender BIC commençant par `CITIUS33` (CITI USD)

-   Condition : le BIC11 normalisé du Receiver Institution figure dans la
    whitelist BEAC « Directions Nationales » (7 BIC11) :
    `BEACCMCX100`, `BEACCMCX090`, `BEACGQGQXXX`, `BEACGALIXXX`,
    `BEACCFCFXXX`, `BEACCGCGXXX`, `BEACTDNDXXX`

-   Action : routage vers `other_exceptions_rows` avec commentaire
    `"opérations DN"`

-   Note : la centrale `BEACCMCX091` (DOF) est volontairement exclue de
    la whitelist

-   Normalisation BIC11 : pour les fichiers RJE, le bloc 1 contient un
    LT12 (BIC8 + LT_id + branch3) ; le helper `_get_block1_bic11()`
    reconstitue le BIC11 = `BIC8 + branch3` avant comparaison

## 6.6 Extraction commentaires

-   MT202 : F72 « Texte descriptif: » (toutes occurrences, jointes par
    /)

-   MT103 : F70 contenu complet

-   MT910 : F72 après « Info émetteur -- destinataire » jusqu\'à Block 5

-   Règle de non-écrasement : les commentaires d\'exception ne sont pas
    remplacés

## 6.7 Priorités extraction donneur d\'ordre

**MT202/MT910 entrants :**

-   Priorité 1 : Code BIC dans F52A

-   Priorité 2 : Code 4 chiffres dans F52D → Résolution via colonne
    Reglement

-   Priorité 3 : Code BIC dans F52D

-   Priorité 4 : Nom extrait de F52D

**MT103 entrants :**

-   Priorité 1 : Code BIC dans F50K/F50F

-   Priorité 2 : Nom après \"Number: Numéro: 1/Details: Détails:\" dans
    F50F

-   Priorité 3 : Fallback nom extrait par parser

**MT103 sortants (non-BdF) :**

-   Priorité 1 : Codes Trésor (1001-6001) dans F50F

-   Priorité 2 : Code BIC dans F52A

-   Priorité 3 : Codes CCF 4 chiffres dans F50F

-   Priorité 4 : Nom après \"Details: Détails:\" dans F50F

-   SCBLGB2LXXX : CITIUS33 dans F57A ET 36357124 dans F58A → feuille
    Exceptions_Correspondants

-   CITIGB2LXXX : BIC Banque de France dans F57A → feuille
    Exceptions_Correspondants

## 6.9 Exception Correspondants BdF (MT202 sortant)

-   MT202 sortants CITI/SCB : donneur d\'ordre = bénéficiaire (copié
    depuis F58A)

-   MT202 entrants BdF : Fallback F58A si F52A non mappé (P1=BIC strict,
    P2=code 4 chiffres → Reglement)

-   MT103 sortants BdF : Priorités inversées --- P2=Nom (Details F50F),
    P3=CCF, P4=BIC (F52A)

-   MT103 entrants BdF : PAYS rempli depuis le RECEIVER BIC (quand le
    sender est BDFEFRPPCCT/BDFEFRPPSRD)

## 6.8 Règles Banque de France (BdF)

## 6.10 Rapprochement MT950

-   4 critères cumulatifs : montant (±0.011), identification_code,
    date_valeur, référence

-   Extraction Excel : traitement automatisé des relevés BDF, CITI et
    Standard Chartered

-   Entrants : match exact sur ref_serv (après //) vs F20

-   Sortants : match préfixe sur ref_owner vs F20

-   Tout le pool de messages (y compris exceptions) est utilisé

## 6.11ter Règle BEAC stricte F50A + fallback F50A pour MT910 entrants RJE (28 avril 2026)

Règle **strictement spécifique au format RJE** (n'affecte pas le flux
PDF). Pour les MT910 entrants extraits d'un fichier RJE, deux
traitements sont appliqués :

**1. Exception BEAC — critère strict F50A.** Le routage vers la feuille
`Exceptions_BEACCMCX091` est déclenché si la chaîne `BEACCMCX091` est
trouvée dans :

-   `code_donneur_dordre` (extrait de F52A/F52D — règle historique), **ou**
-   le tag `:50A:` (Ordering Customer)

Le tag `:25P:` n'est **pas** examiné : il contient presque
systématiquement `BEACCMCX091` sur le flux CITI USD, ce qui produirait
des faux positifs (notamment des messages dont le donneur réel est
une banque commerciale autre que la BEAC, p.ex. BGFIBANK CONGO).

**2. Fallback F50A pour le donneur et le pays.** Si F50A contient un BIC
valide ≠ `BEACCMCX091` et que le message n'a pas déjà été enrichi via
F52A/F52D, ce BIC est résolu dans `bic_codes.xlsx` pour remplir
(sans écrasement) `code_donneur_dordre`, `donneur_dordre`,
`beneficiaire` et `pays_iso3`.

**Impact mesuré** (CITI USD entrants 14/01/2025, 7 MT910 ciblés) :

-   3 MT910 vont en exception BEAC (F50A = `BEACCMCX091`)
-   3 MT910 à F52A=`ECOCGALI` restent au flux principal sans changement
-   1 MT910 (`C0050135966101`) reste au flux principal et est enrichi
    par le fallback F50A : donneur `BGFIBANK CONGO`, pays `CGO`

Les MT202 entrants ne sont pas affectés.

**Localisation** : `hf_spaces/extractors/eastnet_extractor.py`,
fonction `_process_single_message`, étape 9 (classification). Le
fallback s'appuie sur `bic_utils.get_name_for_code` et
`bic_utils.map_code_to_country`.

## 6.11bis Pipeline RJE entrant — MT202 (mise à jour 28 avril 2026)

Pour les fichiers **RJE EastNet** (mode « 📦 Archive EastNet »), le
pipeline d\'extraction des MT202 entrants a été ajusté pour préserver
les BIC bruts non mappés et exploiter systématiquement F58A en
fallback :

-   **Désactivation du post-traitement strict** :
    `_postprocess_row_for_202_103` (calibré pour le texte verbose des
    PDFs SWIFTRef) est SKIP pour les RJE entrants. Cette fonction
    effaçait les BIC F52A absents du référentiel `bic_codes.xlsx`, ce
    qui était inadapté pour le format RJE où le BIC brut est une
    information précieuse.

-   **Extraction F52A simplifiée** : `_extract_f52a_donneur` est utilisée
    seule. Priorités : (1) BIC standard 8-11 caractères, (2) code
    sous-participant à 4 chiffres → table `Reglement`, (3) BIC brut
    préservé même si non mappé.

-   **Fallback F58A généralisé** : alors que sur les PDFs ce fallback
    est limité aux correspondants `CITIUS33` et `BdF`, il s\'applique
    désormais à TOUS les MT202 entrants RJE. Activation dès que F52A
    n\'a pas produit de code mappé OU est absent. Priorités F58A : (1)
    code 4 chiffres → Reglement, (2) BIC standard, (3) code CCF.

-   **Levée de la garde initiale** sur `_citius33_f58a_fallback` et
    `_bdf_f58a_fallback` : la condition `if not code_current: return
    row` a été retirée, permettant d\'activer le fallback aussi quand
    F52A n\'a rien fourni du tout.

**Exemples** (CITI USD entrant 14/01/2025) :

  Référence            F52A brut                  F58A brut                          Donneur final           Pays
  -------------------- -------------------------- ---------------------------------- ----------------------- -----
  C0050146573901       /00151343 SGCMCMCX         /1032310108115 SGCMCMCX            Société Générale CMR    CAM
  S065014314E501       ABOCCNBJXXX (non mappé)    /8414 SCAQCGCG                     Sino Congolaise         CGO
  S0650141C47601       BCMAFRPPXXX (non mappé)    /8512 UGABGALI                     Union Gabonaise         GAB

**Impact mesuré** (51 messages MT202 entrants) : 48/51 lignes avec
`code_donneur_dordre` rempli (vs ~0 avant correction), pays issu du
mapping BIC réel et non plus d\'une heuristique textuelle.

## 6.12 Extraction des relevés de compte Excel (Mode 5)

Ce mode permet de traiter les fichiers Excel (.xlsx) des correspondants
bancaires (BDF, CITI, Standard) au lieu des fichiers PDF SWIFT. Le
module extractors/excel_extractor.py (\~1178 lignes) implémente les
parseurs spécifiques à chaque format de relevé et les règles de routage
adaptées.

-   BDF : colonnes Date opération / Date valeur / Libellé / Réf.
    demandeur (F20) / Réf. client (F21) / Nom contrepartie / Débit /
    Crédit

-   CITI : colonnes Date valeur / Date relevé / Devise / Montant /
    Bénéficiaire-Remettant / Type / Réf. bancaire (F20) / Description /
    Détails paiement

-   Standard (SCBL) : colonnes Account / Currency / Date / Description /
    Withdrawal / Deposit / Balance

Règles de routage adaptées au format tabulaire :

-   BEACCMCX091 : détecté dans le texte Nom contrepartie (BDF) ou
    Bénéficiaire/Remettant (CITI/Standard)

-   EUR T2PI/T2RM/T2PL : mêmes règles que le mode PDF, appliquées sur la
    référence

-   Nivellement (NIVLT) : détecté dans la référence ou le libellé

-   Forex BDF : BEACCMCX091 + pattern /YYMM dans réf. client

-   Forex CITI/Standard : code BIC dans la liste forex de bic_codes.xlsx
    ; noms spécifiques pour CITI USD

-   BANQUE DE FRANCE : CITI sortants EUR avec « BANQUE DE FRANCE » dans
    le bénéficiaire

-   DE-Data Entry / INTEREST : CITI → exceptions (opérations internes)

-   Dédoublonnage BDF sortants : regroupement par réf., montant max
    conservé, autres → « frais et autres »

Sortie : deux fichiers Excel séparés (entrants et sortants), empaquetés
dans un fichier ZIP. Les mêmes onglets que le mode standard sont générés
pour chaque direction.

# 7. Options de déploiement

## 7.1 Serveur Interne (Recommandé)

**Configuration recommandée :**

-   OS : Ubuntu 22.04+ ou Debian 11+

-   Python : 3.10+

-   Proxy : Nginx (reverse proxy + HTTPS)

-   Firewall : Limiter accès au port 8501

-   Authentification : À implémenter selon besoins SI

-   Stockage : Disque local persistant

-   Logs : Rotation automatique via logrotate

-   Monitoring : Supervision CPU/RAM/disque

**Commande de lancement :**

streamlit run app.py \--server.port 8501 \--server.headless true

**Docker :**

docker build -t pdf-swift-extractor .\
docker run -d \--name pdf-extractor -p 8501:8501 \\\
\--restart unless-stopped pdf-swift-extractor

## 7.2 Hugging Face Spaces (Demo/Test)

-   URL : https://huggingface.co/spaces/Born237/SWIFT_EXTRACTOR

-   Statut : ✓ Déployé

-   ⚠️ Limitation : Pas de stockage persistant

-   Usage : Démonstration, tests externes

## 7.3 Local (Développement)

-   URL : http://localhost:8501

-   Usage : Tests, développement, formation utilisateurs

# 8. Caractéristiques techniques

## 8.1 Performance

-   Traitement multi-messages : 120+ messages par PDF

-   Extraction PDF : PyMuPDF (\~50× plus rapide que pdfplumber)

-   Cache BIC : Chargement unique du référentiel en mémoire

-   6 heuristiques de split en cascade pour robustesse

## 8.2 Sécurité

-   Aucune donnée stockée côté serveur --- traitement en mémoire

-   Pas d\'authentification native (à ajouter via reverse proxy)

-   Fichiers traités en RAM uniquement, non persistés

## 8.3 Fiabilité

-   Extraction robuste : 6 heuristiques de split, 5 stratégies de
    détection type

-   Fallback PDF : PyMuPDF → pdfplumber si échec

-   Gestion des formats hétérogènes (labels EN + FR)

-   Traçabilité complète via logs

## 8.4 Maintenabilité

-   Architecture modulaire (1 extracteur par type MT)

-   Référentiel BIC externe modifiable sans recompilation

-   Logs détaillés pour le débogage

# 9. Points d\'attention pour le SI

## 9.1 Sécurité réseau

-   Port 8501 à restreindre au réseau interne

-   HTTPS recommandé via reverse proxy Nginx

-   Authentification à implémenter (LDAP, SSO...)

## 9.2 Ressources serveur

-   RAM : 2 Go minimum recommandé

-   CPU : 2 cœurs minimum

-   Disque : 1 Go pour l\'application + logs

## 9.3 Sauvegarde et archivage

-   Sauvegarder le fichier bic_codes.xlsx (référentiel métier)

-   Archiver les logs applicatifs (rotation recommandée)

-   Versionner le code source (Git)

## 9.4 Maintenance applicative

-   Mise à jour BIC : modifier bic_codes.xlsx + redémarrer

-   Mise à jour code : remplacer fichiers + pip install -r
    requirements.txt + redémarrer

-   Supervision : surveiller logs/app.log pour erreurs

## 9.5 Scalabilité

-   Application mono-instance (suffisant pour usage actuel)

-   Si charge élevée : déployer plusieurs instances derrière un load
    balancer

-   Docker facilite la réplication

# 10. Conclusion

Le PDF SWIFT Extractor est une solution robuste et modulaire pour
l\'automatisation de l\'extraction et de l\'analyse des messages SWIFT.
Son architecture en couches permet une maintenance facilitée et une
évolution progressive des règles métier.

## Avantages clés

-   Extraction automatisée : gain de temps considérable vs. traitement
    manuel

-   Règles métier précises : 15+ exceptions différentes gérées
    automatiquement

-   Traçabilité complète : logs, hyperliens, doublons détectés

-   Rapprochement MT950 : 4 critères cumulatifs pour un matching fiable

-   Déploiement flexible : local, Docker, ou cloud

## Prochaines évolutions possibles

-   Authentification utilisateurs intégrée

-   Support de nouveaux types de messages SWIFT

-   API REST pour intégration avec d\'autres systèmes

-   Tableau de bord statistique (volumes, tendances)
