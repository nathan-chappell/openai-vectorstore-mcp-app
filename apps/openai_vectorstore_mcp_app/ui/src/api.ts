import type {
  DeleteFileResponse,
  FileDetail,
  FileListResponse,
  TagListResponse,
  UploadResponse,
  UploadSessionResponse,
} from "./types";

const API_BASE_URL = normalizeBase(import.meta.env.VITE_API_BASE_URL ?? "/api");
const CHATKIT_DOMAIN_KEY = import.meta.env.VITE_CHATKIT_DOMAIN_KEY ?? "domain_pk_local_file_desk";

let clerkTokenGetter: (() => Promise<string | null>) | null = null;
let chatKitMetadataGetter: (() => Record<string, unknown> | null) | null = null;

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function setClerkTokenGetter(getter: (() => Promise<string | null>) | null): void {
  clerkTokenGetter = getter;
}

export function setChatKitMetadataGetter(
  getter: (() => Record<string, unknown> | null) | null,
): void {
  chatKitMetadataGetter = getter;
}

export function getChatKitConfig(): { url: string; domainKey: string } {
  return {
    url: `${API_BASE_URL}/chatkit`,
    domainKey: CHATKIT_DOMAIN_KEY,
  };
}

export async function authenticatedFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const headers = new Headers(init?.headers ?? {});
  const token = (await clerkTokenGetter?.()) ?? null;
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const preparedRequest = prepareChatKitRequest(input, {
    ...init,
    headers,
  });
  return fetch(preparedRequest.input, preparedRequest.init);
}

export async function listFiles(params: {
  query?: string;
  tagIds?: string[];
  page?: number;
  pageSize?: number;
}): Promise<FileListResponse> {
  const searchParams = new URLSearchParams();
  if (params.query) {
    searchParams.set("query", params.query);
  }
  if (params.tagIds) {
    for (const tagId of params.tagIds) {
      searchParams.append("tag_ids", tagId);
    }
  }
  if (params.page) {
    searchParams.set("page", String(params.page));
  }
  if (params.pageSize) {
    searchParams.set("page_size", String(params.pageSize));
  }

  return apiRequest<FileListResponse>(`/files?${searchParams.toString()}`);
}

export async function getFileDetail(fileId: string): Promise<FileDetail> {
  return apiRequest<FileDetail>(`/files/${encodeURIComponent(fileId)}`);
}

export async function listTags(): Promise<TagListResponse> {
  return apiRequest<TagListResponse>("/tags");
}

export async function deleteFile(fileId: string): Promise<DeleteFileResponse> {
  return apiRequest<DeleteFileResponse>(`/files/${encodeURIComponent(fileId)}`, {
    method: "DELETE",
  });
}

export async function uploadFile(file: File, tagIds: string[]): Promise<UploadResponse> {
  const uploadSession = await apiRequest<UploadSessionResponse>("/uploads/session", {
    method: "POST",
  });
  const formData = new FormData();
  formData.set("file", file, file.name);
  formData.set("upload_token", uploadSession.upload_token);
  for (const tagId of tagIds) {
    formData.append("tag_ids", tagId);
  }

  const response = await authenticatedFetch(`${API_BASE_URL}/uploads`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw await buildApiError(response);
  }
  return (await response.json()) as UploadResponse;
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers ?? {});
  if (!headers.has("Content-Type") && init?.body) {
    headers.set("Content-Type", "application/json");
  }

  const response = await authenticatedFetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    throw await buildApiError(response);
  }
  return (await response.json()) as T;
}

async function buildApiError(response: Response): Promise<ApiError> {
  const fallbackMessage = `Request failed with ${response.status}`;
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    const payload = (await response.json()) as { detail?: unknown; message?: unknown };
    const detail =
      typeof payload.detail === "string"
        ? payload.detail
        : typeof payload.message === "string"
          ? payload.message
          : fallbackMessage;
    return new ApiError(detail, response.status);
  }
  const text = (await response.text()).trim();
  return new ApiError(text || fallbackMessage, response.status);
}

function normalizeBase(baseUrl: string): string {
  if (baseUrl === "/api") {
    return baseUrl;
  }
  return baseUrl.replace(/\/$/, "");
}

function prepareChatKitRequest(
  input: RequestInfo | URL,
  init?: RequestInit,
): { input: RequestInfo | URL; init?: RequestInit } {
  if (!isChatKitRequest(input) || typeof init?.body !== "string") {
    return { input, init };
  }

  const metadata = chatKitMetadataGetter?.();
  if (!metadata || !Object.keys(metadata).length) {
    return { input, init };
  }

  try {
    const payload = JSON.parse(init.body) as { metadata?: Record<string, unknown> };
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return { input, init };
    }

    return {
      input,
      init: {
        ...init,
        body: JSON.stringify({
          ...payload,
          metadata: {
            ...(typeof payload.metadata === "object" &&
            payload.metadata &&
            !Array.isArray(payload.metadata)
              ? payload.metadata
              : {}),
            ...metadata,
          },
        }),
      },
    };
  } catch {
    return { input, init };
  }
}

function isChatKitRequest(input: RequestInfo | URL): boolean {
  const requestUrl =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;
  return requestUrl.includes("/chatkit");
}
