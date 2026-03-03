#!/usr/bin/env python3
"""
model_init.py — Initializes quantized ONNX BERT models for the RAG pipeline.

Runs once per docker compose lifecycle.  Model source is always S3 — the raw
model files are downloaded from S3_MODEL_BUCKET / MODEL_S3_KEY_PREFIX and then
converted to INT8 ONNX.  No HuggingFace Hub or internet access is required.

Environment variables
---------------------
MODEL_DEST          Base directory for storing models      (default: /models)
MODEL_S3_KEY_PREFIX S3 key prefix for the raw model dir   (default: models/models/bert_uncased_L-12_H-768_A-12)
MODEL_S3_BUCKET     S3 bucket that holds the model        (default: $S3_BUCKET)
S3_ENDPOINT_URL     S3 / MinIO endpoint URL               (required for MinIO)
S3_ACCESS_KEY       S3 access key                         (or AWS_ACCESS_KEY_ID)
S3_SECRET_KEY       S3 secret key                         (or AWS_SECRET_ACCESS_KEY)
FORCE_REINIT        Re-download and re-export even if .ready exists  (default: false)
"""
import os
import sys
import shutil
import logging
from pathlib import Path

# Force offline mode — no HuggingFace Hub access allowed at runtime.
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

MODEL_DEST = Path(os.environ.get('MODEL_DEST', '/models'))
FORCE_REINIT = os.environ.get('FORCE_REINIT', 'false').lower() == 'true'
HF_HUB_CACHE = os.environ.get('HF_HUB_CACHE', str(MODEL_DEST / '_hf_cache'))
os.environ['HF_HOME'] = HF_HUB_CACHE
os.environ['TRANSFORMERS_CACHE'] = HF_HUB_CACHE

# S3 model source configuration
S3_ENDPOINT_URL     = os.environ.get('S3_ENDPOINT_URL', '')
S3_ACCESS_KEY       = os.environ.get('S3_ACCESS_KEY', os.environ.get('AWS_ACCESS_KEY_ID', ''))
S3_SECRET_KEY       = os.environ.get('S3_SECRET_KEY', os.environ.get('AWS_SECRET_ACCESS_KEY', ''))
MODEL_S3_BUCKET     = os.environ.get('MODEL_S3_BUCKET', os.environ.get('S3_BUCKET', ''))
MODEL_S3_KEY_PREFIX = os.environ.get('MODEL_S3_KEY_PREFIX', 'models/models/bert_uncased_L-12_H-768_A-12')

READY_FILE  = MODEL_DEST / '.ready'
FP32_STAGING = MODEL_DEST / '_fp32_staging'
S3_STAGING  = MODEL_DEST / '_s3_staging'
MODEL_DIRS = {
    'embedding':    MODEL_DEST / 'embedding'    / 'int8',
    'crossencoder': MODEL_DEST / 'crossencoder' / 'int8',
    'ner':          MODEL_DEST / 'ner'          / 'int8',
}

TOKENIZER_FILES = [
    'tokenizer.json',
    'tokenizer_config.json',
    'vocab.txt',
    'special_tokens_map.json',
]


def is_initialized() -> bool:
    return READY_FILE.exists() and not FORCE_REINIT


