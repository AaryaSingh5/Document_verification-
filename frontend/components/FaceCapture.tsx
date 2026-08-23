import React, { useRef, useState, useCallback, useEffect } from "react";
import { Camera, CheckCircle, RefreshCw, AlertCircle } from "lucide-react";
import { faceMatch, FaceMatchStatus } from "../lib/verificationApi";

interface FaceCaptureProps {
  verificationId: string;
  onMatchSuccess: () => void;
  onMatchFailed: (reason: string) => void;
  mockMode?: boolean;
}

export const FaceCapture: React.FC<FaceCaptureProps> = ({
  verificationId,
  onMatchSuccess,
  onMatchFailed,
  mockMode = false,
}) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [isCapturing, setIsCapturing] = useState(false);
  const [status, setStatus] = useState<FaceMatchStatus>("PENDING");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [countdown, setCountdown] = useState<number | null>(null);

  // Start webcam
  const startCamera = useCallback(async () => {
    try {
      setErrorMsg(null);
      const mediaStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: 640, height: 480 },
      });
      setStream(mediaStream);
      if (videoRef.current) {
        videoRef.current.srcObject = mediaStream;
      }
    } catch (err) {
      setErrorMsg("Camera access denied or unavailable.");
    }
  }, []);

  // Cleanup camera on unmount
  useEffect(() => {
    if (stream && videoRef.current && !videoRef.current.srcObject) {
      videoRef.current.srcObject = stream;
    }
    return () => {
      // We don't want to stop the tracks on every render, only on full unmount, 
      // but the stream state lives as long as the component anyway.
    };
  }, [stream]);

  useEffect(() => {
    return () => {
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
      }
    };
  }, [stream]);

  const captureFrame = (): Promise<Blob | null> => {
    return new Promise((resolve) => {
      if (!videoRef.current) return resolve(null);
      const canvas = document.createElement("canvas");
      canvas.width = videoRef.current.videoWidth;
      canvas.height = videoRef.current.videoHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) return resolve(null);
      ctx.drawImage(videoRef.current, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => resolve(blob), "image/jpeg", 0.9);
    });
  };

  const startLivenessCapture = async () => {
    setIsCapturing(true);
    setErrorMsg(null);

    if (mockMode) {
      setTimeout(() => {
        setStatus("MATCHED");
        onMatchSuccess();
        setIsCapturing(false);
      }, 1500);
      return;
    }

    try {
      const capturedFrames: Blob[] = [];
      
      // Capture 3 frames with 1 second delay between each for liveness
      for (let i = 3; i > 0; i--) {
        setCountdown(i);
        await new Promise((r) => setTimeout(r, 1000));
        const blob = await captureFrame();
        if (blob) capturedFrames.push(blob);
      }
      setCountdown(null);
      
      if (capturedFrames.length < 3) {
        throw new Error("Failed to capture enough frames.");
      }

      setStatus("PENDING");
      const res = await faceMatch(verificationId, capturedFrames);
      
      setStatus(res.status);
      if (res.status === "MATCHED") {
        onMatchSuccess();
      } else {
        setErrorMsg(res.message);
        onMatchFailed(res.message);
      }
    } catch (error: any) {
      setErrorMsg(error.message || "Failed to process face match");
      setStatus("MISMATCH");
    } finally {
      setIsCapturing(false);
    }
  };

  return (
    <div className="w-full max-w-md mx-auto p-4 bg-white border rounded-xl shadow-sm">
      <div className="text-center mb-4">
        <h3 className="text-lg font-semibold text-gray-800 flex items-center justify-center gap-2">
          <Camera className="w-5 h-5 text-indigo-600" />
          Face Verification
        </h3>
        <p className="text-sm text-gray-500 mt-1">
          Let's ensure you match your ID photo. 
          {mockMode && " (Mock Mode active)"}
        </p>
      </div>

      {!stream && status === "PENDING" && !mockMode ? (
        <div className="flex flex-col items-center p-6 bg-indigo-50 rounded-lg">
          <p className="text-indigo-800 text-sm text-center mb-4">
            We need camera access to perform a quick liveness check.
          </p>
          <button
            onClick={startCamera}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 transition"
          >
            Enable Camera
          </button>
        </div>
      ) : (
        <div className="relative rounded-lg overflow-hidden bg-gray-100 aspect-video mb-4 flex items-center justify-center">
          {mockMode ? (
            <div className="text-gray-400 flex flex-col items-center">
              <Camera className="w-12 h-12 mb-2" />
              <span>Mock Camera Feed</span>
            </div>
          ) : (
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              className="w-full h-full object-cover"
            />
          )}

          {/* Overlay elements */}
          {countdown !== null && (
            <div className="absolute inset-0 bg-black/40 flex flex-col items-center justify-center text-white">
              <span className="text-4xl font-bold animate-pulse">{countdown}</span>
              <span className="text-sm mt-2">Blink or move slightly...</span>
            </div>
          )}

          {isCapturing && countdown === null && (
            <div className="absolute inset-0 bg-black/60 flex items-center justify-center">
              <RefreshCw className="w-8 h-8 text-white animate-spin" />
            </div>
          )}
        </div>
      )}

      {errorMsg && (
        <div className="mb-4 p-3 bg-red-50 border border-red-100 text-red-700 text-sm rounded-lg flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}

      {status === "MATCHED" && (
        <div className="mb-4 p-3 bg-green-50 border border-green-100 text-green-700 text-sm rounded-lg flex items-center gap-2">
          <CheckCircle className="w-4 h-4 flex-shrink-0" />
          <span>Face matched successfully.</span>
        </div>
      )}

      {status !== "MATCHED" && (
        <button
          onClick={startLivenessCapture}
          disabled={(!stream && !mockMode) || isCapturing}
          className="w-full py-2.5 bg-indigo-600 text-white rounded-lg font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition flex justify-center items-center gap-2"
        >
          {isCapturing ? "Processing..." : "Start Verification"}
        </button>
      )}
    </div>
  );
};
