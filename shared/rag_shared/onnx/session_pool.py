import asyncio
import os
import time
from contextlib import asynccontextmanager
import onnxruntime as ort
import logging

logger = logging.getLogger(__name__)


def create_ort_session(model_path: str, intra_threads: int) -> ort.InferenceSession:
    """
    Create an ONNX Runtime session optimised for CPU inference.

    intra_op_num_threads: threads for a single operator (MatMul parallelism).
    inter_op_num_threads: threads for running ops in parallel (graph-level).
    """
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = intra_threads
    opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.enable_cpu_mem_arena = True
    opts.enable_mem_pattern = True
    opts.add_session_config_entry('session.use_env_allocators', '1')

    session = ort.InferenceSession(
        model_path,
        sess_options=opts,
        providers=['CPUExecutionProvider'],
    )
    logger.info(f"ONNX session created: {model_path}, threads={intra_threads}")
    return session


class ONNXSessionPool:
    """Pool of ONNX Runtime sessions for concurrent CPU inference."""

    def __init__(
        self,
        model_path: str,
        pool_size: int,
        threads_per_session: int,
    ) -> None:
        self._model_path = model_path
        self._pool_size = pool_size
        self._threads_per_session = threads_per_session
        self._sessions = [
            create_ort_session(model_path, threads_per_session)
            for _ in range(pool_size)
        ]
        self._queue: asyncio.Queue = asyncio.Queue()
        for s in self._sessions:
            self._queue.put_nowait(s)
        logger.info(
            f"ONNX session pool ready: size={pool_size}, "
            f"threads/session={threads_per_session}"
        )

    @asynccontextmanager
    async def acquire(self, timeout: float = 5.0):
        """
        Acquire an ONNX session from the pool with timeout.

        Yields:
            (session, wait_ms) tuple.

        Raises:
            RuntimeError if no session becomes available within *timeout* seconds.
        """
        start = time.monotonic()
        try:
            session = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            wait_ms = (time.monotonic() - start) * 1000
            raise RuntimeError(
                f"ONNX session pool exhausted after {wait_ms:.0f}ms. "
                f"Consider increasing ONNX_POOL_SIZE (current: {self._pool_size})"
            )
        wait_ms = (time.monotonic() - start) * 1000
        try:
            yield session, wait_ms
        finally:
            await self._queue.put(session)

    @classmethod
    def from_env(cls, model_path: str) -> 'ONNXSessionPool':
        pool_size = int(os.getenv('ONNX_POOL_SIZE', '2'))
        threads_per_session = int(os.getenv('ONNX_THREADS_PER_SESSION', '2'))
        return cls(model_path, pool_size, threads_per_session)

    def __len__(self) -> int:
        return self._pool_size

    @property
    def available(self) -> int:
        """Number of sessions currently available in the pool."""
        return self._queue.qsize()
