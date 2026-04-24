from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter

from openai import AsyncOpenAI
from openai.types.file_purpose import FilePurpose
from openai.types.shared_params.comparison_filter import ComparisonFilter
from openai.types.shared_params.compound_filter import CompoundFilter

from .schemas import ImageDescriptionPayload, SearchHit, TagMatchMode
from .settings import AppSettings

logger = logging.getLogger(__name__)


class OpenAIFileLibraryGateway:
    """OpenAI-backed file, search, and multimodal ingestion operations."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        self._logger = logging.getLogger(__name__)

    async def close(self) -> None:
        await self._client.close()

    async def create_vector_store(
        self,
        *,
        name: str,
        description: str | None,
        metadata: dict[str, str] | None,
    ) -> str:
        started_at = perf_counter()
        vector_store = await self._client.vector_stores.create(
            name=name,
            description=description,
            metadata=metadata,
        )
        self._logger.info(
            "file_library_vector_store_created vector_store_id=%s name=%s duration_ms=%.1f",
            vector_store.id,
            name,
            (perf_counter() - started_at) * 1000,
        )
        return vector_store.id

    async def upload_original_file(
        self,
        *,
        local_path: Path,
        purpose: FilePurpose,
    ) -> str:
        started_at = perf_counter()
        with local_path.open("rb") as file_handle:
            file_object = await self._client.files.create(file=file_handle, purpose=purpose)
        self._logger.info(
            "file_library_original_file_uploaded file_id=%s filename=%s purpose=%s duration_ms=%.1f",
            file_object.id,
            file_object.filename,
            purpose,
            (perf_counter() - started_at) * 1000,
        )
        return file_object.id

    async def create_text_artifact_and_attach(
        self,
        *,
        vector_store_id: str,
        filename: str,
        text_content: str,
        attributes: dict[str, str | float | bool],
    ) -> str:
        started_at = perf_counter()
        with NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(text_content)

        try:
            with temp_path.open("rb") as file_handle:
                uploaded_file = await self._client.files.create(
                    file=file_handle,
                    purpose="assistants",
                )
            await self._client.vector_stores.files.create_and_poll(
                file_id=uploaded_file.id,
                vector_store_id=vector_store_id,
                attributes=attributes,
                poll_interval_ms=self._settings.openai_poll_interval_ms,
            )
        finally:
            temp_path.unlink(missing_ok=True)

        self._logger.info(
            "file_library_text_artifact_attached vector_store_id=%s file_id=%s filename=%s duration_ms=%.1f",
            vector_store_id,
            uploaded_file.id,
            filename,
            (perf_counter() - started_at) * 1000,
        )
        return uploaded_file.id

    async def attach_existing_file_to_vector_store(
        self,
        *,
        vector_store_id: str,
        file_id: str,
        attributes: dict[str, str | float | bool],
    ) -> str:
        started_at = perf_counter()
        await self._client.vector_stores.files.create_and_poll(
            file_id=file_id,
            vector_store_id=vector_store_id,
            attributes=attributes,
            poll_interval_ms=self._settings.openai_poll_interval_ms,
        )
        self._logger.info(
            "file_library_existing_file_attached vector_store_id=%s file_id=%s duration_ms=%.1f",
            vector_store_id,
            file_id,
            (perf_counter() - started_at) * 1000,
        )
        return file_id

    async def describe_image(
        self,
        *,
        openai_file_id: str,
    ) -> ImageDescriptionPayload:
        started_at = perf_counter()
        response = await self._client.responses.parse(
            model=self._settings.openai_vision_model,
            text_format=ImageDescriptionPayload,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Describe this uploaded image for a searchable file library. "
                                "Capture objects, scene, layout, visible text, and practical "
                                "details a later user might search for."
                            ),
                        },
                        {
                            "type": "input_image",
                            "file_id": openai_file_id,
                            "detail": "high",
                        },
                    ],
                }
            ],
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("Expected structured image description output from OpenAI.")

        self._logger.info(
            "file_library_image_described file_id=%s model=%s duration_ms=%.1f",
            openai_file_id,
            self._settings.openai_vision_model,
            (perf_counter() - started_at) * 1000,
        )
        return parsed

    async def transcribe_audio(
        self,
        *,
        local_path: Path,
    ) -> tuple[str, dict[str, object]]:
        started_at = perf_counter()
        with local_path.open("rb") as file_handle:
            transcription = await self._client.audio.transcriptions.create(
                file=file_handle,
                model=self._settings.openai_audio_transcription_model,
                response_format="diarized_json",
                chunking_strategy="auto",
            )

        segments = [
            {
                "id": segment.id,
                "speaker": segment.speaker,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "type": segment.type,
            }
            for segment in transcription.segments
        ]
        searchable_text = "\n".join(f"[{segment['speaker']}] {segment['text']}" for segment in segments).strip()
        payload: dict[str, object] = {
            "duration": transcription.duration,
            "task": transcription.task,
            "text": transcription.text,
            "segments": segments,
        }
        self._logger.info(
            "file_library_audio_transcribed filename=%s model=%s segments=%s duration_ms=%.1f",
            local_path.name,
            self._settings.openai_audio_transcription_model,
            len(segments),
            (perf_counter() - started_at) * 1000,
        )
        return searchable_text or transcription.text, payload

    async def transcribe_video(
        self,
        *,
        local_path: Path,
    ) -> tuple[str, dict[str, object]]:
        started_at = perf_counter()
        audio_path = await self._extract_audio_track(local_path)
        try:
            transcript_text, payload = await self.transcribe_audio(local_path=audio_path)
        finally:
            audio_path.unlink(missing_ok=True)

        payload["video_filename"] = local_path.name
        self._logger.info(
            "file_library_video_transcribed filename=%s duration_ms=%.1f",
            local_path.name,
            (perf_counter() - started_at) * 1000,
        )
        return transcript_text, payload

    async def search_vector_store(
        self,
        *,
        vector_store_id: str,
        query: str,
        max_results: int,
        rewrite_query: bool,
        filters: ComparisonFilter | CompoundFilter | None,
    ) -> list[SearchHit]:
        started_at = perf_counter()
        page = await self._client.vector_stores.search(
            vector_store_id,
            query=query,
            max_num_results=max_results,
            rewrite_query=rewrite_query,
            filters=filters,
        )
        hits = [SearchHit.from_openai(search_result) for search_result in page.data]
        self._logger.info(
            "file_library_vector_store_search vector_store_id=%s query=%s hits=%s duration_ms=%.1f",
            vector_store_id,
            query,
            len(hits),
            (perf_counter() - started_at) * 1000,
        )
        return hits

    async def delete_file(self, *, file_id: str) -> None:
        started_at = perf_counter()
        await self._client.files.delete(file_id)
        self._logger.info(
            "file_library_file_deleted file_id=%s duration_ms=%.1f",
            file_id,
            (perf_counter() - started_at) * 1000,
        )

    async def read_file_bytes(self, *, file_id: str) -> bytes:
        started_at = perf_counter()
        response = await self._client.files.content(file_id)
        content = response.content
        self._logger.info(
            "file_library_file_read file_id=%s bytes=%s duration_ms=%.1f",
            file_id,
            len(content),
            (perf_counter() - started_at) * 1000,
        )
        return content

    @staticmethod
    def choose_original_file_purpose(*, source_kind: str) -> FilePurpose:
        if source_kind == "image":
            return "vision"
        return "assistants"

    async def _extract_audio_track(self, local_path: Path) -> Path:
        with NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            audio_path = Path(temp_file.name)

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(local_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(audio_path),
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            audio_path.unlink(missing_ok=True)
            error_message = stderr.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"ffmpeg audio extraction failed: {error_message}")
        return audio_path


def guess_media_type(local_path: Path, declared_media_type: str | None) -> str:
    if declared_media_type:
        return declared_media_type
    guessed_media_type, _ = mimetypes.guess_type(local_path.name)
    return guessed_media_type or "application/octet-stream"


def build_filter_groups(
    *,
    file_ids: list[str],
    media_types: list[str],
    tag_slugs: list[str],
    tag_match_mode: TagMatchMode,
) -> ComparisonFilter | CompoundFilter | None:
    groups: list[ComparisonFilter | CompoundFilter] = []
    if file_ids:
        groups.append(
            _or_group("file_id", file_ids)
            if len(file_ids) > 1
            else {"type": "eq", "key": "file_id", "value": file_ids[0]}
        )
    if media_types:
        groups.append(
            _or_group("media_type", media_types)
            if len(media_types) > 1
            else {"type": "eq", "key": "media_type", "value": media_types[0]}
        )
    if tag_slugs:
        tag_filters: list[ComparisonFilter] = [
            {"type": "eq", "key": f"tag__{slug}", "value": True} for slug in tag_slugs
        ]
        if len(tag_filters) == 1:
            groups.append(tag_filters[0])
        else:
            groups.append(
                {
                    "type": "and" if tag_match_mode == "all" else "or",
                    "filters": tag_filters,
                }
            )

    if not groups:
        return None
    if len(groups) == 1:
        return groups[0]
    return {"type": "and", "filters": groups}


def build_searchable_attributes(
    *,
    file_library_id: str,
    file_id: str,
    file_title: str,
    derived_artifact_id: str | None,
    source_kind: str,
    media_type: str,
    derived_kind: str,
    original_openai_file_id: str | None,
    original_filename: str,
    tag_names: list[str],
    tag_slugs: list[str],
) -> dict[str, str | float | bool]:
    attributes: dict[str, str | float | bool] = {
        "file_library_id": file_library_id,
        "file_id": file_id,
        "file_title": file_title,
        "source_kind": source_kind,
        "media_type": media_type,
        "derived_kind": derived_kind,
        "original_filename": original_filename,
        "tag_names": ",".join(tag_names),
    }
    if derived_artifact_id is not None:
        attributes["derived_artifact_id"] = derived_artifact_id
    if original_openai_file_id is not None:
        attributes["original_openai_file_id"] = original_openai_file_id
    for slug in tag_slugs:
        attributes[f"tag__{slug}"] = True
    return attributes


def _or_group(key: str, values: list[str]) -> CompoundFilter:
    return {
        "type": "or",
        "filters": [{"type": "eq", "key": key, "value": value} for value in values],
    }
