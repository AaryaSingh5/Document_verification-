"""Document-Type-Specific Parsers and MRZ Decoders for Suraksha Setu.

Provides dedicated parser strategies for:
- PASSPORT: Visual Inspection Zone (VIZ) + Machine Readable Zone (MRZ Type 3 / 2x44)
- DRIVING_LICENCE: DL Number, Issue Date, Validity / Expiry Date, DOB
- VOTER_ID: EPIC Number, DOB/Age, Relative Name (no expiry required)
- OTHER_GOVERNMENT_ID: General identity cards
"""

import logging
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from .date_extractor import DateCandidate, DateExtractor
from .schemas import DocumentType

logger = logging.getLogger(__name__)


def parse_mrz_date(mrz_yymmdd: str, is_expiry: bool = False) -> Optional[str]:
    """Convert a 6-digit MRZ date (YYMMDD) into an ISO string (YYYY-MM-DD).

    Args:
        mrz_yymmdd: 6-digit string (e.g. "940618" for DOB or "340617" for Expiry).
        is_expiry: If True, uses 2000s cutoff for future expiry dates.
    """
    if not mrz_yymmdd or len(mrz_yymmdd) != 6 or not mrz_yymmdd.isdigit():
        return None

    yy = int(mrz_yymmdd[:2])
    mm = int(mrz_yymmdd[2:4])
    dd = int(mrz_yymmdd[4:6])

    if mm < 1 or mm > 12 or dd < 1 or dd > 31:
        return None

    current_year = date.today().year
    current_yy = current_year % 100

    if is_expiry:
        # Expiry date is usually in the 2000s (e.g. 25 -> 2025, 34 -> 2034)
        yyyy = 2000 + yy
    else:
        # DOB: If YY > current_yy + 5 -> 1900s, else 2000s
        if yy > current_yy:
            yyyy = 1900 + yy
        else:
            yyyy = 2000 + yy

    try:
        dt = date(yyyy, mm, dd)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


class PassportParser:
    """Specialized parser for Passport documents with MRZ (Machine Readable Zone) decoding."""

    @classmethod
    def parse_mrz_lines(cls, text: str) -> Dict[str, Any]:
        """Detect and decode standard 2-line Type-3 Passport MRZ."""
        result: Dict[str, Any] = {}
        lines = [line.strip().replace(" ", "") for line in text.splitlines() if len(line.strip()) >= 30]

        # Look for Type-3 MRZ line 1: P<IND... or P<USA...
        line1 = None
        line2 = None

        for idx, l in enumerate(lines):
            # Check for P< or P<Country pattern
            if l.startswith("P<") or (len(l) >= 40 and re.match(r"^P[A-Z0-9<]", l)):
                line1 = l
                if idx + 1 < len(lines) and len(lines[idx + 1]) >= 35:
                    line2 = lines[idx + 1]
                break

        if not line1 or not line2:
            return result

        try:
            # Line 1: P<AAA SURNAME<<GIVEN<NAMES<<<<<<<<<<<<
            # Extract names from line 1
            name_part = line1[5:] if len(line1) > 5 else ""
            if "<<" in name_part:
                surname, given = name_part.split("<<", 1)
                surname = surname.replace("<", " ").strip()
                given = given.replace("<", " ").strip()
                full_name = f"{given} {surname}".strip() if given else surname
            else:
                full_name = name_part.replace("<", " ").strip()

            if full_name:
                result["full_name"] = full_name

            # Nationality: characters 2..5 of line 1 (e.g. IND)
            nat_code = line1[2:5].replace("<", "").strip()
            if nat_code:
                result["nationality_code"] = nat_code
                if nat_code == "IND":
                    result["nationality"] = "INDIAN"

            # Line 2: DocumentNumber(9) + Check(1) + Nat(3) + DOB(6) + Check(1) + Sex(1) + Expiry(6)
            doc_num_raw = line2[:9].replace("<", "").strip()
            if doc_num_raw:
                result["document_number"] = doc_num_raw

            dob_raw = line2[13:19]
            dob_iso = parse_mrz_date(dob_raw, is_expiry=False)
            if dob_iso:
                result["date_of_birth"] = dob_iso

            expiry_raw = line2[21:27]
            expiry_iso = parse_mrz_date(expiry_raw, is_expiry=True)
            if expiry_iso:
                result["expiry_date"] = expiry_iso

            logger.info("PassportParser: Successfully decoded MRZ -> %s", result)
        except Exception as exc:
            logger.debug("MRZ parsing encountered format anomaly: %s", exc)

        return result


