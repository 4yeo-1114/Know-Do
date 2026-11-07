"""后台索引 worker：单线程串行消费队列，逐个文档建立索引。

上传接口只负责 建文档行 + 原始文件落盘 + 入队 并立即返回；
本模块负责从磁盘重读原始文件，完成 切块 → embedding → 入库 → 激活。
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any, Callable

from knowledge.document_ingestion import IngestionConfig, run_ingestion_for_document
from knowledge.document_lifecycle import DocumentLifecycle
from knowledge.pdf_parser import extract_pdf_pages


logger = logging.getLogger("study_task.index_worker")


class IndexWorker:
    """串行后台索引任务队列。同一时刻至多一个任务在执行。"""

    def __init__(
        self,
        upload_dir: str | Path,
        ingest: Callable[[str], None],
        *,
        start_in_thread: bool = True,
    ) -> None:
        self._upload_dir = Path(upload_dir)
        self._ingest = ingest
        self._queue: queue.Queue = queue.Queue()
        self._stop_token = object()
        self._thread: threading.Thread | None = None
        self._start_in_thread = start_in_thread

    def start(self) -> None:
        if not self._start_in_thread or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="index-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._queue.put(self._stop_token)
        self._thread.join(timeout=30)
        self._thread = None

    def enqueue(self, document_id: str) -> None:
        self._queue.put(document_id)

    def run_next_job(self) -> bool:
        """同步消费一个任务（测试钩子）。有任务被处理返回 True，队列为空返回 False。"""
        try:
            job = self._queue.get_nowait()
        except queue.Empty:
            return False
        if job is self._stop_token:
            return False
        self._process(job)
        return True

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is self._stop_token:
                return
            self._process(job)

    def _process(self, document_id: str) -> None:
        #任务级失败已由 run_ingestion_for_document 记录为 failed，worker 本身必须存活
        try:
            self._ingest(document_id)
        except Exception:
            logger.exception("后台索引任务异常: %s", document_id)


def save_upload(
    upload_dir: str | Path,
    document_id: str,
    *,
    owner_id: int,
    source: str,
    document_version: str,
    chunk_size: int,
    overlap: int,
    raw: bytes,
) -> Path:
    """落盘原始文件与索引参数（sidecar），供 worker 重读。"""
    directory = Path(upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "owner_id": owner_id,
        "source": source,
        "document_version": document_version,
        "chunk_size": chunk_size,
        "overlap": overlap,
    }
    (directory / f"{document_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    target = directory / f"{document_id}.upload"
    target.write_bytes(raw)
    return target


def load_upload(upload_dir: str | Path, document_id: str) -> tuple[dict[str, Any], bytes]:
    directory = Path(upload_dir)
    meta = json.loads((directory / f"{document_id}.json").read_text(encoding="utf-8"))
    raw = (directory / f"{document_id}.upload").read_bytes()
    return meta, raw


def ingest_document_from_disk(
    upload_dir: str | Path,
    document_id: str,
    sessions,
    store,
    model,
    lifecycle: DocumentLifecycle,
) -> int:
    """从磁盘重读原始文件，对已存在的文档行执行完整索引流程。"""
    meta, raw = load_upload(upload_dir, document_id)
    source = meta["source"]
    suffix = Path(source).suffix.lower()
    pages = None
    if suffix == ".pdf":
        parsed_pages = extract_pdf_pages(raw)
        text = "\n\n".join(page.text for page in parsed_pages)
        pages = tuple(parsed_pages)
    else:
        text = raw.decode("utf-8")
    return run_ingestion_for_document(
        sessions,
        store,
        model,
        document_id=document_id,
        owner_id=int(meta["owner_id"]),
        source=source,
        text=text,
        pages=pages,
        config=IngestionConfig(
            chunk_size=int(meta["chunk_size"]),
            overlap=int(meta["overlap"]),
            document_version=meta["document_version"],
        ),
        lifecycle=lifecycle,
    )


def build_production_index_worker(
    database,
    runtime,
    upload_dir: str | Path,
) -> IndexWorker:
    """生产环境 worker：预加载 embedding 模型、恢复中断的 processing 文档。"""
    service = runtime.document_service()  # 触发模型加载（服务启动时完成）
    lifecycle = DocumentLifecycle(database.sessions)
    lifecycle.recover_interrupted()

    def ingest(document_id: str) -> None:
        ingest_document_from_disk(
            upload_dir, document_id, database.sessions, service.store, service.model, lifecycle
        )

    return IndexWorker(upload_dir, ingest)
