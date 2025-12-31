# streamlit_app/app.py
# Interface Streamlit pour l'extracteur PDF SWIFT
import sys
from pathlib import Path
import tempfile
import shutil
import io
import traceback

import streamlit as st
import pandas as pd

# --- make project root importable and prefer 'backend' package ---
ROOT = Path(__file__).resolve().parents[1]   # project root: pdf-extractor
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# import backend functions (extractor manager)
try:
    # import extract_single, create_workbook and extract_dispatch in one place
    from backend.app.extractor_manager import extract_single, create_workbook, extract_dispatch
except Exception as e:
    st.error(f"Impossible d'importer l'extracteur backend: {e}")
    st.stop()

from backend.app.extractors import bic_utils

try:
    m = bic_utils.load_bic_mapping()    # retourne dict
    st.write("BIC map size:", len(m))
except Exception as e:
    st.write("BIC load failed:", e)

# UI configuration
st.set_page_config(page_title="PDF SWIFT Extractor (GUI)", layout="wide")
st.title("PDF SWIFT Extractor — Interface clic-clic")

st.markdown(
    """
    **Mode d'emploi rapide**
    - Glisser-déposer un ou plusieurs fichiers PDF ci-dessous.
    - Cliquez sur **Extraire**. Les fichiers sont analysés localement.
    - Téléchargez le workbook Excel ou enregistrez-le sur le serveur.
    """
)

# File uploader (multiple)
uploaded_files = st.file_uploader("Choisir des fichiers PDF", type="pdf", accept_multiple_files=True)

# Date filter
st.markdown("### 📅 Filtre par date")
from datetime import date as date_type
default_date = date_type.today()
selected_date = st.date_input("Sélectionner une date de valeur", value=default_date)

col1, col2 = st.columns([1, 1])
with col1:
    save_mode = st.radio("Mode de sortie", ("Télécharger le workbook", "Enregistrer sur le serveur (output/tables)"))

with col2:
    custom_out = st.text_input("Chemin de sortie (optionnel pour enregistrement serveur)", value=str(ROOT / "output" / "tables"))

run_button = st.button("Extraire")

# Logs viewer
with st.expander("Afficher les derniers logs"):
    log_file = ROOT / "logs" / "app.log"
    if log_file.exists():
        try:
            txt = log_file.read_text(encoding="utf-8")
            lines = txt.strip().splitlines()[-400:]
            st.text_area("logs/app.log (tail)", value="\n".join(lines), height=300)
        except Exception as e:
            st.write("Impossible de lire le fichier de logs:", e)
    else:
        st.write("Aucun fichier de logs trouvé (logs/app.log).")

# helper: save uploaded file to temp path and return Path
def save_uploaded_to_temp(uploaded) -> Path:
    tmpdir = Path(tempfile.mkdtemp(prefix="pdf_extr_"))
    dest = tmpdir / uploaded.name
    with open(dest, "wb") as f:
        f.write(uploaded.getbuffer())
    return dest

