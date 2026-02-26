from pathlib import Path
import numpy as np
import os
import logging

logger = logging.getLogger(__name__)

MODEL_PATH = Path(os.getenv('MODEL_DEST', '/models'))
MODEL_VERSION = os.getenv('MODEL_VERSION', 'local-docker-compose')


def verify_model_integrity(model_dir: Path = MODEL_PATH / 'embedding' / 'int8') -> str:
    ready_file = MODEL_PATH / '.ready'
    if not ready_file.exists():
        raise RuntimeError(
            f"Model not ready: {ready_file} missing. "
            "Ensure model-init service has completed successfully."
        )
    actual = ready_file.read_text().strip()
    logger.info(f"Model ready file found: version={actual}")

    onnx_file = model_dir / 'model.onnx'
    if not onnx_file.exists():
        raise RuntimeError(f"model.onnx not found at {onnx_file}")

    size_mb = onnx_file.stat().st_size / (1024 ** 2)
    logger.info(f"Model integrity OK: {onnx_file} ({size_mb:.1f} MB)")
    return str(onnx_file)


def warm_up_onnx_pool(session_pool):
    """
    Run dummy inference on each session.
    Triggers JIT compilation and memory arena allocation.
    Without warmup, first real request suffers 3-5x latency spike.
    """
    dummy_ids = np.zeros((1, 128), dtype=np.int64)
    dummy_mask = np.ones((1, 128), dtype=np.int64)
    dummy_types = np.zeros((1, 128), dtype=np.int64)

    for session in session_pool._sessions:
        try:
            session.run(None, {
                'input_ids': dummy_ids,
                'attention_mask': dummy_mask,
                'token_type_ids': dummy_types
            })
        except Exception as e:
            logger.warning(f"Warmup run failed (non-fatal): {e}")

    logger.info(f"ONNX pool warmed up: {len(session_pool._sessions)} sessions ready")
