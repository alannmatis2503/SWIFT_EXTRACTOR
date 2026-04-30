# Conception d'une base de données locale — SWIFT Extractor BEAC

> **Statut** : conception. La base n'est PAS encore créée. Ce document
> propose une architecture, un schéma et des exemples d'usage à valider
> avec le pôle métier avant implémentation.
>
> **Date** : 28 avril 2026
> **Cible** : Banque Centrale des États de l'Afrique Centrale (BEAC) —
> 6 États membres (Cameroun, Congo, Gabon, Guinée Équatoriale,
> Centrafrique, Tchad)

---

## 1. Objectifs

Permettre la manipulation, la consultation historique et l'analyse des
extractions produites par l'application SWIFT Extractor (PDF, RJE
EastNet, relevés Excel) dans une **base de données locale unique**,
mono-utilisateur ou multi-utilisateurs lecture seule, sans dépendance à
un serveur de base.

### 1.1 Cas d'usage cibles

1. **Historisation** : conserver tous les exports Excel d'extraction
   d'une journée/mois/année dans une seule source de vérité requêtable.
2. **Analyse transversale** : volumes par pays/correspondant/devise sur
   plusieurs périodes, sans rouvrir 30 fichiers Excel.
3. **Détection d'anomalies** : montants atypiques, doublons potentiels,
   références orphelines (MT202 sans MT950 correspondant).
4. **Rapprochement multi-jours** : étendre le mode 4 (rapprochement
   MT950 actuellement intra-fichier) à un horizon glissant.
5. **Audit & traçabilité** : pour chaque ligne, savoir d'où elle vient
   (fichier source, hash, date de l'extraction, version du code).
6. **Reporting BEAC 6 États** : agrégats par pays CEMAC (CAM, CGO, GAB,
   GEQ, RCA, TCH) sur n'importe quel intervalle.

### 1.2 Hors-scope (à ne PAS faire)

- Pas de remplacement du moteur d'extraction. La BDD est une couche de
  **persistance** en aval, pas un moteur d'ETL.
- Pas de saisie manuelle de transactions. Les seules entrées sont les
  exports de l'application.
- Pas de multi-tenant. Une seule banque centrale = une base.
- Pas d'API REST publique pour démarrer (l'accès se fait via SQL
  direct, ou via un onglet « Requêtes » futur dans Streamlit).

---

## 2. Choix technologique

### 2.1 Comparaison des candidats

| Moteur | Avantages | Inconvénients | Verdict |
|---|---|---|---|
| **SQLite** | Embarqué, zéro install, fichier unique `.db`, lectures concurrentes, supporté par Python natif (`sqlite3`) | Écritures concurrentes limitées, types laxistes | ✅ **Recommandé phase 1** |
| **DuckDB** | Analytique colonnaire ultra-rapide, lit Parquet/CSV directement, syntaxe Postgres-like | Ecosystème plus jeune, moins d'outils GUI | 🔵 Recommandé phase 2 (analytique) |
| **PostgreSQL local** | Robustesse industrielle, multi-utilisateurs vrais | Service à installer/maintenir, surdimensionné pour 1 poste | ⚠️ Si déploiement serveur ultérieur |
| **MS Access** | Familier en banque, GUI intégrée | Propriétaire, taille limitée, peu portable | ❌ |

**Recommandation** : démarrer en **SQLite** (fichier
`beac_swift_history.db`), avec option future d'attacher DuckDB en
lecture seule pour les agrégations lourdes.

### 2.2 Avantages SQLite pour ce cas

- Une copie du fichier `.db` = une sauvegarde complète.
- Lecture concurrente sûre depuis Streamlit + DBeaver simultanément.
- Supporte des bases de plusieurs Go sans dégradation perceptible pour
  des requêtes typiques (millions de lignes restent rapides avec
  index).
- Migration vers PostgreSQL triviale si le besoin grandit (script
  `pgloader`).

---

## 3. Modèle conceptuel

### 3.1 Vue d'ensemble (diagramme)

```
┌──────────────────┐       ┌─────────────────────┐
│  extraction_run  │ 1 ──N │  message            │
│  (audit/import)  │       │  (1 ligne MT)       │
└──────────────────┘       └─────────────────────┘
                                  │
                          ┌───────┼───────────┬──────────────┐
                          ▼       ▼           ▼              ▼
                    ┌──────────┐ ┌──────┐ ┌──────────┐ ┌────────────┐
                    │exception │ │mt950 │ │commen-   │ │match_      │
                    │_route    │ │_line │ │taire     │ │mt950       │
                    └──────────┘ └──────┘ └──────────┘ └────────────┘

  Tables référentielles (peuplées une fois) :
  ┌──────┐ ┌────────┐ ┌────────────────┐
  │ pays │ │ devise │ │ bic_correspond │
  └──────┘ └────────┘ └────────────────┘
```

