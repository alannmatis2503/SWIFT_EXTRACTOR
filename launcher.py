# launcher.py
import sys
from pathlib import Path

def app_root() -> Path:
    """Return path where bundled files live (PyInstaller _MEIPASS) or project root."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent

ROOT = app_root()
APP_PATH = ROOT / "streamlit_app" / "app.py"

if not APP_PATH.exists():
    print("Erreur: l'application Streamlit introuvable à", APP_PATH)
    sys.exit(1)

# emulate CLI: streamlit run <app.py> --server.headless true --server.port 8501
sys.argv = ["streamlit", "run", str(APP_PATH), "--server.headless", "true", "--server.port", "8501"]

# import the streamlit CLI entrypoint
try:
    # streamlit >=1.0
    from streamlit.web import cli as stcli
except Exception:
    try:
        from streamlit import cli as stcli
    except Exception:
        stcli = None

if not stcli:
    print("Impossible d'importer streamlit. Vérifiez l'environnement.")
    sys.exit(1)

# Run Streamlit
sys.exit(stcli.main())
