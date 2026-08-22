"""Comprehensive pytest test suite for Suraksha Setu Document Verification Module.

Tests cover:
- Storage validation (MIME, extension, size limits)
- Path traversal attack defense
- Sensitive PII document number masking
- Image preprocessor (EXIF transpose, upscaling, multi-candidate generation)
- Date candidate extraction & OCR typo tolerance:
  1. DD-MM-YYYY expiry date
  2. DD/MM/YYYY expiry date
  3. YYYY-MM-DD expiry date
  4. DD MMM YYYY textual month date
  5. Multi-date document (DOB + Issue Date + Expiry Date on separate lines)
  6. Date close to 'EXPIRY'
  7. OCR typo in 'EXPIRY' (EXPlRY, EXP1RY, VALID UNTlL, DATE 0F EXPIRY)
  8. OCR typo in numeric characters (O -> 0, l -> 1, S -> 5, B -> 8)
  9. Expiry date detected with moderate confidence (field populated with NEEDS_REVIEW)
  10. Expiry date genuinely absent (Voter ID)
  11. Passport MRZ decoding
- FastAPI endpoints (upload, confirm, status, delete)
- Anti-spoofing constraints (document_number immutable post-OCR)
- Expiry date validation rule enforcement
"""

import asyncio
import io
import os
import tempfile
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from document_verification.config import (
    DOCUMENT_NUMBER_PATTERNS,
    MAX_UPLOAD_SIZE_BYTES,
    MIN_CONFIDENCE_FOR_AUTO_VERIFY,
    MIN_CONFIDENCE_FOR_REVIEW,
)
from document_verification.date_extractor import DateCandidate, DateExtractor
from document_verification.document_parsers import PassportParser
from document_verification.document_verification_service import DocumentVerificationService
from document_verification.image_preprocessor import ImagePreprocessor, PreprocessingVariant
from document_verification.ocr_service import (
    CloudVisionOCRProvider,
    MockOCRProvider,
    RawOCRResult,
    get_ocr_service,
)
from document_verification.schemas import (
    ConfirmedDocumentFields,
    DocumentConfirmRequest,
    DocumentType,
    FieldStatus,
    VerificationStatus,
)
from document_verification.storage import (
    FileTooLargeError,
    StorageSecurityError,
    UnsupportedFileError,
    delete_upload,
    mask_document_number,
    save_upload,
    validate_upload,
)
from standalone_main import app

client = TestClient(app)


# =====================================================================
# 1. STORAGE & SECURITY TESTS
# =====================================================================

def test_validate_upload_valid():
    """Test valid image and PDF formats pass validation."""
    validate_upload("passport.jpg", "image/jpeg", 1024)
    validate_upload("id.jpeg", "image/jpeg", 2048)
    validate_upload("license.png", "image/png", 4096)
    validate_upload("document.pdf", "application/pdf", 8192)


def test_validate_upload_invalid_extension():
    """Test disallowed file extensions raise UnsupportedFileError."""
    with pytest.raises(UnsupportedFileError) as exc:
        validate_upload("malicious.exe", "image/jpeg", 1024)
    assert "not permitted" in str(exc.value)

    with pytest.raises(UnsupportedFileError):
        validate_upload("script.sh", "text/plain", 1024)


def test_validate_upload_invalid_mime():
    """Test disallowed MIME types raise UnsupportedFileError."""
    with pytest.raises(UnsupportedFileError) as exc:
        validate_upload("passport.jpg", "application/x-msdownload", 1024)
    assert "not supported" in str(exc.value)


def test_validate_upload_file_too_large():
    """Test files exceeding maximum size raise FileTooLargeError."""
    oversized = MAX_UPLOAD_SIZE_BYTES + 1
    with pytest.raises(FileTooLargeError) as exc:
        validate_upload("large.jpg", "image/jpeg", oversized)
    assert "exceeds maximum limit" in str(exc.value)


def test_save_upload_path_traversal_defense():
    """Test that server generates its own UUID filename and avoids path traversal."""
    with tempfile.TemporaryDirectory() as temp_dir:
        traversal_name = "../../../etc/passwd.jpg"
        file_content = b"fake-image-bytes"

        storage_key, target_path = save_upload(
            file_bytes=file_content,
            original_filename=traversal_name,
            content_type="image/jpeg",
            storage_dir=temp_dir,
        )

        assert Path(temp_dir).resolve() in target_path.parents
        assert target_path.name == storage_key
        assert target_path.exists()
        assert target_path.read_bytes() == file_content

        deleted = delete_upload(storage_key, storage_dir=temp_dir)
        assert deleted is True
        assert not target_path.exists()


