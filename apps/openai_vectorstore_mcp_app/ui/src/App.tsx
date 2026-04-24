import { useDeferredValue, useEffect, useEffectEvent, useMemo, useRef, useState, startTransition, type ChangeEvent } from "react";
import { SignInButton, UserButton, useAuth, useUser } from "@clerk/react";

import { ApiError, deleteFile, getFileDetail, listFiles, listTags, setClerkTokenGetter, uploadFile } from "./api";
import { ChatPane } from "./ChatPane";
import type { FileDetail, FileSummary, TagSummary } from "./types";

const PAGE_SIZE = 20;

export default function App() {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const { user } = useUser();

  useEffect(() => {
    setClerkTokenGetter(async () => (await getToken()) ?? null);
    return () => {
      setClerkTokenGetter(null);
    };
  }, [getToken]);

  if (!isLoaded) {
    return (
      <div className="screen-shell">
        <div className="status-card">
          <p className="eyebrow">Loading</p>
          <h1>Preparing your file desk</h1>
          <p>Connecting Clerk, loading the explorer, and getting the chat surface ready.</p>
        </div>
      </div>
    );
  }

  if (!isSignedIn) {
    return (
      <div className="screen-shell">
        <div className="status-card">
          <p className="eyebrow">Sign In Required</p>
          <h1>Open the library with your Clerk session</h1>
          <p>
            This workspace uses the same Clerk-backed flow as the companion apps, so the
            explorer, uploads, and chat agent all stay scoped to your account.
          </p>
          <SignInButton mode="modal">
            <button className="primary-button" type="button">
              Sign in
            </button>
          </SignInButton>
        </div>
      </div>
    );
  }

  return (
    <Workspace
      userLabel={
        user?.fullName ??
        user?.primaryEmailAddress?.emailAddress ??
        "Signed-in user"
      }
    />
  );
}

