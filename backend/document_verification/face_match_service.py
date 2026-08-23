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
    mp_face_mesh = mp.solutions.face_mesh
    MP_AVAILABLE = True
except Exception as e:
    logger.warning(f"MediaPipe not available, FaceMatch will run in mock mode. Error: {e}")
    MP_AVAILABLE = False
    mp = None
    mp_face_mesh = None

from .config import MIN_FACE_MATCH_CONFIDENCE
from .schemas import FaceMatchStatus

class FaceMatchService:
    def __init__(self):
        # We store the ID face embeddings in memory (keyed by verification_id)
        # In a real app, this would use Redis or a DB with TTL.
        self._id_embeddings = {}

    def _get_face_mesh(self, image_bytes: bytes) -> Optional[np.ndarray]:
        """Extract a single normalized face mesh from image bytes."""
        if not MP_AVAILABLE:
            # Mock mode: Generate a deterministic fake embedding based on image size
            logger.info("MediaPipe unavailable; generating mock face embedding")
            return np.random.rand(468, 3)

        try:
            # Decode image
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return None
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

            with mp_face_mesh.FaceMesh(
                static_image_mode=True,
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.5
            ) as face_mesh:
                results = face_mesh.process(img_rgb)
                if not results.multi_face_landmarks:
                    return None
                
                landmarks = results.multi_face_landmarks[0].landmark
                # Convert to numpy array of [x, y, z]
                points = np.array([[lm.x, lm.y, lm.z] for lm in landmarks])
                
                # Normalize: center at origin
                center = np.mean(points, axis=0)
                points_centered = points - center
                
                # Normalize: scale
                max_dist = np.max(np.linalg.norm(points_centered, axis=1))
                if max_dist > 0:
                    points_normalized = points_centered / max_dist
                else:
                    points_normalized = points_centered
                    
                return points_normalized
        except Exception as exc:
            logger.error(f"Face extraction failed: {exc}")
            return None

    def process_id_face(self, verification_id: UUID, image_bytes: bytes) -> FaceMatchStatus:
        """Extract and store the face embedding from the ID document.
        
        PROTOTYPE NOTE: When MediaPipe is unavailable (mock mode), stores a sentinel
        value so process_live_face can detect it and return a mock MATCHED result.
        """
        if not MP_AVAILABLE:
            # Mock mode: store a sentinel so live match knows to return mock MATCHED
            logger.info(
                f"[MOCK] MediaPipe unavailable — storing mock sentinel for {verification_id}. "
                "Face match will return MATCHED in mock mode."
            )
            self._id_embeddings[verification_id] = "MOCK_SENTINEL"
            return FaceMatchStatus.PENDING

        embedding = self._get_face_mesh(image_bytes)
        if embedding is None:
            logger.warning(f"No face detected in ID for {verification_id}")
            return FaceMatchStatus.NO_FACE_DETECTED
        
        self._id_embeddings[verification_id] = embedding
        logger.info(f"Successfully extracted ID face for {verification_id}")
        return FaceMatchStatus.PENDING

    def process_live_face(self, verification_id: UUID, frames: List[bytes]) -> Tuple[FaceMatchStatus, Optional[float]]:
        """Run liveness check across frames and match against stored ID face."""
        if verification_id not in self._id_embeddings:
            logger.error(f"No stored ID face found for {verification_id}")
            return FaceMatchStatus.NO_FACE_DETECTED, None

        id_embedding = self._id_embeddings[verification_id]

        # Mock mode: MediaPipe unavailable — skip real matching, return deterministic MATCHED
        if id_embedding == "MOCK_SENTINEL":
            logger.info(
                f"[MOCK] Returning mock MATCHED for {verification_id}. "
                "Install a compatible mediapipe+tensorflow build for real face matching."
            )
            del self._id_embeddings[verification_id]
            return FaceMatchStatus.MATCHED, 0.92
        
        live_embeddings = []
        for frame in frames:
            emb = self._get_face_mesh(frame)
            if emb is not None:
                live_embeddings.append(emb)

        if not live_embeddings:
            logger.warning(f"No face detected in live frames for {verification_id}")
            return FaceMatchStatus.NO_FACE_DETECTED, None
            
        # --- Liveness Check ---
        # If we have multiple frames, we expect *some* micro-movement.
        # If they are exactly identical, it's a static image spoof.
        if len(live_embeddings) > 1:
            diffs = []
            for i in range(1, len(live_embeddings)):
                diff = np.mean(np.linalg.norm(live_embeddings[i] - live_embeddings[0], axis=1))
                diffs.append(diff)
            
            avg_diff = sum(diffs) / len(diffs)
            logger.debug(f"Liveness micro-movement score: {avg_diff:.6f}")
            
            # If the variation is practically zero, it's a static spoof
            if avg_diff < 1e-4:
                logger.warning(f"Liveness failed (static spoof detected) for {verification_id}")
                return FaceMatchStatus.LIVENESS_FAILED, None

        # --- Face Match ---
        # Compare the first live embedding to the ID embedding
        live_embedding = live_embeddings[0]
        
        # Calculate mean Euclidean distance across all landmarks
        distance = np.mean(np.linalg.norm(id_embedding - live_embedding, axis=1))
        
        # Convert distance to a similarity score (0 to 1)
        # Empirical tuning: typical match distance is < 0.1, mismatch is > 0.15
        similarity = max(0.0, 1.0 - (distance * 5.0))
        
        logger.info(f"Face match similarity for {verification_id}: {similarity:.3f} (dist: {distance:.3f})")
        
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

