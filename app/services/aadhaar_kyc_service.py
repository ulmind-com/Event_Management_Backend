"""
Aadhaar KYC Verification Service
=================================
Production-grade Aadhaar identity verification:
  1. OCR extraction of 12-digit Aadhaar number from uploaded card image
  2. Verhoeff checksum validation (UIDAI standard)
  3. SHA-256 hashing with project salt for secure storage (never stores raw Aadhaar)
  4. Automatic duplicate detection via unique hash index
  5. KYC status lifecycle: PENDING → VERIFIED / DUPLICATE / EXTRACTION_FAILED / REJECTED

Compliance:
  - Only masked Aadhaar (XXXX-XXXX-1234) and SHA-256 hash are stored.
  - Raw 12-digit Aadhaar number is NEVER persisted to disk or database.
  - Meets UIDAI masking guidelines and DPDP Act requirements.
"""

import hashlib
import io
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Tuple

from PIL import Image, ImageFilter

from app.core.config import settings
from app.core.database import kyc_verification_collection, user_collection

logger = logging.getLogger("eventora.aadhaar_kyc")


# ════════════════════════════════════════════════════════════
# Verhoeff Algorithm Lookup Tables (UIDAI Standard for Aadhaar)
# https://en.wikipedia.org/wiki/Verhoeff_algorithm
# ════════════════════════════════════════════════════════════

_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]

_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


