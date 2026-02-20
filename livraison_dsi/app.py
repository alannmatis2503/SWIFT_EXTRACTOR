# streamlit_app/app.py
# Interface Streamlit pour l'extracteur PDF SWIFT - Version Hugging Face Spaces
# Version: 2026-01-12 - BIC codes 81, MT900 support
import sys
from pathlib import Path
import tempfile
import shutil
import traceback

import streamlit as st
import pandas as pd

# --- Configuration des chemins pour HF Spaces ---
ROOT = Path(__file__).resolve().parent

# Import des modules backend (tous dans le même répertoire pour HF Spaces)
try:
    from extractor_manager import extract_single, create_workbook, extract_dispatch
    from extractors import bic_utils
except Exception as e:
    st.error(f"Impossible d'importer l'extracteur backend: {e}")
    st.stop()

# Test du chargement de la base BIC
try:
    m = bic_utils.load_bic_mapping()
    # Utiliser le cache interne de bic_utils pour obtenir le compte
    # load_bic_mapping charge déjà le fichier, on récupère le nombre depuis le cache fullkey
    from extractors.bic_utils import _BIC_FULLKEY_MAP
    nb_codes = len(_BIC_FULLKEY_MAP) if _BIC_FULLKEY_MAP else len(m)
    st.sidebar.success(f"✅ {nb_codes} codes BIC chargés")
except Exception as e:
    st.sidebar.warning(f"⚠️ BIC mapping: {e}")

# UI configuration
st.set_page_config(page_title="PDF SWIFT Extractor", layout="wide", page_icon="📄")

# Header avec style
st.markdown("""
    <h1 style='text-align: center;'>📄 PDF SWIFT Extractor</h1>
    <p style='text-align: center; color: gray;'>Extraction automatique de messages MT103, MT202, MT910</p>
""", unsafe_allow_html=True)

with st.expander("📖 Mode d'emploi", expanded=False):
    st.markdown("""
    **Comment utiliser l'application :**
    1. Sélectionnez le type de messages (entrants ou sortants)
    2. Glissez-déposez un ou plusieurs fichiers PDF
    3. Choisissez une date de référence (optionnel)
    4. Cliquez sur **Extraire**
    5. Téléchargez le fichier Excel généré
    
    **Types de messages supportés :**
    - **MT202** : Virements interbancaires
    - **MT103** : Virements clients
    - **MT910** : Confirmations de crédit
    - **MT900/fin.900** : Analyse des transferts exécutés
    """)

# Direction selector
st.markdown("### 📨 Type de messages")
direction = st.radio(
    "Sélectionnez le type de messages à extraire",
    ("incoming", "outgoing", "transfer_analysis"),
    format_func=lambda x: "📥 Messages Entrants (MT202, MT103, MT910)" if x == "incoming" else ("📤 Messages Sortants (MT202, MT103)" if x == "outgoing" else "🔄 Analyse Transferts Exécutés (MT202/MT103 + fin.900)"),
    horizontal=False
)

if direction == "incoming":
    st.info("**Messages entrants** : Pour les MT202, le bénéficiaire sera vide. Pour les MT910, le bénéficiaire sera identique au donneur d'ordre.")
elif direction == "outgoing":
    st.info("**Messages sortants** : Pour les MT202, le bénéficiaire sera extrait depuis F58A.")
else:
    st.info("**Analyse Transferts Exécutés** : Charge les fichiers MT900 d'un côté et les fichiers MT103/MT202 de l'autre. Le système match les références (F21 du MT900 = F20 du MT103/MT202) pour compléter les infos.")

# File uploader(s) - différent selon le mode
st.markdown("### 📁 Fichiers PDF")

# Gestion du Clear All : utiliser un compteur pour régénérer les clés des uploaders
if 'uploader_key_counter' not in st.session_state:
    st.session_state.uploader_key_counter = 0

def clear_all_files():
    """Réinitialiser tous les fichiers chargés en incrémentant le compteur de clés."""
    st.session_state.uploader_key_counter += 1
    # Réinitialiser aussi les résultats d'extraction
    st.session_state.extraction_results = None
    st.session_state.excel_data = None
    st.session_state.excel_filename = None