def download_from_s3() -> Path:
    """
    Download raw model files from S3 into S3_STAGING.

    Uses boto3 (synchronous) since model_init.py runs outside asyncio.
    Supports both AWS S3 and MinIO (via S3_ENDPOINT_URL).
    """
    import boto3
    from botocore.client import Config

    if not MODEL_S3_BUCKET:
        raise RuntimeError(
            "MODEL_S3_BUCKET (or S3_BUCKET) is not set — "
            "cannot download model from S3. Set S3_BUCKET in the environment."
        )

    prefix = MODEL_S3_KEY_PREFIX.rstrip('/')
    logger.info(f"Downloading model from s3://{MODEL_S3_BUCKET}/{prefix}")

    client_kwargs: dict = {
        'config': Config(signature_version='s3v4'),
    }
    if S3_ACCESS_KEY:
        client_kwargs['aws_access_key_id'] = S3_ACCESS_KEY
    if S3_SECRET_KEY:
        client_kwargs['aws_secret_access_key'] = S3_SECRET_KEY
    if S3_ENDPOINT_URL:
        client_kwargs['endpoint_url'] = S3_ENDPOINT_URL

    s3 = boto3.client('s3', **client_kwargs)

    paginator = s3.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=MODEL_S3_BUCKET, Prefix=prefix + '/')

    S3_STAGING.mkdir(parents=True, exist_ok=True)
    downloaded = 0

    for page in pages:
        for obj in page.get('Contents', []):
            key = obj['Key']
            # Strip the prefix to get the relative path inside the model dir
            rel_path = key[len(prefix):].lstrip('/')
            if not rel_path:
                continue  # skip the directory placeholder object itself

            dest = S3_STAGING / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"  s3://{MODEL_S3_BUCKET}/{key}  ->  {dest}")
            s3.download_file(MODEL_S3_BUCKET, key, str(dest))
            downloaded += 1

    if downloaded == 0:
        raise RuntimeError(
            f"No files found at s3://{MODEL_S3_BUCKET}/{prefix}/ — "
            "ensure the model has been uploaded to S3 before starting."
        )

    logger.info(f"Downloaded {downloaded} file(s) to {S3_STAGING}")
    return S3_STAGING


def export_fp32(local_model_path: Path) -> Path:
    """Convert the raw model at *local_model_path* to FP32 ONNX."""
    from optimum.onnxruntime import ORTModelForFeatureExtraction
    from transformers import AutoTokenizer, AutoModel

    FP32_STAGING.mkdir(parents=True, exist_ok=True)

    logger.info(f"Exporting FP32 ONNX from: {local_model_path}")

    # Detect available weight format
    has_pytorch = (
        (local_model_path / 'pytorch_model.bin').exists()
        or (local_model_path / 'model.safetensors').exists()
    )
    has_tf_ckpt = bool(list(local_model_path.glob('*.ckpt.index')))

    if has_pytorch:
        # ── PyTorch weights: export directly to ONNX ──────────────────────
        logger.info("Found PyTorch weights — exporting directly")
        ort_model = ORTModelForFeatureExtraction.from_pretrained(
            str(local_model_path), export=True,
        )
        ort_model.save_pretrained(str(FP32_STAGING))
        tokenizer = AutoTokenizer.from_pretrained(str(local_model_path))
        tokenizer.save_pretrained(str(FP32_STAGING))

    elif has_tf_ckpt:
        # ── TF checkpoint: rename files if needed, convert via PyTorch ────
        logger.info("Found TF checkpoint — converting via PyTorch")

        # Google's BERT uses "bert_model.ckpt.*"; transformers expects "model.ckpt.*"
        ckpt_files = list(local_model_path.glob('*.ckpt.index'))
        if not (local_model_path / 'model.ckpt.index').exists():
            ckpt_prefix = ckpt_files[0].name.rsplit('.ckpt.index', 1)[0]
            logger.info(f"Renaming checkpoint prefix '{ckpt_prefix}' → 'model'")
            load_staging = MODEL_DEST / '_load_staging'
            if load_staging.exists():
                shutil.rmtree(load_staging)
            load_staging.mkdir(parents=True)
            for f in local_model_path.iterdir():
                if f.is_file():
                    target_name = f.name
                    if target_name.startswith(ckpt_prefix + '.ckpt'):
                        target_name = 'model.ckpt' + target_name[len(ckpt_prefix) + len('.ckpt'):]
                    os.symlink(f.resolve(), load_staging / target_name)
            load_path = str(load_staging)
        else:
            load_path = str(local_model_path)

        # TF ckpt → PyTorch (requires tensorflow)
        pt_staging = MODEL_DEST / '_pt_staging'
        pt_staging.mkdir(parents=True, exist_ok=True)
        model = AutoModel.from_pretrained(load_path, from_tf=True)
        model.save_pretrained(str(pt_staging))
        tokenizer = AutoTokenizer.from_pretrained(load_path)
        tokenizer.save_pretrained(str(pt_staging))
        logger.info(f"PyTorch model staged at {pt_staging}")
        shutil.rmtree(MODEL_DEST / '_load_staging', ignore_errors=True)

        # PyTorch → ONNX FP32
        ort_model = ORTModelForFeatureExtraction.from_pretrained(
            str(pt_staging), export=True,
        )
        ort_model.save_pretrained(str(FP32_STAGING))
        tokenizer.save_pretrained(str(FP32_STAGING))
        shutil.rmtree(pt_staging, ignore_errors=True)
        logger.info("Staging cleaned up")

    else:
        raise RuntimeError(
            f"No loadable weights found in {local_model_path}. "
            "Expected pytorch_model.bin, model.safetensors, or *.ckpt.index"
        )

    logger.info(f"FP32 ONNX + tokenizer ready at {FP32_STAGING}")
    return FP32_STAGING