if run_button:
    if not uploaded_files:
        st.warning("Aucun fichier sélectionné.")
    else:
        rows = []
        progress = st.progress(0)
        total = len(uploaded_files)
        idx = 0
        errors = []
        tmp_dirs = []  # to cleanup later
        st.info(f"Lancement de l'extraction pour {total} fichier(s)...")

        all_missing_codes = {"unmapped": set(), "empty": set()}

        for uf in uploaded_files:
            idx += 1
            st.write(f"Traitement : **{uf.name}** ({idx}/{total})")
            try:
                tmp_path = save_uploaded_to_temp(uf)
                tmp_dirs.append(tmp_path.parent)
            except Exception as e:
                errors.append((uf.name, f"Impossible d'enregistrer temporairement: {e}"))
                st.error(f"Impossible d'enregistrer temporairement {uf.name}: {e}")
                progress.progress(int(idx / total * 100))
                continue

            try:
                # extract_dispatch retourne (rows, missing_codes)
                new_rows, missing_codes = extract_dispatch(tmp_path)

                # Accumulate missing codes
                all_missing_codes["unmapped"].update(missing_codes.get("unmapped", set()))
                all_missing_codes["empty"].update(missing_codes.get("empty", set()))

                # Normalisations/garanties : mêmes clés pour chaque row, et source_pdf bien renseigné
                for r in new_rows:
                    # garantir la clé 'beneficiaire'
                    if "beneficiaire" not in r:
                        r["beneficiaire"] = None

                    # mapping backward-compatible pour 'donneur_dordre' si l'extracteur a renvoyé 'institution_name'
                    if "donneur_dordre" not in r:
                        if "institution_name" in r and r["institution_name"]:
                            r["donneur_dordre"] = r.get("institution_name")
                        else:
                            r["donneur_dordre"] = None

                    # s'assurer d'un source_pdf correct :
                    # - si l'extracteur n'a pas rempli source_pdf (rare), utiliser le nom du fichier uploadé
                    if not r.get("source_pdf"):
                        r["source_pdf"] = uf.name

                    # pour sécurité, si type_MT est None, on met un placeholder
                    if not r.get("type_MT"):
                        r["type_MT"] = None

                    rows.append(r)

                # message utilisateur synthétique
                types = sorted({rr.get("type_MT") or "type inconnu" for rr in new_rows})
                st.success(f"OK : {len(new_rows)} message(s) traité(s) — types : {', '.join(types)}")

            except Exception as e:
                tb = traceback.format_exc()
                errors.append((uf.name, str(e)))
                st.error(f"Erreur pendant l'extraction de {uf.name} : {e}")
                # affichage du traceback pour debug (expandable)
                with st.expander(f"Détails erreur pour {uf.name}"):
                    st.text(tb)
            progress.progress(int(idx / total * 100))

        # cleanup temp dirs
        for d in tmp_dirs:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass

        progress.empty()

        # Filter rows by selected date
        if selected_date:
            selected_date_str = selected_date.strftime("%Y-%m-%d")
            rows_filtered = [r for r in rows if r.get("date_reference") == selected_date_str]
            if len(rows_filtered) < len(rows):
                st.info(f"📅 Filtrage appliqué : {len(rows_filtered)} message(s) pour la date {selected_date_str} (sur {len(rows)} total)")
            rows = rows_filtered
        
        # assemble display DataFrame (map internal keys -> user-facing labels)
        if rows:
            display_rows = []
            for r in rows:
                display_rows.append({
                    "code_banque": r.get("code_banque"),
                    "date_reference": r.get("date_reference"),
                    "reference": r.get("reference"),
                    "type_MT": r.get("type_MT"),
                    "pays_iso3": r.get("pays_iso3"),
                    "Code du donneur d'ordre": r.get("code_donneur_dordre"),
                    "donneur d'ordre": r.get("donneur_dordre"),
                    "Bénéficiaire": r.get("beneficiaire"),
                    "montant": r.get("montant"),
                    "devise": r.get("devise"),
                    "source_pdf": r.get("source_pdf")
                })

            df = pd.DataFrame(display_rows)

            # format montant column for display (no permanent change to rows)
            if "montant" in df.columns:
                try:
                    df["montant"] = df["montant"].apply(lambda x: ("{:,}".format(x)).replace(",", " ") if pd.notnull(x) else x)
                except Exception:
                    pass

            st.success("Extraction terminée — aperçu ci-dessous")
            st.dataframe(df, use_container_width=True)

            # Display missing codes if any
            if all_missing_codes["unmapped"] or all_missing_codes["empty"]:
                st.markdown("---")
                st.markdown("### 📋 Codes BIC manquants")
                
                col1, col2 = st.columns(2)
                
                if all_missing_codes["unmapped"]:
                    with col1:
                        st.warning(f"**{len(all_missing_codes['unmapped'])} codes non mappés**\n\n"
                                   "Codes trouvés dans le PDF mais sans nom de banque mapping :")
                        for code in sorted(all_missing_codes["unmapped"]):
                            st.code(code)
                
                if all_missing_codes["empty"]:
                    with col2:
                        st.error(f"**{len(all_missing_codes['empty'])} codes vides**\n\n"
                                 "Champs BIC complètement vides dans le PDF :")
                        for code in sorted(all_missing_codes["empty"]):
                            st.code(code)
                
                # Form to add missing BIC codes
                st.markdown("#### ➕ Ajouter un nouveau code BIC")
                st.info("Aidez-nous à améliorer la base de données en renseignant les codes manquants.")
                
                with st.form("add_bic_form"):
                    code = st.text_input("Code BIC (8-11 caractères)", max_chars=11)
                    name = st.text_input("Nom de la banque", max_chars=100)
                    country = st.text_input("Code ISO3 du pays (ex: CMR, GAB)", max_chars=3)
                    
                    submitted = st.form_submit_button("Ajouter le code")
                    
                    if submitted:
                        if not code or not name or not country:
                            st.error("Tous les champs sont obligatoires")
                        else:
                            try:
                                from backend.app.extractors.bic_utils import add_bic_code_to_xlsx
                                
                                # Try to use relative path that works in Streamlit
                                bic_file = Path("data/bic_codes.xlsx")
                                if not bic_file.exists():
                                    # Fallback to absolute path in repo
                                    bic_file = ROOT / "data" / "bic_codes.xlsx"
                                
                                add_bic_code_to_xlsx(code.upper(), name, country.upper(), str(bic_file))
                                st.success(f"✅ Code **{code.upper()}** ajouté avec succès à la base de données !")
                                st.info("Le code sera disponible pour les prochaines extractions.")
                            except Exception as e:
                                st.error(f"Erreur lors de l'ajout du code : {e}")
                
                st.markdown("---")

            # Ensure backward-compatibility: create_workbook expects 'institution_name'
            for r in rows:
                if not r.get("institution_name"):
                    r["institution_name"] = r.get("donneur_dordre")

            # create workbook and either offer download or save on server
            if save_mode == "Télécharger le workbook":
                # create workbook in a temp directory and provide download
                temp_outdir = Path(tempfile.mkdtemp(prefix="swift_out_"))
                try:
                    out_path = create_workbook(rows, temp_outdir)  # returns Path to created workbook
                    with open(out_path, "rb") as f:
                        data = f.read()
                    st.download_button(
                        label="Télécharger le workbook Excel",
                        data=data,
                        file_name=out_path.name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    st.info(f"Workbook généré: {out_path.name} (temp)")
                except Exception as e:
                    st.error(f"Impossible de créer le workbook: {e}")
                finally:
                    # optional: remove temp_outdir after offering download (download keeps data in browser)
                    try:
                        shutil.rmtree(temp_outdir, ignore_errors=True)
                    except Exception:
                        pass
            else:
                # save on server (custom_out or default)
                outdir = Path(custom_out) if custom_out else (ROOT / "output" / "tables")
                outdir.mkdir(parents=True, exist_ok=True)
                try:
                    outpath = create_workbook(rows, outdir)
                    st.success(f"Workbook enregistré : {outpath}")
                    st.write("Fichiers présents dans", outdir)
                    st.write(sorted([p.name for p in outdir.glob("*.xlsx")], reverse=True))
                except Exception as e:
                    st.error(f"Impossible d'enregistrer le workbook sur le serveur: {e}")

        else:
            st.warning("Aucun résultat extrait. Vérifiez le format des PDFs ou les logs.")

        # show errors list if any
        if errors:
            st.markdown("### Erreurs rencontrées")
            for name, msg in errors:
                st.write(f"- **{name}** : {msg}")

        st.info("Opération terminée.")
