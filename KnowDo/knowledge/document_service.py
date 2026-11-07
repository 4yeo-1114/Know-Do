from dataclasses import dataclass
import logging
from pathlib import Path
from time import perf_counter

from knowledge.document_ingestion import IngestionConfig, ParsedPage, ingest_document
from knowledge.document_lifecycle import DocumentLifecycle, DocumentState
from knowledge.embedding_retrieval import EmbeddingModel
from knowledge.index_worker import save_upload
from storage.models import Document
from storage.pgvector_store import PgVectorStore
from core.observability import log_event


logger = logging.getLogger("study_task.document")


class DocumentNotFoundError(LookupError):
    pass


class DocumentUploadInProgressError(ValueError):
    pass


@dataclass(frozen=True)
class DocumentUpload:
    source: str
    content: str
    document_version: str = "v1"
    chunk_size: int = 500
    overlap: int = 50
    pages: tuple[ParsedPage, ...] | None = None


@dataclass(frozen=True)
class DocumentIngestResult:
    document: Document
    chunks_ingested: int


class DocumentService:
    def __init__(self, sessions, store: PgVectorStore, model: EmbeddingModel) -> None:
        self._sessions = sessions
        self._store = store
        self._model = model
        self._lifecycle = DocumentLifecycle(sessions)

    @property
    def store(self) -> PgVectorStore:
        return self._store

    @property
    def model(self) -> EmbeddingModel:
        return self._model

    def begin_file_upload(
        self,
        owner_id: int,
        upload: DocumentUpload,
        *,
        raw: bytes,
        upload_dir: str | Path,
    ) -> DocumentIngestResult:
        """为文件上传建立文档行并落盘原始文件，不执行索引（索引由后台 worker 完成）。"""
        existed = (
            self._lifecycle.get_version(
                owner_id=owner_id,
                source=upload.source,
                version=upload.document_version,
            )
            is not None
        )
        document = self._lifecycle.start_upload(
            owner_id=owner_id,
            source=upload.source,
            version=upload.document_version,
            content=upload.content,
        )
        if document.state == DocumentState.READY:
            return DocumentIngestResult(document, 0)
        if existed and document.state in (DocumentState.PENDING, DocumentState.PROCESSING):
            raise DocumentUploadInProgressError("该文件版本正在处理中，请稍后重试")
        save_upload(
            upload_dir,
            document.id,
            owner_id=owner_id,
            source=upload.source,
            document_version=upload.document_version,
            chunk_size=upload.chunk_size,
            overlap=upload.overlap,
            raw=raw,
        )
        return DocumentIngestResult(document, 0)

    def ingest(self, owner_id: int, upload: DocumentUpload) -> DocumentIngestResult:
        started = perf_counter()
        try:
            chunks_ingested = ingest_document(
                self._sessions,
                self._store,
                self._model,
                owner_id=owner_id,
                source=upload.source,
                text=upload.content,
                pages=upload.pages,
                config=IngestionConfig(
                    chunk_size=upload.chunk_size,
                    overlap=upload.overlap,
                    document_version=upload.document_version,
                ),
                lifecycle=self._lifecycle,
            )
            document = self._lifecycle.get_version(
                owner_id=owner_id,
                source=upload.source,
                version=upload.document_version,
            )
            if document is None:
                raise RuntimeError("文档写入完成后无法读取生命周期记录")
        except Exception as exc:
            log_event(
                logger,
                "document_ingestion_failed",
                level=logging.WARNING,
                duration_ms=(perf_counter() - started) * 1000,
                error_type=type(exc).__name__,
            )
            raise
        log_event(
            logger,
            "document_ingestion_completed",
            document_id=document.id,
            state=document.state,
            chunks_ingested=chunks_ingested,
            duration_ms=(perf_counter() - started) * 1000,
        )
        return DocumentIngestResult(document, chunks_ingested)

    def list(self, owner_id: int) -> list[Document]:
        return self._lifecycle.list_for_owner(owner_id)

    def get(self, owner_id: int, document_id: str) -> Document:
        document = self._lifecycle.get_for_owner(
            owner_id=owner_id,
            document_id=document_id,
        )
        if document is None:
            raise DocumentNotFoundError("文档不存在")
        return document

    def delete(self, owner_id: int, document_id: str) -> Document:
        try:
            return self._lifecycle.delete(
                document_id,
                self._store,
                owner_id=owner_id,
            )
        except KeyError as exc:
            raise DocumentNotFoundError("文档不存在") from exc
