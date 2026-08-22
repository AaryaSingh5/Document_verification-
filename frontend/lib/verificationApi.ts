/**
 * Type-Safe API Client for Suraksha Setu Document Verification Module.
 *
 * Designed to connect to the standalone FastAPI server or integrated
 * Suraksha Setu backend.
 */

export type DocumentType =
  | "PASSPORT"
  | "DRIVING_LICENCE"
  | "VOTER_ID"
  | "OTHER_GOVERNMENT_ID";

export type VerificationStatus =
  | "PENDING"
  | "EXTRACTED"
  | "VERIFIED"
  | "PENDING_REVIEW"
  | "REUPLOAD_REQUIRED"
  | "REJECTED";

export type FieldStatus = "FOUND" | "NEEDS_REVIEW" | "NOT_FOUND";

export interface ExtractedField {
  value: string | null;
  status: FieldStatus;
  confidence?: number;
}

export interface ExtractedDocumentData {
  full_name: ExtractedField;
  document_number: ExtractedField;
  nationality: ExtractedField;
  date_of_birth: ExtractedField;
  expiry_date: ExtractedField;
  fields_found: string[];
  fields_missing: string[];
}

export interface DocumentUploadResponse {
  verification_id: string;
  document_type: DocumentType;
  status: VerificationStatus;
  confidence: number;
  extracted: ExtractedDocumentData;
  mock_mode: boolean;
  message: string;
}

export interface ConfirmedDocumentFields {
  full_name?: string;
  nationality?: string;
  date_of_birth?: string;
  expiry_date?: string;
}

export interface DocumentConfirmRequest {
  verification_id: string;
  confirmed_fields: ConfirmedDocumentFields;
}

export interface DocumentConfirmResponse {
  verification_id: string;
  status: VerificationStatus;
  reasons: string[];
  tourist_id: string | null;
}

export interface VerificationStatusResponse {
  verification_id: string;
  document_type: DocumentType;
  status: VerificationStatus;
  confidence: number;
  created_at: string;
  verified_at: string | null;
}

export class VerificationApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public details?: unknown
  ) {
    super(message);
    this.name = "VerificationApiError";
  }
}

const getEnvVar = (key: string): string | undefined => {
  try {
    if (typeof import.meta !== "undefined" && (import.meta as unknown as { env?: Record<string, string> }).env) {
      return (import.meta as unknown as { env: Record<string, string> }).env[key];
    }
    if (typeof globalThis !== "undefined" && (globalThis as unknown as { process?: { env?: Record<string, string> } }).process?.env) {
      return (globalThis as unknown as { process: { env: Record<string, string> } }).process.env[key];
    }
  } catch {
    // fallback
  }
  return undefined;
};

const DEFAULT_BASE_URL =
  getEnvVar("VITE_VERIFICATION_API_URL") ||
  getEnvVar("NEXT_PUBLIC_VERIFICATION_API_URL") ||
  "http://localhost:8000/api/v1/verifications";

/**
 * Upload document image or PDF for OCR processing and identity extraction.
 */
export async function uploadDocument(
  file: File,
  documentType: DocumentType,
  touristId?: string,
  baseUrl: string = DEFAULT_BASE_URL
): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("document_type", documentType);
  if (touristId) {
    formData.append("tourist_id", touristId);
  }

  const endpoint = `${baseUrl.replace(/\/$/, "")}/upload`;

  try {
    const response = await fetch(endpoint, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const errorBody = await response.json().catch(() => ({}));
      throw new VerificationApiError(
        errorBody.detail || `Upload failed with HTTP status ${response.status}`,
        response.status,
        errorBody
      );
    }

    return (await response.json()) as DocumentUploadResponse;
  } catch (error) {
    if (error instanceof VerificationApiError) {
      throw error;
    }
    throw new VerificationApiError(
      `Network error communicating with verification service: ${(error as Error).message}`,
      0,
      error
    );
  }
}

/**
 * Confirm editable fields and execute anti-spoofing verification rules.
 */
export async function confirmDocument(
  request: DocumentConfirmRequest,
  baseUrl: string = DEFAULT_BASE_URL
): Promise<DocumentConfirmResponse> {
  const endpoint = `${baseUrl.replace(/\/$/, "")}/confirm`;

  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      const errorBody = await response.json().catch(() => ({}));
      throw new VerificationApiError(
        errorBody.detail || `Confirmation failed with HTTP status ${response.status}`,
        response.status,
        errorBody
      );
    }

    return (await response.json()) as DocumentConfirmResponse;
  } catch (error) {
    if (error instanceof VerificationApiError) {
      throw error;
    }
    throw new VerificationApiError(
      `Network error communicating with verification service: ${(error as Error).message}`,
      0,
      error
    );
  }
}

/**
 * Retrieve status of a verification session by UUID.
 */
export async function getVerificationStatus(
  verificationId: string,
  baseUrl: string = DEFAULT_BASE_URL
): Promise<VerificationStatusResponse> {
  const endpoint = `${baseUrl.replace(/\/$/, "")}/${verificationId}`;

  try {
    const response = await fetch(endpoint, {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
    });

    if (!response.ok) {
      const errorBody = await response.json().catch(() => ({}));
      throw new VerificationApiError(
        errorBody.detail || `Status check failed with HTTP status ${response.status}`,
        response.status,
        errorBody
      );
    }

    return (await response.json()) as VerificationStatusResponse;
  } catch (error) {
    if (error instanceof VerificationApiError) {
      throw error;
    }
    throw new VerificationApiError(
      `Network error checking status: ${(error as Error).message}`,
      0,
      error
    );
  }
}

/**
 * Best-effort deletion of a verification session and associated temporary file.
 */
export async function deleteVerification(
  verificationId: string,
  baseUrl: string = DEFAULT_BASE_URL
): Promise<void> {
  const endpoint = `${baseUrl.replace(/\/$/, "")}/${verificationId}`;
  try {
    await fetch(endpoint, {
      method: "DELETE",
    });
  } catch {
    // Best-effort cleanup
  }
}
