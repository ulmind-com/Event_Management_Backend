"""
User WebAuthn (fingerprint / Face ID / passkey) sign-in for the public website.

Mirrors the admin flow in ``admin_webauthn.py`` but for ordinary accounts: a
signed-in user adds a passkey from their profile, then signs in with the
biometric alone. Credentials live server-side, so a passkey the OS syncs (the
same Google / Apple account on a laptop and a phone) works on every synced
device without re-registering, and discoverable (resident) credentials allow
usernameless login.

Additive and isolated: this router does not touch password login, Google
sign-in, /auth/me, or any other user endpoint. User passkeys are stored in their
own collections, and a passkey is bound to the rp_id of the site it was created
on, so an admin-panel passkey can never authenticate a website session.
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
from app.core.database import (
    user_collection,
    user_webauthn_credential_collection as creds,
    user_webauthn_challenge_collection as challenges,
)
from app.api.deps import get_current_user_allow_incomplete
from app.core.profile_lifecycle import check_profile_completion, get_profile_form_schema

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


async def _user_handle(user: dict) -> bytes:
    """Stable per-user WebAuthn user handle (created lazily)."""
    handle = user.get("webauthn_user_handle")
    if not handle:
        handle = bytes_to_base64url(os.urandom(16))
        await user_collection.update_one(
            {"_id": user["_id"]}, {"$set": {"webauthn_user_handle": handle}}
        )
    return base64url_to_bytes(handle)


def _display_name(user: dict) -> str:
    return (user.get("name") or "").strip() or user.get("email", "")


# ─────────────── REGISTER (signed-in user adds a passkey) ───────────────
@router.post("/register/options")
async def register_options(
    request: Request,
    user: dict = Depends(get_current_user_allow_incomplete),
):
    origin, rp_id = _resolve_origin(request)
    existing = [c async for c in creds.find({"email": user["email"]})]
    opts = generate_registration_options(
        rp_id=rp_id,
        rp_name=settings.WEBAUTHN_USER_RP_NAME,
        user_name=user["email"],
        user_id=await _user_handle(user),
        user_display_name=_display_name(user),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        # Stops the browser silently creating a second passkey for a device that
        # already has one.
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["_id"])) for c in existing
        ],
    )
    await user_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "webauthn_reg_challenge": bytes_to_base64url(opts.challenge),
            "webauthn_reg_rp_id": rp_id,
            "webauthn_reg_origin": origin,
            "webauthn_reg_exp": _now() + timedelta(seconds=CHALLENGE_TTL_SECONDS),
        }},
    )
    return json.loads(options_to_json(opts))


@router.post("/register/verify")
async def register_verify(
    request: Request,
    user: dict = Depends(get_current_user_allow_incomplete),
    body: dict = Body(...),
):
    challenge = user.get("webauthn_reg_challenge")
    exp = user.get("webauthn_reg_exp")
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
            expected_rp_id=user.get("webauthn_reg_rp_id"),
            expected_origin=user.get("webauthn_reg_origin"),
            require_user_verification=True,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not verify passkey: {e}")

    cred_id = bytes_to_base64url(verification.credential_id)
    try:
        transports = (response.get("response") or {}).get("transports") or response.get("transports") or []
    except Exception:
        transports = []

    await creds.update_one(
        {"_id": cred_id},
        {"$set": {
            "_id": cred_id,
            "email": user["email"],
            "public_key": bytes_to_base64url(verification.credential_public_key),
            "sign_count": verification.sign_count,
            "transports": transports,
            "label": label,
            "rp_id": user.get("webauthn_reg_rp_id"),
            "created_at": _now(),
        }},
        upsert=True,
    )
    await user_collection.update_one(
        {"_id": user["_id"]},
        {"$unset": {
            "webauthn_reg_challenge": "",
            "webauthn_reg_exp": "",
            "webauthn_reg_rp_id": "",
            "webauthn_reg_origin": "",
        }},
    )
    return {"message": "Passkey added.", "credential_id": cred_id, "label": label}


# ─────────────── LOGIN (usernameless passkey sign-in) ───────────────
@router.post("/login/options")
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


@router.post("/login/verify")
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

    user = await user_collection.find_one({"email": cred["email"]})
    if not user:
        raise HTTPException(status_code=403, detail="Account not found for this passkey.")
    if user.get("is_deleted"):
        raise HTTPException(status_code=403, detail="Account is disabled or deleted. Contact support.")

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

    await creds.update_one(
        {"_id": cred_id},
        {"$set": {"sign_count": verification.new_sign_count, "last_used_at": _now()}},
    )
    await challenges.delete_one({"_id": challenge_id})

    # Same shape the password login returns, so the website's existing
    # post-login routing (profile completion, role redirects) works unchanged.
    email = cred["email"]
    is_completed, missing = check_profile_completion(user)
    form_schema = get_profile_form_schema(user)
    await user_collection.update_one(
        {"email": email},
        {"$set": {
            "registration_complete": is_completed,
            "is_profile_completed": is_completed,
            "profile_completed": is_completed,
            "first_time_login": not is_completed,
            "updated_at": _now(),
        }},
    )

    access_token = create_access_token(data={"sub": email, "type": "user"})
    verified = bool(
        user.get("is_verified")
        or user.get("is_id_verified")
        or user.get("is_govt_id_verified")
        or user.get("kyc_status") == "VERIFIED"
    )
    return {
        "message": "Login successful" if is_completed else "Login successful. Please complete your profile details.",
        "access_token": access_token,
        "token_type": "bearer",
        "user_email": email,
        "role": user.get("role", "USER"),
        "registration_complete": is_completed,
        "is_profile_completed": is_completed,
        "profile_completed": is_completed,
        "requires_profile_completion": not is_completed,
        "prompt_profile_form": not is_completed,
        "first_time_login": not is_completed,
        "missing_fields": missing,
        "next_step": "COMPLETE_PROFILE" if not is_completed else "DASHBOARD",
        "redirect_url": "/complete-profile" if not is_completed else "/dashboard",
        "profile_form": form_schema,
        "user": {
            "email": email,
            "name": user.get("name"),
            "role": user.get("role", "USER"),
            "phone_number": user.get("phone_number"),
            "date_of_birth": user.get("date_of_birth"),
            "address": user.get("address"),
            "parent_phone_number": user.get("parent_phone_number"),
            "profile_photo": user.get("profile_photo"),
            "registration_complete": is_completed,
            "is_profile_completed": is_completed,
            "profile_completed": is_completed,
            "requires_profile_completion": not is_completed,
            "prompt_profile_form": not is_completed,
            "first_time_login": not is_completed,
            "missing_fields": missing,
            "profile_form": form_schema,
            "is_verified": verified,
            "is_id_verified": verified,
            "verified_badge": verified,
            "verified_tick": verified,
            "verification_status": "VERIFIED" if verified else user.get("verification_status", "NOT_VERIFIED"),
        },
    }


# ─────────────── MANAGE (list / remove passkeys) ───────────────
def _iso(value) -> str:
    """ISO-8601 so the browser can parse it in every engine, "" when unset."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return ""


@router.get("/credentials")
async def list_credentials(user: dict = Depends(get_current_user_allow_incomplete)):
    out = []
    async for c in creds.find({"email": user["email"]}):
        out.append({
            "id": c["_id"],
            "label": c.get("label", "Device"),
            "created_at": _iso(c.get("created_at")),
            "last_used_at": _iso(c.get("last_used_at")),
        })
    return {"credentials": out, "count": len(out)}


@router.delete("/credentials/{credential_id:path}")
async def delete_credential(
    credential_id: str,
    user: dict = Depends(get_current_user_allow_incomplete),
):
    res = await creds.delete_one({"_id": credential_id, "email": user["email"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Passkey not found")
    return {"message": "Passkey removed."}