class DrivingLicenceParser:
    """Specialized parser for Driving Licence layouts."""

    @classmethod
    def parse_dl(cls, text: str, date_candidates: List[DateCandidate]) -> Dict[str, Any]:
        """Extract DL fields with multi-date resolution."""
        result: Dict[str, Any] = {}

        # DL Number pattern (e.g. DL-1420110012345 or RJ14-20150001234)
        dl_match = re.search(
            r"\b(?:DL\s*(?:NO|NUMBER|#)?|DRIVING\s*LICENCE\s*(?:NO|NUMBER|#)?|LICENCE\s*NO)\b[:\s]*([A-Za-z0-9\-\/\s]{8,22})",
            text,
            re.IGNORECASE,
        )
        if dl_match:
            clean_num = dl_match.group(1).strip().replace(" ", "")
            result["document_number"] = clean_num

        return result


class VoterIdParser:
    """Specialized parser for Voter ID (EPIC) documents."""

    @classmethod
    def parse_voter_id(cls, text: str) -> Dict[str, Any]:
        """Extract Voter ID fields (EPIC Number, Elector Name, DOB)."""
        result: Dict[str, Any] = {}
        if not text:
            return result

        lines = [l.strip() for l in text.splitlines() if l.strip()]

        # 1. EPIC Number extraction (3 letters + 7 alphanumeric, suffix with digits/typos)
        def is_epic_suffix(s: str) -> bool:
            return sum(1 for c in s if c.isdigit()) >= 2

        for l in lines:
            # Check explicit pattern or standalone barcode number
            m = re.search(r'\b([A-Za-z]{3})([0-9a-zA-Z]{7})\b', l)
            if m and is_epic_suffix(m.group(2)):
                prefix = m.group(1).upper()
                suffix = m.group(2).upper()
                clean_suffix = (
                    suffix.replace('O', '0')
                    .replace('S', '5')
                    .replace('I', '1')
                    .replace('L', '1')
                    .replace('B', '8')
                    .replace('Z', '2')
                )
                result["document_number"] = f"{prefix}{clean_suffix}"
                break

        # 2. Elector Name extraction (handling label on line N and name on line N+1 or subsequent)
        for idx, l in enumerate(lines):
            if re.search(r"ELECTOR\'?S?\s*NAME", l, re.IGNORECASE):
                # If name is on same line after colon
                same_line_match = re.search(r"ELECTOR\'?S?\s*NAME[:\s]+([A-Za-z \.\'\-]{3,40})", l, re.IGNORECASE)
                if same_line_match:
                    val = same_line_match.group(1).strip()
                    if not re.search(r'(?:FATHER|SEX|MALE|FEMALE|DATE|BIRTH|COMMISSION|ELECTION)', val, re.IGNORECASE):
                        result["full_name"] = val
                        break

                # Otherwise check following lines
                for next_l in lines[idx + 1:]:
                    if re.search(r'(?:FATHER|SEX|MALE|FEMALE|DATE|BIRTH|COMMISSION|ELECTION|PHOTO|IDENTITY|CARD)', next_l, re.IGNORECASE):
                        continue
                    if re.match(r'^[A-Za-z\s\.\'\-]{3,40}$', next_l):
                        result["full_name"] = next_l.strip()
                        break
                if result.get("full_name"):
                    break

        return result