### 3.2 Entités principales

| Entité | Rôle | Cardinalité |
|---|---|---|
| `extraction_run` | Une exécution d'extraction (1 fichier RJE/PDF/Excel donné) | 1 par import |
| `message` | Une ligne MT extraite (102/103/202/910/950) | N par run |
| `exception_route` | Routage d'un message vers une feuille d'exception (BEAC091, 323201, EUR T2PI, BdF, forex…) | 0..1 par message |
| `mt950_line` | Ligne brute d'un relevé MT950 ou Excel | N par run de mode 4 |
| `match_mt950` | Lien réconcilié MT202/MT103 ↔ MT950 | 0..1 par message |
| `commentaire` | Champ commentaire libre extrait | 0..N par message |
| `pays` | Référentiel ISO3 + nom + zone (CEMAC/UEMOA/SEPA/…) | 1 par pays |
| `devise` | Référentiel ISO4217 | 1 par devise |
| `bic_correspondant` | Snapshot du `bic_codes.xlsx` au moment de l'import | 1 par BIC |

---

## 4. Schéma SQL (DDL SQLite)

> Convention : tous les noms en `snake_case`, clés primaires `id`
> auto-incrémentées, dates ISO8601 string (SQLite n'a pas de type DATE
> natif), montants en `REAL` à 6 décimales.

### 4.1 Tables référentielles

```sql
CREATE TABLE pays (
    iso3            TEXT PRIMARY KEY,        -- 'CAM', 'CGO', 'GAB'…
    nom_fr          TEXT NOT NULL,
    nom_en          TEXT,
    zone_economique TEXT,                    -- 'CEMAC', 'UEMOA', 'SEPA', 'WORLD'
    est_membre_beac INTEGER NOT NULL DEFAULT 0  -- 1 pour les 6 États CEMAC
);

CREATE TABLE devise (
    code_iso  TEXT PRIMARY KEY,              -- 'XAF', 'EUR', 'USD'…
    libelle   TEXT NOT NULL,
    nb_decimales INTEGER NOT NULL DEFAULT 2
);

CREATE TABLE bic_correspondant (
    bic           TEXT PRIMARY KEY,          -- ex 'SGCMCMCXXXX'
    nom_court     TEXT,                      -- ex 'Société Générale Cameroun'
    pays_iso3     TEXT REFERENCES pays(iso3),
    code_4chiffres TEXT,                     -- code Reglement le cas échéant
    code_ccf      TEXT,                      -- code CCF le cas échéant
    is_forex      INTEGER NOT NULL DEFAULT 0,
    snapshot_date TEXT NOT NULL              -- date d'extraction du xlsx
);
```

### 4.2 Audit & traçabilité

```sql
CREATE TABLE extraction_run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uuid        TEXT NOT NULL UNIQUE,           -- uuid4 généré côté app
    started_at      TEXT NOT NULL,                  -- ISO8601 timestamp
    finished_at     TEXT,
    mode            TEXT NOT NULL,                  -- 'PDF_MT202', 'PDF_MT103', 'RJE_EASTNET', 'EXCEL_BDF', etc.
    sous_mode       TEXT,                           -- 'incoming', 'outgoing', 'mt950_match'…
    source_file     TEXT NOT NULL,                  -- chemin/nom du fichier source
    source_hash_sha256 TEXT NOT NULL,               -- hash du fichier d'entrée (idempotence)
    source_size_bytes INTEGER,
    nb_messages     INTEGER NOT NULL DEFAULT 0,
    nb_exceptions   INTEGER NOT NULL DEFAULT 0,
    app_version     TEXT,                           -- ex 'v5.1'
    git_commit      TEXT,                           -- sha court du build
    operator        TEXT,                           -- login/initiales
    notes           TEXT
);

CREATE INDEX idx_run_mode_date ON extraction_run(mode, started_at);
CREATE INDEX idx_run_hash ON extraction_run(source_hash_sha256);
```

### 4.3 Table principale : `message`

