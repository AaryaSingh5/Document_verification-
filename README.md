# Suraksha Setu — OCR Identity Document Verification Module

A standalone, secure, and embeddable identity document verification module built for the **Suraksha Setu** Smart Tourist Safety platform.

Designed to run completely in isolation for local development and demos (zero external dependencies required), and seamlessly drop into any larger **FastAPI + React/Vite/TypeScript** web application.

---

## 🌟 Key Features

1. **Deterministic Mock & Offline Windows OCR:** Zero-cost local development using a deterministic fallback strategy or the native Windows SDK `Windows.Media.Ocr` engine.
2. **Multi-Variant OCR Processing:** Uses advanced image preprocessing (Otsu Binarization, Deskew, Contrast Boost) with a fallback loop to extract text accurately even from blurry IDs or those with glare.
3. **ICAO MRZ Checksum Validation:** Implements strict ICAO Document 9303 Mod-10 checksum validations on passport MRZ lines to ensure data integrity and resist basic spoofing.
4. **Liveness & Face Verification:** Employs mediapipe facial landmark variance heuristics to check if the user is a live person, and validates similarity against the ID document photo.
5. **Context-Aware Date Extraction:** Extracts and parses non-standard date formats (e.g. `23-Nov-1994`) intelligently assigning them to DOB/Issue/Expiry bins.
6. **Privacy First API:** Real document numbers are stored safely server-side for backend validation while only securely masked versions (`*******425`) are returned to frontend interfaces.
7. **Secure File Storage**:
   - Defends against path traversal attacks (`../../`) via canonical path resolution.
   - Server-generated UUID4 filenames ensure client file names are never trusted.
   - Strict MIME and file extension allow-lists (`.jpg`, `.jpeg`, `.png`, `.pdf`) and 8MB payload limit.
8. **Multi-Tier Identity Validation Rules**:
   - Confidence threshold gates (`>= 0.75` auto-extract, `< 0.50` re-upload required).
   - Mandatory required fields validation (`full_name`, `document_number`, `date_of_birth`).
   - Expiration validation for Passports and Driving Licences (expired documents rejected with clear rationale).
   - Regex format sanity checking per document type.
6. **Production-Ready Frontend**:
   - Strongly typed TypeScript API client (`verificationApi.ts`).
   - Accessible, responsive React 3-step verification component (`IdentityVerification.tsx`).

---

## 📁 Project Structure

```
document-verification-module/
├── README.md
├── backend/
│   ├── document_verification/
│   │   ├── __init__.py                  # Package exports
│   │   ├── schemas.py                   # Pydantic v2 data models
│   │   ├── config.py                    # Centralized thresholds & config
│   │   ├── storage.py                   # Secure file handling & PII masking
│   │   ├── ocr_service.py               # OCR provider abstraction & regex parser
│   │   ├── document_verification_service.py # Core verification domain logic
│   │   └── router.py                    # FastAPI APIRouter endpoints
│   ├── standalone_main.py               # Runnable standalone FastAPI server
│   ├── requirements-additions.txt       # Python dependencies
│   ├── migrations/
│   │   └── 001_add_document_verifications.sql # PostgreSQL schema & enums
│   └── test_document_verification.py   # Comprehensive automated test suite
└── frontend/
    ├── lib/
    │   └── verificationApi.ts           # Type-safe API client
    └── components/
        └── IdentityVerification.tsx     # 3-step React verification wizard
```

---

## 🚀 Quickstart: Running the Backend

### 1. Install Dependencies

```bash
cd backend
pip install -r requirements-additions.txt
```

### 2. Start the Standalone Server

```bash
# Option A: Direct Python runner
python standalone_main.py

# Option B: Uvicorn CLI
uvicorn standalone_main:app --reload --port 8000
```

Once running:
- **Interactive Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Service Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

---

## 🧪 Running Automated Tests

Run the test suite with `pytest`:

```bash
cd backend
python -m pytest test_document_verification.py -v
```

### Test Coverage Summary:
- ✅ Allowed vs disallowed MIME types and extensions
- ✅ Maximum payload file size limits (8MB)
- ✅ Path traversal security exploit defense
- ✅ Sensitive document number PII masking
- ✅ OCR Mock provider behavior (`clear`, `blurry`, `expired`, default)
- ✅ CloudVision error handling when unconfigured
- ✅ FastAPI upload endpoint with masked OCR output
- ✅ Confirmation workflow (happy path -> `VERIFIED`)
- ✅ Expiry validation rule enforcement (expired passport -> `REJECTED`)
- ✅ Anti-spoofing immutable document number protection
- ✅ Verification status query and session cleanup

