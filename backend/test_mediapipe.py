import cv2
import numpy as np

try:
    print("Importing MediaPipe...")
    import mediapipe as mp
    print("MediaPipe imported.")
    
    # create a dummy image
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    
    print("Initializing FaceMesh...")
    mp_face_mesh = mp.solutions.face_mesh
    with mp_face_mesh.FaceMesh(
        static_image_mode=True,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5
    ) as face_mesh:
        print("Running process()...")
        results = face_mesh.process(img)
        print("Success! Results:", results)

except Exception as e:
    print("Caught exception:", type(e), e)
