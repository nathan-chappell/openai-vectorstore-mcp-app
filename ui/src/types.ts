export type TagSummary = {
  id: string;
  name: string;
  slug: string;
  color: string | null;
  file_count: number;
};

export type AuthUser = {
  clerk_user_id: string;
  display_name: string;
  primary_email: string | null;
  active: boolean;
  role: string | null;
};

export type DerivedArtifactSummary = {
  id: string;
  kind: string;
  openai_file_id: string | null;
  text_content: string;
  structured_payload: Record<string, unknown> | unknown[] | null;
  created_at: string;
  updated_at: string;
};

export type FileSummary = {
  id: string;
  display_title: string;
  original_filename: string;
  media_type: string;
  source_kind: string;
  status: "processing" | "ready" | "failed";
  byte_size: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  tags: TagSummary[];
  derived_kinds: string[];
  openai_original_file_id: string | null;
  download_url: string | null;
};

export type FileDetail = FileSummary & {
  original_mime_type: string | null;
  derived_artifacts: DerivedArtifactSummary[];
};

export type FileListResponse = {
  files: FileSummary[];
  total_count: number;
  page: number;
  page_size: number;
  has_more: boolean;
};

export type TagListResponse = {
  tags: TagSummary[];
};

export type UploadSessionResponse = {
  upload_url: string;
  upload_token: string;
  expires_at: number;
};

export type UploadResponse = {
  file: FileSummary;
};

export type DeleteFileResponse = {
  deleted_file_id: string;
};
