# DigiLocker Official OAuth 2.0 Integration & Document Verification

## 1. Architectural Overview

This module implements the officially supported **DigiLocker OAuth 2.0 with PKCE (Proof Key for Code Exchange, S256)** authentication and document verification workflow, compliant with MeitY (Ministry of Electronics and Information Technology, Government of India) specifications.

### Key Architectural Tenets
1. **Zero Third-Party SMS Gateways**: The application never generates, handles, or dispatches mobile OTPs. DigiLocker and UIDAI execute all citizen identity challenges, consent prompts, and SMS OTP deliveries directly on official government infrastructure.
2. **Strict Backend-to-Backend Token Exchange**: Frontend clients and browsers are never exposed to `client_secret` or raw OAuth tokens.
3. **PKCE Security Standard (RFC 7636)**: Every authorization session creates a cryptographically secure `code_verifier` (64 characters) and derives a SHA-256 `code_challenge` (`code_challenge_method=S256`), preventing authorization code interception.
4. **CSRF State Validation**: High-entropy 32-character state tokens with 10-minute automatic TTL protect against cross-site request forgery and replay attacks.
5. **Masked Citizen Data**: Aadhaar numbers are permanently masked (`XXXX-XXXX-1234`) and hashed for duplicate prevention. Raw Aadhaar numbers are never logged or stored unencrypted.
6. **Decoupled User Registration**: Profile creation in Step 3 does not mandate a manual file upload (`govt_id_proof` removed). Citizen KYC verification can be performed seamlessly before or after account creation via the official DigiLocker portal.

---

## 2. OAuth 2.0 Authorization & Verification Flow

```
+-----------+            +-------------------+            +----------------------+
|  Citizen  |            | Eventora Backend  |            | DigiLocker (Govt IN) |
+-----------+            +-------------------+            +----------------------+
      |                            |                                 |
      | 1. GET /api/digilocker/    |                                 |
      |    authorize               |                                 |
      |--------------------------->| (Creates PKCE verifier + S256   |
      |                            |  challenge + CSRF state)        |
      | 2. HTTP 302 Redirect       |                                 |
      |<---------------------------|                                 |
      |                                                              |
      | 3. Redirected to DigiLocker Authorization Portal             |
      |------------------------------------------------------------->|
      |                                                              |
      | 4. Citizen enters Mobile/Aadhaar & OTP on DigiLocker Portal  |
      |<============================================================>|
      |    (DigiLocker sends official SMS directly to citizen)       |
      |                                                              |
      | 5. Citizen gives consent & DigiLocker redirects with code    |
      |<-------------------------------------------------------------|
      |                                                              |
      | 6. GET /api/digilocker/callback?code=...&state=...           |
      |--------------------------->|                                 |
      |                            | 7. POST /oauth2/1/token         |
      |                            |    (Backend exchanges code +    |
      |                            |     code_verifier for token)    |
      |                            |-------------------------------->|
      |                            |<--------------------------------|
      |                            |                                 |
      |                            | 8. GET /oauth2/2/xml/eaadhaar   |
      |                            |-------------------------------->|
      |                            |<--------------------------------|
      |                            | (Parses e-Aadhaar, extracts     |
      |                            |  masked Aadhaar & sets VERIFIED)|
      | 9. Returns Verified Page   |                                 |
      |<---------------------------|                                 |
```

---

## 3. API Endpoints Specification

### 3.1. Initiate Authorization
* **Endpoint:** `GET /api/digilocker/authorize`
* **Query Parameters:**
  * `user_email` *(optional, string)*: Citizen email associated with the session.
  * `redirect_uri` *(optional, string)*: Callback URI registered with DigiLocker.
  * `format` *(optional, string)*: `"redirect"` (default HTTP 302) or `"json"` for SPA/mobile clients.
* **Headers:** `Authorization: Bearer <jwt_token>` *(optional)*
* **Responses:**
  * `302 Found`: Redirects browser directly to `DIGILOCKER_AUTH_URL`.
  * `200 OK` (when `format=json`):
    ```json
    {
      "success": true,
      "authorization_url": "https://api.digitallocker.gov.in/public/oauth2/1/authorize?response_type=code&client_id=...&state=...&code_challenge=...&code_challenge_method=S256&scope=openid+profile+eaadhaar",
      "state": "b8f4c2810a99...",
      "redirect_uri": "http://localhost:8000/api/digilocker/callback"
    }
    ```

---

### 3.2. Authorization Callback
* **Endpoint:** `GET /api/digilocker/callback`
* **Query Parameters:**
  * `code` *(string)*: One-time authorization code generated by DigiLocker.
  * `state` *(string)*: State token for CSRF verification.
  * `error` *(optional, string)*: OAuth error code (e.g., `access_denied` if user cancels).
  * `error_description` *(optional, string)*: Human-readable error message.
  * `format` *(optional, string)*: Pass `"json"` to receive JSON response.
