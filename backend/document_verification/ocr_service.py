"""OCR provider abstraction and normalization for Suraksha Setu.

Provides:
- RawOCRResult data structure
- Abstract base OCRService with normalize() parser powered by DateExtractor and DocumentParsers
- Deterministic MockOCRProvider for testing and demo flows without external dependencies
- CloudVisionOCRProvider with ImagePreprocessor integration
- get_ocr_service factory function
"""

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .config import (
    DOCUMENT_TYPES_WITH_EXPIRY,
    MIN_CONFIDENCE_FOR_AUTO_VERIFY,
    MIN_CONFIDENCE_FOR_REVIEW,
    OCR_CREDENTIALS_PATH,
    OCR_MODE,
    REQUIRED_FIELDS,
)
from .date_extractor import DateCandidate, DateExtractor
from .document_parsers import DrivingLicenceParser, PassportParser, VoterIdParser
from .image_preprocessor import ImagePreprocessor, PreprocessingVariant
from .ocr_debugger import OCRDebugger
from .schemas import (
    DocumentType,
    ExtractedDocumentData,
    ExtractedField,
    FieldStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class RawOCRResult:
    """Internal representation of OCR engine output.

    Note: This is strictly internal to the OCR engine and service layers.
    It is never exposed in external REST API schemas.
    """

    raw_text: str
    confidence: float
    provider: str
    is_mock: bool


class OCRService(ABC):
    """Abstract Base Class for OCR Service Providers."""

    @abstractmethod
    async def extract_document_data(
        self, file_bytes: bytes, filename: str, document_type: DocumentType
    ) -> RawOCRResult:
        """Extract raw text and confidence score from document bytes.

        Args:
            file_bytes: Uploaded binary content.
            filename: Document filename.
            document_type: The expected document type.

        Returns:
            RawOCRResult containing raw extracted text and confidence.
        """
        pass

    def normalize(
        self, raw: RawOCRResult, document_type: DocumentType
    ) -> ExtractedDocumentData:
        """Parse raw OCR text into structured per-field results using context-aware date ranking.

        Parses NAME, DOCUMENT NO, NATIONALITY, DOB, and EXPIRY accurately.
        """
        text = raw.raw_text or ""
        confidence = raw.confidence
        doc_type_val = document_type.value if hasattr(document_type, "value") else str(document_type)

        # 1. Log Raw OCR for Dev Debugging
        OCRDebugger.log_raw_ocr(text, raw.provider, confidence)

        # 2. Extract All Date Candidates across the document
        date_candidates = DateExtractor.extract_candidates_from_text(text, base_confidence=confidence)

        # 3. Check Document-Specific MRZ & Layout Parsers
        mrz_data: Dict[str, Any] = {}
        voter_data: Dict[str, Any] = {}
        if doc_type_val == "PASSPORT":
            mrz_data = PassportParser.parse_mrz_lines(text)
        elif doc_type_val == "VOTER_ID":
            voter_data = VoterIdParser.parse_voter_id(text)

        # 4. Classify and Rank Date Candidates
        best_dob, best_expiry, best_issue = DateExtractor.rank_and_classify_dates(
            date_candidates,
            expected_has_expiry=(doc_type_val in DOCUMENT_TYPES_WITH_EXPIRY),
        )

        # Log candidate diagnostics
        candidate_debug_dicts = [
            {
                "value": c.raw_value,
                "normalized": c.normalized_iso,
                "confidence": c.confidence,
                "label_context": c.label_context,
                "scores": c.scores,
            }
            for c in date_candidates
        ]
        OCRDebugger.log_date_candidates(candidate_debug_dicts)

        def _clean_match(m: Optional[re.Match]) -> Optional[str]:
            if not m:
                return None
            val = m.group(1).strip().strip(":").strip("-").strip()
            val = re.sub(r"\s+", " ", val)
            return val if len(val) > 0 else None

        # 5. Regex Patterns for Non-Date Fields (Strict horizontal boundaries)
        name_match = re.search(
            r"\b(?:FULL\s*NAME|GIVEN\s*NAME|NAME\s*OF\s*HOLDER|CARD\s*HOLDER|ELECTOR\'?S?\s*NAME|NAME)\b[:\s]+([A-Za-z \.\'\-]{2,40})",
            text,
            re.IGNORECASE,
        )

        doc_num_match = re.search(
            r"\b(?:PASSPORT\s*(?:NO|NUMBER|#)?|DL\s*(?:NO|NUMBER|#)?|DRIVING\s*LICENCE\s*(?:NO|NUMBER|#)?|"
            r"EPIC\s*(?:NO|NUMBER|#)?|VOTER\s*(?:ID|NO|CARD\s*NO)?|DOC(?:UMENT)?\s*(?:NO|NUMBER|ID)|"
            r"ID\s*(?:NO|NUMBER|#)|IDENTITY\s*(?:NO|NUMBER)|DOCUMENT\s*NUMBER|ID\s*NUMBER)\b[:\s]*([A-Za-z0-9\-\/]{5,25})",
            text,
            re.IGNORECASE,
        )

        nationality_match = re.search(
            r"\b(?:NATIONALITY|CITIZENSHIP|COUNTRY)\b[:\s]*([A-Za-z]{3,25})",
            text,
            re.IGNORECASE,
        )

        # Merge MRZ / Voter parser / Visual matches
        full_name_val = (
            mrz_data.get("full_name")
            or voter_data.get("full_name")
            or _clean_match(name_match)
        )
        doc_num_val = (
            mrz_data.get("document_number")
            or voter_data.get("document_number")
            or _clean_match(doc_num_match)
        )
        nat_val = (
            mrz_data.get("nationality")
            or _clean_match(nationality_match)
            or ("INDIAN" if doc_type_val == "VOTER_ID" else None)
        )

        # Dates from MRZ or Context-Aware Classifier
        dob_val = mrz_data.get("date_of_birth") or (best_dob.normalized_iso if best_dob else None)
        exp_val = mrz_data.get("expiry_date") or (best_expiry.normalized_iso if best_expiry else None)

        # 6. Log Final Classification
        OCRDebugger.log_final_classification(
            date_of_birth=dob_val,
            expiry_date=exp_val,
            issue_date=(best_issue.normalized_iso if best_issue else None),
            extra_fields={"full_name": full_name_val, "document_number": doc_num_val},
        )

        def _make_extracted_field(val: Optional[str], field_conf: float) -> ExtractedField:
            if not val:
                return ExtractedField(value=None, status=FieldStatus.NOT_FOUND, confidence=None)

            if field_conf >= MIN_CONFIDENCE_FOR_AUTO_VERIFY:
                status = FieldStatus.FOUND
            elif field_conf >= MIN_CONFIDENCE_FOR_REVIEW:
                status = FieldStatus.NEEDS_REVIEW
            else:
                status = FieldStatus.NEEDS_REVIEW

            return ExtractedField(value=val, status=status, confidence=field_conf)

        dob_conf = 0.95 if mrz_data.get("date_of_birth") else (best_dob.confidence if best_dob else confidence)
        exp_conf = 0.95 if mrz_data.get("expiry_date") else (best_expiry.confidence if best_expiry else confidence)

        data = ExtractedDocumentData(
            full_name=_make_extracted_field(full_name_val, confidence),
            document_number=_make_extracted_field(doc_num_val, confidence),
            nationality=_make_extracted_field(nat_val, confidence),
            date_of_birth=_make_extracted_field(dob_val, dob_conf),
            expiry_date=_make_extracted_field(exp_val, exp_conf),
        )

        # Populate fields_found and fields_missing
        found = []
        missing = []
        all_field_names = [
            "full_name",
            "document_number",
            "nationality",
            "date_of_birth",
            "expiry_date",
        ]

        for field_name in all_field_names:
            field_obj: ExtractedField = getattr(data, field_name)
            if field_obj.status != FieldStatus.NOT_FOUND and field_obj.value:
                found.append(field_name)
            else:
                missing.append(field_name)

        data.fields_found = found
        data.fields_missing = missing

        return data


class MockOCRProvider(OCRService):
    """Deterministic development and demo OCR provider.

    Performs NO external API calls. Behavior is deterministically keyed off
    the uploaded filename to allow predictable testing of all user journeys:
    - 'clear' / 'good' in filename -> High confidence (0.93), complete realistic sample data
    - 'blurry' / 'low' / 'unclear' in filename -> Low confidence (0.41), partial unreadable extraction
    - 'expired' in filename -> High confidence (0.91) with an expiry date in the past
    - default / any other filename -> Moderate confidence (0.68) with multi-line DOB and Expiry fields
    """

    async def extract_document_data(
        self, file_bytes: bytes, filename: str, document_type: DocumentType
    ) -> RawOCRResult:
        """Extract mock document text based on deterministic filename cues."""
        name_lower = (filename or "").lower()
        doc_type_val = document_type.value if hasattr(document_type, "value") else str(document_type)

        if any(k in name_lower for k in ("clear", "good", "valid", "sample")):
            confidence = 0.93
            if doc_type_val == "PASSPORT":
                raw_text = (
                    "REPUBLIC OF INDIA / PASSPORT\n"
                    "TYPE: P  CODE: IND  PASSPORT NO: P8472910\n"
                    "NAME: AARAV RAJESH SHARMA\n"
                    "NATIONALITY: INDIAN\n"
                    "DOB: 1994-06-18\n"
                    "SEX: M  PLACE OF BIRTH: NEW DELHI\n"
                    "EXPIRY: 2034-06-17\n"
                    "P<INDSHARMA<<AARAV<RAJESH<<<<<<<<<<<<<<<<<<<\n"
                    "P8472910<2IND9406184M3406176<<<<<<<<<<<<<<02"
                )
            elif doc_type_val == "DRIVING_LICENCE":
                raw_text = (
                    "UNION OF INDIA - DRIVING LICENCE\n"
                    "DL NO: DL-1420110012345\n"
                    "NAME: AARAV RAJESH SHARMA\n"
                    "DOB: 1994-06-18\n"
                    "NATIONALITY: INDIAN\n"
                    "EXPIRY: 2034-06-17\n"
                    "AUTHORISED TO DRIVE: LMV, MCWG"
                )
            elif doc_type_val == "VOTER_ID":
                raw_text = (
                    "ELECTION COMMISSION OF INDIA\n"
                    "EPIC NO: ZXC1982736\n"
                    "NAME: AARAV RAJESH SHARMA\n"
                    "NATIONALITY: INDIAN\n"
                    "DOB: 1994-06-18\n"
                    "GENDER: MALE"
                )
            else:
                raw_text = (
                    "GOVERNMENT OF INDIA IDENTITY CARD\n"
                    "ID NUMBER: GOV-8492019\n"
                    "NAME: AARAV RAJESH SHARMA\n"
                    "NATIONALITY: INDIAN\n"
                    "DOB: 1994-06-18\n"
                    "EXPIRY: 2034-06-17"
                )

        elif any(k in name_lower for k in ("blurry", "low", "unclear", "bad", "dark")):
            confidence = 0.41
            raw_text = (
                "~REP... OF IN...~\n"
                "NAME: AARAV SH...\n"
                "~unreadable scan noise~~~"
            )

        elif "expired" in name_lower:
            confidence = 0.91
            raw_text = (
                "REPUBLIC OF INDIA / PASSPORT\n"
                "PASSPORT NO: P8472910\n"
                "NAME: PRIYA VIKRAM PATEL\n"
                "NATIONALITY: INDIAN\n"
                "DOB: 1988-11-23\n"
                "EXPIRY: 2021-05-14\n"
                "P<INDPATEL<<PRIYA<VIKRAM<<<<<<<<<<<<<<<<<<<<\n"
            )

        else:
            confidence = 0.68
            # Multi-line realistic OCR text demonstrating robust date context matching
            raw_text = (
                "OFFICIAL IDENTITY DOCUMENT\n"
                "ID NO: ID-998822\n"
                "NAME: VIKRAM MEHTA\n"
                "NATIONALITY: INDIAN\n"
                "DATE OF BIRTH\n"
                "10-02-1995\n"
                "DATE OF EXPIRY\n"
                "18-11-2030\n"
            )

        logger.info(
            "MockOCRProvider: processed '%s' for type %s (confidence=%.2f)",
            filename,
            doc_type_val,
            confidence,
        )
        return RawOCRResult(
            raw_text=raw_text,
            confidence=confidence,
            provider="mock",
            is_mock=True,
        )


class CloudVisionOCRProvider(OCRService):
    """Production OCR Provider utilizing Google Cloud Vision API with ImagePreprocessor."""

    def __init__(self, credentials_path: Optional[str] = None):
        self.credentials_path = credentials_path or OCR_CREDENTIALS_PATH
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client

        try:
            from google.cloud import vision  # type: ignore
        except ImportError as err:
            raise RuntimeError(
                "google-cloud-vision package is not installed. "
                "Install it using `pip install google-cloud-vision` to use CloudVisionOCRProvider."
            ) from err

        if self.credentials_path:
            import os
            if not os.path.exists(self.credentials_path):
                raise RuntimeError(
                    f"OCR credentials file not found at '{self.credentials_path}'. "
                    "Please set a valid OCR_CREDENTIALS_PATH environment variable."
                )
            self._client = vision.ImageAnnotatorClient.from_service_account_json(
                self.credentials_path
            )
        else:
            self._client = vision.ImageAnnotatorClient()

        return self._client

    async def extract_document_data(
        self, file_bytes: bytes, filename: str, document_type: DocumentType
    ) -> RawOCRResult:
        """Call Google Cloud Vision document_text_detection with preprocessed variants."""
        client = self._get_client()

        try:
            from google.cloud import vision  # type: ignore

            # Preprocess image for optimal OCR resolution and orientation
            variants = ImagePreprocessor.generate_variants(file_bytes)
            processed_bytes = variants.get(PreprocessingVariant.ENHANCED, file_bytes)

            image = vision.Image(content=processed_bytes)
            response = client.document_text_detection(image=image)
            if response.error.message:
                raise RuntimeError(f"Cloud Vision API Error: {response.error.message}")

            full_text = (
                response.full_text_annotation.text
                if response.full_text_annotation
                else ""
            )

            total_conf = 0.0
            block_count = 0
            if response.full_text_annotation:
                for page in response.full_text_annotation.pages:
                    for block in page.blocks:
                        total_conf += getattr(block, "confidence", 0.85)
                        block_count += 1
            avg_confidence = (total_conf / block_count) if block_count > 0 else 0.85

            return RawOCRResult(
                raw_text=full_text,
                confidence=avg_confidence,
                provider="google_cloud_vision",
                is_mock=False,
            )
        except Exception as exc:
            logger.error("Cloud Vision execution error: %s", exc)
            raise RuntimeError(f"Cloud Vision OCR extraction failed: {exc}") from exc


class WindowsNativeOCRProvider(OCRService):
    """Real local OCR Provider utilizing Windows Native OCR (Windows.Media.Ocr).

    Performs actual optical character recognition locally on Windows systems
    without requiring external cloud APIs or keys.
    """

    def __init__(self):
        self._available = False
        try:
            from winsdk.windows.media.ocr import OcrEngine  # type: ignore
            self._available = True
        except ImportError:
            self._available = False

    async def extract_document_data(
        self, file_bytes: bytes, filename: str, document_type: DocumentType
    ) -> RawOCRResult:
        """Run real Windows Native OCR on uploaded document image."""
        try:
            from winsdk.windows.graphics.imaging import BitmapDecoder  # type: ignore
            from winsdk.windows.media.ocr import OcrEngine  # type: ignore
            from winsdk.windows.storage.streams import (  # type: ignore
                DataWriter,
                InMemoryRandomAccessStream,
            )

            # Preprocess image for optimal clarity
            variants = ImagePreprocessor.generate_variants(file_bytes)
            processed_bytes = variants.get(PreprocessingVariant.ENHANCED, file_bytes)

            stream = InMemoryRandomAccessStream()
            writer = DataWriter(stream)
            writer.write_bytes(processed_bytes)
            await writer.store_async()
            await writer.flush_async()
            stream.seek(0)

            decoder = await BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()
            engine = OcrEngine.try_create_from_user_profile_languages()
            if not engine:
                raise RuntimeError("Could not initialize Windows OCR engine from user languages.")

            ocr_res = await engine.recognize_async(bitmap)
            lines_text = [l.text for l in ocr_res.lines]
            full_text = "\n".join(lines_text)

            # Calculate confidence score based on recognized structure
            confidence = 0.92 if len(lines_text) >= 4 else (0.80 if len(lines_text) >= 2 else 0.45)
            logger.info("WindowsNativeOCRProvider: recognized %d lines from '%s'", len(lines_text), filename)

            return RawOCRResult(
                raw_text=full_text,
                confidence=confidence,
                provider="windows_native_ocr",
                is_mock=False,
            )
        except Exception as exc:
            logger.warning("WindowsNativeOCRProvider encountered error (%s). Falling back to MockOCRProvider.", exc)
            mock = MockOCRProvider()
            return await mock.extract_document_data(file_bytes, filename, document_type)


def get_ocr_service(
    mode: str = OCR_MODE, credentials_path: Optional[str] = OCR_CREDENTIALS_PATH
) -> OCRService:
    """Factory function for instantiating the appropriate OCR provider."""
    normalized_mode = (mode or "auto").strip().lower()

    if normalized_mode == "cloud_vision":
        logger.info("Initializing CloudVisionOCRProvider")
        return CloudVisionOCRProvider(credentials_path=credentials_path)

    if normalized_mode in ("windows_ocr", "local"):
        logger.info("Initializing WindowsNativeOCRProvider")
        return WindowsNativeOCRProvider()

    if normalized_mode == "auto":
        try:
            import winsdk  # type: ignore
            logger.info("Auto-selected WindowsNativeOCRProvider (real local OCR)")
            return WindowsNativeOCRProvider()
        except ImportError:
            pass

    logger.info("Initializing MockOCRProvider (mode='%s')", normalized_mode)
    return MockOCRProvider()
