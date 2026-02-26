import numpy as np


def mean_pooling_np(
    token_embeddings: np.ndarray,
    attention_mask: np.ndarray,
) -> np.ndarray:
    """
    Pure NumPy mean pooling from Section 10.6 of the design document.
    No PyTorch dependency at runtime.

    Args:
        token_embeddings: [batch, seq_len, hidden_size] float32
        attention_mask:   [batch, seq_len] int64

    Returns:
        [batch, hidden_size] float32
    """
    # Expand mask to [batch, seq_len, 1] and cast to float32
    mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
    sum_embeddings = np.sum(token_embeddings * mask_expanded, axis=1)
    # Clamp to avoid division by zero for all-padding sequences
    count = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
    return sum_embeddings / count


def l2_normalize_np(embeddings: np.ndarray) -> np.ndarray:
    """
    L2 normalize embeddings. Pure NumPy, no PyTorch.

    Args:
        embeddings: [batch, hidden_size] float32

    Returns:
        [batch, hidden_size] float32, unit vectors
    """
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    return embeddings / np.clip(norms, a_min=1e-9, a_max=None)