def quantize_to_int8(fp32_dir: Path, output_dir: Path) -> None:
    logger.info(f"Quantizing to INT8: {fp32_dir} -> {output_dir}")
    from onnxruntime.quantization import quantize_dynamic, QuantType

    output_dir.mkdir(parents=True, exist_ok=True)
    fp32_model = fp32_dir / 'model.onnx'
    int8_model = output_dir / 'model.onnx'

    quantize_dynamic(
        model_input=str(fp32_model),
        model_output=str(int8_model),
        weight_type=QuantType.QInt8,
        op_types_to_quantize=['MatMul', 'Attention'],
        per_channel=True,
        reduce_range=True,
    )

    size_fp32 = fp32_model.stat().st_size / (1024 ** 2)
    size_int8 = int8_model.stat().st_size / (1024 ** 2)
    logger.info(f"Quantization complete: {size_fp32:.1f} MB -> {size_int8:.1f} MB")


def copy_tokenizer(src: Path, dst: Path) -> None:
    for fname in TOKENIZER_FILES:
        src_file = src / fname
        if src_file.exists():
            shutil.copy2(src_file, dst / fname)
            logger.info(f"Copied tokenizer file: {fname}")
        else:
            logger.warning(f"Tokenizer file not found, skipping: {fname}")


def main() -> None:
    MODEL_DEST.mkdir(parents=True, exist_ok=True)

    if is_initialized():
        logger.info(f"Models already initialized at {MODEL_DEST}. Skipping.")
        logger.info("Set FORCE_REINIT=true to re-initialize.")
        sys.exit(0)

    logger.info("=== Starting model initialization ===")
    logger.info(f"Source: s3://{MODEL_S3_BUCKET}/{MODEL_S3_KEY_PREFIX}")
    logger.info(f"Destination: {MODEL_DEST}")

    try:
        # Step 1: Download raw model from S3
        s3_model_path = download_from_s3()

        # Step 2: Export to FP32 ONNX
        fp32_dir = export_fp32(s3_model_path)

        # Step 3: Quantize embedding model (INT8)
        embedding_dir = MODEL_DIRS['embedding']
        quantize_to_int8(fp32_dir, embedding_dir)
        copy_tokenizer(fp32_dir, embedding_dir)
        logger.info(f"Embedding model ready: {embedding_dir}")

        # Step 4: Copy same INT8 model for cross-encoder
        crossencoder_dir = MODEL_DIRS['crossencoder']
        crossencoder_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(embedding_dir / 'model.onnx', crossencoder_dir / 'model.onnx')
        copy_tokenizer(fp32_dir, crossencoder_dir)
        logger.info(f"Cross-encoder model ready: {crossencoder_dir}")

        # Step 5: Copy same INT8 model for NER
        ner_dir = MODEL_DIRS['ner']
        ner_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(embedding_dir / 'model.onnx', ner_dir / 'model.onnx')
        copy_tokenizer(fp32_dir, ner_dir)
        logger.info(f"NER model ready: {ner_dir}")

        # Step 6: Clean up staging directories
        shutil.rmtree(FP32_STAGING, ignore_errors=True)
        shutil.rmtree(S3_STAGING, ignore_errors=True)
        logger.info("Staging directories cleaned up")

        # Step 7: Write ready sentinel file
        READY_FILE.write_text(MODEL_S3_KEY_PREFIX)
        logger.info(f"=== Model initialization complete: {READY_FILE} ===")

    except Exception as e:
        logger.error(f"Model initialization failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
