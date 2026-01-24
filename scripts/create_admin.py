
"""One-time bootstrap script to create a Boss user.

Usage:
  python scripts/create_admin.py --username admin --email admin@example.com --password secret
Or provide via env: ADMIN_USERNAME, ADMIN_EMAIL, ADMIN_PASSWORD
"""
import os
import argparse
from getpass import getpass

from backend_core.app.db import SessionLocal, create_db_and_tables
from backend_core.app import models
import bcrypt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--username')
    parser.add_argument('--email')
    parser.add_argument('--password')
    args = parser.parse_args()

    username = args.username or os.getenv('ADMIN_USERNAME')
    email = args.email or os.getenv('ADMIN_EMAIL')
    password = args.password or os.getenv('ADMIN_PASSWORD')
    if not username:
        username = input('Username: ').strip()
    if not email:
        email = input('Email: ').strip()
    if not password:
        password = getpass('Password: ')

    create_db_and_tables()
    db = SessionLocal()
    try:
        existing = db.query(models.User).filter(models.User.username == username).first()
        if existing:
            print('User already exists:', username)
            return
        hashed = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt()
        ).decode("utf-8")
        user = models.User(full_name='Admin', email=email, username=username, password_hash=hashed, role='Boss')
        db.add(user)
        db.commit()
        print('Created Boss user:', username)
    finally:
        db.close()


if __name__ == '__main__':
    main()