# Bouton Clear All
col_clear1, col_clear2 = st.columns([4, 1])
with col_clear2:
    if st.button("🗑️ Clear All", help="Supprimer tous les fichiers chargés", use_container_width=True):
        clear_all_files()
        st.rerun()

uploader_suffix = f"_{st.session_state.uploader_key_counter}"

if direction == "transfer_analysis":
    # Mode analyse transferts: deux zones d'upload séparées (disposition verticale)
    st.markdown("#### 📋 Fichiers MT900 (confirmations)")
    uploaded_mt900_files = st.file_uploader(
        "Déposez les fichiers contenant les MT900",
        type="pdf",
        accept_multiple_files=True,
        help="Fichiers PDF contenant les messages MT900/fin.900",
        key=f"mt900_uploader{uploader_suffix}"
    )
    
    st.markdown("#### 📤 Fichiers MT103/MT202 (transferts sortants)")
    uploaded_mt103_202_files = st.file_uploader(
        "Déposez les fichiers contenant les MT103/MT202",
        type="pdf",
        accept_multiple_files=True,
        help="Fichiers PDF contenant les messages MT103 et MT202 sortants",
        key=f"mt103_202_uploader{uploader_suffix}"
    )
    
    # Pour compatibilité avec le reste du code
    uploaded_files = None  # Sera géré séparément
else:
    # Mode standard: un seul uploader
    uploaded_files = st.file_uploader(
        "Déposez vos fichiers PDF ici",
        type="pdf",
        accept_multiple_files=True,
        help="Vous pouvez sélectionner plusieurs fichiers à la fois",
        key=f"main_uploader{uploader_suffix}"
    )
    uploaded_mt900_files = None
    uploaded_mt103_202_files = None

# Date filter - PLAGE DE DATES
st.markdown("### 📅 Filtre par plage de dates")
from datetime import date as date_type, timedelta
default_date = date_type.today()
col_date1, col_date2 = st.columns(2)
with col_date1:
    date_debut = st.date_input("Date de début", value=default_date, help="Date de début de la plage (incluse)")
with col_date2:
    date_fin = st.date_input("Date de fin", value=default_date, help="Date de fin de la plage (incluse)")

if date_debut > date_fin:
    st.warning("⚠️ La date de début doit être antérieure ou égale à la date de fin.")

# Extract button
col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    run_button = st.button("🚀 Extraire les données", use_container_width=True, type="primary")

# Initialiser session_state pour persister les résultats après téléchargement
if 'extraction_results' not in st.session_state:
    st.session_state.extraction_results = None
if 'excel_data' not in st.session_state:
    st.session_state.excel_data = None
if 'excel_filename' not in st.session_state:
    st.session_state.excel_filename = None

# Helper function
def save_uploaded_to_temp(uploaded) -> Path:
    tmpdir = Path(tempfile.mkdtemp(prefix="pdf_extr_"))
    dest = tmpdir / uploaded.name
    with open(dest, "wb") as f:
        f.write(uploaded.getbuffer())
    return dest

