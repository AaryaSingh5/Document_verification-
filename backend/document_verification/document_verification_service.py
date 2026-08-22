"""Business logic service for document verification in Suraksha Setu.

Coordinates:
- Secure file storage
- OCR extraction & normalization
- PII masking
- Multi-step validation (required fields, expiration, DOB sanity, pattern checks)
- Anti-spoofing constraints (document_number immutable post-OCR)
"""

import copy
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional
from uuid import UUID, uuid4

from .config import (
    DOCUMENT_NUMBER_PATTERNS,
    DOCUMENT_TYPES_WITH_EXPIRY,
    MIN_CONFIDENCE_FOR_AUTO_VERIFY,
    MIN_CONFIDENCE_FOR_REVIEW,
    REQUIRED_FIELDS,
)
from .ocr_service import OCRService, get_ocr_service
from .schemas import (
    ConfirmedDocumentFields,
    DocumentConfirmResponse,
    DocumentType,
    DocumentUploadResponse,
    ExtractedDocumentData,
    ExtractedField,
    FieldStatus,
    VerificationStatus,
    VerificationStatusResponse,
)
from .storage import (
    delete_upload,
    mask_document_number,
    save_upload,
)

logger = logging.getLogger(__name__)


@dataclass
class VerificationRecord:
    """Internal entity representing an active or completed verification transaction."""

    verification_id: UUID
    tourist_id: Optional[UUID]
    document_type: DocumentType
    status: VerificationStatus
    confidence: float
    storage_key: str
    raw_document_number: Optional[str]  # Kept internally for validation; never exposed
    extracted_data: ExtractedDocumentData
    confirmed_fields: Optional[ConfirmedDocumentFields]
    reasons: List[str]
    is_mock: bool
    created_at: datetime
    verified_at: Optional[datetime] = None