```sql
CREATE TABLE message (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                INTEGER NOT NULL REFERENCES extraction_run(id) ON DELETE CASCADE,

    -- Identité du message
    type_mt               TEXT NOT NULL,            -- '202', '103', '910', '950', '102'…
    direction             TEXT NOT NULL,            -- 'incoming' | 'outgoing'
    reference             TEXT NOT NULL,            -- F20
    related_reference     TEXT,                     -- F21
    date_reference        TEXT,                     -- F30/F32A date — ISO8601 (YYYY-MM-DD)

    -- Montant
    devise                TEXT REFERENCES devise(code_iso),
    montant               REAL,                     -- positif

    -- Acteurs
    correspondant_bic     TEXT,                     -- sender BIC
    correspondant_pays    TEXT REFERENCES pays(iso3),
    code_donneur_dordre   TEXT,                     -- F52A code/BIC final (post-fallback F58A)
    donneur_dordre        TEXT,                     -- libellé long
    beneficiaire          TEXT,                     -- F58A/F59
    pays_iso3             TEXT REFERENCES pays(iso3),  -- pays du donneur d'ordre

    -- Provenance bas niveau
    source_pdf            TEXT,                     -- nom court du fichier d'origine
    msg_index_in_source   INTEGER,                  -- N° de message dans le fichier RJE/PDF

    -- Méta extraction
    extraction_method     TEXT NOT NULL,            -- 'PDF_MT202', 'RJE_EASTNET', 'EXCEL_CITI'…
    f52a_raw              TEXT,                     -- pour debug/traçabilité
    f58a_raw              TEXT,                     -- pour debug/traçabilité
    f57a_raw              TEXT,
    fallback_applied      TEXT,                     -- 'F58A_CITIUS33', 'F58A_BDF', 'F58A_RJE_GENERIC', NULL

    created_at            TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE(run_id, reference, type_mt, msg_index_in_source)
);

CREATE INDEX idx_msg_ref ON message(reference);
CREATE INDEX idx_msg_date ON message(date_reference);
CREATE INDEX idx_msg_pays ON message(pays_iso3);
CREATE INDEX idx_msg_type_dir ON message(type_mt, direction);
CREATE INDEX idx_msg_devise ON message(devise);
CREATE INDEX idx_msg_correspondant ON message(correspondant_bic);
CREATE INDEX idx_msg_run ON message(run_id);
```

### 4.4 Routage en exception

```sql
CREATE TABLE exception_route (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    bucket       TEXT NOT NULL,
    -- Valeurs possibles :
    --   'BEAC091'        : BEACCMCX091 (TCFM/Trésor)
    --   'EXC_323201'     : code 323201 dans F58A
    --   'EUR_T2PI'       : EUR Target 2 PI
    --   'EUR_T2RM'       : EUR Target 2 RM
    --   'EUR_T2PL'       : EUR Target 2 PL
    --   'NIVELLEMENT'    : opérations de nivellement intra-groupe
    --   'SALLE_MARCHE'   : forex/salle des marchés
    --   'BDF_CORR'       : correspondants BdF (CITI, SCB)
    --   'BDF_REJET'      : MT103 reject BdF
    --   'FOREX'          : forex CITI/SCB/BDF
    --   'DE_DATA_ENTRY'  : opérations internes CITI
    --   'AUTRE'          : non classé
    raison       TEXT,                              -- description lisible
    detected_via TEXT                               -- 'F58A_323201', 'BIC_BEACCMCX091', 'reference_pattern'…
);

CREATE INDEX idx_exc_bucket ON exception_route(bucket);
CREATE INDEX idx_exc_msg ON exception_route(message_id);
```

### 4.5 Rapprochement MT950

```sql
CREATE TABLE mt950_line (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES extraction_run(id),
    correspondant   TEXT NOT NULL,                  -- 'BDF', 'CITI_USD', 'CITI_EUR', 'SCBL'…
    devise          TEXT REFERENCES devise(code_iso),
    date_valeur     TEXT,                           -- ISO8601
    sens            TEXT NOT NULL,                  -- 'D' (débit) | 'C' (crédit)
    montant         REAL NOT NULL,
    identification_code TEXT,                       -- :86: code transaction
    reference_servicer  TEXT,                       -- ref après //
    reference_owner     TEXT,                       -- ref propriétaire
    libelle         TEXT,
    raw_line        TEXT
);

CREATE INDEX idx_mt950_ref ON mt950_line(reference_servicer, reference_owner);
CREATE INDEX idx_mt950_date ON mt950_line(date_valeur);

CREATE TABLE match_mt950 (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL REFERENCES message(id),
    mt950_line_id   INTEGER NOT NULL REFERENCES mt950_line(id),
    score           REAL NOT NULL,                  -- 0..1, qualité du match
    critere_montant INTEGER NOT NULL DEFAULT 0,     -- bool
    critere_id_code INTEGER NOT NULL DEFAULT 0,
    critere_date    INTEGER NOT NULL DEFAULT 0,
    critere_ref     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(message_id, mt950_line_id)
);
```

