"""
Admin WebAuthn (fingerprint / Face ID / passkey) sign-in.

Server-side credential storage so a passkey works across the admin's devices:
register once (while signed in with the password), then sign in with the
biometric alone. Discoverable (resident) credentials enable usernameless login,
and platform passkeys that the OS syncs (e.g. the same Google/Apple account on a
laptop and a phone) appear on every synced device without re-registering.

Additive and isolated: this router does not touch the existing password login,
/me, or any other admin endpoint.
"""
import os
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Depends, Body

from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    options_to_json,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
    PublicKeyCredentialDescriptor,
)

from app.core.config import settings
from app.core.security import create_access_token
from app.core.admin_auth import get_current_admin
from app.core.database import (
    admin_collection,
    admin_webauthn_credential_collection as creds,
    admin_webauthn_challenge_collection as challenges,
)
from app.services.audit import log_admin_action

router = APIRouter()

CHALLENGE_TTL_SECONDS = 300


def _now():
    return datetime.now(timezone.utc)


def _resolve_origin(request: Request):
    """Return (origin, rp_id) for an allowlisted Origin, else 400."""
    origin = request.headers.get("origin")
    allow = [o.strip() for o in settings.WEBAUTHN_ORIGINS.split(",") if o.strip()]
    if not origin or origin not in allow:
        raise HTTPException(status_code=400, detail="Origin not allowed for passkey sign-in")
    rp_id = urlparse(origin).hostname
    if not rp_id:
        raise HTTPException(status_code=400, detail="Invalid origin")
    return origin, rp_id


async def _user_handle(admin: dict) -> bytes:
    """Stable per-admin WebAuthn user handle (created lazily)."""
    handle = admin.get("webauthn_user_handle")
    if not handle:
        handle = bytes_to_base64url(os.urandom(16))
        await admin_collection.update_one({"_id": admin["_id"]}, {"$set": {"webauthn_user_handle": handle}})
    return base64url_to_bytes(handle)


# ─────────────── REGISTER (signed-in admin adds a passkey) ───────────────
@router.post("/webauthn/register/options")
async def register_options(request: Request, admin: dict = Depends(get_current_admin)):
    origin, rp_id = _resolve_origin(request)
    existing = [c async for c in creds.find({"email": admin["email"]})]
    opts = generate_registration_options(
        rp_id=rp_id,
        rp_name=settings.WEBAUTHN_RP_NAME,
        user_name=admin["email"],
        user_id=await _user_handle(admin),
        user_display_name=admin["email"],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["_id"])) for c in existing
        ],
    )
    await admin_collection.update_one(
        {"_id": admin["_id"]},
        {"$set": {
            "webauthn_reg_challenge": bytes_to_base64url(opts.challenge),
            "webauthn_reg_rp_id": rp_id,
            "webauthn_reg_origin": origin,
            "webauthn_reg_exp": _now() + timedelta(seconds=CHALLENGE_TTL_SECONDS),
        }},
    )
    return json.loads(options_to_json(opts))