class DocumentVerificationService:
    """Core domain service managing the verification lifecycle."""

    def __init__(self, ocr_service: Optional[OCRService] = None):
        self.ocr_service = ocr_service or get_ocr_service()
        # In-memory store for standalone execution / dev mode
        self._store: Dict[UUID, VerificationRecord] = {}

    async def process_upload(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        document_type: DocumentType,
        tourist_id: Optional[UUID] = None,
    ) -> DocumentUploadResponse:
        """Handle document upload, secure storage, OCR extraction, and PII masking.

        Args:
            file_bytes: Uploaded binary content.
            filename: Client-provided filename.
            content_type: MIME type string.
            document_type: Document type enum.
            tourist_id: Optional associated tourist UUID.

        Returns:
            DocumentUploadResponse with masked document number and extraction status.
        """
        # 1. Secure storage
        storage_key, _ = save_upload(file_bytes, filename, content_type)

        # 2. Run OCR Extraction
        raw_ocr = await self.ocr_service.extract_document_data(
            file_bytes, filename, document_type
        )

        # 3. Normalize structured fields
        extracted = self.ocr_service.normalize(raw_ocr, document_type)
        raw_doc_number = (
            extracted.document_number.value
            if extracted.document_number
            else None
        )

        # 4. Assess initial verification state
        confidence = raw_ocr.confidence
        has_required = all(
            f in extracted.fields_found for f in REQUIRED_FIELDS
        )

        if confidence < MIN_CONFIDENCE_FOR_REVIEW:
            status = VerificationStatus.REUPLOAD_REQUIRED
            message = (
                "Document image quality is too low or unreadable. "
                "Please upload a clear, well-lit photograph of the document."
            )
        elif confidence < MIN_CONFIDENCE_FOR_AUTO_VERIFY or not has_required:
            status = VerificationStatus.PENDING_REVIEW
            if extracted.fields_missing:
                missing_names = ", ".join(extracted.fields_missing)
                message = (
                    f"Document scanned with warnings. Missing expected fields: ({missing_names}). "
                    "Please review and enter required details."
                )
            else:
                review_fields = [
                    f
                    for f in ("full_name", "document_number", "date_of_birth", "expiry_date")
                    if getattr(extracted, f).status == FieldStatus.NEEDS_REVIEW
                ]
                review_names = ", ".join(review_fields) if review_fields else "some fields"
                message = (
                    f"Document extracted with moderate confidence. "
                    f"Please review detected fields ({review_names}) and confirm your details."
                )
        else:
            status = VerificationStatus.EXTRACTED
            message = (
                "Document scanned successfully. Please review your details and confirm."
            )

        verification_id = uuid4()
        now = datetime.now(timezone.utc)

        # 5. Persist record in store (with unmasked document number stored internally)
        record = VerificationRecord(
            verification_id=verification_id,
            tourist_id=tourist_id,
            document_type=document_type,
            status=status,
            confidence=confidence,
            storage_key=storage_key,
            raw_document_number=raw_doc_number,
            extracted_data=extracted,
            confirmed_fields=None,
            reasons=[],
            is_mock=raw_ocr.is_mock,
            created_at=now,
            verified_at=None,
        )
        self._store[verification_id] = record

        # 6. Prepare public response with masked document number
        public_extracted = copy.deepcopy(extracted)
        if public_extracted.document_number.value:
            public_extracted.document_number.value = mask_document_number(
                public_extracted.document_number.value
            )

        return DocumentUploadResponse(
            verification_id=verification_id,
            document_type=document_type,
            status=status,
            confidence=confidence,
            extracted=public_extracted,
            mock_mode=raw_ocr.is_mock,
            message=message,
        )

    async def confirm_verification(
        self,
        verification_id: UUID,
        confirmed_fields: ConfirmedDocumentFields,
        tourist_id: Optional[UUID] = None,
    ) -> DocumentConfirmResponse:
        """Validate confirmed fields against security rules and make final verification decision.

        ANTI-SPOOFING ENFORCEMENT:
        - `document_number` and `document_type` are taken strictly from the server-side
          record and cannot be modified by user submission.

        Args:
            verification_id: Unique verification session identifier.
            confirmed_fields: User-confirmed fields.
            tourist_id: Optional tourist UUID.

        Returns:
            DocumentConfirmResponse with status and reasons.

        Raises:
            KeyError: If verification session is not found.
        """
        record = self._store.get(verification_id)
        if not record:
            raise KeyError(f"Verification session '{verification_id}' not found.")

        # Update record with submitted fields
        record.confirmed_fields = confirmed_fields
        reasons: List[str] = []

        # Effective values (user-confirmed overrides OCR for editable fields only)
        effective_name = (
            confirmed_fields.full_name
            or (record.extracted_data.full_name.value if record.extracted_data.full_name else None)
        )
        effective_nationality = (
            confirmed_fields.nationality
            or (record.extracted_data.nationality.value if record.extracted_data.nationality else None)
        )
        effective_dob = (
            confirmed_fields.date_of_birth
            or (record.extracted_data.date_of_birth.value if record.extracted_data.date_of_birth else None)
        )
        effective_expiry = (
            confirmed_fields.expiry_date
            or (record.extracted_data.expiry_date.value if record.extracted_data.expiry_date else None)
        )

        # IMMUTABLE: document number and document type are locked
        effective_doc_number = record.raw_document_number
        doc_type_str = record.document_type.value if hasattr(record.document_type, "value") else str(record.document_type)

        # --- Rule 1: Required Fields Check ---
        if not effective_name or not effective_name.strip():
            reasons.append("Full Name is missing or empty.")

        if not effective_doc_number or not effective_doc_number.strip():
            reasons.append("Document Number could not be read from the document image.")

        if not effective_dob or not effective_dob.strip():
            reasons.append("Date of Birth is missing or empty.")

        # --- Rule 2: Expiry Date Validation ---
        if doc_type_str in DOCUMENT_TYPES_WITH_EXPIRY:
            if not effective_expiry or not effective_expiry.strip():
                reasons.append(
                    f"Document type '{doc_type_str}' requires a valid expiry date."
                )
            else:
                try:
                    exp_date = datetime.strptime(
                        effective_expiry.strip(), "%Y-%m-%d"
                    ).date()
                    if exp_date <= date.today():
                        reasons.append(
                            f"Document has expired on {effective_expiry}. An unexpired document is required for verification."
                        )
                except ValueError:
                    reasons.append(
                        f"Expiry date '{effective_expiry}' is not in valid YYYY-MM-DD format."
                    )

        # --- Rule 3: Date of Birth Sanity Check ---
        if effective_dob and effective_dob.strip():
            try:
                dob_date = datetime.strptime(
                    effective_dob.strip(), "%Y-%m-%d"
                ).date()
                if dob_date >= date.today():
                    reasons.append("Date of birth must be a past date.")
            except ValueError:
                reasons.append(
                    f"Date of birth '{effective_dob}' is not in valid YYYY-MM-DD format."
                )

        # --- Rule 4: Document Number Format Sanity Check ---
        if effective_doc_number:
            pattern = DOCUMENT_NUMBER_PATTERNS.get(doc_type_str)
            if pattern:
                cleaned_num = effective_doc_number.replace(" ", "").replace("-", "")
                if not re.match(pattern, effective_doc_number, re.IGNORECASE) and not re.match(pattern, cleaned_num, re.IGNORECASE):
                    reasons.append(
                        f"Document number '{effective_doc_number}' does not match expected format for {doc_type_str}."
                    )

        # --- Rule 5: Confidence Threshold Check ---
        if record.confidence < MIN_CONFIDENCE_FOR_REVIEW:
            reasons.append(
                f"OCR confidence score ({record.confidence:.2f}) is below minimum threshold ({MIN_CONFIDENCE_FOR_REVIEW:.2f})."
            )

        # --- Decision Evaluation ---
        if reasons:
            record.status = VerificationStatus.REJECTED
            record.reasons = reasons
            record.verified_at = None
            assigned_tourist_id = None
        else:
            record.status = VerificationStatus.VERIFIED
            record.verified_at = datetime.now(timezone.utc)
            assigned_tourist_id = tourist_id or record.tourist_id or uuid4()
            record.tourist_id = assigned_tourist_id
            record.reasons = [
                "Document identity successfully verified against Suraksha Setu safety standards."
            ]

        logger.info(
            "Verification %s finalized with status %s (reasons count=%d)",
            verification_id,
            record.status,
            len(record.reasons),
        )

        return DocumentConfirmResponse(
            verification_id=verification_id,
            status=record.status,
            reasons=record.reasons,
            tourist_id=assigned_tourist_id,
        )

    def get_verification_status(
        self, verification_id: UUID
    ) -> VerificationStatusResponse:
        """Fetch read-only status of a verification transaction.

        Args:
            verification_id: Session identifier.

        Returns:
            VerificationStatusResponse.

        Raises:
            KeyError: If session is not found.
        """
        record = self._store.get(verification_id)
        if not record:
            raise KeyError(f"Verification session '{verification_id}' not found.")

        return VerificationStatusResponse(
            verification_id=record.verification_id,
            document_type=record.document_type,
            status=record.status,
            confidence=record.confidence,
            created_at=record.created_at,
            verified_at=record.verified_at,
        )

    def cleanup_session(self, verification_id: UUID) -> bool:
        """Delete stored file and verification session record.

        Args:
            verification_id: Session UUID.

        Returns:
            True if removed, False otherwise.
        """
        record = self._store.pop(verification_id, None)
        if record and record.storage_key:
            return delete_upload(record.storage_key)
        return False


# Global singleton instance for standalone runner and shared imports
verification_service = DocumentVerificationService()
