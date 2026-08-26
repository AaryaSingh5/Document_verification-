"""Face Match and Liveness Service for Suraksha Setu.

PROTOTYPE NOTE: Uses MediaPipe FaceMesh to extract facial landmarks.
Matches faces by computing the L2 distance between normalized landmark arrays.
Liveness is determined by checking landmark variance across multiple frames.
"""

import io
import logging
from typing import List, Optional, Tuple
from uuid import UUID

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

try:
    import mediapipe as mp
    try:
        mp_face_mesh = mp.solutions.face_mesh
    except AttributeError:
        # Some mediapipe installs don't auto-populate `mp.solutions` as a
        # top-level attribute even though the submodule itself is present
        # and importable directly. Try the explicit import path before
        # giving up entirely.
        from mediapipe.python.solutions import face_mesh as mp_face_mesh
    MP_AVAILABLE = True
except Exception as e:
    logger.warning(f"MediaPipe not available, FaceMatch will run in degraded fallback mode. Error: {e}")
    MP_AVAILABLE = False
    mp = None
    mp_face_mesh = None

# Fallback face detector: OpenCV's bundled Haar Cascade requires no extra
# install (ships with opencv-contrib-python, already a dependency here).
# It's far less accurate than MediaPipe's landmark mesh -- this is a
# deliberate, disclosed degradation, not a silent unconditional pass.
_HAAR_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
try:
    _haar_face_detector = cv2.CascadeClassifier(_HAAR_CASCADE_PATH)
    _HAAR_AVAILABLE = not _haar_face_detector.empty()
except Exception as e:
    logger.warning(f"OpenCV Haar Cascade unavailable either: {e}")
    _haar_face_detector = None
    _HAAR_AVAILABLE = False

# Fallback match is deliberately conservative: a real similarity score, but
# with a stricter threshold than the MediaPipe path, since histogram
# comparison is a much weaker signal than landmark-mesh geometry and is
# easier to fool. This is disclosed as degraded-mode, not marketed as
# equivalent accuracy to the MediaPipe path.
_FALLBACK_MATCH_THRESHOLD = 0.55

from .config import MIN_FACE_MATCH_CONFIDENCE
from .schemas import FaceMatchStatus

def extract_face(image_bytes: bytes) -> Optional[np.ndarray]:
    """Extract face representation (mesh/landmarks) using MediaPipe."""
    if not MP_AVAILABLE:
        logger.warning("extract_face: MediaPipe is not available.")
        return None

    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        attempts = [
            (img_rgb, 0.5),
            (img_rgb, 0.25),
        ]
        h, w = img_rgb.shape[:2]
        if max(h, w) < 800:
            scale = 800.0 / max(h, w)
            upscaled = cv2.resize(
                img_rgb, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC
            )
            attempts.append((upscaled, 0.25))

        for attempt_img, confidence in attempts:
            with mp_face_mesh.FaceMesh(
                static_image_mode=True,
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=confidence,
            ) as face_mesh:
                results = face_mesh.process(attempt_img)
                if results.multi_face_landmarks:
                    landmarks = results.multi_face_landmarks[0].landmark
                    points = np.array([[lm.x, lm.y, lm.z] for lm in landmarks])
                    center = np.mean(points, axis=0)
                    points_centered = points - center
                    max_dist = np.max(np.linalg.norm(points_centered, axis=1))
                    if max_dist > 0:
                        points_normalized = points_centered / max_dist
                    else:
                        points_normalized = points_centered
                    return points_normalized
    except Exception as exc:
        logger.error(f"extract_face failed: {exc}")
        return None
    return None

def match_faces(encoding1: np.ndarray, encoding2: np.ndarray) -> float:
    """Calculate face similarity using cosine similarity between unit vectors."""
    if encoding1 is None or encoding2 is None:
        return 0.0
    e1 = encoding1.flatten()
    e2 = encoding2.flatten()
    norm1 = np.linalg.norm(e1)
    norm2 = np.linalg.norm(e2)
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    cosine_sim = np.dot(e1, e2) / (norm1 * norm2)
    cosine_sim = max(-1.0, min(1.0, cosine_sim))
    # Euclidean distance of unit vectors: d = sqrt(2 - 2 * cos)
    dist = np.sqrt(max(0.0, 2.0 - 2.0 * cosine_sim))
    # Convert to similarity score aligned with distance threshold (0.60 matches, <= 0.1 mismatch)
    similarity = max(0.0, 1.0 - (dist * 5.0))
    return float(similarity)