function Workspace({ userLabel }: { userLabel: string }) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [tags, setTags] = useState<TagSummary[]>([]);
  const [selectedTagIds, setSelectedTagIds] = useState<string[]>([]);
  const [files, setFiles] = useState<FileSummary[]>([]);
  const [selectedFileIds, setSelectedFileIds] = useState<string[]>([]);
  const [activeFileId, setActiveFileId] = useState<string | null>(null);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [fileDetail, setFileDetail] = useState<FileDetail | null>(null);
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [isLibraryLoading, setIsLibraryLoading] = useState(true);
  const [isDetailLoading, setIsDetailLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const selectedTagKey = useMemo(() => selectedTagIds.join(","), [selectedTagIds]);

  const loadTags = useEffectEvent(async () => {
    try {
      const response = await listTags();
      setTags(response.tags);
    } catch (error) {
      if (error instanceof ApiError) {
        setErrorMessage(error.message);
      }
    }
  });

  const loadLibrary = useEffectEvent(async () => {
    setIsLibraryLoading(true);
    setErrorMessage(null);
    try {
      const response = await listFiles({
        query: deferredQuery || undefined,
        tagIds: selectedTagIds,
        page,
        pageSize: PAGE_SIZE,
      });
      setFiles(response.files);
      setTotalCount(response.total_count);
      setSelectedFileIds((current) =>
        current.filter((fileId) => response.files.some((file) => file.id === fileId)),
      );
      setActiveFileId((current) => {
        if (current && response.files.some((file) => file.id === current)) {
          return current;
        }
        return response.files[0]?.id ?? null;
      });
    } catch (error) {
      setFiles([]);
      setTotalCount(0);
      if (error instanceof ApiError) {
        setErrorMessage(error.message);
      } else {
        setErrorMessage("Could not load your files.");
      }
    } finally {
      setIsLibraryLoading(false);
    }
  });

  const loadDetail = useEffectEvent(async () => {
    if (!activeFileId) {
      setFileDetail(null);
      return;
    }
    setIsDetailLoading(true);
    try {
      setFileDetail(await getFileDetail(activeFileId));
    } catch (error) {
      if (error instanceof ApiError) {
        setErrorMessage(error.message);
      }
      setFileDetail(null);
    } finally {
      setIsDetailLoading(false);
    }
  });

  useEffect(() => {
    void loadTags();
  }, []);

  useEffect(() => {
    void loadLibrary();
  }, [deferredQuery, page, selectedTagKey]);

  useEffect(() => {
    void loadDetail();
  }, [activeFileId]);

  function toggleTag(tagId: string): void {
    startTransition(() => {
      setPage(1);
      setSelectedTagIds((current) =>
        current.includes(tagId)
          ? current.filter((value) => value !== tagId)
          : [...current, tagId],
      );
    });
  }

  function toggleSelectedFile(fileId: string): void {
    setSelectedFileIds((current) =>
      current.includes(fileId)
        ? current.filter((value) => value !== fileId)
        : [...current, fileId],
    );
  }

  async function handleUploadSelection(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const chosenFiles = Array.from(event.currentTarget.files ?? []);
    if (!chosenFiles.length) {
      return;
    }

    setIsUploading(true);
    setErrorMessage(null);
    try {
      for (const file of chosenFiles) {
        await uploadFile(file, selectedTagIds);
      }
      await Promise.all([loadTags(), loadLibrary()]);
    } catch (error) {
      if (error instanceof ApiError) {
        setErrorMessage(error.message);
      } else {
        setErrorMessage("Upload failed.");
      }
    } finally {
      setIsUploading(false);
      event.currentTarget.value = "";
    }
  }

  async function handleDelete(file: FileSummary): Promise<void> {
    const confirmed = window.confirm(`Delete "${file.display_title}" from the library?`);
    if (!confirmed) {
      return;
    }
    setErrorMessage(null);
    try {
      await deleteFile(file.id);
      setSelectedFileIds((current) => current.filter((value) => value !== file.id));
      await Promise.all([loadTags(), loadLibrary()]);
    } catch (error) {
      if (error instanceof ApiError) {
        setErrorMessage(error.message);
      } else {
        setErrorMessage("Delete failed.");
      }
    }
  }

  const selectedCount = selectedFileIds.length;
  const activeFile = files.find((file) => file.id === activeFileId) ?? null;

  return (
    <div className="app-shell">
      <div className="workspace">
        <aside className="explorer-panel">
          <div className="explorer-header">
            <div>
              <p className="eyebrow">File Desk</p>
              <h1>Explorer</h1>
              <p className="panel-copy">
                Search the library, keep a few files selected, and send that context straight into
                the chat on the right.
              </p>
            </div>
            <div className="explorer-actions">
              <button
                className="ghost-button"
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading}
              >
                {isUploading ? "Uploading..." : "Upload files"}
              </button>
              <UserButton />
            </div>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            className="hidden-input"
            multiple
            onChange={(event) => {
              void handleUploadSelection(event);
            }}
          />

          <section className="explorer-card">
            <label className="field-label" htmlFor="file-search">
              Search files
            </label>
            <input
              id="file-search"
              className="search-input"
              type="search"
              value={query}
              onChange={(event) => {
                startTransition(() => {
                  setPage(1);
                  setQuery(event.target.value);
                });
              }}
              placeholder="Titles, tags, or extracted text"
            />

            <div className="tag-bar">
              {tags.map((tag) => (
                <button
                  key={tag.id}
                  className={selectedTagIds.includes(tag.id) ? "tag-chip tag-chip--active" : "tag-chip"}
                  type="button"
                  onClick={() => {
                    toggleTag(tag.id);
                  }}
                >
                  {tag.name}
                  <span>{tag.node_count}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="explorer-card explorer-card--stretch">
            <div className="section-heading">
              <div>
                <h2>Files</h2>
                <p>
                  {isLibraryLoading ? "Loading..." : `${totalCount} file${totalCount === 1 ? "" : "s"}`}
                </p>
              </div>
              <div className="selection-pill">{selectedCount} selected</div>
            </div>

            {errorMessage ? <div className="error-banner">{errorMessage}</div> : null}

            <div className="file-list">
              {files.map((file) => {
                const isActive = file.id === activeFileId;
                const isSelected = selectedFileIds.includes(file.id);
                return (
                  <article
                    key={file.id}
                    className={isActive ? "file-row file-row--active" : "file-row"}
                    onClick={() => {
                      setActiveFileId(file.id);
                    }}
                  >
                    <div className="file-row__top">
                      <button
                        className={isSelected ? "select-toggle select-toggle--active" : "select-toggle"}
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          toggleSelectedFile(file.id);
                        }}
                      >
                        {isSelected ? "Selected" : "Select"}
                      </button>
                      <button
                        className="delete-link"
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          void handleDelete(file);
                        }}
                      >
                        Delete
                      </button>
                    </div>
                    <h3>{file.display_title}</h3>
                    <p className="file-row__meta">{file.original_filename}</p>
                    <div className="file-badges">
                      <span>{file.media_type}</span>
                      <span>{formatBytes(file.byte_size)}</span>
                      <span>{file.status}</span>
                    </div>
                    {file.tags.length ? (
                      <div className="file-tags">
                        {file.tags.map((tag) => (
                          <span key={tag.id}>{tag.name}</span>
                        ))}
                      </div>
                    ) : null}
                  </article>
                );
              })}
              {!isLibraryLoading && !files.length ? (
                <div className="empty-state">
                  <h3>No matching files yet</h3>
                  <p>Upload a few files or clear your filters to widen the library view.</p>
                </div>
              ) : null}
            </div>

            <div className="pagination-row">
              <button
                className="ghost-button"
                type="button"
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                disabled={page === 1 || isLibraryLoading}
              >
                Previous
              </button>
              <span>Page {page}</span>
              <button
                className="ghost-button"
                type="button"
                onClick={() => setPage((current) => current + 1)}
                disabled={isLibraryLoading || page * PAGE_SIZE >= totalCount}
              >
                Next
              </button>
            </div>
          </section>

          <section className="explorer-card preview-card">
            <div className="section-heading">
              <div>
                <h2>Preview</h2>
                <p>{activeFile ? activeFile.display_title : "Select a file"}</p>
              </div>
            </div>
            {isDetailLoading ? <p className="muted-copy">Loading file detail...</p> : null}
            {!isDetailLoading && fileDetail ? (
              <>
                <div className="detail-grid">
                  <div>
                    <span className="detail-label">Type</span>
                    <strong>{fileDetail.media_type}</strong>
                  </div>
                  <div>
                    <span className="detail-label">Updated</span>
                    <strong>{formatDate(fileDetail.updated_at)}</strong>
                  </div>
                </div>
                <div className="preview-text">
                  {fileDetail.derived_artifacts.length ? (
                    fileDetail.derived_artifacts[0].text_content
                  ) : (
                    "No extracted text is available for this file yet."
                  )}
                </div>
                {fileDetail.download_url ? (
                  <a className="download-link" href={fileDetail.download_url} target="_blank" rel="noreferrer">
                    Download original file
                  </a>
                ) : null}
              </>
            ) : null}
            {!isDetailLoading && !fileDetail ? (
              <p className="muted-copy">Select a file to inspect its extracted text and metadata.</p>
            ) : null}
          </section>
        </aside>

        <main className="chat-panel">
          <div className="chat-header">
            <div>
              <p className="eyebrow">Assistant</p>
              <h2>{userLabel}</h2>
              <p className="panel-copy">
                The chat can see the files you explicitly select in the explorer, and it can widen
                out to the full MCP-backed library when needed.
              </p>
            </div>
            <div className="chat-summary">
              <span>{selectedCount}</span>
              <small>files in current chat context</small>
            </div>
          </div>
          <ChatPane
            selectedFileIds={selectedFileIds}
            activeThreadId={activeThreadId}
            onActiveThreadIdChange={setActiveThreadId}
          />
        </main>
      </div>
    </div>
  );
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value));
  } catch {
    return value;
  }
}