# Main extraction logic
if run_button:
    # Vérification des fichiers selon le mode
    if direction == "transfer_analysis":
        if not uploaded_mt900_files and not uploaded_mt103_202_files:
            st.warning("⚠️ Aucun fichier sélectionné. Veuillez charger au moins des fichiers MT900 ou MT103/MT202.")
            has_files = False
        elif not uploaded_mt900_files:
            st.warning("⚠️ Aucun fichier MT900 sélectionné. Veuillez charger les fichiers contenant les confirmations MT900.")
            has_files = False
        elif not uploaded_mt103_202_files:
            st.warning("⚠️ Aucun fichier MT103/MT202 sélectionné. Veuillez charger les fichiers contenant les transferts sortants.")
            has_files = False
        else:
            has_files = True
    else:
        if not uploaded_files:
            st.warning("⚠️ Aucun fichier sélectionné.")
            has_files = False
        else:
            has_files = True
    
    if has_files:
        # Réinitialiser les résultats précédents
        st.session_state.extraction_results = None
        st.session_state.excel_data = None
        st.session_state.excel_filename = None
        
        rows = []
        beaccmcx091_rows = []  # Liste séparée pour les BEACCMCX091
        errors = []
        tmp_dirs = []
        
        all_missing_codes = {"unmapped": set(), "empty": set()}
        exception_323201_rows = []  # Liste pour les exceptions 323201
        other_exceptions_rows = []  # Liste pour les autres exceptions (EUR/nivellement)
        banque_de_france_rows = []  # MT103 USD avec BANQUE DE FRANCE / FW021083459
        
        if direction == "transfer_analysis":
            # ======== MODE ANALYSE DES TRANSFERTS ========
            # Logique: extraire MT900 séparément, puis les MT103/MT202 comme sortants, puis matcher
            from extractor_manager import extract_mt900_only, match_mt900_with_transfers, create_transfer_analysis_workbook
            
            total_files = len(uploaded_mt900_files) + len(uploaded_mt103_202_files)
            progress = st.progress(0, text="Initialisation...")
            idx = 0
            
            st.info(f"🔄 Traitement de {len(uploaded_mt900_files)} fichier(s) MT900 et {len(uploaded_mt103_202_files)} fichier(s) MT103/MT202...")
            
            # Étape 1: Extraire tous les MT900
            all_mt900_rows = []
            for uf in uploaded_mt900_files:
                idx += 1
                progress.progress(idx / total_files, text=f"MT900 : {uf.name} ({idx}/{total_files})")
                
                try:
                    tmp_path = save_uploaded_to_temp(uf)
                    tmp_dirs.append(tmp_path.parent)
                    
                    mt900_rows, missing = extract_mt900_only(tmp_path)
                    all_missing_codes["unmapped"].update(missing.get("unmapped", set()))
                    all_missing_codes["empty"].update(missing.get("empty", set()))
                    
                    all_mt900_rows.extend(mt900_rows)
                    st.success(f"✅ {uf.name}: {len(mt900_rows)} MT900 extraits")
                    
                except Exception as e:
                    tb = traceback.format_exc()
                    errors.append((uf.name, str(e)))
                    st.error(f"❌ Erreur: {uf.name}")
                    with st.expander(f"Détails de l'erreur pour {uf.name}"):
                        st.code(tb)
            
            # Étape 2: Extraire tous les MT103/MT202 (traités comme sortants)
            all_transfer_rows = []
            for uf in uploaded_mt103_202_files:
                idx += 1
                progress.progress(idx / total_files, text=f"MT103/MT202 : {uf.name} ({idx}/{total_files})")
                
                try:
                    tmp_path = save_uploaded_to_temp(uf)
                    tmp_dirs.append(tmp_path.parent)
                    
                    # Utiliser extract_dispatch avec direction="outgoing" pour bénéficier de toute la logique sortante
                    # (F58A bénéficiaire, codes Trésor/CCF, etc.)
                    new_rows, new_beac_rows, new_exc_rows, new_other_exc_rows, new_bdf_rows, missing = extract_dispatch(tmp_path, direction="outgoing")
                    all_missing_codes["unmapped"].update(missing.get("unmapped", set()))
                    all_missing_codes["empty"].update(missing.get("empty", set()))
                    
                    # Filtrer pour ne garder que MT103 et MT202 (exclure MT910 et autres)
                    transfer_rows = [r for r in new_rows if r.get("type_MT") and ("103" in r.get("type_MT") or "202" in r.get("type_MT"))]
                    
                    all_transfer_rows.extend(transfer_rows)
                    st.success(f"✅ {uf.name}: {len(transfer_rows)} MT103/MT202 extraits")
                    
                except Exception as e:
                    tb = traceback.format_exc()
                    errors.append((uf.name, str(e)))
                    st.error(f"❌ Erreur: {uf.name}")
                    with st.expander(f"Détails de l'erreur pour {uf.name}"):
                        st.code(tb)
            
            progress.progress(1.0, text="Matching des références...")
            
            # Étape 3: Matcher les MT900 avec les MT103/MT202
            # Retourne maintenant 4 éléments: matched, suspens, exceptions, unmatched_mt900
            matched_rows, suspens_rows, exception_mt900_rows, unmatched_mt900_rows = match_mt900_with_transfers(all_mt900_rows, all_transfer_rows)
            
            rows = matched_rows
            st.session_state.suspens_rows = suspens_rows
            st.session_state.exception_mt900_rows = exception_mt900_rows
            st.session_state.unmatched_mt900_rows = unmatched_mt900_rows
            
            progress.empty()
            st.info(f"📊 **Résumé** : {len(matched_rows)} MT900 matchés, {len(unmatched_mt900_rows)} MT900 sans correspondant, {len(suspens_rows)} MT103/MT202 en suspens, {len(exception_mt900_rows)} MT900 en exception")
            
        else:
            # ======== MODE STANDARD (incoming/outgoing) ========
            progress = st.progress(0, text="Initialisation...")
            total = len(uploaded_files)
            idx = 0
            
            st.info(f"🔄 Traitement de {total} fichier(s)...")
            
            for uf in uploaded_files:
                idx += 1
                progress.progress(idx / total, text=f"Traitement : {uf.name} ({idx}/{total})")
                
                try:
                    tmp_path = save_uploaded_to_temp(uf)
                    tmp_dirs.append(tmp_path.parent)
                except Exception as e:
                    errors.append((uf.name, f"Impossible d'enregistrer temporairement: {e}"))
                    st.error(f"❌ Erreur avec {uf.name}")
                    continue
                
                try:
                    # Mode standard (incoming/outgoing)
                    # extract_dispatch retourne (rows, beaccmcx091_rows, exception_323201_rows, other_exceptions_rows, missing_codes)
                    new_rows, new_beac_rows, new_exc_rows, new_other_exc_rows, new_bdf_rows, missing_codes = extract_dispatch(tmp_path, direction=direction)
                    
                    # Accumulate missing codes
                    all_missing_codes["unmapped"].update(missing_codes.get("unmapped", set()))
                    all_missing_codes["empty"].update(missing_codes.get("empty", set()))
                    
                    # Normalisations pour les rows normales
                    for r in new_rows:
                        if "beneficiaire" not in r:
                            r["beneficiaire"] = None
                        
                        if "donneur_dordre" not in r:
                            if "institution_name" in r and r["institution_name"]:
                                r["donneur_dordre"] = r.get("institution_name")
                            else:
                                r["donneur_dordre"] = None
                        
                        if not r.get("source_pdf"):
                            r["source_pdf"] = uf.name
                        
                        if not r.get("type_MT"):
                            r["type_MT"] = None
                        
                        rows.append(r)
                    
                    # Normalisations pour les BEACCMCX091
                    for r in new_beac_rows:
                        if "beneficiaire" not in r:
                            r["beneficiaire"] = None
                        
                        if "donneur_dordre" not in r:
                            if "institution_name" in r and r["institution_name"]:
                                r["donneur_dordre"] = r.get("institution_name")
                            else:
                                r["donneur_dordre"] = None
                        
                        if not r.get("source_pdf"):
                            r["source_pdf"] = uf.name
                        
                        if not r.get("type_MT"):
                            r["type_MT"] = None
                        
                        beaccmcx091_rows.append(r)
                    
                    # Normalisations pour les exceptions 323201
                    for r in new_exc_rows:
                        if "beneficiaire" not in r:
                            r["beneficiaire"] = None
                        
                        if "donneur_dordre" not in r:
                            if "institution_name" in r and r["institution_name"]:
                                r["donneur_dordre"] = r.get("institution_name")
                            else:
                                r["donneur_dordre"] = None
                        
                        if not r.get("source_pdf"):
                            r["source_pdf"] = uf.name
                        
                        if not r.get("type_MT"):
                            r["type_MT"] = None
                        
                        exception_323201_rows.append(r)
                    
                    # Normalisations pour les autres exceptions (EUR/nivellement)
                    for r in new_other_exc_rows:
                        if "beneficiaire" not in r:
                            r["beneficiaire"] = None
                        
                        if "donneur_dordre" not in r:
                            if "institution_name" in r and r["institution_name"]:
                                r["donneur_dordre"] = r.get("institution_name")
                            else:
                                r["donneur_dordre"] = None
                        
                        if not r.get("source_pdf"):
                            r["source_pdf"] = uf.name
                        
                        if not r.get("type_MT"):
                            r["type_MT"] = None
                        
                        other_exceptions_rows.append(r)
                    
                    # Normalisations pour les BANQUE DE FRANCE
                    for r in new_bdf_rows:
                        if "beneficiaire" not in r:
                            r["beneficiaire"] = None
                        
                        if "donneur_dordre" not in r:
                            if "institution_name" in r and r["institution_name"]:
                                r["donneur_dordre"] = r.get("institution_name")
                            else:
                                r["donneur_dordre"] = None
                        
                        if not r.get("source_pdf"):
                            r["source_pdf"] = uf.name
                        
                        if not r.get("type_MT"):
                            r["type_MT"] = None
                        
                        banque_de_france_rows.append(r)
                    
                    types = sorted({rr.get("type_MT") or "inconnu" for rr in new_rows})
                    beac_msg = f" + {len(new_beac_rows)} BEACCMCX091" if new_beac_rows else ""
                    exc_msg = f" + {len(new_exc_rows)} exceptions 323201" if new_exc_rows else ""
                    other_exc_msg = f" + {len(new_other_exc_rows)} autres exceptions" if new_other_exc_rows else ""
                    bdf_msg = f" + {len(new_bdf_rows)} BANQUE DE FRANCE" if new_bdf_rows else ""
                    
                    st.success(f"✅ {uf.name}: {len(new_rows)} message(s){beac_msg}{exc_msg}{other_exc_msg}{bdf_msg} — Types: {', '.join(types)}")
                    
                except Exception as e:
                    tb = traceback.format_exc()
                    errors.append((uf.name, str(e)))
                    st.error(f"❌ Erreur: {uf.name}")
                    with st.expander(f"Détails de l'erreur pour {uf.name}"):
                        st.code(tb)
            
            progress.empty()
        
        # Cleanup
        for d in tmp_dirs:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
        
        # Filter by date range (plage de dates)
        if date_debut and date_fin and rows and date_debut <= date_fin:
            date_debut_str = date_debut.strftime("%Y-%m-%d")
            date_fin_str = date_fin.strftime("%Y-%m-%d")
            rows_filtered = [r for r in rows if r.get("date_reference") and date_debut_str <= r.get("date_reference") <= date_fin_str]
            if len(rows_filtered) < len(rows):
                if date_debut_str == date_fin_str:
                    st.info(f"📅 Filtrage: {len(rows_filtered)} message(s) pour {date_debut_str} (sur {len(rows)})")
                else:
                    st.info(f"📅 Filtrage: {len(rows_filtered)} message(s) du {date_debut_str} au {date_fin_str} (sur {len(rows)})")
            rows = rows_filtered
        
        # Stocker les résultats si disponibles
        if rows:
            # Ensure backward-compatibility
            for r in rows:
                if not r.get("institution_name"):
                    r["institution_name"] = r.get("donneur_dordre")
            
            # Create and download workbook
            
            temp_outdir = Path(tempfile.mkdtemp(prefix="swift_out_"))
            try:
                if direction == "transfer_analysis":
                    # Mode analyse des transferts - utiliser create_transfer_analysis_workbook
                    from extractor_manager import create_transfer_analysis_workbook
                    suspens_rows = st.session_state.get('suspens_rows', [])
                    exception_mt900_rows = st.session_state.get('exception_mt900_rows', [])
                    unmatched_mt900_rows = st.session_state.get('unmatched_mt900_rows', [])
                    
                    # Convertir les dates en string pour le workbook
                    date_start_str = date_debut.strftime("%Y-%m-%d") if date_debut else None
                    date_end_str = date_fin.strftime("%Y-%m-%d") if date_fin else None
                    
                    out_path = create_transfer_analysis_workbook(
                        rows, suspens_rows, exception_mt900_rows, temp_outdir, 
                        date_start=date_start_str, date_end=date_end_str,
                        unmatched_mt900_rows=unmatched_mt900_rows
                    )
                    
                    # Afficher le résumé
                    st.info(f"📊 **Résumé** : {len(rows)} transfert(s) exécuté(s), {len(unmatched_mt900_rows)} MT900 non matchés, {len(suspens_rows)} en suspens, {len(exception_mt900_rows)} en exception")
                else:
                    out_path = create_workbook(rows, temp_outdir, direction=direction, beaccmcx091_rows=beaccmcx091_rows, exception_323201_rows=exception_323201_rows, other_exceptions_rows=other_exceptions_rows, banque_de_france_rows=banque_de_france_rows)
                with open(out_path, "rb") as f:
                    excel_data = f.read()
                
                # Stocker les données dans session_state pour persister après téléchargement
                st.session_state.excel_data = excel_data
                st.session_state.excel_filename = out_path.name
                st.session_state.extraction_results = {
                    'rows': rows,
                    'beaccmcx091_rows': beaccmcx091_rows,
                    'exception_323201_rows': exception_323201_rows,
                    'all_missing_codes': all_missing_codes,
                    'direction': direction
                }
                
            except Exception as e:
                st.error(f"❌ Impossible de créer le workbook: {e}")
            finally:
                try:
                    shutil.rmtree(temp_outdir, ignore_errors=True)
                except Exception:
                    pass
        
        else:
            st.warning("⚠️ Aucun résultat extrait. Vérifiez le format des PDFs.")
        
        # Show errors
        if errors:
            st.markdown("---")
            st.markdown("### ❌ Erreurs rencontrées")
            for name, msg in errors:
                st.error(f"**{name}** : {msg}")
        
        if st.session_state.excel_data:
            st.success("✅ Extraction terminée ! Les résultats sont affichés ci-dessous.")

