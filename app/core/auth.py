"""Password hashing and signed browser sessions."""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import UserAccount

password_hash = PasswordHash.recommended()
SESSION_COOKIE = "rag_session"
SESSION_TTL = 60 * 60 * 24 * 7


def serializer(settings):
    secret = settings.session_secret
    if not secret:
        raise RuntimeError("SESSION_SECRET must be set before authentication is used")
    return URLSafeTimedSerializer(secret, salt="rag-browser-session-v1")


def create_account(session, email: str, password: str) -> UserAccount:
    email = email.strip().casefold()
    if len(email) > 320 or "@" not in email or email.startswith("@") or email.endswith("@") or len(password) < 12:
        raise HTTPException(status_code=422, detail="Enter a valid email and a password of at least 12 characters")
    if session.scalar(select(UserAccount.id).where(UserAccount.email == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    account = UserAccount(id=str(uuid4()), email=email, password_hash=password_hash.hash(password))
    session.add(account)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists") from None
    return account


def authenticate(session, email: str, password: str) -> UserAccount:
    account = session.scalar(select(UserAccount).where(UserAccount.email == email.strip().casefold()))
    # Do comparable Argon2 work for unknown users to reduce account-enumeration timing differences.
    if not account:
        password_hash.hash(password)
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    valid, updated_hash = password_hash.verify_and_update(password, account.password_hash)
    if not valid:
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    if updated_hash:
        account.password_hash = updated_hash
        session.commit()
    return account


def issue_session(response, account: UserAccount, settings) -> None:
    token = serializer(settings).dumps({"sub": account.id, "ver": account.session_version,
                                        "iat": int(datetime.now(timezone.utc).timestamp())})
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True,
        secure=settings.session_cookie_secure, samesite="lax", path="/",
    )


def clear_session(response, settings) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=settings.session_cookie_secure,
                           httponly=True, samesite="lax")


def revoke_sessions(request: Request, response) -> None:
    session = request.app.state.session_factory()
    try:
        account = session.get(UserAccount, request.state.owner_id)
        if account:
            account.session_version += 1
            session.commit()
    finally:
        session.close()
    clear_session(response, request.app.state.settings)


def current_user(request: Request) -> UserAccount:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    try:
        data = serializer(request.app.state.settings).loads(token, max_age=SESSION_TTL)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=401, detail="Your session expired. Please sign in again") from None
    session = request.app.state.session_factory()
    try:
        account = session.get(UserAccount, data.get("sub"))
        if not account or data.get("ver") != account.session_version:
            raise HTTPException(status_code=401, detail="Account no longer exists")
        return account
    finally:
        session.close()


def request_owner(request: Request) -> str:
    return current_user(request).id
