"""Create or reset a user account for PDF Parse.

Usage:
    python seed_user.py <username> <password>
    python seed_user.py                          # interactive prompts
"""

import sys
import uuid
import getpass
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

from app import get_db, init_db


def create_user(username: str, password: str) -> None:
    init_db()
    db = get_db()

    existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        pw_hash = generate_password_hash(password)
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, existing["id"]))
        db.commit()
        db.close()
        print(f"Updated password for existing user '{username}'.")
        return

    user_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    pw_hash = generate_password_hash(password)

    db.execute(
        "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (user_id, username, pw_hash, now),
    )
    db.commit()
    db.close()
    print(f"Created user '{username}' (id: {user_id}).")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        create_user(sys.argv[1], sys.argv[2])
    else:
        uname = input("Username: ").strip()
        if not uname:
            print("Username cannot be empty.")
            sys.exit(1)
        pw = getpass.getpass("Password: ")
        if not pw:
            print("Password cannot be empty.")
            sys.exit(1)
        create_user(uname, pw)
