# Implementation Plan — Document Verification Enhancements

This plan outlines the enhancements to be made to the document verification module to support robust MRZ validation, real webcam face authentication using MediaPipe (cosine similarity), a pixel-level liveness check, and complete elimination of mocked verification behavior.

## User Review Required

> [!IMPORTANT]
> The virtual environment (`backend/venv`) was recreated using the system Python interpreter since the original virtualenv was hardcoded to a path pointing to a different user's directory. All tests now pass on this local system.
>
> Face verification is now strictly required for all sessions before finalizing verification to `VERIFIED`.

## Proposed Changes

### Component 1: MRZ Checksum Validation

#### [MODIFY] [document_parsers.py](file:///c:/Users/Intel/Downloads/document-verification-module%20(2)/document-verification-module/backend/document_verification/document_parsers.py)
- In `PassportParser.parse_mrz_lines`, we will refactor check digit extraction and validation:
  - Verify that each check digit character (`l2[9]`, `l2[19]`, `l2[27]`, `l2[43]`) is a digit.
  - If a check digit character is non-numeric (e.g. letters, space, or `<`), add a specific validation error to `checksum_errors` rather than setting it to `-1` or ignoring it.
  - Ensure that `mrz_checksum_valid` is `True` only when `checksum_errors` is empty.

---

### Component 2: Real Face Authentication Backend

#### [MODIFY] [face_match_service.py](file:///c:/Users/Intel/Downloads/document-verification-module%20(2)/document-verification-module/backend/document_verification/face_match_service.py)
- Implement global helper functions or module-level wrappers:
  - `extract_face(image_bytes: bytes) -> Optional[np.ndarray]`: Safely decodes the image, runs MediaPipe FaceMesh to extract landmarks, centers and normalizes them, and returns a 1D numpy array representing the face landmarks. Returns `None` if no face is detected or if an exception is caught.
  - `match_faces(encoding1: np.ndarray, encoding2: np.ndarray) -> float`: Calculates the cosine similarity of the flattened landmark arrays. Computes the equivalent distance-based score mapped to the `[0.0, 1.0]` range such that a unit vector distance aligns with the existing similarity conventions and the `0.60` threshold.
- Refactor `process_live_face(self, verification_id: UUID, frames: List[bytes])`:
  - Require at least 2 frames to perform a pixel-level liveness check.
  - Implement the liveness check by computing the mean absolute pixel difference (`cv2.absdiff` and `np.mean`) between the first two frames. If the mean difference is `<= 1.0` (indicating identical or static frames), return `(FaceMatchStatus.LIVENESS_FAILED, None)`.
  - Perform real face detection and extraction using `extract_face` on the two live frames. If face extraction fails or no face is found, return `(FaceMatchStatus.NO_FACE_DETECTED, None)`.
  - Match the first live face embedding against the stored ID face embedding using `match_faces` (cosine similarity).
  - Compare the resulting similarity score against `MIN_FACE_MATCH_CONFIDENCE` (0.60) to return `FaceMatchStatus.MATCHED` or `FaceMatchStatus.MISMATCH`.

---

### Component 3: Remove Mock Face Verification Completely

We will eliminate all fake/simulated camera capture and hardcoded backend matching code:
- In `FaceCapture.tsx`, remove `mockMode` from props and remove the conditional mock block in `startLivenessCapture`.
- In `FaceCapture.tsx`, remove the display of `Mock Mode active` and `Mock Camera Feed`.
- In `IdentityVerification.tsx`, remove passing `mockMode` / `mock_mode` to `FaceCapture`.

---

### Component 4: Frontend Real Webcam Face Capture

#### [MODIFY] [FaceCapture.tsx](file:///c:/Users/Intel/Downloads/document-verification-module%20(2)/document-verification-module/frontend/components/FaceCapture.tsx)
- Rephrase liveness flow to capture exactly **two** frames:
  - Capture first frame, wait ~1 second, capture second frame.
  - Stop the camera stream and release media tracks immediately on unmount or once the capture flow completes.
  - Send the two captured frames to the backend `/face-match` API endpoint using `faceMatch` from `verificationApi.ts`.
  - Handle camera errors (permission denied, no device, startup failure) and API errors safely.

---

### Component 5: Integration into Verification Flow

#### [MODIFY] [document_verification_service.py](file:///c:/Users/Intel/Downloads/document-verification-module%20(2)/document-verification-module/backend/document_verification/document_verification_service.py)
- Always enforce face match checks in `confirm_verification`:
  - Regardless of whether `record.face_match_required` is set, we will read `record.extracted_data.face_match_status`.
  - If `face_match_status != FaceMatchStatus.MATCHED`, prevent setting status to `VERIFIED` and append the appropriate explanation message to the rejection reasons.

## Verification Plan

### Automated Tests
- Run `python -m pytest test_document_verification.py -v` to ensure all existing and new test cases pass.
- Add test cases in `test_document_verification.py` to assert:
  - MRZ checksum validation behavior under valid, incorrect, and non-numeric check digits.
  - The face verification constraint rules in `document_verification_service.py`.

### Manual Verification
- Run the FastAPI backend server (`python standalone_main.py`).
- Run the React frontend development server, verify webcam capture, frame timing, liveness failure on identical input, and final verification outcome.