### 4.6 Commentaires libres

```sql
CREATE TABLE commentaire (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    contenu     TEXT NOT NULL,
    source_tag  TEXT                                -- 'F70', 'F72', 'F86', 'free_text'
);
```

---

## 5. Flux d'alimentation

### 5.1 Stratégie

À chaque clic sur **« Lancer l'extraction »** dans Streamlit, en plus
de générer l'Excel, l'application :

1. Calcule un `sha256` du fichier source.
2. Insère un enregistrement `extraction_run`.
3. Pour chaque ligne extraite, insère un `message`.
4. Pour chaque ligne en exception, insère un `exception_route`.
5. Pour chaque ligne MT950, insère un `mt950_line` puis les `match_mt950`.
6. Tout dans une transaction unique (rollback si échec).

### 5.2 Idempotence

Si le même fichier (même sha256) est ré-extrait :
- option A : refus avec message « déjà importé le JJ/MM/AAAA »
- option B : nouvelle ligne `extraction_run`, mais marquée
  `notes = 're-run'`. Les anciennes données restent (audit).
- **Recommandation** : option B, plus tolérante au reprocessing après
  bug fix (cf. le présent fix RJE).

### 5.3 Rétro-import des exports Excel passés

Pour ne pas perdre l'historique des extractions déjà faites en Excel,
prévoir un script `import_legacy_excel.py` qui :
- parcourt un dossier d'archives Excel,
- parse chaque feuille, déduit le mode/sous-mode,
- alimente `extraction_run` + `message` avec
  `app_version='legacy_import'`.

---

## 6. Requêtes types (SQL)

### 6.1 Volumes par pays CEMAC sur un mois

```sql
SELECT p.iso3, p.nom_fr,
       COUNT(*)         AS nb_messages,
       SUM(m.montant)   AS total_montant,
       m.devise
FROM message m
JOIN pays p ON p.iso3 = m.pays_iso3
WHERE p.est_membre_beac = 1
  AND m.date_reference BETWEEN '2026-01-01' AND '2026-01-31'
  AND m.type_mt = '202'
GROUP BY p.iso3, m.devise
ORDER BY total_montant DESC;
```

### 6.2 Top 10 donneurs d'ordre USD entrants

```sql
SELECT m.code_donneur_dordre, m.donneur_dordre,
       m.pays_iso3,
       COUNT(*) AS nb,
       SUM(m.montant) AS total
FROM message m
WHERE m.devise = 'USD'
  AND m.direction = 'incoming'
  AND m.type_mt = '202'
  AND m.date_reference >= date('now', '-90 days')
GROUP BY m.code_donneur_dordre
ORDER BY total DESC
LIMIT 10;
```

### 6.3 MT202 sans rapprochement MT950

```sql
SELECT m.reference, m.date_reference, m.montant, m.devise,
       m.correspondant_bic
FROM message m
LEFT JOIN match_mt950 mm ON mm.message_id = m.id
WHERE m.type_mt = '202'
  AND mm.id IS NULL
  AND m.date_reference < date('now', '-7 days');
```

### 6.4 Détection d'anomalies — montants atypiques

```sql
WITH stats AS (
  SELECT pays_iso3, devise,
         AVG(montant) AS moy,
         -- SQLite n'a pas STDDEV natif → utiliser variance via sqrt
         AVG(montant*montant) - AVG(montant)*AVG(montant) AS var
  FROM message
  WHERE date_reference >= date('now', '-180 days')
  GROUP BY pays_iso3, devise
)
SELECT m.reference, m.pays_iso3, m.devise, m.montant,
       s.moy, (m.montant - s.moy) / NULLIF(SQRT(s.var), 0) AS z_score
FROM message m
JOIN stats s ON s.pays_iso3 = m.pays_iso3 AND s.devise = m.devise
WHERE ABS((m.montant - s.moy) / NULLIF(SQRT(s.var), 0)) > 3
ORDER BY z_score DESC;
```

### 6.5 Historique d'une référence

```sql
SELECT er.started_at, er.mode, er.source_file,
       m.type_mt, m.direction, m.montant, m.devise, m.donneur_dordre
FROM message m
JOIN extraction_run er ON er.id = m.run_id
WHERE m.reference = ?
ORDER BY er.started_at;
```

