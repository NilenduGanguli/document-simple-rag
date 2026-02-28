#!/usr/bin/env python3
"""
model_init.py — Initializes quantized ONNX BERT models for the RAG pipeline.

Runs once per docker compose lifecycle. Downloaded models are stored in
the 'models_volume' Docker volume so subsequent starts are fast.
"""
import os
import sys
import shutil
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

MODEL_DEST = Path(os.environ.get('MODEL_DEST', '/models'))
HF_MODEL_NAME = os.environ.get('HF_MODEL_NAME', 'bert-base-multilingual-cased')
LOCAL_MODEL_PATH_STR = os.environ.get('LOCAL_MODEL_PATH', '')
LOCAL_MODEL_PATH = Path(LOCAL_MODEL_PATH_STR) if LOCAL_MODEL_PATH_STR else None
FORCE_REINIT = os.environ.get('FORCE_REINIT', 'false').lower() == 'true'
HF_HUB_CACHE = os.environ.get('HF_HUB_CACHE', str(MODEL_DEST / '_hf_cache'))
os.environ['HF_HOME'] = HF_HUB_CACHE
os.environ['TRANSFORMERS_CACHE'] = HF_HUB_CACHE
# Set offline flags before any HuggingFace imports
os.environ.setdefault('TRANSFORMERS_OFFLINE', os.environ.get('TRANSFORMERS_OFFLINE', '0'))
os.environ.setdefault('HF_HUB_OFFLINE', os.environ.get('HF_HUB_OFFLINE', '0'))

READY_FILE = MODEL_DEST / '.ready'
FP32_STAGING = MODEL_DEST / '_fp32_staging'
MODEL_DIRS = {
    'embedding': MODEL_DEST / 'embedding' / 'int8',
    'crossencoder': MODEL_DEST / 'crossencoder' / 'int8',
    'ner': MODEL_DEST / 'ner' / 'int8',
}

TOKENIZER_FILES = [
    'tokenizer.json',
    'tokenizer_config.json',
    'vocab.txt',
    'special_tokens_map.json',
]


def is_initialized() -> bool:
    return READY_FILE.exists() and not FORCE_REINIT


def export_fp32() -> Path:
    from optimum.onnxruntime import ORTModelForFeatureExtraction
    from transformers import AutoTokenizer, AutoModel

    FP32_STAGING.mkdir(parents=True, exist_ok=True)

    if LOCAL_MODEL_PATH and LOCAL_MODEL_PATH.exists():
        logger.info(f"Loading model from local path: {LOCAL_MODEL_PATH}")

        # Detect available weight format
        has_pytorch = (
            (LOCAL_MODEL_PATH / 'pytorch_model.bin').exists()
            or (LOCAL_MODEL_PATH / 'model.safetensors').exists()
        )
        has_tf_ckpt = bool(list(LOCAL_MODEL_PATH.glob('*.ckpt.index')))

        if has_pytorch:
            # ── PyTorch weights available: load directly (no TF needed) ──
            logger.info("Found PyTorch weights — loading directly")
            ort_model = ORTModelForFeatureExtraction.from_pretrained(
                str(LOCAL_MODEL_PATH), export=True,
            )
            ort_model.save_pretrained(str(FP32_STAGING))
            tokenizer = AutoTokenizer.from_pretrained(str(LOCAL_MODEL_PATH))
            tokenizer.save_pretrained(str(FP32_STAGING))

        elif has_tf_ckpt:
            # ── TF checkpoint: rename files if needed, convert via PyTorch ──
            logger.info("Found TF checkpoint — converting via PyTorch")

            # Google's BERT uses "bert_model.ckpt.*"; transformers expects "model.ckpt.*"
            ckpt_files = list(LOCAL_MODEL_PATH.glob('*.ckpt.index'))
            if not (LOCAL_MODEL_PATH / 'model.ckpt.index').exists():
                ckpt_prefix = ckpt_files[0].name.rsplit('.ckpt.index', 1)[0]
                logger.info(f"Renaming checkpoint prefix '{ckpt_prefix}' → 'model'")
                load_staging = MODEL_DEST / '_load_staging'
                if load_staging.exists():
                    shutil.rmtree(load_staging)
                load_staging.mkdir(parents=True)
                for f in LOCAL_MODEL_PATH.iterdir():
                    if f.is_file():
                        target_name = f.name
                        if target_name.startswith(ckpt_prefix + '.ckpt'):
                            target_name = 'model.ckpt' + target_name[len(ckpt_prefix) + len('.ckpt'):]
                        os.symlink(f.resolve(), load_staging / target_name)
                load_path = str(load_staging)
            else:
                load_path = str(LOCAL_MODEL_PATH)

            # TF ckpt → PyTorch (requires tensorflow)
            pt_staging = MODEL_DEST / '_pt_staging'
            pt_staging.mkdir(parents=True, exist_ok=True)
            model = AutoModel.from_pretrained(load_path, from_tf=True)
            model.save_pretrained(str(pt_staging))
            tokenizer = AutoTokenizer.from_pretrained(load_path)
            tokenizer.save_pretrained(str(pt_staging))
            logger.info(f"PyTorch model staged at {pt_staging}")

            # Clean up load staging
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
                f"No loadable weights found in {LOCAL_MODEL_PATH}. "
                "Expected pytorch_model.bin, model.safetensors, or *.ckpt.index"
            )
    else:
        logger.info(f"Downloading {HF_MODEL_NAME} from HuggingFace Hub...")
        ort_model = ORTModelForFeatureExtraction.from_pretrained(HF_MODEL_NAME, export=True)
        ort_model.save_pretrained(str(FP32_STAGING))
        tokenizer = AutoTokenizer.from_pretrained(HF_MODEL_NAME)
        tokenizer.save_pretrained(str(FP32_STAGING))

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
    if LOCAL_MODEL_PATH and LOCAL_MODEL_PATH.exists():
        logger.info(f"Source: local TF checkpoint at {LOCAL_MODEL_PATH}")
    else:
        logger.info(f"Source: HuggingFace Hub — {HF_MODEL_NAME}")
    logger.info(f"Destination: {MODEL_DEST}")

    try:
        # Step 1: Export FP32 ONNX
        fp32_dir = export_fp32()

        # Step 2: Quantize embedding model (INT8)
        embedding_dir = MODEL_DIRS['embedding']
        quantize_to_int8(fp32_dir, embedding_dir)
        copy_tokenizer(fp32_dir, embedding_dir)
        logger.info(f"Embedding model ready: {embedding_dir}")

        # Step 3: Copy same INT8 model for cross-encoder
        crossencoder_dir = MODEL_DIRS['crossencoder']
        crossencoder_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(embedding_dir / 'model.onnx', crossencoder_dir / 'model.onnx')
        copy_tokenizer(fp32_dir, crossencoder_dir)
        logger.info(f"Cross-encoder model ready: {crossencoder_dir}")

        # Step 4: Copy same INT8 model for NER
        ner_dir = MODEL_DIRS['ner']
        ner_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(embedding_dir / 'model.onnx', ner_dir / 'model.onnx')
        copy_tokenizer(fp32_dir, ner_dir)
        logger.info(f"NER model ready: {ner_dir}")

        # Step 5: Clean up FP32 staging directory
        shutil.rmtree(FP32_STAGING, ignore_errors=True)
        logger.info("FP32 staging cleaned up")

        # Step 6: Write ready sentinel file
        READY_FILE.write_text("local-docker-compose")
        logger.info(f"=== Model initialization complete: {READY_FILE} ===")

    except Exception as e:
        logger.error(f"Model initialization failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