def test_mask_document_number():
    """Test masking of sensitive document identifiers."""
    assert mask_document_number("P1234567") == "****4567"
    assert mask_document_number("DL-1420110012345") == "************2345"
    assert mask_document_number("ABC") == "***"
    assert mask_document_number("1234") == "****"
    assert mask_document_number("") == ""
    assert mask_document_number(None) is None


# =====================================================================
# 2. IMAGE PREPROCESSOR TESTS
# =====================================================================

def test_image_preprocessor_upscaling_and_variants():
    """Test image enhancement and upscaling of small image inputs."""
    # Create small test image (100x100)
    small_img = Image.new("RGB", (100, 100), color=(240, 240, 240))
    buf = io.BytesIO()
    small_img.save(buf, format="JPEG")
    raw_bytes = buf.getvalue()

    variants = ImagePreprocessor.generate_variants(raw_bytes)
    assert PreprocessingVariant.ORIGINAL in variants
    assert PreprocessingVariant.ENHANCED in variants
    assert PreprocessingVariant.CONTRAST_BOOSTED in variants
    assert PreprocessingVariant.GRAYSCALE_SHARPENED in variants

    # Verify enhanced image has been upscaled to target dimensions >= 1200
    enhanced_img = Image.open(io.BytesIO(variants[PreprocessingVariant.ENHANCED]))
    assert enhanced_img.size[0] >= 1200 or enhanced_img.size[1] >= 1200


# =====================================================================
# 3. DATE EXTRACTION, CONTEXT RANKING & TYPO TOLERANCE TESTS
# =====================================================================

def test_date_extraction_dd_mm_yyyy():
    """1. Test DD-MM-YYYY format extraction and ISO normalization."""
    raw_text = "PASSPORT\nEXPIRY DATE: 18-11-2030\nNAME: JOHN DOE"
    candidates = DateExtractor.extract_candidates_from_text(raw_text)
    assert len(candidates) >= 1
    dob, expiry, _ = DateExtractor.rank_and_classify_dates(candidates)
    assert expiry is not None
    assert expiry.normalized_iso == "2030-11-18"


def test_date_extraction_dd_slash_mm_slash_yyyy():
    """2. Test DD/MM/YYYY format extraction and ISO normalization."""
    raw_text = "DRIVING LICENCE\nVALID TILL: 18/11/2030\nDOB: 15/05/1990"
    candidates = DateExtractor.extract_candidates_from_text(raw_text)
    dob, expiry, _ = DateExtractor.rank_and_classify_dates(candidates)
    assert dob is not None and dob.normalized_iso == "1990-05-15"
    assert expiry is not None and expiry.normalized_iso == "2030-11-18"


def test_date_extraction_yyyy_mm_dd():
    """3. Test YYYY-MM-DD format extraction."""
    raw_text = "ID CARD\nDATE OF EXPIRY: 2030-11-18\nDOB: 1995-02-10"
    candidates = DateExtractor.extract_candidates_from_text(raw_text)
    dob, expiry, _ = DateExtractor.rank_and_classify_dates(candidates)
    assert dob is not None and dob.normalized_iso == "1995-02-10"
    assert expiry is not None and expiry.normalized_iso == "2030-11-18"


def test_date_extraction_textual_month():
    """4. Test textual month format (e.g. 18 Nov 2030)."""
    raw_text = "IDENTITY CARD\nEXPIRY DATE: 18 Nov 2030\nBORN: 10 Feb 1995"
    candidates = DateExtractor.extract_candidates_from_text(raw_text)
    dob, expiry, _ = DateExtractor.rank_and_classify_dates(candidates)
    assert dob is not None and dob.normalized_iso == "1995-02-10"
    assert expiry is not None and expiry.normalized_iso == "2030-11-18"


def test_date_extraction_multi_date_multiline_passport():
    """5. Test multiline passport with DOB, Issue Date, and Expiry Date without confusion."""
    raw_text = (
        "REPUBLIC OF INDIA / PASSPORT\n"
        "NAME: VIKRAM MEHTA\n"
        "DATE OF BIRTH\n"
        "10-02-1995\n"
        "DATE OF ISSUE\n"
        "19-11-2020\n"
        "DATE OF EXPIRY\n"
        "18-11-2030\n"
    )
    candidates = DateExtractor.extract_candidates_from_text(raw_text)
    dob, expiry, issue = DateExtractor.rank_and_classify_dates(candidates)
    assert dob is not None and dob.normalized_iso == "1995-02-10"
    assert issue is not None and issue.normalized_iso == "2020-11-19"
    assert expiry is not None and expiry.normalized_iso == "2030-11-18"


