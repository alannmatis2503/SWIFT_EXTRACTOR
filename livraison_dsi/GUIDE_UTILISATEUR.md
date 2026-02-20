# Guide Utilisateur — PDF SWIFT Extractor

**Version** : 3.0  
**Date** : 20 juin 2025  
**Destinataires** : Utilisateurs finaux (Back-Office, Opérations)

---

## Table des matières

1. [Accéder à l'application](#1-accéder-à-lapplication)
2. [Extraire des messages entrants](#2-extraire-des-messages-entrants)
3. [Extraire des messages sortants](#3-extraire-des-messages-sortants)
4. [Analyser des transferts (MT900)](#4-analyser-des-transferts-mt900)
5. [Comprendre le fichier Excel](#5-comprendre-le-fichier-excel)
6. [Ajouter un nouveau code BIC](#6-ajouter-un-nouveau-code-bic)
7. [Filtrer par dates](#7-filtrer-par-dates)
8. [FAQ — Questions fréquentes](#8-faq--questions-fréquentes)

---

## 1. Accéder à l'application

Ouvrez votre navigateur web (Chrome, Firefox, Edge) et accédez à l'adresse fournie par votre administrateur :

```
http://<adresse-du-serveur>:8501
```

> Si l'application tourne en local : `http://localhost:8501`

La page d'accueil affiche le titre **« BEAC PDF SWIFT Extractor »** avec :
- **Zone principale** : sélection du mode (radio buttons verticaux), upload de fichiers, résultats et téléchargement
- **Bouton 🗑️ Clear All** : pour effacer tous les fichiers chargés d’un seul clic

---

## 2. Extraire des messages entrants

Les **messages entrants** sont les virements reçus (MT202, MT103, MT910).

### Étapes

1. **Sélectionner le mode** : choisissez **📥 Messages Entrants** parmi les radio buttons verticaux

2. **Déposer vos fichiers PDF** :
   - Cliquez sur le cadre **« Déposez vos fichiers PDF ici »**
   - Ou glissez-déposez vos fichiers directement
   - Vous pouvez déposer **plusieurs fichiers** à la fois

3. **Choisir les dates** (optionnel) :
   - Renseignez une **date de début** et une **date de fin**
   - Seuls les messages dont la date valeur est dans cette plage seront extraits

4. **Cliquer sur** ▶ **Extraire**

5. **Consulter les résultats** :
   - Un tableau récapitulatif s'affiche dans la zone centrale
   - Le nombre de messages extraits est indiqué au-dessus

6. **Télécharger le fichier Excel** :
   - Cliquez sur le bouton **📥 Télécharger le résultat Excel**
   - Le fichier est enregistré dans votre dossier « Téléchargements »

---

## 3. Extraire des messages sortants

Les **messages sortants** sont les virements émis (MT202, MT103).

### Étapes

1. Sélectionner **📤 Messages Sortants** parmi les radio buttons
2. Déposer vos fichiers PDF
3. Régler les dates si nécessaire
4. Cliquer sur **Extraire**
5. Télécharger le résultat

> La procédure est identique aux messages entrants. La différence se fait dans le traitement interne : le donneur d'ordre et le bénéficiaire sont inversés.

---

## 4. Analyser des transferts (MT900)

Le mode **Analyse des transferts** permet de rapprocher des confirmations de débit (MT900) avec les virements correspondants (MT103 / MT202).

### Étapes

1. Sélectionner **🔄 Analyse Transferts Exécutés** parmi les radio buttons

2. **Déposer les fichiers MT900** dans la **première zone de dépôt** (« Fichiers MT900 »)

3. **Déposer les fichiers MT103/MT202** dans la **deuxième zone de dépôt** (« Fichiers MT103/MT202 »)

4. Cliquer sur **Extraire**

5. L'application va **automatiquement rapprocher** les MT900 avec les MT103/MT202 en utilisant les références (F21 du MT900 = F20 du MT103/MT202)

6. Télécharger le résultat

### Résultat de l'analyse

Le fichier Excel contient 4 onglets :

| Onglet | Contenu |
|--------|---------|
| **Transferts_Executes** | MT900 qui ont trouvé un MT103/MT202 correspondant (avec BIC du correspondant) |
| **MT900_non_rapproches** | MT900 sans correspondant (à investiguer) |
| **Suspens** | MT103/MT202 sans confirmation MT900 |
| **Exceptions** | Messages en exception (placement, nivellement…) |

---

## 5. Comprendre le fichier Excel

### Onglet principal — « summary »

Cet onglet contient **tous les messages extraits** dans un tableau unique :

| Colonne | Ce qu'elle contient |
|---------|---------------------|
| **code_banque** | Code SWIFT de l'institution |
| **date_reference** | Date valeur du virement (JJ/MM/AAAA) |
| **reference** | Numéro de référence unique du message |
| **reference_origine** | Référence du message originel (si applicable) |
| **type_MT** | Type de message : fin.202, fin.103, fin.910 |
| **pays_iso3** | Code pays à 3 lettres (CMR, GAB, TCD, COG, GNQ, CAF) |
| **Code du donneur d'ordre** | Code BIC du donneur d'ordre |
| **donneur d'ordre** | Nom du donneur d'ordre |
| **Bénéficiaire** | Nom du bénéficiaire |
| **correspondant** | BIC du correspondant |
| **montant** | Montant du virement (nombre) |
| **devise** | Devise (XAF, USD, EUR…) |
| **commentaires** | Informations complémentaires |
| **source_pdf** | Nom du fichier PDF source |

### Onglets par pays

Des onglets sont créés pour chaque pays rencontré (ex : **CMR**, **GAB**, **TCD**…). Ils contiennent les mêmes colonnes que le summary, filtrées par pays.

### Onglets spéciaux

| Onglet | Description |
|--------|-------------|
| **BEACCMCX091** | Messages de la BEAC Cameroun (code spécial) |
| **Exceptions_323201** | Messages avec le code 323201 |
| **Autres_Exceptions** | Nivellement, intérêts EUR, salle des marchés… |
| **BANQUE DE FRANCE** | MT103 en USD passant par la Banque de France (FW021083459) |
| **Doublons_potentiels** | Messages potentiellement en double |

### Navigation dans Excel

- Dans l’onglet **summary**, chaque nom de fichier dans la colonne `source_pdf` est un **lien cliquable** qui mène à la feuille de détail du fichier.
- Dans chaque feuille de détail, un lien **« ⬅ Retour au summary »** en haut de page permet de revenir facilement au tableau principal.

---

## 6. Ajouter un nouveau code BIC

Si un code BIC n'est pas reconnu par l'application (le donneur d'ordre ou le pays apparaît vide), vous pouvez l'ajouter directement depuis l'interface.

### Étapes

1. Faites défiler la page jusqu'à la section **« ➕ Ajouter un nouveau code BIC »** (en bas de la sidebar)

2. Remplissez les champs :
   - **Code BIC** : le code SWIFT (8 ou 11 caractères, ex : `ECOCMLCX`)
   - **Nom de l'institution** : le nom complet (ex : `ECOBANK CAMEROUN S.A.`)
   - **Code pays ISO3** : le code à 3 lettres (ex : `CMR`)

3. Cliquez sur **Ajouter**

4. Le code est immédiatement enregistré dans le fichier `data/bic_codes.xlsx`

5. **Relancez l'extraction** pour que le nouveau code soit pris en compte

---

## 7. Filtrer par dates

Le filtre par dates permet de limiter l'extraction aux messages dont la **date valeur** (champ F32A du message SWIFT) se situe dans une plage donnée.

### Comment faire

1. Dans la barre latérale, cochez la case **« Filtrer par dates »**
2. Sélectionnez la **date de début**
3. Sélectionnez la **date de fin**
4. Lancez l'extraction

Les messages dont la date valeur est en dehors de cette plage seront ignorés.

> Astuce : Laissez le filtre désactivé pour extraire tous les messages sans distinction de date.

---

## 8. FAQ — Questions fréquentes

### Le fichier Excel est vide ou contient très peu de lignes

**Causes possibles :**
- Le filtre par dates est trop restrictif → Élargissez la plage ou désactivez le filtre
- Le PDF ne contient pas de messages SWIFT valides → Vérifiez le contenu du PDF
- Le mauvais mode est sélectionné (entrants au lieu de sortants)

### Un donneur d'ordre ou un pays apparaît vide

**Cause :** Le code BIC n'est pas dans le référentiel.

**Solution :** Ajoutez le code via le formulaire « Ajouter un nouveau code BIC » (voir section 6).

### J'obtiens un message d'erreur lors de l'extraction

**Solutions à essayer :**
1. Vérifiez que le fichier est bien un PDF (pas un .doc ou .jpg)
2. Essayez avec un seul fichier pour isoler le problème
3. Contactez votre administrateur avec le message d'erreur affiché

### Les montants semblent incorrects

**Cause possible :** Le format du PDF est inhabituel et le champ montant n'a pas pu être lu correctement.

**Solution :** Vérifiez le message original dans le PDF et comparez avec le résultat Excel.

### Je ne peux pas accéder à l'application

**Vérifiez que :**
- Vous êtes connecté au réseau interne
- L'adresse saisie est correcte (avec le port `:8501`)
- Le service est démarré (contactez votre administrateur DSI)

### Comment extraire uniquement les MT103 ?

L'application extrait **tous les types de messages** présents dans le PDF. Pour n'avoir que les MT103, vous pouvez filtrer le résultat Excel :
1. Ouvrez le fichier Excel
2. Appliquez un filtre sur la colonne **type_MT**
3. Sélectionnez **fin.103** uniquement

### Puis-je traiter des fichiers volumineux ?

Oui. L'application peut traiter des fichiers PDF de plusieurs centaines de pages contenant des milliers de messages. Le temps de traitement augmente avec le nombre de messages (environ 1 à 2 secondes par page).

---

*Document généré le 20 juin 2025 — PDF SWIFT Extractor v3.0*