* **Responses:**
  * `200 OK` (Browser): Renders an official success verification page which posts `DIGILOCKER_VERIFIED` to the parent window and closes the modal.
  * `200 OK` (JSON):
    ```json
    {
      "success": true,
      "message": "Aadhaar KYC successfully verified directly through official DigiLocker portal.",
      "kyc_status": "VERIFIED",
      "verification_id": "dl_oauth_98af7120a",
      "aadhaar_masked": "XXXX-XXXX-4912",
      "doc_uri": "digilocker://org.uidai/aadhaar/XXXX-XXXX-4912",
      "verified_details": {
        "name": "Verified Citizen",
        "date_of_birth": "1998-07-21",
        "gender": "M",
        "doc_uri": "digilocker://org.uidai/aadhaar/XXXX-XXXX-4912"
      }
    }
    ```
  * `400 Bad Request`: If state is invalid/expired or user rejected consent.

---

### 3.3. Check Verification Status
* **Endpoint:** `GET /api/digilocker/status`
* **Query Parameters:** `user_email` *(string)*
* **Headers:** `Authorization: Bearer <jwt_token>` *(optional)*
* **Response:** `200 OK`
  ```json
  {
    "user_email": "citizen@example.com",
    "kyc_status": "VERIFIED",
    "verified": true,
    "verification_source": "DIGILOCKER_OAUTH",
    "verification_id": "dl_oauth_98af7120a",
    "aadhaar_masked": "XXXX-XXXX-4912",
    "doc_uri": "digilocker://org.uidai/aadhaar/XXXX-XXXX-4912",
    "verified_at": "2026-10-18T17:15:30Z",
    "message": "Aadhaar successfully verified via official DigiLocker integration."
  }
  ```

---

### 3.4. Revoke Verification
* **Endpoint:** `POST /api/digilocker/revoke`
* **Headers:** `Authorization: Bearer <jwt_token>` *(optional)*
* **Body:**
  ```json
  {
    "user_email": "citizen@example.com"
  }
  ```
* **Response:** `200 OK`
  ```json
  {
    "success": true,
    "user_email": "citizen@example.com",
    "message": "DigiLocker verification status successfully revoked and reset."
  }
  ```

---

## 4. Configuration & Production Activation

### Environment Variables
Configure the following in your `.env` file (refer to `.env.example`):

| Variable | Description | Default |
|---|---|---|
| `DIGILOCKER_CLIENT_ID` | Client ID issued by DigiLocker Partner Portal | *(Provided upon approval)* |
| `DIGILOCKER_CLIENT_SECRET` | Client Secret issued by DigiLocker Partner Portal | *(Keep secret, backend-only)* |
| `DIGILOCKER_REDIRECT_URI` | Whitelisted callback URL matching portal | `http://localhost:8000/api/digilocker/callback` |
| `DIGILOCKER_AUTH_URL` | Official DigiLocker Authorization endpoint | `https://api.digitallocker.gov.in/public/oauth2/1/authorize` |
| `DIGILOCKER_TOKEN_URL` | Official DigiLocker Token endpoint | `https://api.digitallocker.gov.in/public/oauth2/1/token` |
| `DIGILOCKER_USER_INFO_URL`| Official DigiLocker User info endpoint | `https://api.digitallocker.gov.in/public/oauth2/1/user` |
| `DIGILOCKER_EAADHAAR_URL` | Official DigiLocker e-Aadhaar XML endpoint | `https://api.digitallocker.gov.in/public/oauth2/2/xml/eaadhaar` |
| `DIGILOCKER_REVOKE_URL`   | Official DigiLocker Token Revoke endpoint | `https://api.digitallocker.gov.in/public/oauth2/1/revoke` |
| `DIGILOCKER_ENV`          | Mode: `"sandbox"` or `"production"` | `"sandbox"` |
| `DIGILOCKER_SCOPE`        | OAuth 2.0 Scope permissions | `openid profile eaadhaar` |

### Pre-Activation vs Live Production
* **Sandbox Mode (`DIGILOCKER_ENV=sandbox`)**:
  When live partner credentials have not yet been granted by MeitY, the backend automatically operates in certified Sandbox Mode. This allows all unit, integration, and UI acceptance tests to run cleanly.
* **Production Live Activation**:
  To go live in production:
  1. Register your organization at [partners.digitallocker.gov.in](https://partners.digitallocker.gov.in/).
  2. Complete your entity agreement with MeitY and whitelist your production callback URL (`https://yourdomain.com/api/digilocker/callback`).
  3. Add your issued `DIGILOCKER_CLIENT_ID` and `DIGILOCKER_CLIENT_SECRET` to your production `.env` and set `DIGILOCKER_ENV=production`.

---

## 5. User Profile Registration Form (Step 3)

The legacy manual file upload requirement (`govt_id_proof`) has been removed end-to-end:
* **Route:** `POST /api/auth/register/step3`
* **Profile Fields Required:**
  * `name` (string)
  * `phone_number` (int)
  * `date_of_birth` (string, YYYY-MM-DD)
  * `address` (string)
  * `password` (string)
  * `confirm_password` (string)
  * `parent_phone_number` (optional int for minors)
  * `profile_photo` (optional file)
* **Zero Manual File Upload**: No PDF, JPG, or PNG upload is requested for government ID.
* **Decoupled Verification**: If the user has already completed DigiLocker OAuth verification, their profile is automatically tied to their verified `kyc_doc_uri`. If not, their basic account setup succeeds immediately with `kyc_status: "NOT_VERIFIED"`, and they can verify at any time via `/api/digilocker/authorize`.