# Afficher les résultats stockés (persiste après clic sur téléchargement)
if st.session_state.extraction_results is not None and st.session_state.excel_data is not None:
    st.markdown("---")
    st.markdown("### 📊 Résultats de l'extraction")
    
    results = st.session_state.extraction_results
    rows = results['rows']
    beaccmcx091_rows = results.get('beaccmcx091_rows', [])
    exception_323201_rows = results.get('exception_323201_rows', [])
    all_missing_codes = results.get('all_missing_codes', {"unmapped": set(), "empty": set()})
    direction = results.get('direction', 'incoming')
    
    # Afficher le tableau des résultats
    display_rows = []
    for r in rows:
        display_rows.append({
            "Code Banque": r.get("code_banque"),
            "Date Référence": r.get("date_reference"),
            "Référence": r.get("reference"),
            "Réf. Origine": r.get("reference_origine"),
            "Type MT": r.get("type_MT"),
            "Pays ISO3": r.get("pays_iso3"),
            "Code Donneur": r.get("code_donneur_dordre"),
            "Donneur d'ordre": r.get("donneur_dordre"),
            "Bénéficiaire": r.get("beneficiaire"),
            "Correspondant": r.get("correspondant"),
            "Montant": r.get("montant"),
            "Devise": r.get("devise"),
            "Commentaires": r.get("commentaires"),
            "Source PDF": r.get("source_pdf")
        })
    
    df = pd.DataFrame(display_rows)
    
    # Format montant
    if "Montant" in df.columns:
        try:
            df["Montant"] = df["Montant"].apply(
                lambda x: ("{:,}".format(x)).replace(",", " ") if pd.notnull(x) else x
            )
        except Exception:
            pass
    
    st.dataframe(df, use_container_width=True, height=400)
    
    # Afficher BEACCMCX091 si présents
    if beaccmcx091_rows:
        st.markdown("---")
        st.markdown("### 🔴 Messages BEACCMCX091 (stockés séparément)")
        st.info(f"📌 {len(beaccmcx091_rows)} message(s) BEACCMCX091 détecté(s) et stocké(s) dans une feuille séparée de l'Excel")
        
        beac_display_rows = []
        for r in beaccmcx091_rows:
            beac_display_rows.append({
                "Code Banque": r.get("code_banque"),
                "Date Référence": r.get("date_reference"),
                "Référence": r.get("reference"),
                "Type MT": r.get("type_MT"),
                "Montant": r.get("montant"),
                "Devise": r.get("devise"),
                "Source PDF": r.get("source_pdf")
            })
        df_beac = pd.DataFrame(beac_display_rows)
        st.dataframe(df_beac, use_container_width=True, height=200)
    
    # Afficher exceptions 323201 si présentes
    if exception_323201_rows:
        st.markdown("---")
        st.markdown("### 🟠 Exceptions 323201 (stockées séparément)")
        st.info(f"📌 {len(exception_323201_rows)} message(s) avec 323201 dans F58A détecté(s)")
        
        exc_display_rows = []
        for r in exception_323201_rows:
            exc_display_rows.append({
                "Code Banque": r.get("code_banque"),
                "Date Référence": r.get("date_reference"),
                "Référence": r.get("reference"),
                "Type MT": r.get("type_MT"),
                "Montant": r.get("montant"),
                "Devise": r.get("devise"),
                "Source PDF": r.get("source_pdf")
            })
        df_exc = pd.DataFrame(exc_display_rows)
        st.dataframe(df_exc, use_container_width=True, height=200)
    
    # Statistics
    st.markdown("---")
    st.markdown("### 📈 Statistiques")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Messages extraits", len(rows))
    with col2:
        types_list = [r.get("type_MT") for r in rows if r.get("type_MT")]
        types_count = len(set(types_list))
        st.metric("Types de messages", types_count)
    with col3:
        sources_count = len(set(r.get("source_pdf") for r in rows if r.get("source_pdf")))
        st.metric("Fichiers sources", sources_count)
    
    # Afficher les types de messages détectés
    if types_list:
        types_summary = {}
        for t in types_list:
            types_summary[t] = types_summary.get(t, 0) + 1
        st.caption("**Types détectés:** " + ", ".join([f"{k} ({v})" for k, v in sorted(types_summary.items())]))
    
    # Missing codes display
    if all_missing_codes.get("unmapped") or all_missing_codes.get("empty"):
        st.markdown("---")
        st.markdown("### 📋 Codes BIC manquants")
        
        col1, col2 = st.columns(2)
        
        if all_missing_codes.get("unmapped"):
            with col1:
                st.warning(f"**{len(all_missing_codes['unmapped'])} codes non mappés**")
                st.caption("Codes trouvés dans le PDF mais sans nom de banque mapping :")
                for code in sorted(all_missing_codes["unmapped"]):
                    st.code(code)
        
        if all_missing_codes.get("empty"):
            with col2:
                st.error(f"**{len(all_missing_codes['empty'])} codes vides**")
                st.caption("Champs BIC complètement vides dans le PDF :")
                for code in sorted(all_missing_codes["empty"]):
                    st.code(code)
        
        # Form to add missing BIC codes
        st.markdown("#### ➕ Ajouter un nouveau code BIC")
        
        st.warning("""
            ⚠️ **Important** : Sur Hugging Face Spaces gratuit, les modifications sont **temporaires** 
            et seront perdues au redémarrage de l'application.
            
            **Pour ajouter des codes de manière permanente :**
            - Remplissez le formulaire ci-dessous pour tester immédiatement
            - Ou cliquez sur le bouton "📧 Envoyer par email" pour nous transmettre les codes
        """)
        
        with st.form("add_bic_form"):
            col1, col2 = st.columns(2)
            with col1:
                new_code = st.text_input("Code BIC (8-11 caractères)", max_chars=11, placeholder="Ex: ECOCMLCX")
                new_country = st.text_input("Code ISO3 du pays (3 lettres)", max_chars=3, placeholder="Ex: CMR")
            with col2:
                new_name = st.text_input("Nom de la banque", max_chars=100, placeholder="Ex: ECOBANK CAMEROUN")
                new_reglement = st.text_input("Code de règlement (4 chiffres, optionnel)", max_chars=4, placeholder="Ex: 8101")
            
            col_btn1, col_btn2 = st.columns([3, 2])
            with col_btn1:
                submitted = st.form_submit_button("✅ Ajouter temporairement", use_container_width=True, type="primary")
            with col_btn2:
                if new_code and new_name and new_country:
                    email_subject = "Codes BIC manquants - SWIFT Extractor"
                    email_body = f"""Bonjour,

Je souhaite ajouter un nouveau code BIC à la base de données :

Code BIC : {new_code.upper().strip()}
Nom de la banque : {new_name.strip()}
Pays (ISO3) : {new_country.upper().strip()}
Code de règlement : {new_reglement.strip() if new_reglement else 'Non renseigné'}

Merci de l'ajouter de manière permanente.

Cordialement"""
                    
                    import urllib.parse
                    mailto_link = f"mailto:alannmatistiabou@gmail.com?subject={urllib.parse.quote(email_subject)}&body={urllib.parse.quote(email_body)}"
                    st.markdown(f'<a href="{mailto_link}" target="_blank"><button style="width:100%;padding:0.5rem;background-color:#4CAF50;color:white;border:none;border-radius:0.3rem;cursor:pointer;">📧 Envoyer par email</button></a>', unsafe_allow_html=True)
                else:
                    st.form_submit_button("📧 Envoyer par email", use_container_width=True, disabled=True, help="Remplissez d'abord tous les champs obligatoires")
            
            if submitted:
                if not new_code or not new_name or not new_country:
                    st.error("⚠️ Code BIC, Nom et Pays sont obligatoires")
                elif len(new_code) < 8 or len(new_code) > 11:
                    st.error("⚠️ Le code BIC doit contenir entre 8 et 11 caractères")
                elif len(new_country) != 3:
                    st.error("⚠️ Le code pays doit contenir exactement 3 lettres (ISO3)")
                else:
                    try:
                        from extractors.bic_utils import add_bic_code_to_xlsx
                        
                        bic_file = Path("data/bic_codes.xlsx")
                        if not bic_file.exists():
                            bic_file = ROOT / "data" / "bic_codes.xlsx"
                        
                        add_bic_code_to_xlsx(new_code.upper().strip(), new_name.strip(), new_country.upper().strip(), str(bic_file), new_reglement.strip() if new_reglement else None)
                        st.success(f"✅ Code **{new_code.upper()}** ajouté temporairement !")
                        st.info("💡 Rechargez la page pour utiliser ce code. ⚠️ Il sera perdu au redémarrage du Space.")
                    except Exception as e:
                        st.error(f"❌ Erreur lors de l'ajout du code : {e}")
    
    # Bouton de téléchargement persistant
    st.markdown("---")
    st.markdown("### 💾 Téléchargement")
    st.download_button(
        label="⬇️ Télécharger le fichier Excel",
        data=st.session_state.excel_data,
        file_name=st.session_state.excel_filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        type="primary"
    )
    st.success(f"✅ Workbook prêt: {st.session_state.excel_filename}")
    
    # Aperçu de toutes les feuilles du fichier Excel
    st.markdown("---")
    st.markdown("### 📑 Aperçu de toutes les feuilles du fichier Excel")
    try:
        import io
        from openpyxl import load_workbook
        wb_preview = load_workbook(io.BytesIO(st.session_state.excel_data), read_only=True, data_only=True)
        sheet_names = wb_preview.sheetnames
        
        # Onglets pour chaque feuille
        if len(sheet_names) > 1:
            tabs = st.tabs(sheet_names)
            for tab, sheet_name in zip(tabs, sheet_names):
                with tab:
                    ws = wb_preview[sheet_name]
                    sheet_data = []
                    headers = None
                    for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
                        # Ignorer la ligne "⬅ Retour au summary" et les lignes vides
                        row_str = " ".join(str(c) for c in row if c is not None)
                        if "Retour au summary" in row_str:
                            continue
                        if all(c is None for c in row):
                            continue
                        if headers is None:
                            raw_headers = [str(c) if c is not None else "" for c in row]
                            # Dé-dupliquer les en-têtes (ajouter _1, _2... si nécessaire)
                            seen = {}
                            deduped = []
                            for h in raw_headers:
                                if h in seen:
                                    seen[h] += 1
                                    deduped.append(f"{h}_{seen[h]}" if h else f"col_{seen[h]}")
                                else:
                                    seen[h] = 0
                                    deduped.append(h)
                            headers = deduped
                        else:
                            sheet_data.append(row)
                    
                    if headers and sheet_data:
                        df_sheet = pd.DataFrame(sheet_data, columns=headers)
                        # Formater les montants si la colonne existe
                        if "montant" in df_sheet.columns:
                            try:
                                df_sheet["montant"] = df_sheet["montant"].apply(
                                    lambda x: ("{:,}".format(x)).replace(",", " ") if pd.notnull(x) and isinstance(x, (int, float)) else x
                                )
                            except Exception:
                                pass
                        st.dataframe(df_sheet, use_container_width=True, height=300)
                        st.caption(f"📊 {len(sheet_data)} ligne(s)")
                    elif headers:
                        st.info("Feuille vide (en-têtes uniquement)")
                    else:
                        st.info("Feuille vide")
        wb_preview.close()
    except Exception as e:
        st.warning(f"⚠️ Impossible de charger l'aperçu multi-feuilles: {e}")

# Footer
st.markdown("---")
st.markdown("""
    <div style='text-align: center; color: gray; font-size: 0.8em;'>
        PDF SWIFT Extractor | Déployé sur Hugging Face Spaces
    </div>
""", unsafe_allow_html=True)