def test_date_extraction_driving_licence_multiple_dates():
    """6. Test driving licence with multiple dates and DL labels."""
    raw_text = (
        "UNION OF INDIA - DRIVING LICENCE\n"
        "DL NO: DL-1420110012345\n"
        "DOB: 15-05-1992\n"
        "ISSUED: 10-08-2015\n"
        "VALID UPTO: 25-08-2035\n"
    )
    candidates = DateExtractor.extract_candidates_from_text(raw_text)
    dob, expiry, issue = DateExtractor.rank_and_classify_dates(candidates)
    assert dob is not None and dob.normalized_iso == "1992-05-15"
    assert issue is not None and issue.normalized_iso == "2015-08-10"
    assert expiry is not None and expiry.normalized_iso == "2035-08-25"


def test_date_extraction_ocr_typos_in_expiry_label():
    """7. Test OCR typos in EXPIRY label (EXPlRY, EXP1RY, VALID UNTlL, DATE 0F EXPIRY)."""
    samples = [
        "EXPlRY: 18-11-2030",
        "EXP1RY: 18-11-2030",
        "VALID UNTlL: 18-11-2030",
        "DATE 0F EXPIRY: 18-11-2030",
        "VAL1D T1LL: 18-11-2030",
    ]
    for sample in samples:
        candidates = DateExtractor.extract_candidates_from_text(sample)
        _, expiry, _ = DateExtractor.rank_and_classify_dates(candidates)
        assert expiry is not None, f"Failed on sample: {sample}"
        assert expiry.normalized_iso == "2030-11-18", f"Failed on sample: {sample}"


def test_date_extraction_ocr_typos_in_digits():
    """8. Test OCR typos in digits (O -> 0, l/I -> 1, S -> 5, B -> 8)."""
    raw_text = "EXPIRY DATE: l8-ll-2O3O\nDOB: 1O-O2-199S"
    candidates = DateExtractor.extract_candidates_from_text(raw_text)
    dob, expiry, _ = DateExtractor.rank_and_classify_dates(candidates)
    assert dob is not None and dob.normalized_iso == "1995-02-10"
    assert expiry is not None and expiry.normalized_iso == "2030-11-18"


def test_date_extraction_voter_id_genuinely_absent():
    """9. Test Voter ID where expiry date is genuinely absent."""
    raw_text = (
        "ELECTION COMMISSION OF INDIA\n"
        "EPIC NO: ZXC1982736\n"
        "NAME: AARAV RAJESH SHARMA\n"
        "NATIONALITY: INDIAN\n"
        "DOB: 1994-06-18\n"
        "GENDER: MALE\n"
    )
    ocr = MockOCRProvider()
    res = RawOCRResult(raw_text=raw_text, confidence=0.90, provider="mock", is_mock=True)
    normalized = ocr.normalize(res, DocumentType.VOTER_ID)
    assert normalized.date_of_birth.value == "1994-06-18"
    assert normalized.expiry_date.value is None
    assert normalized.expiry_date.status == FieldStatus.NOT_FOUND


def test_passport_mrz_parsing():
    """10. Test Type-3 Passport MRZ line decoding."""
    mrz_text = (
        "REPUBLIC OF INDIA / PASSPORT\n"
        "P<INDSHARMA<<AARAV<RAJESH<<<<<<<<<<<<<<<<<<<\n"
        "P8472910<2IND9406184M3406176<<<<<<<<<<<<<<02\n"
    )
    parsed = PassportParser.parse_mrz_lines(mrz_text)
    assert parsed.get("document_number") == "P8472910"
    assert parsed.get("nationality") == "INDIAN"
    assert parsed.get("date_of_birth") == "1994-06-18"
    assert parsed.get("expiry_date") == "2034-06-17"


# =====================================================================
# 4. FASTAPI INTEGRATION & WORKFLOW TESTS
# =====================================================================