@router.post("/webauthn/register/verify")
async def register_verify(
    request: Request,
    admin: dict = Depends(get_current_admin),
    body: dict = Body(...),
):
    challenge = admin.get("webauthn_reg_challenge")
    exp = admin.get("webauthn_reg_exp")
    if not challenge or not exp:
        raise HTTPException(status_code=400, detail="Passkey setup expired. Try again.")
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if _now() > exp:
        raise HTTPException(status_code=400, detail="Passkey setup expired. Try again.")
    response = body.get("response") or body
    label = (body.get("label") or "").strip() or "This device"
    try:
        verification = verify_registration_response(
            credential=json.dumps(response),
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=admin.get("webauthn_reg_rp_id"),
            expected_origin=admin.get("webauthn_reg_origin"),
            require_user_verification=True,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not verify passkey: {e}")

    cred_id = bytes_to_base64url(verification.credential_id)
    transports = []
    try:
        transports = (response.get("response") or {}).get("transports") or response.get("transports") or []
    except Exception:
        transports = []
    await creds.update_one(
        {"_id": cred_id},
        {"$set": {
            "_id": cred_id,
            "email": admin["email"],
            "public_key": bytes_to_base64url(verification.credential_public_key),
            "sign_count": verification.sign_count,
            "transports": transports,
            "label": label,
            "rp_id": admin.get("webauthn_reg_rp_id"),
            "created_at": _now(),
        }},
        upsert=True,
    )
    await admin_collection.update_one(
        {"_id": admin["_id"]},
        {"$unset": {"webauthn_reg_challenge": "", "webauthn_reg_exp": "", "webauthn_reg_rp_id": "", "webauthn_reg_origin": ""}},
    )
    await log_admin_action(admin["email"], "webauthn_register", "admin", outcome="success", metadata={"label": label})
    return {"message": "Passkey added.", "credential_id": cred_id, "label": label}


# ─────────────── LOGIN (usernameless passkey sign-in) ───────────────
@router.post("/webauthn/login/options")
async def login_options(request: Request):
    origin, rp_id = _resolve_origin(request)
    opts = generate_authentication_options(
        rp_id=rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge_id = bytes_to_base64url(os.urandom(16))
    await challenges.update_one(
        {"_id": challenge_id},
        {"$set": {
            "_id": challenge_id,
            "challenge": bytes_to_base64url(opts.challenge),
            "rp_id": rp_id,
            "origin": origin,
            "exp": _now() + timedelta(seconds=CHALLENGE_TTL_SECONDS),
        }},
        upsert=True,
    )
    return {"challenge_id": challenge_id, "options": json.loads(options_to_json(opts))}


@router.post("/webauthn/login/verify")
async def login_verify(request: Request, body: dict = Body(...)):
    challenge_id = body.get("challenge_id")
    response = body.get("response")
    if not challenge_id or not response:
        raise HTTPException(status_code=400, detail="Missing passkey response")

    ch = await challenges.find_one({"_id": challenge_id})
    if not ch:
        raise HTTPException(status_code=400, detail="Passkey session expired. Try again.")
    exp = ch["exp"]
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if _now() > exp:
        await challenges.delete_one({"_id": challenge_id})
        raise HTTPException(status_code=400, detail="Passkey session expired. Try again.")

    cred_id = response.get("id")
    cred = await creds.find_one({"_id": cred_id})
    if not cred:
        raise HTTPException(status_code=401, detail="This passkey is not registered.")

    admin = await admin_collection.find_one({"email": cred["email"], "role": "super_admin"})
    if not admin:
        raise HTTPException(status_code=403, detail="Admin account not found for this passkey.")

    try:
        verification = verify_authentication_response(
            credential=json.dumps(response),
            expected_challenge=base64url_to_bytes(ch["challenge"]),
            expected_rp_id=ch["rp_id"],
            expected_origin=ch["origin"],
            credential_public_key=base64url_to_bytes(cred["public_key"]),
            credential_current_sign_count=cred.get("sign_count", 0),
            require_user_verification=True,
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Passkey verification failed: {e}")

    await creds.update_one({"_id": cred_id}, {"$set": {"sign_count": verification.new_sign_count, "last_used_at": _now()}})
    await challenges.delete_one({"_id": challenge_id})

    token = create_access_token(
        data={"sub": admin["email"], "type": "admin", "role": admin.get("role", "super_admin")},
        expires_delta=timedelta(minutes=settings.ADMIN_TOKEN_EXPIRE_MINUTES),
    )
    await log_admin_action(admin["email"], "webauthn_login", "admin", outcome="success")
    return {"access_token": token, "token_type": "bearer"}


# ─────────────── MANAGE (list / remove passkeys) ───────────────
@router.get("/webauthn/credentials")
async def list_credentials(admin: dict = Depends(get_current_admin)):
    out = []
    async for c in creds.find({"email": admin["email"]}):
        out.append({
            "id": c["_id"],
            "label": c.get("label", "Device"),
            "created_at": str(c.get("created_at", "")),
            "last_used_at": str(c.get("last_used_at", "")),
        })
    return {"credentials": out, "count": len(out)}


@router.delete("/webauthn/credentials/{credential_id}")
async def delete_credential(credential_id: str, admin: dict = Depends(get_current_admin)):
    res = await creds.delete_one({"_id": credential_id, "email": admin["email"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Passkey not found")
    await log_admin_action(admin["email"], "webauthn_remove", "admin", outcome="success")
    return {"message": "Passkey removed."}