---

## 📡 API Reference

### 1. Upload Document & Run OCR
- **Endpoint**: `POST /api/v1/verifications/upload`
- **Content-Type**: `multipart/form-data`
- **Parameters**:
  - `file`: Binary document image (`JPEG`, `PNG`, or `PDF`)
  - `document_type`: `PASSPORT` | `DRIVING_LICENCE` | `VOTER_ID` | `OTHER_GOVERNMENT_ID`
  - `tourist_id` *(optional)*: UUID of an existing tourist profile
- **Response** (`200 OK`):
```json
{
  "verification_id": "c1f7b889-4091-4d32-9cb8-b0a5dbff4816",
  "document_type": "PASSPORT",
  "status": "EXTRACTED",
  "confidence": 0.93,
  "extracted": {
    "full_name": { "value": "AARAV RAJESH SHARMA", "status": "FOUND" },
    "document_number": { "value": "****2910", "status": "FOUND" },
    "nationality": { "value": "INDIAN", "status": "FOUND" },
    "date_of_birth": { "value": "1994-06-18", "status": "FOUND" },
    "expiry_date": { "value": "2034-06-17", "status": "FOUND" },
    "fields_found": ["full_name", "document_number", "nationality", "date_of_birth", "expiry_date"],
    "fields_missing": []
  },
  "mock_mode": false,
  "message": "Document scanned successfully. Please review your details and confirm."
}
```

### 2. Confirm Verification Details
- **Endpoint**: `POST /api/v1/verifications/confirm`
- **Content-Type**: `application/json`
- **Request Body**:
```json
{
  "verification_id": "c1f7b889-4091-4d32-9cb8-b0a5dbff4816",
  "confirmed_fields": {
    "full_name": "Aarav Rajesh Sharma",
    "nationality": "Indian",
    "date_of_birth": "1994-06-18",
    "expiry_date": "2034-06-17"
  }
}
```
*(Notice that `document_number` and `document_type` are omitted to prevent spoofing).*
- **Response** (`200 OK`):
```json
{
  "verification_id": "c1f7b889-4091-4d32-9cb8-b0a5dbff4816",
  "status": "VERIFIED",
  "reasons": [
    "Document identity successfully verified against Suraksha Setu safety standards."
  ],
  "tourist_id": "893c5fb9-8133-4f9e-b9ef-d60237937402"
}
```

### 3. Get Verification Status
- **Endpoint**: `GET /api/v1/verifications/{verification_id}`
- **Response** (`200 OK`):
```json
{
  "verification_id": "c1f7b889-4091-4d32-9cb8-b0a5dbff4816",
  "document_type": "PASSPORT",
  "status": "VERIFIED",
  "confidence": 0.93,
  "created_at": "2026-08-22T14:40:00Z",
  "verified_at": "2026-08-22T14:40:05Z"
}
```

---

## 🎨 Frontend Integration

### Using in React / Vite / Next.js

1. Copy `frontend/lib/verificationApi.ts` and `frontend/components/IdentityVerification.tsx` into your web app.
2. Render the component:

```tsx
import React from "react";
import { IdentityVerification } from "./components/IdentityVerification";

export function TouristRegistrationPage() {
  const handleComplete = (result) => {
    console.log("Tourist verified!", result.tourist_id);
  };

  return (
    <div className="min-h-screen bg-slate-100 p-8">
      <IdentityVerification
        apiUrl="http://localhost:8000/api/v1/verifications"
        onVerificationComplete={handleComplete}
      />
    </div>
  );
}
```

---

## 🔒 Configuration & Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OCR_MODE` | `mock` | `mock` (deterministic local) or `cloud_vision` (Google Cloud Vision API) |
| `OCR_CREDENTIALS_PATH` | `None` | Path to Google Cloud service account JSON credentials |
| `MIN_CONFIDENCE_FOR_AUTO_VERIFY` | `0.75` | Minimum confidence score for auto extraction |
| `MIN_CONFIDENCE_FOR_REVIEW` | `0.50` | Minimum score threshold; lower scores require re-upload |
| `MAX_UPLOAD_SIZE_BYTES` | `8388608` (8MB) | Max upload size limit in bytes |
| `UPLOAD_STORAGE_DIR` | `/tmp/suraksha_setu_uploads` | Target directory for safe temporary document staging |

---

## 🗄️ Database Integration

When integrating into the parent Suraksha Setu database (PostgreSQL), run:

```bash
psql -d suraksha_setu_db -f backend/migrations/001_add_document_verifications.sql
```