class AadhaarKYCService:
    """
    End-to-end Aadhaar KYC verification service.

    Pipeline:
    ┌──────────────┐   ┌───────────────┐   ┌────────────┐   ┌─────────────┐   ┌──────────┐
    │ Image Upload │──▸│ OCR Extraction│──▸│  Verhoeff  │──▸│  SHA-256    │──▸│ Duplicate│
    │ (Aadhaar)    │   │ (Tesseract)   │   │  Validate  │   │  Hash+Mask  │   │ Check    │
    └──────────────┘   └───────────────┘   └────────────┘   └─────────────┘   └──────────┘
    """

    # ── Verhoeff Checksum Validation ──

    @staticmethod
    def _verhoeff_checksum(number: str) -> bool:
        """Validate number using the Verhoeff checksum algorithm (UIDAI standard)."""
        c = 0
        for i, digit in enumerate(reversed(number)):
            c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(digit)]]
        return c == 0

    @classmethod
    def validate_aadhaar_format(cls, aadhaar: str) -> bool:
        """
        Validates Aadhaar number:
        - Exactly 12 digits
        - First digit is 2-9 (UIDAI rule: 0 and 1 are reserved)
        - Passes Verhoeff checksum
        """
        if not aadhaar or len(aadhaar) != 12 or not aadhaar.isdigit():
            return False
        if aadhaar[0] in ("0", "1"):
            return False
        return cls._verhoeff_checksum(aadhaar)

    # ── Hashing & Masking ──

    @staticmethod
    def hash_aadhaar(aadhaar: str) -> str:
        """
        SHA-256 hash with project-specific salt.
        NEVER store raw Aadhaar — only this hash for duplicate detection.
        """
        salt = settings.AADHAAR_HASH_SALT
        return hashlib.sha256(f"{salt}:{aadhaar}".encode("utf-8")).hexdigest()

    @staticmethod
    def mask_aadhaar(aadhaar: str) -> str:
        """Returns UIDAI-compliant masked format: XXXX-XXXX-1234"""
        if not aadhaar or len(aadhaar) != 12:
            return "XXXX-XXXX-XXXX"
        return f"XXXX-XXXX-{aadhaar[-4:]}"

    # ── Document Extraction (PDF, DigiLocker XML, and Images) ──

    @classmethod
    def _extract_from_pdf(cls, pdf_bytes: bytes) -> Tuple[Optional[str], str, bool]:
        """
        Extract text from DigiLocker / e-Aadhaar PDF.
        Returns: (aadhaar_number | None, status, is_digilocker)
        """
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            all_text = ""
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    all_text += " " + text

            is_digilocker = any(
                keyword in all_text.lower()
                for keyword in ("digilocker", "digitallocker", "electronic signature", "uidai")
            )

            pattern = r"[2-9]\d{3}[\s\-]?\d{4}[\s\-]?\d{4}"
            matches = re.findall(pattern, all_text)

            seen = set()
            for match in matches:
                clean = re.sub(r"\D", "", match)
                if len(clean) == 12 and clean not in seen:
                    seen.add(clean)
                    if cls.validate_aadhaar_format(clean):
                        return clean, "EXTRACTED_PDF", is_digilocker

            return None, "NO_AADHAAR_DETECTED_PDF", is_digilocker
        except Exception as e:
            logger.warning(f"PDF extraction error: {e}")
            return None, "PDF_ERROR", False

    @classmethod
    def _extract_from_xml(cls, xml_bytes: bytes) -> Tuple[Optional[str], str, bool]:
        """
        Extract Aadhaar from DigiLocker e-Aadhaar XML.
        Returns: (aadhaar_number | None, status, is_digilocker)
        """
        try:
            text = xml_bytes.decode("utf-8", errors="ignore")
            is_digilocker = "digilocker" in text.lower() or "printletterbarcodedata" in text.lower() or "uiddata" in text.lower()

            # UID attribute pattern: uid="123456789012"
            uid_match = re.search(r'uid=[\'"]([2-9]\d{11})[\'"]', text)
            if uid_match:
                clean = uid_match.group(1)
                if cls.validate_aadhaar_format(clean):
                    return clean, "EXTRACTED_DIGILOCKER_XML", True

            # General pattern in XML text
            pattern = r"[2-9]\d{3}[\s\-]?\d{4}[\s\-]?\d{4}"
            matches = re.findall(pattern, text)
            for match in matches:
                clean = re.sub(r"\D", "", match)
                if len(clean) == 12 and cls.validate_aadhaar_format(clean):
                    return clean, "EXTRACTED_XML", is_digilocker

            return None, "NO_AADHAAR_DETECTED_XML", is_digilocker
        except Exception as e:
            logger.warning(f"XML extraction error: {e}")
            return None, "XML_ERROR", False

    @classmethod
    def _preprocess_image(cls, image_bytes: bytes) -> Image.Image:
        """Preprocess image for optimal OCR accuracy."""
        img = Image.open(io.BytesIO(image_bytes))

        # Convert to grayscale
        img = img.convert("L")

        # Upscale small images for better digit recognition
        width, height = img.size
        if width < 1200:
            scale = 2400 / width
            img = img.resize(
                (int(width * scale), int(height * scale)), Image.LANCZOS
            )

        # Sharpen for clearer digit edges
        img = img.filter(ImageFilter.SHARPEN)

        return img

    @classmethod
    async def extract_aadhaar_from_image(
        cls, image_bytes: bytes
    ) -> Tuple[Optional[str], str]:
        """
        Extract 12-digit Aadhaar number from uploaded card image using Tesseract OCR.
        """
        try:
            import pytesseract
        except ImportError:
            logger.warning("pytesseract is not installed")
            return None, "OCR_UNAVAILABLE"

        tesseract_cmd = settings.TESSERACT_CMD
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

        try:
            img = cls._preprocess_image(image_bytes)

            all_text = ""
            for psm in (6, 3, 11):
                config = f"--oem 3 --psm {psm}"
                try:
                    text = pytesseract.image_to_string(img, config=config)
                    all_text += " " + text
                except Exception:
                    continue

            logger.info(f"OCR extracted {len(all_text)} characters from Aadhaar image")

            pattern = r"[2-9]\d{3}[\s\-]?\d{4}[\s\-]?\d{4}"
            matches = re.findall(pattern, all_text)

            seen = set()
            valid_numbers = []
            unverified_numbers = []

            for match in matches:
                clean = re.sub(r"\D", "", match)
                if len(clean) != 12 or clean in seen:
                    continue
                seen.add(clean)
                if cls.validate_aadhaar_format(clean):
                    valid_numbers.append(clean)
                else:
                    unverified_numbers.append(clean)

            if valid_numbers:
                return valid_numbers[0], "EXTRACTED"
            elif unverified_numbers:
                return unverified_numbers[0], "EXTRACTED_UNVERIFIED"
            else:
                return None, "NO_AADHAAR_DETECTED"

        except Exception as e:
            logger.error(f"OCR extraction failed: {e}")
            return None, "OCR_ERROR"

    @classmethod
    async def extract_aadhaar_from_document(
        cls, doc_bytes: bytes, filename: Optional[str] = None
    ) -> Tuple[Optional[str], str, str]:
        """
        Smart unified document extractor.
        Supports:
          1. DigiLocker PDF / e-Aadhaar PDF
          2. DigiLocker XML
          3. Aadhaar Card Photo / Scan (JPG, PNG, WEBP)
        Returns:
          (aadhaar_number | None, extraction_status, source)
        """
        if not doc_bytes or len(doc_bytes) == 0:
            return None, "EMPTY_FILE", "UNKNOWN"

        fname = (filename or "").lower()

        # 1. Check if PDF (via magic bytes or filename)
        if doc_bytes[:4] == b"%PDF" or fname.endswith(".pdf"):
            aadhaar, status, is_dl = cls._extract_from_pdf(doc_bytes)
            source = "DIGILOCKER_PDF" if is_dl else "PDF"
            if aadhaar:
                return aadhaar, status, source

        # 2. Check if XML
        if doc_bytes[:5] in (b"<?xml", b"<Prin", b"<UidD") or fname.endswith(".xml"):
            aadhaar, status, is_dl = cls._extract_from_xml(doc_bytes)
            source = "DIGILOCKER_XML" if is_dl else "XML"
            if aadhaar:
                return aadhaar, status, source

        # 3. Process as Image (JPG, PNG, etc.)
        try:
            aadhaar, status = await cls.extract_aadhaar_from_image(doc_bytes)
            return aadhaar, status, "IMAGE_OCR"
        except Exception as e:
            logger.warning(f"Image processing failed: {e}")
            return None, "IMAGE_ERROR", "IMAGE"

    # ── Duplicate Detection ──

    @classmethod
    async def check_duplicate(
        cls, aadhaar_hash: str, exclude_email: Optional[str] = None
    ) -> Optional[dict]:
        """
        Check if this Aadhaar hash already exists in the KYC collection.
        Returns existing record if duplicate, None otherwise.
        """
        query = {
            "aadhaar_hash": aadhaar_hash,
            "status": {"$in": ["VERIFIED", "PENDING"]},
        }
        if exclude_email:
            query["user_email"] = {"$ne": exclude_email}
        return await kyc_verification_collection.find_one(query)

    # ════════════════════════════════════════════════
    # Full Verification Pipeline
    # ════════════════════════════════════════════════

    @classmethod
    async def verify_and_register(
        cls,
        user_email: str,
        doc_bytes: bytes,
        filename: Optional[str] = None,
        manual_aadhaar: Optional[str] = None,
    ) -> dict:
        """
        Complete KYC verification pipeline:
          1. Extract Aadhaar from document (PDF / DigiLocker XML / Image OCR)
          2. Validate format + Verhoeff checksum
          3. SHA-256 hash for secure storage
          4. Check duplicates across all accounts
          5. Store verification record
        """
        now = datetime.now(timezone.utc)

        result = {
            "kyc_status": "PENDING",
            "aadhaar_masked": None,
            "message": "",
            "extraction_method": "AUTO_DOCUMENT",
            "source": "DOCUMENT",
            "verhoeff_valid": False,
        }

        aadhaar_number = None
        extraction_status = "NOT_ATTEMPTED"
        source = "DOCUMENT"

        # If a manual or pre-extracted Aadhaar is provided
        if manual_aadhaar:
            clean = re.sub(r"\D", "", manual_aadhaar)
            if len(clean) == 12:
                aadhaar_number = clean
                extraction_status = "PROVIDED"
                source = "DIRECT"

        # Otherwise extract from uploaded document (PDF, XML, or Image)
        if not aadhaar_number and doc_bytes:
            aadhaar_number, extraction_status, source = await cls.extract_aadhaar_from_document(
                doc_bytes, filename=filename
            )

        result["source"] = source

        if not aadhaar_number:
            result["kyc_status"] = "EXTRACTION_FAILED"
            result[
                "message"
            ] = f"Could not extract Aadhaar number from uploaded document ({extraction_status}). Please ensure you upload a clear Aadhaar card image or official DigiLocker PDF."

            await kyc_verification_collection.update_one(
                {"user_email": user_email},
                {
                    "$set": {
                        "user_email": user_email,
                        "status": "EXTRACTION_FAILED",
                        "extraction_status": extraction_status,
                        "verification_source": source,
                        "aadhaar_hash": None,
                        "aadhaar_masked": None,
                        "verhoeff_valid": False,
                        "verified_at": None,
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            return result

        # ── Step 2: Validate Aadhaar format ──
        is_valid = cls.validate_aadhaar_format(aadhaar_number)
        result["verhoeff_valid"] = is_valid

        # ── Step 3: Hash for storage ──
        aadhaar_hash = cls.hash_aadhaar(aadhaar_number)
        aadhaar_masked = cls.mask_aadhaar(aadhaar_number)
        result["aadhaar_masked"] = aadhaar_masked

        # ── Step 4: Check for duplicates ──
        duplicate = await cls.check_duplicate(aadhaar_hash, exclude_email=user_email)
        if duplicate:
            result["kyc_status"] = "DUPLICATE"
            result[
                "message"
            ] = f"This Aadhaar card ({aadhaar_masked}) is already registered with another account. Duplicate registrations are not allowed."

            await kyc_verification_collection.update_one(
                {"user_email": user_email},
                {
                    "$set": {
                        "user_email": user_email,
                        "aadhaar_hash": aadhaar_hash,
                        "aadhaar_masked": aadhaar_masked,
                        "status": "DUPLICATE",
                        "verhoeff_valid": is_valid,
                        "extraction_status": extraction_status,
                        "duplicate_of_email": duplicate.get("user_email"),
                        "verified_at": None,
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            return result

        # ── Step 5: Store verified record ──
        if is_valid:
            result["kyc_status"] = "VERIFIED"
            result[
                "message"
            ] = f"Aadhaar card ({aadhaar_masked}) verified successfully. KYC complete."
        else:
            result["kyc_status"] = "PENDING"
            result[
                "message"
            ] = f"Aadhaar number extracted ({aadhaar_masked}) but checksum verification is pending review."

        await kyc_verification_collection.update_one(
            {"user_email": user_email},
            {
                "$set": {
                    "user_email": user_email,
                    "aadhaar_hash": aadhaar_hash,
                    "aadhaar_masked": aadhaar_masked,
                    "status": result["kyc_status"],
                    "verhoeff_valid": is_valid,
                    "extraction_status": extraction_status,
                    "duplicate_of_email": None,
                    "verified_at": now if is_valid else None,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

        # Update user document with KYC status
        await user_collection.update_one(
            {"email": user_email},
            {
                "$set": {
                    "kyc_status": result["kyc_status"],
                    "kyc_aadhaar_masked": aadhaar_masked,
                    "kyc_verified_at": now if is_valid else None,
                }
            },
        )

        return result

    # ════════════════════════════════════════════════
    # Admin Methods
    # ════════════════════════════════════════════════

    @classmethod
    async def get_kyc_status(cls, user_email: str) -> Optional[dict]:
        """Get KYC verification status for a user."""
        doc = await kyc_verification_collection.find_one(
            {"user_email": user_email}, {"_id": 0}
        )
        return doc

    @classmethod
    async def admin_override_kyc(
        cls,
        user_email: str,
        new_status: str,
        admin_email: str,
        reason: str,
    ) -> Optional[dict]:
        """Admin manually overrides KYC status (VERIFIED or REJECTED)."""
        if new_status not in ("VERIFIED", "REJECTED"):
            return None

        now = datetime.now(timezone.utc)
        result = await kyc_verification_collection.update_one(
            {"user_email": user_email},
            {
                "$set": {
                    "status": new_status,
                    "admin_override": True,
                    "admin_override_by": admin_email,
                    "admin_override_reason": reason,
                    "admin_override_at": now,
                    "updated_at": now,
                    "verified_at": now if new_status == "VERIFIED" else None,
                }
            },
        )

        if result.modified_count > 0:
            await user_collection.update_one(
                {"email": user_email},
                {
                    "$set": {
                        "kyc_status": new_status,
                        "kyc_verified_at": now if new_status == "VERIFIED" else None,
                    }
                },
            )
            return await cls.get_kyc_status(user_email)
        return None

    @classmethod
    async def list_kyc_records(
        cls,
        status_filter: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """List KYC records with optional status filter, paginated."""
        query = {}
        if status_filter:
            query["status"] = status_filter

        total = await kyc_verification_collection.count_documents(query)
        skip = max(0, (page - 1) * page_size)
        cursor = (
            kyc_verification_collection.find(query, {"_id": 0})
            .sort("created_at", -1)
            .skip(skip)
            .limit(page_size)
        )
        items = await cursor.to_list(page_size)

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    @classmethod
    async def get_kyc_analytics(cls) -> dict:
        """Get KYC verification analytics summary."""
        total = await kyc_verification_collection.count_documents({})
        verified = await kyc_verification_collection.count_documents({"status": "VERIFIED"})
        pending = await kyc_verification_collection.count_documents({"status": "PENDING"})
        duplicate = await kyc_verification_collection.count_documents({"status": "DUPLICATE"})
        extraction_failed = await kyc_verification_collection.count_documents({"status": "EXTRACTION_FAILED"})
        rejected = await kyc_verification_collection.count_documents({"status": "REJECTED"})

        return {
            "total_kyc_attempts": total,
            "verified": verified,
            "pending": pending,
            "duplicate_blocked": duplicate,
            "extraction_failed": extraction_failed,
            "rejected": rejected,
            "verification_rate": round((verified / total * 100), 2) if total > 0 else 0,
        }
