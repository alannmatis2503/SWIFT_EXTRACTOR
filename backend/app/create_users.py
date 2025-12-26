# backend/app/create_users.py
#!/usr/bin/env python3
import argparse, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from db import init_db, SessionLocal, create_user

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("password")
    parser.add_argument("--role", default="user", choices=["user", "admin", "superadmin"])
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        u = create_user(db, args.username, args.password, role=args.role)
        print(f"Created user: {u.username} (role={u.role})")
    finally:
        db.close()

if __name__ == "__main__":
    main()
