from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from database.schema import init_database

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
VERIFICATION_CODE_DIGITS = 6
MAX_VERIFICATION_ATTEMPTS = 6
VERIFICATION_RESEND_SECONDS = 60


class AuthError(ValueError):
    pass


class AuthRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        init_database(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def create_user(
        self,
        email: str,
        password: str,
        *,
        email_verified: bool = False,
    ) -> dict[str, Any]:
        normalized_email = _normalize_email(email)
        _validate_password(password)
        salt = os.urandom(16)
        password_hash = _hash_password(password, salt)
        email_verified_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S") if email_verified else None
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO users(email, password_hash, password_salt, email_verified_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (normalized_email, password_hash.hex(), salt.hex(), email_verified_at),
                )
                conn.commit()
                user_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise AuthError("Un compte existe déjà pour cette adresse e-mail") from exc
        return {
            "id": user_id,
            "email": normalized_email,
            "email_verified": email_verified,
        }

    def ensure_user(self, email: str, password: str, *, email_verified: bool = True) -> dict[str, Any]:
        normalized_email = _normalize_email(email)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, email, email_verified_at FROM users WHERE email = ?",
                (normalized_email,),
            ).fetchone()
            if row:
                if email_verified and not row["email_verified_at"]:
                    conn.execute(
                        "UPDATE users SET email_verified_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (row["id"],),
                    )
                    conn.execute(
                        "DELETE FROM email_verification_codes WHERE user_id = ?",
                        (row["id"],),
                    )
                    conn.commit()
                return {
                    "id": row["id"],
                    "email": row["email"],
                    "email_verified": bool(row["email_verified_at"]) or email_verified,
                }
        return self.create_user(normalized_email, password, email_verified=email_verified)

    def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        normalized_email = _normalize_email(email)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, email, password_hash, password_salt, email_verified_at
                FROM users
                WHERE email = ? AND is_active = 1
                """,
                (normalized_email,),
            ).fetchone()
            if not row:
                return None
            actual = _hash_password(password, bytes.fromhex(row["password_salt"]))
            expected = bytes.fromhex(row["password_hash"])
            if not hmac.compare_digest(actual, expected):
                return None
            if not row["email_verified_at"]:
                raise AuthError("Veuillez d'abord confirmer votre adresse e-mail")
            conn.execute(
                "UPDATE users SET last_login_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
            conn.commit()
        return {"id": row["id"], "email": row["email"]}

    def create_verification_code(
        self,
        email: str,
        *,
        duration_minutes: int,
        enforce_cooldown: bool = False,
    ) -> tuple[dict[str, Any], str]:
        normalized_email = _normalize_email(email)
        code = f"{secrets.randbelow(10**VERIFICATION_CODE_DIGITS):0{VERIFICATION_CODE_DIGITS}d}"
        expires_at = datetime.now(UTC) + timedelta(minutes=duration_minutes)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT u.id, u.email, u.email_verified_at, c.created_at AS code_created_at
                FROM users u
                LEFT JOIN email_verification_codes c ON c.user_id = u.id
                WHERE u.email = ? AND u.is_active = 1
                """,
                (normalized_email,),
            ).fetchone()
            if not row:
                raise AuthError("Compte introuvable")
            if row["email_verified_at"]:
                raise AuthError("Cette adresse e-mail est déjà confirmée")
            if enforce_cooldown and row["code_created_at"]:
                created_at = datetime.fromisoformat(str(row["code_created_at"])).replace(tzinfo=UTC)
                elapsed = (datetime.now(UTC) - created_at).total_seconds()
                if elapsed < VERIFICATION_RESEND_SECONDS:
                    remaining = max(1, int(VERIFICATION_RESEND_SECONDS - elapsed))
                    raise AuthError(f"Attendez {remaining} secondes avant un nouvel envoi")
            conn.execute(
                """
                INSERT INTO email_verification_codes(user_id, code_hash, expires_at, attempts)
                VALUES(?, ?, ?, 0)
                ON CONFLICT(user_id) DO UPDATE SET
                    code_hash = excluded.code_hash,
                    created_at = CURRENT_TIMESTAMP,
                    expires_at = excluded.expires_at,
                    attempts = 0
                """,
                (
                    row["id"],
                    _hash_verification_code(int(row["id"]), code),
                    expires_at.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            conn.commit()
        return {"id": row["id"], "email": row["email"]}, code

    def verify_email(self, email: str, code: str) -> dict[str, Any]:
        normalized_email = _normalize_email(email)
        normalized_code = code.strip()
        if not normalized_code.isdigit() or len(normalized_code) != VERIFICATION_CODE_DIGITS:
            raise AuthError("Le code doit contenir 6 chiffres")

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT u.id, u.email, u.email_verified_at,
                       c.code_hash, c.expires_at, c.attempts
                FROM users u
                LEFT JOIN email_verification_codes c ON c.user_id = u.id
                WHERE u.email = ? AND u.is_active = 1
                """,
                (normalized_email,),
            ).fetchone()
            if not row:
                raise AuthError("Compte introuvable")
            if row["email_verified_at"]:
                return {"id": row["id"], "email": row["email"]}
            if not row["code_hash"]:
                raise AuthError("Demandez un nouveau code de vérification")
            if row["attempts"] >= MAX_VERIFICATION_ATTEMPTS:
                raise AuthError("Trop de tentatives. Demandez un nouveau code")
            if row["expires_at"] <= datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"):
                raise AuthError("Ce code a expiré. Demandez un nouveau code")

            expected = row["code_hash"]
            actual = _hash_verification_code(int(row["id"]), normalized_code)
            if not hmac.compare_digest(actual, expected):
                conn.execute(
                    "UPDATE email_verification_codes SET attempts = attempts + 1 WHERE user_id = ?",
                    (row["id"],),
                )
                conn.commit()
                raise AuthError("Code de vérification incorrect")

            conn.execute(
                "UPDATE users SET email_verified_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],),
            )
            conn.execute("DELETE FROM email_verification_codes WHERE user_id = ?", (row["id"],))
            conn.commit()
        return {"id": row["id"], "email": row["email"]}

    def delete_verification_code(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM email_verification_codes WHERE user_id = ?", (user_id,))
            conn.commit()

    def delete_unverified_user(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM users WHERE id = ? AND email_verified_at IS NULL",
                (user_id,),
            )
            conn.commit()

    def create_session(self, user_id: int, duration_days: int) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = _hash_token(token)
        expires_at = datetime.now(UTC) + timedelta(days=duration_days)
        with self._connect() as conn:
            conn.execute("DELETE FROM user_sessions WHERE expires_at <= datetime('now')")
            conn.execute(
                """
                INSERT INTO user_sessions(token_hash, user_id, expires_at)
                VALUES(?, ?, ?)
                """,
                (token_hash, user_id, expires_at.strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
        return token

    def get_session_user(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT u.id, u.email
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ?
                  AND s.expires_at > datetime('now')
                  AND u.is_active = 1
                """,
                (_hash_token(token),),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token: str) -> None:
        if not token:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM user_sessions WHERE token_hash = ?", (_hash_token(token),))
            conn.commit()


def _normalize_email(email: str) -> str:
    normalized = email.strip().casefold()
    if not EMAIL_RE.fullmatch(normalized):
        raise AuthError("Adresse e-mail invalide")
    return normalized


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise AuthError("Le mot de passe doit contenir au moins 8 caractères")
    if len(password) > 256:
        raise AuthError("Le mot de passe est trop long")


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    )


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_verification_code(user_id: int, code: str) -> str:
    return hashlib.sha256(f"{user_id}:{code}".encode("utf-8")).hexdigest()
