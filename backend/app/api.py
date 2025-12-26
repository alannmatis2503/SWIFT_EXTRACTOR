# backend/app/api.py
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from jose import jwt, JWTError
from datetime import timedelta, datetime
from pathlib import Path
import shutil, os
from .db import SessionLocal, init_db, get_user, verify_password
from .db import create_user as create_user_db
from .utils import logger
from .extractor_manager import extract_single, create_workbook
from typing import List

router = APIRouter()
BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
RAW_DIR = BASE.parent / "data" / "raw"
OUT_DIR = BASE.parent / "output" / "tables"
LOG_FILE = BASE.parent.parent / "logs" / "app.log"

# auth / secrets
SECRET_KEY = os.environ.get("JWT_SECRET", "dev_secret_change_me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(status_code=401, detail="Could not validate credentials")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        role: str = payload.get("role")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    db = SessionLocal()
    user = db.query(__import__("app.db", fromlist=["User"]).User).filter_by(username=username).first()
    db.close()
    if not user:
        raise credentials_exception
    return {"username": username, "role": role}

def require_role(min_role: str):
    order = {"user":0, "admin":1, "superadmin":2}
    def dep(user = Depends(get_current_user)):
        if order.get(user["role"], 0) < order.get(min_role, 0):
            raise HTTPException(status_code=403, detail="Insufficient privileges")
        return user
    return dep

@router.post("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    db = SessionLocal()
    try:
        u = db.query(__import__("app.db", fromlist=["User"]).User).filter_by(username=form_data.username).first()
        if not u or not verify_password(form_data.password, u.hashed_password):
            raise HTTPException(status_code=400, detail="Incorrect username or password")
        token = create_access_token({"sub": u.username, "role": u.role})
        return {"access_token": token, "token_type": "bearer"}
    finally:
        db.close()

@router.post("/upload")
async def upload(files: List[UploadFile] = File(...), current = Depends(require_role("user"))):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for up in files:
        dest = RAW_DIR / up.filename
        with open(dest, "wb") as f:
            shutil.copyfileobj(up.file, f)
        logger.info(f"Saved upload {up.filename}")
        r = extract_single(dest)
        rows.append(r)
    out_file = create_workbook(rows, OUT_DIR)
    return FileResponse(str(out_file), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=out_file.name)

@router.get("/runs")
def list_runs(current = Depends(require_role("admin"))):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted([p.name for p in OUT_DIR.glob("*.xlsx")], reverse=True)
    return {"runs": files}

@router.get("/logs")
def get_logs(skip: int = 0, limit: int = 200, current = Depends(require_role("admin"))):
    if not LOG_FILE.exists():
        return {"lines": []}
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    # return last lines
    lines = lines[-(skip + limit):] if (skip+limit) <= len(lines) else lines
    return {"lines": [l.rstrip("\n") for l in lines]}

# bootstrap helper route (only for dev)
@router.post("/bootstrap_users")
def bootstrap_users(current = Depends(require_role("superadmin"))):
    init_db()
    db = SessionLocal()
    # create a default set if not existing
    for u,p,r in [("alice","alicepass","user"),("bob","bobpass","admin"),("root","rootpass","superadmin")]:
        existing = db.query(__import__("app.db", fromlist=["User"]).User).filter_by(username=u).first()
        if not existing:
            create_user_db(db, u, p, r)
    db.close()
    return {"ok": True}