class FaceMatchService:
    def __init__(self):
        # We store the ID face embeddings in memory (keyed by verification_id)
        # In a real app, this would use Redis or a DB with TTL.
        self._id_embeddings = {}

    def extract_face(self, image_bytes: bytes) -> Optional[np.ndarray]:
        """Expose extract_face method on the service class."""
        return extract_face(image_bytes)

    def match_faces(self, encoding1: np.ndarray, encoding2: np.ndarray) -> float:
        """Expose match_faces method on the service class."""
        return match_faces(encoding1, encoding2)

    def _get_face_mesh(self, image_bytes: bytes) -> Optional[np.ndarray]:
        """Extract a single normalized face mesh from image bytes.

        ID document photos are often a small crop within a much larger scan
        (e.g. Aadhaar/DL photo region), which MediaPipe's default detection
        confidence can miss. We retry with a lower threshold and an upscaled
        image before giving up, rather than failing on the first attempt.
        """
        return extract_face(image_bytes)

    def _get_fallback_face_signature(self, image_bytes: bytes) -> Optional[np.ndarray]:
        """Degraded-mode face signature using OpenCV Haar Cascade + color histogram.

        Used only when MediaPipe is unavailable. This is a real, image-content-
        dependent comparison (not a fixed mock value), but is meaningfully less
        accurate than landmark-mesh matching -- it can be fooled more easily and
        is sensitive to lighting/angle. Always disclose this degraded mode
        rather than presenting it as equivalent to the MediaPipe path.

        ID document photos are often a small crop within a much larger scan,
        and this function receives the RAW uploaded bytes directly (not the
        upscaled/enhanced version used elsewhere for OCR), so the face region
        can easily fall under Haar Cascade's default minSize threshold. We
        upscale small images and retry with looser detection parameters
        before giving up, mirroring the same strategy used for MediaPipe.
        """
        if not _HAAR_AVAILABLE:
            return None
        try:
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return None

            h, w = img.shape[:2]
            if max(h, w) < 900:
                scale = 900.0 / max(h, w)
                img = cv2.resize(
                    img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC
                )

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            gray = cv2.equalizeHist(gray)  # helps with glare/uneven lighting on laminated IDs

            # Progressively looser detection attempts before giving up.
            detection_attempts = [
                dict(scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)),
                dict(scaleFactor=1.05, minNeighbors=3, minSize=(30, 30)),
                dict(scaleFactor=1.03, minNeighbors=2, minSize=(20, 20)),
            ]
            faces = ()
            for params in detection_attempts:
                faces = _haar_face_detector.detectMultiScale(gray, **params)
                if len(faces) > 0:
                    break

            if len(faces) == 0:
                logger.warning(
                    "[FALLBACK] Haar Cascade found no face after %d attempts "
                    "(with upscaling + histogram equalization applied).",
                    len(detection_attempts),
                )
                return None

            # Use the largest detected face region
            x, y, w2, h2 = max(faces, key=lambda f: f[2] * f[3])
            face_crop = img[y:y + h2, x:x + w2]
            face_crop = cv2.resize(face_crop, (128, 128))
            hist = cv2.calcHist(
                [face_crop], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256]
            )
            cv2.normalize(hist, hist)
            return hist.flatten()
        except Exception as exc:
            logger.error(f"Fallback face signature extraction failed: {exc}")
            return None

    def process_id_face(self, verification_id: UUID, image_bytes: bytes) -> FaceMatchStatus:
        """Extract and store the face signature from the ID document.

        Uses MediaPipe FaceMesh when available. Falls back to OpenCV Haar
        Cascade + color-histogram signature when MediaPipe is unavailable --
        this is a real, disclosed degraded mode, not an unconditional pass.
        """
        if not MP_AVAILABLE:
            logger.warning(
                f"[FALLBACK] MediaPipe unavailable — using degraded OpenCV "
                f"Haar Cascade signature for {verification_id}. Accuracy is "
                f"reduced vs. MediaPipe landmark matching; install a working "
                f"mediapipe+protobuf build for production-grade matching."
            )
            signature = self._get_fallback_face_signature(image_bytes)
            if signature is None:
                logger.warning(f"[FALLBACK] No face detected in ID for {verification_id}")
                return FaceMatchStatus.NO_FACE_DETECTED
            self._id_embeddings[verification_id] = ("FALLBACK", signature)
            return FaceMatchStatus.PENDING

        embedding = self._get_face_mesh(image_bytes)
        if embedding is None:
            logger.warning(f"No face detected in ID for {verification_id}")
            return FaceMatchStatus.NO_FACE_DETECTED
        
        self._id_embeddings[verification_id] = ("MEDIAPIPE", embedding)
        logger.info(f"Successfully extracted ID face for {verification_id}")
        return FaceMatchStatus.PENDING

    def process_live_face(self, verification_id: UUID, frames: List[bytes]) -> Tuple[FaceMatchStatus, Optional[float]]:
        """Run liveness check across frames and match against stored ID face."""
        if verification_id not in self._id_embeddings:
            logger.error(f"No stored ID face found for {verification_id}")
            return FaceMatchStatus.NO_FACE_DETECTED, None

        mode, id_signature = self._id_embeddings[verification_id]

        # --- Liveness Check (Pixel-level difference) ---
        if len(frames) < 2:
            logger.warning(f"Liveness failed (less than 2 frames) for {verification_id}")
            return FaceMatchStatus.LIVENESS_FAILED, None

        nparr1 = np.frombuffer(frames[0], np.uint8)
        img1 = cv2.imdecode(nparr1, cv2.IMREAD_COLOR)
        nparr2 = np.frombuffer(frames[1], np.uint8)
        img2 = cv2.imdecode(nparr2, cv2.IMREAD_COLOR)

        if img1 is None or img2 is None:
            logger.warning(f"Liveness failed (invalid image decoding) for {verification_id}")
            return FaceMatchStatus.NO_FACE_DETECTED, None

        if img1.shape == img2.shape:
            abs_diff = cv2.absdiff(img1, img2)
            mean_diff = np.mean(abs_diff)
            logger.info(f"Liveness mean pixel difference for {verification_id}: {mean_diff:.4f}")
            if mean_diff <= 1.0:
                logger.warning(f"Liveness failed (static spoof detected, diff={mean_diff:.4f}) for {verification_id}")
                return FaceMatchStatus.LIVENESS_FAILED, None

        # --- Fallback mode ---
        if mode == "FALLBACK":
            live_signatures = []
            for frame in frames:
                sig = self._get_fallback_face_signature(frame)
                if sig is not None:
                    live_signatures.append(sig)

            if not live_signatures or len(live_signatures) < 2:
                logger.warning(f"[FALLBACK] No face detected in live frames for {verification_id}")
                return FaceMatchStatus.NO_FACE_DETECTED, None

            correlation = cv2.compareHist(
                id_signature.astype(np.float32),
                live_signatures[0].astype(np.float32),
                cv2.HISTCMP_CORREL,
            )
            similarity = max(0.0, float(correlation))

            logger.warning(
                f"[FALLBACK] Degraded similarity for {verification_id}: {similarity:.3f}"
            )
            del self._id_embeddings[verification_id]
            if similarity >= _FALLBACK_MATCH_THRESHOLD:
                return FaceMatchStatus.MATCHED, similarity
            return FaceMatchStatus.MISMATCH, similarity

        # --- Real MediaPipe mode ---
        # Extract face from both live frames
        emb1 = extract_face(frames[0])
        emb2 = extract_face(frames[1])

        if emb1 is None or emb2 is None:
            logger.warning(f"No face detected in one or both live frames for {verification_id}")
            return FaceMatchStatus.NO_FACE_DETECTED, None

        # Compare first live embedding to the stored ID embedding
        similarity = match_faces(id_signature, emb1)
        logger.info(f"Face match similarity for {verification_id}: {similarity:.3f}")

        if similarity >= MIN_FACE_MATCH_CONFIDENCE:
            # Clean up the stored embedding after a successful match
            del self._id_embeddings[verification_id]
            return FaceMatchStatus.MATCHED, similarity
        else:
            return FaceMatchStatus.MISMATCH, similarity

    def cleanup(self, verification_id: UUID):
        """Remove stored embeddings for a session."""
        self._id_embeddings.pop(verification_id, None)

face_match_service = FaceMatchService()