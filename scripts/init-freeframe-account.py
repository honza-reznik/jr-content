"""Idempotentní založení místního servisního účtu; pouze v Dockeru."""
import os

from apps.api.database import SessionLocal
from apps.api.models.user import User, UserStatus
from apps.api.services.auth_service import hash_password, verify_password


def main():
    password = os.environ["JRC_FF_SYNC_PASSWORD"]
    if not password:
        raise RuntimeError("Chybí JRC_FF_SYNC_PASSWORD; spusťte make env.")
    with SessionLocal() as db:
        email = os.environ["JRC_FF_SYNC_EMAIL"]
        user = db.query(User).filter(User.email == email).one_or_none()
        if user is None:
            user = User(email=email, name="jrc-sync")
            db.add(user)
        user.name = "jrc-sync"
        user.is_superadmin = True
        user.email_verified = True
        user.status = UserStatus.active
        user.deleted_at = None
        if not user.password_hash or not verify_password(password, user.password_hash):
            user.password_hash = hash_password(password)
            # Změna hesla zneplatní dříve vydané přístupové tokeny.
            user.token_version = (user.token_version or 1) + 1
        db.commit()
    print("Servisní účet jrc-sync připraven (superadmin); heslo zůstává v .env.")


if __name__ == "__main__":
    main()
