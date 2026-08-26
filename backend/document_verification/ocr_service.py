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
from typing import Any, Dict, List, Optional, Tuple

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
        logger.debug(
            "normalize(): provider=%s is_mock=%s confidence=%.3f doc_type=%s raw_text=%r",
            raw.provider,
            raw.is_mock,
            confidence,
            doc_type_val,
            text,
        )
        if raw.is_mock and "FALLBACK_TO_MOCK" in (raw.provider or ""):
            logger.warning(
                "normalize(): This document was processed with SYNTHETIC fallback data, "
                "not real OCR. Extracted fields will not reflect the actual uploaded image."
            )

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

        # 4.5 Latin-script Line Filter
        # Filter out lines that contain absolutely no Latin characters or digits (e.g. pure Hindi/Devanagari lines)
        latin_lines = [l for l in text.splitlines() if re.search(r'[A-Za-z0-9]', l)]

        # 4.6 UI-Chrome Noise Filter
        # Defends against users uploading a SCREENSHOT of the web app itself (which
        # contains a thumbnail of the real document plus surrounding app UI text),
        # rather than a direct photo/scan of the document. If OCR runs on the full
        # screenshot, app UI strings pollute the text and break field extraction.
        # This is a mitigation, not a substitute for prompting the user to upload a
        # real document photo -- it only removes text we can confidently identify as
        # belonging to our own app chrome, not arbitrary document noise.
        UI_NOISE_PATTERNS = re.compile(
            r"suraksha\s*setu|smart\s*tourist\s*identity|safety\s*verification|"
            r"upload\s*id\b|verify\s*details|safety\s*badge|encrypted\s*ocr|"
            r"upload\s*identity\s*document|select\s*document\s*type|"
            r"click\s*to\s*choose|different\s*file|verification\s*notice|"
            r"document\s*image\s*quality|well[\s-]?lit\s*photograph",
            re.IGNORECASE,
        )
        pre_filter_count = len(latin_lines)
        latin_lines = [l for l in latin_lines if not UI_NOISE_PATTERNS.search(l)]
        if len(latin_lines) < pre_filter_count:
            logger.warning(
                "normalize(): stripped %d line(s) matching app UI chrome text \u2014 "
                "this usually means the user uploaded a SCREENSHOT of the web app "
                "rather than a direct photo of the document. Consider prompting "
                "the user to re-upload a clean photo for reliable results.",
                pre_filter_count - len(latin_lines),
            )

        latin_text = "\n".join(latin_lines)

        # 5. Regex Patterns for Non-Date Fields (Strict horizontal boundaries, using latin_text)
        name_match = re.search(
            r"\b(?:FULL\s*NAME|GIVEN\s*NAME|NAME\s*OF\s*HOLDER|CARD\s*HOLDER|ELECTOR\'?S?\s*NAME|NAME)\b[:\s]+([A-Za-z \.\'\-]{2,40})",
            latin_text,
            re.IGNORECASE,
        )

        doc_num_match = re.search(
            r"\b(?:PASSPORT\s*(?:NO|NUMBER|#)?|DL\s*(?:NO|NUMBER|#)?|DRIVING\s*LICENCE\s*(?:NO|NUMBER|#)?|"
            r"EPIC\s*(?:NO|NUMBER|#)?|VOTER\s*(?:ID|NO|CARD\s*NO)?|DOC(?:UMENT)?\s*(?:NO|NUMBER|ID)|"
            r"ID\s*(?:NO|NUMBER|#)|IDENTITY\s*(?:NO|NUMBER)|DOCUMENT\s*NUMBER|ID\s*NUMBER)\b[:\s]*([A-Za-z0-9\-\/]{5,25})",
            latin_text,
            re.IGNORECASE,
        )

        aadhaar_match = re.search(r"\b(\d{4}[ \t]?\d{4}[ \t]?\d{4})\b", latin_text)

        nationality_match = re.search(
            r"\b(?:NATIONALITY|CITIZENSHIP|COUNTRY)\b[:\s]*([A-Za-z]{3,25})",
            latin_text,
            re.IGNORECASE,
        )

        # Merge MRZ / Voter parser / Visual matches
        aadhaar_name = None
        if doc_type_val == "OTHER_GOVERNMENT_ID" and not _clean_match(name_match):
            # Aadhaar name is typically the line just before the DOB line, but real
            # scans can occasionally insert a stray/blank noise line in between, so
            # check up to 2 lines above before giving up.
            for i, line in enumerate(latin_lines):
                if re.search(
                    r'\bDOB\b|\bD0B\b|\b00B\b|\bYear of Birth\b|\b\d{2}/\d{2}/\d{4}\b',
                    line,
                    re.IGNORECASE,
                ):
                    for back in (1, 2):
                        idx = i - back
                        if idx < 0:
                            break
                        potential_name = re.sub(r'[^A-Za-z\s]', '', latin_lines[idx]).strip()
                        potential_name = re.sub(r'\s+', ' ', potential_name)
                        # Prefer a multi-word name (first + last), but accept a
                        # single garbled token too (e.g. real-world OCR sometimes
                        # drops/merges words on low-quality photos) as long as it's
                        # a plausible name-length token, since leaving the field
                        # empty is worse than a lower-confidence single-token guess
                        # that the user can correct during the review step.
                        if 3 <= len(potential_name) <= 40:
                            aadhaar_name = potential_name
                            break
                    break

        full_name_val = (
            mrz_data.get("full_name")
            or voter_data.get("full_name")
            or _clean_match(name_match)
            or aadhaar_name
        )
        doc_num_val = (
            mrz_data.get("document_number")
            or voter_data.get("document_number")
            or _clean_match(doc_num_match)
            or (aadhaar_match.group(1).replace(" ", "") if aadhaar_match and doc_type_val == "OTHER_GOVERNMENT_ID" else None)
        )
        nat_val = (
            mrz_data.get("nationality")
            or _clean_match(nationality_match)
            or ("INDIAN" if doc_type_val in ("VOTER_ID", "OTHER_GOVERNMENT_ID") else None)
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

        # Base regex confidence is slightly discounted (0.90 penalty) to reflect less structure
        base_regex_conf = confidence * 0.90

        # Refine confidences using MRZ checksum signals if available
        name_conf = mrz_data.get("full_name_confidence", 0.95) if mrz_data.get("full_name") else base_regex_conf
        doc_num_conf = mrz_data.get("document_number_confidence", 0.95) if mrz_data.get("document_number") else base_regex_conf
        nat_conf = 0.95 if mrz_data.get("nationality") else base_regex_conf
        
        dob_conf = mrz_data.get("date_of_birth_confidence", 0.95) if mrz_data.get("date_of_birth") else (best_dob.confidence if best_dob else base_regex_conf)
        exp_conf = mrz_data.get("expiry_date_confidence", 0.95) if mrz_data.get("expiry_date") else (best_expiry.confidence if best_expiry else base_regex_conf)

        data = ExtractedDocumentData(
            full_name=_make_extracted_field(full_name_val, name_conf),
            document_number=_make_extracted_field(doc_num_val, doc_num_conf),
            nationality=_make_extracted_field(nat_val, nat_conf),
            date_of_birth=_make_extracted_field(dob_val, dob_conf),
            expiry_date=_make_extracted_field(exp_val, exp_conf),
            mrz_checksum_valid=mrz_data.get("mrz_checksum_valid"),
            mrz_checksum_errors=mrz_data.get("mrz_checksum_errors", []),
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
            symbol_count = 0
            if response.full_text_annotation:
                for page in response.full_text_annotation.pages:
                    for block in page.blocks:
                        for paragraph in block.paragraphs:
                            for word in paragraph.words:
                                for symbol in word.symbols:
                                    total_conf += getattr(symbol, "confidence", 0.85)
                                    symbol_count += 1
            avg_confidence = (total_conf / symbol_count) if symbol_count > 0 else 0.85

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

    async def _run_windows_ocr(self, img_bytes: bytes) -> Tuple[str, float]:
        from winsdk.windows.graphics.imaging import BitmapDecoder  # type: ignore
        from winsdk.windows.media.ocr import OcrEngine  # type: ignore
        from winsdk.windows.storage.streams import (  # type: ignore
            DataWriter,
            InMemoryRandomAccessStream,
        )

        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(img_bytes)
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
        return full_text, confidence

    async def extract_document_data(
        self, file_bytes: bytes, filename: str, document_type: DocumentType
    ) -> RawOCRResult:
        """Run real Windows Native OCR with Multi-Variant Fallback."""
        try:
            try:
                from .config import MULTI_VARIANT_FALLBACK_THRESHOLD
            except ImportError:
                logger.warning(
                    "MULTI_VARIANT_FALLBACK_THRESHOLD not found in config.py; "
                    "defaulting to 0.75. Add it to config.py to make this configurable."
                )
                MULTI_VARIANT_FALLBACK_THRESHOLD = 0.75

            variants = ImagePreprocessor.generate_variants(file_bytes)
            
            # Primary pass
            primary_bytes = variants.get(PreprocessingVariant.ENHANCED, file_bytes)
            best_text, best_conf = await self._run_windows_ocr(primary_bytes)
            best_variant = "ENHANCED"

            # Fallback pass if primary confidence is low
            if best_conf < MULTI_VARIANT_FALLBACK_THRESHOLD:
                logger.info(f"Windows OCR: Primary confidence ({best_conf}) low, attempting fallback variants...")
                for variant_name in (PreprocessingVariant.BINARIZED, PreprocessingVariant.CONTRAST_BOOSTED, PreprocessingVariant.GRAYSCALE_SHARPENED):
                    v_bytes = variants.get(variant_name)
                    if v_bytes:
                        try:
                            v_text, v_conf = await self._run_windows_ocr(v_bytes)
                            if v_conf > best_conf:
                                best_text, best_conf = v_text, v_conf
                                best_variant = variant_name
                            if best_conf >= MULTI_VARIANT_FALLBACK_THRESHOLD:
                                break
                        except Exception as var_exc:
                            logger.warning(f"Windows OCR: Variant {variant_name} failed: {var_exc}")
                            continue

            logger.info("WindowsNativeOCRProvider: processed '%s' using %s variant (confidence=%.2f)", filename, best_variant, best_conf)

            return RawOCRResult(
                raw_text=best_text,
                confidence=best_conf,
                provider="windows_native_ocr",
                is_mock=False,
            )
        except Exception as exc:
            logger.error(
                "WindowsNativeOCRProvider FAILED for '%s' (%s: %s). "
                "Returning empty result so verification fails properly.",
                filename,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return RawOCRResult(
                raw_text="",
                confidence=0.0,
                provider="windows_native_ocr_FAILED",
                is_mock=False,
            )


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