### 6.6 Exceptions par bucket sur la période

```sql
SELECT ex.bucket, COUNT(*) AS nb, SUM(m.montant) AS total
FROM exception_route ex
JOIN message m ON m.id = ex.message_id
WHERE m.date_reference BETWEEN ? AND ?
GROUP BY ex.bucket
ORDER BY total DESC;
```

### 6.7 Suivi du fallback F58A (audit du fix 28/04/2026)

```sql
SELECT extraction_method,
       fallback_applied,
       COUNT(*) AS nb,
       SUM(CASE WHEN code_donneur_dordre IS NOT NULL THEN 1 ELSE 0 END) AS avec_code,
       SUM(CASE WHEN pays_iso3 IS NOT NULL THEN 1 ELSE 0 END) AS avec_pays
FROM message
WHERE extraction_method LIKE 'RJE%'
  AND type_mt = '202'
  AND direction = 'incoming'
GROUP BY extraction_method, fallback_applied;
```

---

## 7. Vues pratiques

```sql
-- Vue facilitant les requêtes BEAC : un message + son bucket exception (ou 'NORMAL')
CREATE VIEW v_message_classifie AS
SELECT m.*,
       COALESCE(ex.bucket, 'NORMAL') AS classification,
       ex.raison AS classification_raison
FROM message m
LEFT JOIN exception_route ex ON ex.message_id = m.id;

-- Vue agrégée mensuelle CEMAC
CREATE VIEW v_volumes_mensuels_cemac AS
SELECT strftime('%Y-%m', m.date_reference) AS mois,
       m.pays_iso3, m.devise, m.type_mt, m.direction,
       COUNT(*) AS nb,
       SUM(m.montant) AS total
FROM message m
JOIN pays p ON p.iso3 = m.pays_iso3
WHERE p.est_membre_beac = 1
GROUP BY mois, m.pays_iso3, m.devise, m.type_mt, m.direction;
```

---

## 8. Sécurité & gouvernance

- **Stockage** : fichier `.db` dans un dossier de données contrôlé
  (ex : `data/db/beac_swift_history.db`). Hors versioning Git.
- **Sauvegarde** : copie quotidienne automatique en `*.db.YYYYMMDD`,
  rétention 90 jours.
- **Chiffrement au repos** : envisager SQLCipher si les données sortent
  du poste de travail BEAC.
- **Accès** : lecture seule pour la majorité des utilisateurs ; seul le
  process Streamlit a le droit d'écrire (mode WAL recommandé).
- **RGPD/secret bancaire** : aucune donnée personnelle d'individu —
  seulement BIC institutions et libellés métier. Pas de problème
  conformité a priori.
- **Purge** : pas de suppression automatique. Archivage à 5 ans
  (politique BEAC à valider).

---

## 9. Plan de mise en œuvre proposé

| Phase | Livrable | Effort |
|---|---|---|
| **P0 — Validation** | Ce document validé par le pôle métier + DSI | — |
| **P1 — Création schéma** | Script `init_db.sql` + chargement référentiels (pays, devises, BIC) | 1-2 j |
| **P2 — Hook d'écriture** | Modifier `extractor_manager.py` pour insérer en fin de pipeline | 2-3 j |
| **P3 — Rétro-import** | Script `import_legacy_excel.py` pour les archives existantes | 2 j |
| **P4 — Onglet Streamlit « Requêtes »** | UI minimale pour les 6 requêtes types | 2-3 j |
| **P5 — Documentation utilisateur** | Annexe au GUIDE_UTILISATEUR.md | 1 j |
| **Total estimé** | | **8-12 jours-homme** |

---

## 10. Points ouverts à arbitrer

1. **Idempotence** : option A (refus) ou B (re-run autorisé) ? *Recommandation : B.*
2. **Stockage du Excel original** en blob dans la BDD pour audit total ? *Recommandation : non, garder seulement le hash + chemin.*
3. **Multi-devises** : un seul total cross-devises est trompeur. Faut-il
   stocker un montant converti en XAF (taux du jour) ? *À discuter avec le métier.*
4. **Nommage des buckets exception** : harmoniser avec les onglets Excel
   actuels pour éviter les confusions.
5. **Référentiel BIC** : snapshot à chaque run, ou table maître mise à
   jour manuellement ? *Recommandation : table maître + colonne
   `snapshot_date` pour traçabilité.*

---

*Document de conception — non implémenté — à valider avant
développement.*
