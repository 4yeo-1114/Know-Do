from dataclasses import dataclass
import re

from knowledge.embedding_retrieval import EmbeddingModel
from storage.pgvector_store import PgVectorStore
from knowledge.rag import DocumentChunk


@dataclass(frozen=True)
class IngestionConfig:
    chunk_size: int = 500
    overlap: int = 50
    document_version: str = "v1"

    def __post_init__(self) -> None:
        if self.chunk_size < 1:
            raise ValueError("chunk_size 必须大于 0")
        if self.overlap < 0 or self.overlap >= self.chunk_size:
            raise ValueError("overlap 必须大于等于 0 且小于 chunk_size")
        if not self.document_version.strip():
            raise ValueError("document_version 不能为空")


@dataclass(frozen=True)
class ParsedPage:
    page_number: int
    text: str

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("page_number 必须从 1 开始")


def chunk_text(
    text: str,
    *,
    source: str,
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[DocumentChunk]:
    config = IngestionConfig(chunk_size, overlap)
    if not text.strip():
        raise ValueError("文档内容不能为空")
    chunks: list[DocumentChunk] = []
    step = config.chunk_size - config.overlap
    for position, start in enumerate(range(0, len(text), step), start=1):
        content = text[start : start + config.chunk_size]
        if content:
            chunks.append(DocumentChunk(source, position, content))
        if start + config.chunk_size >= len(text):
            break
    return chunks


def chunk_pages(
    pages: list[ParsedPage],
    *,
    source: str,
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[DocumentChunk]:
    """Chunk page text without crossing page boundaries or paragraph boundaries."""
    config = IngestionConfig(chunk_size, overlap)
    chunks: list[DocumentChunk] = []
    for page in pages:
        paragraphs = []
        for part in re.split(r"\n\s*\n", page.text):
            #剥离 NUL 字节：PostgreSQL text 字段不允许 \x00
            cleaned = part.replace("\x00", "").strip()
            if cleaned:
                paragraphs.append(cleaned)
        if not paragraphs:
            continue
        for paragraph in paragraphs:
            if len(paragraph) <= config.chunk_size:
                chunks.append(DocumentChunk(source, len(chunks) + 1, paragraph, page.page_number))
                continue
            for item in chunk_text(
                paragraph,
                source=source,
                chunk_size=config.chunk_size,
                overlap=config.overlap,
            ):
                chunks.append(DocumentChunk(source, len(chunks) + 1, item.text, page.page_number))
    if not chunks:
        raise ValueError("文档内容不能为空")
    return chunks


def ingest_document(
    sessions,
    store: PgVectorStore,
    model: EmbeddingModel,
    *,
    owner_id: int,
    source: str,
    text: str,
    pages: tuple[ParsedPage, ...] | None = None,
    config: IngestionConfig = IngestionConfig(),
    replace_existing: bool = True,
    lifecycle=None,
) -> int:
    """Chunk, encode and persist one document in one database transaction."""
    if lifecycle is not None:
        document = lifecycle.start_upload(
            owner_id=owner_id,
            source=source,
            version=config.document_version,
            content=text,
        )
        #若上传重复版本 直接返回
        if document.state == "ready":
            return 0
        return run_ingestion_for_document(
            sessions,
            store,
            model,
            document_id=document.id,
            owner_id=owner_id,
            source=source,
            text=text,
            pages=pages,
            config=config,
            replace_existing=replace_existing,
            lifecycle=lifecycle,
        )

    #无生命周期管理的直写路径（评测等）保持原行为
    chunks = chunk_pages(
        list(pages) if pages is not None else [ParsedPage(1, text)],
        source=source,
        chunk_size=config.chunk_size,
        overlap=config.overlap,
    )
    vectors = _encode_many(model, [chunk.text for chunk in chunks])
    if hasattr(sessions, "begin"):
        transaction_context = sessions.begin()
    else:
        session = sessions()
        transaction_context = session.begin()
    with transaction_context as session:
        if replace_existing:
            store.delete_document(session,
                                  owner_id=owner_id,
                                  source=source)
        for chunk, vector in zip(chunks, vectors):
            store.insert_chunk(
                session,
                owner_id=owner_id,
                chunk=chunk,
                vector=vector,
                document_version=config.document_version,
            )
    return len(chunks)


def run_ingestion_for_document(
    sessions,
    store,
    model: EmbeddingModel,
    *,
    document_id: str,
    owner_id: int,
    source: str,
    text: str,
    pages: tuple[ParsedPage, ...] | None = None,
    config: IngestionConfig = IngestionConfig(),
    replace_existing: bool = True,
    lifecycle,
) -> int:
    """对已存在的文档行执行 切块→编码→入库→激活 的完整索引流程。

    与 ingest_document 的区别：文档行已存在（pending/failed/processing），
    不再调用 start_upload；失败时先把文档标记为 failed 再重新抛出。
    """
    lifecycle.mark_processing(document_id)
    try:
        chunks = chunk_pages(
            list(pages) if pages is not None else [ParsedPage(1, text)],
            source=source,
            chunk_size=config.chunk_size,
            overlap=config.overlap,
        )
        vectors = _encode_many(model, [chunk.text for chunk in chunks])
        if hasattr(sessions, "begin"):
            transaction_context = sessions.begin()
        else:
            session = sessions()
            transaction_context = session.begin()
        with transaction_context as session:
            if replace_existing:
                store.delete_document_version(
                    session,
                    owner_id=owner_id,
                    source=source,
                    document_version=config.document_version,
                )
            for chunk, vector in zip(chunks, vectors):
                store.insert_chunk(
                    session,
                    owner_id=owner_id,
                    chunk=chunk,
                    vector=vector,
                    document_version=config.document_version,
                )
        lifecycle.activate(document_id)
        return len(chunks)
    except Exception as exc:
        lifecycle.mark_failed(document_id, str(exc) or type(exc).__name__)
        raise


def _encode_many(model: EmbeddingModel, texts: list[str]) -> list[tuple[float, ...]]:
    batch_encoder = getattr(model, "encode_many", None)
    raw_vectors = batch_encoder(texts) if callable(batch_encoder) else [model.encode(text) for text in texts]
    vectors = [tuple(float(value) for value in vector) for vector in raw_vectors]
    if len(vectors) != len(texts):
        raise ValueError("embedding 返回数量与分块数量不一致")
    return vectors