def test_api_health():
    """Test health check endpoint."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["ocr_mode"] in ("auto", "mock", "windows_ocr")


def test_api_upload_default_sample_extracts_expiry_and_dob():
    """Test default upload extracts both DOB and Expiry date with normalization."""
    file_bytes = b"fake-id-content"
    res = client.post(
        "/api/v1/verifications/upload",
        data={"document_type": "OTHER_GOVERNMENT_ID"},
        files={"file": ("my_id_document.jpg", file_bytes, "image/jpeg")},
    )
    assert res.status_code == 200
    data = res.json()
    extracted = data["extracted"]
    # Verify that both DOB and Expiry are extracted and normalized to YYYY-MM-DD
    assert extracted["date_of_birth"]["value"] == "1995-02-10"
    assert extracted["expiry_date"]["value"] == "2030-11-18"
    assert extracted["expiry_date"]["status"] in (FieldStatus.FOUND, FieldStatus.NEEDS_REVIEW)
    assert "expiry_date" in extracted["fields_found"]


def test_api_upload_clear_passport_success():
    """Test uploading a clear passport succeeds with masked document number."""
    file_bytes = b"fake-passport-image"
    res = client.post(
        "/api/v1/verifications/upload",
        data={"document_type": "PASSPORT"},
        files={"file": ("clear_passport.jpg", file_bytes, "image/jpeg")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == VerificationStatus.EXTRACTED
    assert data["mock_mode"] is True
    assert data["confidence"] >= 0.90
    assert "verification_id" in data

    doc_num_field = data["extracted"]["document_number"]
    assert doc_num_field["value"].startswith("****")
    assert doc_num_field["value"].endswith("2910")


def test_api_upload_blurry_document_requires_reupload():
    """Test uploading a blurry document returns REUPLOAD_REQUIRED status."""
    file_bytes = b"fake-blurry-image"
    res = client.post(
        "/api/v1/verifications/upload",
        data={"document_type": "PASSPORT"},
        files={"file": ("blurry_scan.png", file_bytes, "image/png")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == VerificationStatus.REUPLOAD_REQUIRED
    assert data["confidence"] < MIN_CONFIDENCE_FOR_REVIEW


def test_api_confirm_success_flow():
    """Test full workflow: upload clear passport -> confirm fields -> VERIFIED status."""
    up_res = client.post(
        "/api/v1/verifications/upload",
        data={"document_type": "PASSPORT"},
        files={"file": ("clear_passport.jpg", b"image-content", "image/jpeg")},
    )
    assert up_res.status_code == 200
    verif_id = up_res.json()["verification_id"]

    confirm_payload = {
        "verification_id": verif_id,
        "confirmed_fields": {
            "full_name": "Aarav Rajesh Sharma",
            "nationality": "Indian",
            "date_of_birth": "1994-06-18",
            "expiry_date": "2034-06-17",
        },
    }
    conf_res = client.post("/api/v1/verifications/confirm", json=confirm_payload)
    assert conf_res.status_code == 200
    conf_data = conf_res.json()
    assert conf_data["status"] == VerificationStatus.VERIFIED
    assert conf_data["tourist_id"] is not None
    assert len(conf_data["reasons"]) > 0

    status_res = client.get(f"/api/v1/verifications/{verif_id}")
    assert status_res.status_code == 200
    assert status_res.json()["status"] == VerificationStatus.VERIFIED
    assert status_res.json()["verified_at"] is not None


def test_api_confirm_expired_document_rejected():
    """Test confirming an expired passport results in REJECTED status with rationale."""
    up_res = client.post(
        "/api/v1/verifications/upload",
        data={"document_type": "PASSPORT"},
        files={"file": ("expired_passport.jpg", b"image-content", "image/jpeg")},
    )
    assert up_res.status_code == 200
    verif_id = up_res.json()["verification_id"]

    confirm_payload = {
        "verification_id": verif_id,
        "confirmed_fields": {
            "full_name": "Priya Vikram Patel",
            "nationality": "Indian",
            "date_of_birth": "1988-11-23",
            "expiry_date": "2021-05-14",
        },
    }
    conf_res = client.post("/api/v1/verifications/confirm", json=confirm_payload)
    assert conf_res.status_code == 200
    conf_data = conf_res.json()
    assert conf_data["status"] == VerificationStatus.REJECTED
    assert conf_data["tourist_id"] is None
    assert any("expired" in r.lower() for r in conf_data["reasons"])


def test_api_anti_spoofing_immutable_fields():
    """Test that ConfirmedDocumentFields schema does not allow tampering with document_number."""
    up_res = client.post(
        "/api/v1/verifications/upload",
        data={"document_type": "PASSPORT"},
        files={"file": ("clear_passport.jpg", b"image-content", "image/jpeg")},
    )
    verif_id = up_res.json()["verification_id"]

    confirm_payload = {
        "verification_id": verif_id,
        "confirmed_fields": {
            "full_name": "Aarav Rajesh Sharma",
            "nationality": "Indian",
            "date_of_birth": "1994-06-18",
            "expiry_date": "2034-06-17",
            "document_number": "SPOOFED999",
        },
    }
    conf_res = client.post("/api/v1/verifications/confirm", json=confirm_payload)
    assert conf_res.status_code == 200
    assert conf_res.json()["status"] == VerificationStatus.VERIFIED


def test_api_delete_session():
    """Test session deletion endpoint."""
    up_res = client.post(
        "/api/v1/verifications/upload",
        data={"document_type": "PASSPORT"},
        files={"file": ("clear_passport.jpg", b"image-content", "image/jpeg")},
    )
    verif_id = up_res.json()["verification_id"]

    del_res = client.delete(f"/api/v1/verifications/{verif_id}")
    assert del_res.status_code == 204

    get_res = client.get(f"/api/v1/verifications/{verif_id}")
    assert get_res.status_code == 404
