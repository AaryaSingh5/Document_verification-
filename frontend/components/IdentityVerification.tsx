/**
 * Suraksha Setu - Identity Document Verification React Component.
 *
 * Self-contained, accessible 3-step verification workflow:
 * 1. Document Upload & Preview (with live demo filename hints)
 * 2. OCR Extraction Review (confidence gauge, locked masked doc number, editable fields)
 * 3. Tourist Safety Shield decision & verified credentials badge
 */

import React, { useState, useRef, ChangeEvent, FormEvent } from "react";
import {
  DocumentType,
  DocumentUploadResponse,
  DocumentConfirmResponse,
  ConfirmedDocumentFields,
  uploadDocument,
  confirmDocument,
  VerificationApiError,
} from "../lib/verificationApi";
import { FaceCapture } from "./FaceCapture";

export interface IdentityVerificationProps {
  /** Optional callback fired when verification completes with final decision */
  onVerificationComplete?: (result: DocumentConfirmResponse) => void;
  /** Optional existing tourist ID to attach to this verification */
  touristId?: string;
  /** Base URL of the verification backend API */
  apiUrl?: string;
  /** Optional custom CSS class for outer container */
  className?: string;
}

type WizardStep = "UPLOAD" | "REVIEW" | "COMPLETED";

export const IdentityVerification: React.FC<IdentityVerificationProps> = ({
  onVerificationComplete,
  touristId,
  apiUrl,
  className = "",
}) => {
  // Wizard State
  const [step, setStep] = useState<WizardStep>("UPLOAD");
  const [documentType, setDocumentType] = useState<DocumentType>("PASSPORT");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  // Async & Processing States
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [loadingMessage, setLoadingMessage] = useState<string>("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // OCR Response & Form Data
  const [uploadResult, setUploadResult] = useState<DocumentUploadResponse | null>(null);
  const [confirmedFields, setConfirmedFields] = useState<ConfirmedDocumentFields>({
    full_name: "",
    nationality: "",
    date_of_birth: "",
    expiry_date: "",
  });

  // Final Confirmation Result
  const [decisionResult, setDecisionResult] = useState<DocumentConfirmResponse | null>(null);

  // Face Capture State
  const [faceExtractDone, setFaceExtractDone] = useState(false);
  const [faceMatchSuccess, setFaceMatchSuccess] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  // Handle File Selection
  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    setErrorMessage(null);
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      setSelectedFile(file);
      if (file.type.startsWith("image/")) {
        setPreviewUrl(URL.createObjectURL(file));
      } else {
        setPreviewUrl(null);
      }
    }
  };

  // Handle Upload & OCR Processing
  const handleUploadSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!selectedFile) {
      setErrorMessage("Please select a valid document file to upload.");
      return;
    }

    setIsLoading(true);
    setLoadingMessage("Encrypting, storing securely, and executing OCR analysis...");
    setErrorMessage(null);
    setFaceExtractDone(false);
    setFaceMatchSuccess(false);

    try {
      const result = await uploadDocument(
        selectedFile,
        documentType,
        touristId,
        apiUrl
      );
      setUploadResult(result);

      // Pre-fill editable fields from OCR extraction
      setConfirmedFields({
        full_name: result.extracted.full_name?.value || "",
        nationality: result.extracted.nationality?.value || "",
        date_of_birth: result.extracted.date_of_birth?.value || "",
        expiry_date: result.extracted.expiry_date?.value || "",
      });

      // If document was completely unreadable, stay on upload step with error
      if (result.status === "REUPLOAD_REQUIRED") {
        setErrorMessage(result.message);
      } else {
        // Trigger background face extract
        import("../lib/verificationApi").then(({ faceExtract }) => {
          faceExtract(result.verification_id, apiUrl)
            .then(() => setFaceExtractDone(true))
            .catch(() => {
                // If it fails, we still let them proceed but faceMatch step will handle the error
                setFaceExtractDone(true);
            });
        });
        setStep("REVIEW");
      }
    } catch (err) {
      const apiErr = err as VerificationApiError;
      setErrorMessage(apiErr.message || "Failed to process document upload.");
    } finally {
      setIsLoading(false);
      setLoadingMessage("");
    }
  };

  // Handle Final Confirmation
  const handleConfirmSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!uploadResult) return;
    
    if (!faceMatchSuccess) {
        setErrorMessage("Please complete the face verification step before confirming.");
        return;
    }

    setIsLoading(true);
    setLoadingMessage("Validating identity against Suraksha Setu security rules...");
    setErrorMessage(null);

    try {
      const result = await confirmDocument(
        {
          verification_id: uploadResult.verification_id,
          confirmed_fields: confirmedFields,
        },
        apiUrl
      );

      setDecisionResult(result);
      setStep("COMPLETED");
      if (onVerificationComplete) {
        onVerificationComplete(result);
      }
    } catch (err) {
      const apiErr = err as VerificationApiError;
      setErrorMessage(apiErr.message || "Failed to confirm verification details.");
    } finally {
      setIsLoading(false);
      setLoadingMessage("");
    }
  };

  // Reset to Upload Step
  const handleReset = () => {
    setStep("UPLOAD");
    setSelectedFile(null);
    setPreviewUrl(null);
    setUploadResult(null);
    setDecisionResult(null);
    setErrorMessage(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  // Helpers for Confidence Meter
  const getConfidenceLevel = (score: number) => {
    if (score >= 0.75) return { label: "High Accuracy", color: "bg-emerald-500", text: "text-emerald-700" };
    if (score >= 0.50) return { label: "Moderate (Needs Review)", color: "bg-amber-500", text: "text-amber-700" };
    return { label: "Low Quality", color: "bg-rose-500", text: "text-rose-700" };
  };

  return (
    <div
      className={`max-w-2xl mx-auto bg-white rounded-2xl shadow-xl border border-slate-100 overflow-hidden font-sans text-slate-800 ${className}`}
      style={{ minHeight: "560px" }}
    >
      {/* Header & Brand Banner */}
      <header className="bg-gradient-to-r from-blue-900 via-indigo-900 to-slate-900 text-white p-6 relative overflow-hidden">
        <div className="flex items-center justify-between relative z-10">
          <div>
            <div className="flex items-center space-x-2">
              <span className="text-2xl" role="img" aria-label="Shield">🛡️</span>
              <h1 className="text-xl font-bold tracking-tight">Suraksha Setu</h1>
            </div>
            <p className="text-xs text-blue-200 mt-1">Smart Tourist Identity & Safety Verification</p>
          </div>
          <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-blue-800/80 text-blue-100 border border-blue-600/50">
            {uploadResult?.mock_mode ? "Demo OCR Engine" : "Encrypted OCR v1.0"}
          </span>
        </div>

        {/* Step Indicator */}
        <nav aria-label="Verification Steps" className="mt-6 flex justify-between items-center text-xs">
          {[
            { id: "UPLOAD", label: "1. Upload ID" },
            { id: "REVIEW", label: "2. Verify Details" },
            { id: "COMPLETED", label: "3. Safety Badge" },
          ].map((s, idx) => {
            const isActive = step === s.id;
            const isDone = (s.id === "UPLOAD" && step !== "UPLOAD") || (s.id === "REVIEW" && step === "COMPLETED");
            return (
              <div key={s.id} className="flex items-center space-x-1.5">
                <span
                  className={`w-6 h-6 rounded-full flex items-center justify-center font-bold text-xs transition-colors ${
                    isDone
                      ? "bg-emerald-500 text-white"
                      : isActive
                      ? "bg-blue-400 text-slate-950 shadow-md shadow-blue-500/30"
                      : "bg-slate-800 text-slate-400 border border-slate-700"
                  }`}
                >
                  {isDone ? "✓" : idx + 1}
                </span>
                <span className={`font-medium ${isActive ? "text-white" : "text-blue-300/70"}`}>
                  {s.label}
                </span>
              </div>
            );
          })}
        </nav>
      </header>

      {/* Main Body */}
      <main className="p-6">
        {/* Error Alert Box */}
        {errorMessage && (
          <div
            role="alert"
            className="mb-6 p-4 rounded-xl bg-rose-50 border border-rose-200 text-rose-800 flex items-start space-x-3 text-sm animate-fade-in"
          >
            <span className="text-lg">⚠️</span>
            <div className="flex-1">
              <p className="font-semibold">Verification Notice</p>
              <p className="text-rose-700 mt-0.5 leading-relaxed">{errorMessage}</p>
            </div>
          </div>
        )}

        {/* Loading Overlay */}
        {isLoading && (
          <div
            role="status"
            aria-live="polite"
            className="my-12 text-center py-10 flex flex-col items-center justify-center space-y-4"
          >
            <div className="w-12 h-12 border-4 border-blue-200 border-t-blue-700 rounded-full animate-spin"></div>
            <p className="text-sm font-semibold text-slate-700">{loadingMessage}</p>
            <p className="text-xs text-slate-400">Processing identity document with neural OCR...</p>
          </div>
        )}

        {/* STEP 1: UPLOAD DOCUMENT */}
        {!isLoading && step === "UPLOAD" && (
          <form onSubmit={handleUploadSubmit} className="space-y-6">
            <div>
              <label htmlFor="document-type-select" className="block text-sm font-bold text-slate-700 mb-2">
                Select Document Type
              </label>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {[
                  { id: "PASSPORT", label: "Passport", icon: "🛂" },
                  { id: "DRIVING_LICENCE", label: "Driving Licence", icon: "🪪" },
                  { id: "VOTER_ID", label: "Voter ID", icon: "🗳️" },
                  { id: "OTHER_GOVERNMENT_ID", label: "Govt ID", icon: "🏛️" },
                ].map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    onClick={() => setDocumentType(item.id as DocumentType)}
                    className={`flex flex-col items-center justify-center p-3.5 rounded-xl border text-center transition-all ${
                      documentType === item.id
                        ? "border-blue-700 bg-blue-50/70 text-blue-900 ring-2 ring-blue-700/20 font-semibold"
                        : "border-slate-200 hover:border-slate-300 bg-white text-slate-600"
                    }`}
                  >
                    <span className="text-2xl mb-1">{item.icon}</span>
                    <span className="text-xs">{item.label}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Drag & Drop Upload Zone */}
            <div>
              <label className="block text-sm font-bold text-slate-700 mb-2">
                Upload Identity Document
              </label>
              <div
                onClick={() => fileInputRef.current?.click()}
                className={`border-2 border-dashed rounded-2xl p-6 text-center cursor-pointer transition-colors ${
                  selectedFile
                    ? "border-emerald-400 bg-emerald-50/30"
                    : "border-slate-300 hover:border-blue-500 bg-slate-50/50 hover:bg-blue-50/20"
                }`}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  id="document-type-select"
                  accept=".jpg,.jpeg,.png,.pdf"
                  onChange={handleFileChange}
                  className="hidden"
                />

                {previewUrl ? (
                  <div className="flex flex-col items-center space-y-3">
                    <img
                      src={previewUrl}
                      alt="Document scan preview"
                      className="max-h-48 rounded-lg shadow-sm border border-slate-200 object-contain"
                    />
                    <div className="text-xs font-semibold text-emerald-800">
                      📄 {selectedFile?.name} ({(selectedFile?.size ? (selectedFile.size / 1024).toFixed(1) : 0)} KB)
                    </div>
                    <span className="text-xs text-blue-600 underline">Click to choose a different file</span>
                  </div>
                ) : selectedFile ? (
                  <div className="space-y-2 py-4">
                    <span className="text-3xl">📄</span>
                    <p className="text-sm font-semibold text-slate-800">{selectedFile.name}</p>
                    <p className="text-xs text-slate-500">PDF Document Ready for OCR Scan</p>
                  </div>
                ) : (
                  <div className="space-y-2 py-6">
                    <span className="text-4xl text-slate-400">📸</span>
                    <p className="text-sm font-semibold text-slate-700">
                      Click to browse or drag & drop document
                    </p>
                    <p className="text-xs text-slate-400">
                      Supports JPEG, PNG, or PDF (up to 8MB)
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* Test Helper Guide for Demo Mode */}
            <div className="p-3.5 bg-slate-50 border border-slate-200 rounded-xl text-xs text-slate-600">
              <p className="font-semibold text-slate-800 mb-1 flex items-center gap-1.5">
                <span>💡</span> Demo OCR Test Cues
              </p>
              <p className="leading-relaxed">
                Test different workflows by naming your file:
                <br />• <code className="bg-slate-200 px-1 py-0.5 rounded text-slate-900 font-mono">clear_passport.jpg</code> → High confidence (0.93) success
                <br />• <code className="bg-slate-200 px-1 py-0.5 rounded text-slate-900 font-mono">blurry_id.png</code> → Low confidence (0.41) rejection
                <br />• <code className="bg-slate-200 px-1 py-0.5 rounded text-slate-900 font-mono">expired_dl.jpg</code> → Expiry rule validation rejection
              </p>
            </div>

            <button
              type="submit"
              disabled={!selectedFile}
              className={`w-full py-3.5 px-4 rounded-xl font-bold text-sm text-white transition-all shadow-md ${
                selectedFile
                  ? "bg-blue-700 hover:bg-blue-800 shadow-blue-700/20 active:scale-[0.99]"
                  : "bg-slate-300 cursor-not-allowed shadow-none"
              }`}
            >
              Scan & Extract Details →
            </button>
          </form>
        )}

        {/* STEP 2: REVIEW & CONFIRM OCR DETAILS */}
        {!isLoading && step === "REVIEW" && uploadResult && (
          <form onSubmit={handleConfirmSubmit} className="space-y-5">
            {/* Confidence & Quality Meter */}
            <div className="p-4 bg-slate-50 border border-slate-200 rounded-xl space-y-2">
              <div className="flex justify-between items-center text-xs">
                <span className="font-bold text-slate-700">OCR Scan Confidence</span>
                <span className={`font-semibold ${getConfidenceLevel(uploadResult.confidence).text}`}>
                  {getConfidenceLevel(uploadResult.confidence).label} ({(uploadResult.confidence * 100).toFixed(0)}%)
                </span>
              </div>
              <div className="w-full bg-slate-200 h-2 rounded-full overflow-hidden">
                <div
                  className={`h-full ${getConfidenceLevel(uploadResult.confidence).color} transition-all duration-500`}
                  style={{ width: `${Math.max(10, uploadResult.confidence * 100)}%` }}
                ></div>
              </div>
              <p className="text-xs text-slate-500 mt-1">{uploadResult.message}</p>
            </div>

            {/* Read-Only Masked Document Number (Anti-Spoofing Protected) */}
            <div className="p-3.5 bg-amber-50/70 border border-amber-200 rounded-xl">
              <div className="flex items-center justify-between">
                <div>
                  <span className="text-xs font-bold text-amber-900 flex items-center gap-1.5">
                    🔒 Document Number (Locked)
                  </span>
                  <p className="text-sm font-mono font-bold text-slate-800 mt-0.5">
                    {uploadResult.extracted.document_number.value || "Not Detected"}
                  </p>
                </div>
                <span className="text-xs px-2 py-1 rounded bg-amber-200/80 text-amber-900 font-medium">
                  {uploadResult.extracted.document_number.status}
                </span>
              </div>
              <p className="text-[11px] text-amber-800/80 mt-1">
                Document numbers are locked post-OCR to prevent document tampering or identity spoofing.
              </p>
            </div>

            {/* User-Editable Fields */}
            <div className="space-y-3">
              <div>
                <div className="flex justify-between items-center mb-1">
                  <label className="block text-xs font-bold text-slate-700">
                    Full Legal Name *
                  </label>
                  {uploadResult.extracted.full_name?.status && (
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${uploadResult.extracted.full_name.status === 'FOUND' ? 'bg-emerald-100 text-emerald-800' : uploadResult.extracted.full_name.status === 'NEEDS_REVIEW' ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-600'}`}>
                      {uploadResult.extracted.full_name.status}
                    </span>
                  )}
                </div>
                <input
                  type="text"
                  required
                  value={confirmedFields.full_name || ""}
                  onChange={(e) => setConfirmedFields({ ...confirmedFields, full_name: e.target.value })}
                  placeholder="e.g. Aarav Rajesh Sharma"
                  className="w-full p-2.5 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-600 focus:border-blue-600 outline-none"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <div className="flex justify-between items-center mb-1">
                    <label className="block text-xs font-bold text-slate-700">
                      Nationality
                    </label>
                    {uploadResult.extracted.nationality?.status && (
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${uploadResult.extracted.nationality.status === 'FOUND' ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'}`}>
                        {uploadResult.extracted.nationality.status}
                      </span>
                    )}
                  </div>
                  <input
                    type="text"
                    value={confirmedFields.nationality || ""}
                    onChange={(e) => setConfirmedFields({ ...confirmedFields, nationality: e.target.value })}
                    placeholder="e.g. Indian"
                    className="w-full p-2.5 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-600 focus:border-blue-600 outline-none"
                  />
                </div>

                <div>
                  <div className="flex justify-between items-center mb-1">
                    <label className="block text-xs font-bold text-slate-700">
                      Date of Birth (YYYY-MM-DD) *
                    </label>
                    {uploadResult.extracted.date_of_birth?.status && (
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${uploadResult.extracted.date_of_birth.status === 'FOUND' ? 'bg-emerald-100 text-emerald-800' : uploadResult.extracted.date_of_birth.status === 'NEEDS_REVIEW' ? 'bg-amber-100 text-amber-800' : 'bg-rose-100 text-rose-800'}`}>
                        {uploadResult.extracted.date_of_birth.status}
                      </span>
                    )}
                  </div>
                  <input
                    type="date"
                    required
                    value={confirmedFields.date_of_birth || ""}
                    onChange={(e) => setConfirmedFields({ ...confirmedFields, date_of_birth: e.target.value })}
                    className="w-full p-2.5 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-600 focus:border-blue-600 outline-none"
                  />
                  {uploadResult.extracted.date_of_birth?.status === "NEEDS_REVIEW" && (
                    <p className="text-[11px] text-amber-700 mt-1">
                      ⚠️ Date of birth detected with moderate confidence. Please verify.
                    </p>
                  )}
                </div>
              </div>

              {(documentType === "PASSPORT" || documentType === "DRIVING_LICENCE") && (
                <div>
                  <div className="flex justify-between items-center mb-1">
                    <label className="block text-xs font-bold text-slate-700">
                      Expiry Date (YYYY-MM-DD) *
                    </label>
                    {uploadResult.extracted.expiry_date?.status && (
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${uploadResult.extracted.expiry_date.status === 'FOUND' ? 'bg-emerald-100 text-emerald-800' : uploadResult.extracted.expiry_date.status === 'NEEDS_REVIEW' ? 'bg-amber-100 text-amber-800' : 'bg-rose-100 text-rose-800'}`}>
                        {uploadResult.extracted.expiry_date.status}
                      </span>
                    )}
                  </div>
                  <input
                    type="date"
                    required
                    value={confirmedFields.expiry_date || ""}
                    onChange={(e) => setConfirmedFields({ ...confirmedFields, expiry_date: e.target.value })}
                    className="w-full p-2.5 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-600 focus:border-blue-600 outline-none"
                  />
                  {uploadResult.extracted.expiry_date?.status === "NEEDS_REVIEW" && confirmedFields.expiry_date ? (
                    <p className="text-[11px] text-amber-700 mt-1">
                      ⚠️ Expiry date detected with moderate confidence ({((uploadResult.extracted.expiry_date.confidence || 0.68) * 100).toFixed(0)}%). Please verify.
                    </p>
                  ) : !confirmedFields.expiry_date ? (
                    <p className="text-[11px] text-rose-700 mt-1">
                      ⚠️ We could not confidently extract the expiry date. Please check the document or enter it manually.
                    </p>
                  ) : null}
                </div>
              )}
            </div>

            {/* Liveness Face Verification Step */}
            <div className="pt-4 border-t border-slate-200">
              <label className="block text-sm font-bold text-slate-700 mb-3">
                Live Face Verification *
              </label>
              {!faceExtractDone ? (
                <div className="p-4 bg-slate-50 text-center text-sm text-slate-500 rounded-lg animate-pulse">
                  Extracting face from document...
                </div>
              ) : (
                <FaceCapture 
                  verificationId={uploadResult.verification_id}
                  mockMode={uploadResult.mock_mode}
                  onMatchSuccess={() => {
                      setFaceMatchSuccess(true);
                      setErrorMessage(null);
                  }}
                  onMatchFailed={(err) => {
                      setFaceMatchSuccess(false);
                      setErrorMessage(err);
                  }}
                />
              )}
            </div>

            {/* Action Buttons */}
            <div className="flex gap-3 pt-2">
              <button
                type="button"
                onClick={handleReset}
                className="flex-1 py-3 px-4 rounded-xl font-semibold text-sm border border-slate-300 hover:bg-slate-50 text-slate-700 transition-colors"
              >
                ← Re-upload
              </button>
              <button
                type="submit"
                disabled={!faceMatchSuccess}
                className={`flex-2 py-3 px-6 rounded-xl font-bold text-sm text-white shadow-md transition-all active:scale-[0.99] ${
                  faceMatchSuccess 
                    ? "bg-emerald-600 hover:bg-emerald-700 shadow-emerald-600/20" 
                    : "bg-slate-400 cursor-not-allowed shadow-none"
                }`}
              >
                Confirm & Verify Identity ✓
              </button>
            </div>
          </form>
        )}

        {/* STEP 3: OUTCOME & SAFETY DECISION BADGE */}
        {!isLoading && step === "COMPLETED" && decisionResult && (
          <div className="text-center py-6 space-y-6">
            {decisionResult.status === "VERIFIED" ? (
              <div className="space-y-4">
                <div className="w-16 h-16 bg-emerald-100 text-emerald-700 rounded-full flex items-center justify-center mx-auto text-3xl shadow-inner">
                  🛡️
                </div>
                <div>
                  <span className="inline-block px-3 py-1 bg-emerald-100 text-emerald-800 font-bold text-xs rounded-full uppercase tracking-wider mb-2">
                    Suraksha Setu Verified
                  </span>
                  <h2 className="text-2xl font-black text-slate-900">Identity Verification Successful</h2>
                  <p className="text-sm text-slate-600 max-w-md mx-auto mt-1">
                    Your tourist safety identity credential has been registered and secured on the Suraksha Setu safety network.
                  </p>
                </div>

                <div className="bg-slate-50 border border-slate-200 rounded-2xl p-4 text-left max-w-md mx-auto space-y-2 text-xs">
                  <div className="flex justify-between py-1 border-b border-slate-200">
                    <span className="text-slate-500">Tourist ID Token</span>
                    <span className="font-mono font-bold text-slate-800">{decisionResult.tourist_id || "N/A"}</span>
                  </div>
                  <div className="flex justify-between py-1 border-b border-slate-200">
                    <span className="text-slate-500">Verification Ref</span>
                    <span className="font-mono text-slate-700">{decisionResult.verification_id}</span>
                  </div>
                  <div className="flex justify-between py-1">
                    <span className="text-slate-500">Safety Status</span>
                    <span className="font-bold text-emerald-700">Active & Protected</span>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={handleReset}
                  className="py-2.5 px-6 rounded-xl font-semibold text-xs border border-slate-300 hover:bg-slate-100 text-slate-700"
                >
                  Verify Another Document
                </button>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="w-16 h-16 bg-rose-100 text-rose-700 rounded-full flex items-center justify-center mx-auto text-3xl shadow-inner">
                  ✕
                </div>
                <div>
                  <span className="inline-block px-3 py-1 bg-rose-100 text-rose-800 font-bold text-xs rounded-full uppercase tracking-wider mb-2">
                    Verification Rejected
                  </span>
                  <h2 className="text-2xl font-black text-slate-900">Document Could Not Be Verified</h2>
                  <p className="text-sm text-slate-600 max-w-md mx-auto mt-1">
                    The submitted document does not meet Suraksha Setu validation criteria.
                  </p>
                </div>

                {decisionResult.reasons.length > 0 && (
                  <div className="bg-rose-50 border border-rose-200 rounded-2xl p-4 text-left max-w-md mx-auto space-y-1.5 text-xs text-rose-900">
                    <p className="font-bold text-rose-950 mb-1">Rejection Reasons:</p>
                    <ul className="list-disc pl-4 space-y-1">
                      {decisionResult.reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <button
                  type="button"
                  onClick={handleReset}
                  className="py-3 px-6 bg-blue-700 hover:bg-blue-800 text-white font-bold text-sm rounded-xl shadow-md"
                >
                  Try Again with Clear Document
                </button>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
};

export default IdentityVerification;
