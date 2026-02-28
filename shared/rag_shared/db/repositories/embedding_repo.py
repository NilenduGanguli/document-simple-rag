"""Embedding repository backed by ChromaDB."""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class EmbeddingRepository:
    """Store and query vector embeddings in a ChromaDB collection."""

    def __init__(self, collection) -> None:
        self.collection = collection

    async def bulk_upsert(
        self,
        chunk_ids: list[str],
        parent_doc_ids: list[str],
        embeddings: list[list[float]],
        model_name: str,
        model_version: str,
    ) -> None:
        """Upsert embeddings into the ChromaDB collection."""
        if not chunk_ids:
            return

        if len(chunk_ids) != len(parent_doc_ids) or len(chunk_ids) != len(embeddings):
            raise ValueError(
                "chunk_ids, parent_doc_ids and embeddings must have the same length"
            )

        metadatas = [
            {
                "parent_document_id": pid,
                "model_name": model_name,
                "model_version": model_version,
            }
            for pid in parent_doc_ids
        ]

        await self.collection.upsert(
            ids=chunk_ids,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        logger.debug(
            f"Upserted {len(chunk_ids)} embeddings to ChromaDB "
            f"(model={model_name}, version={model_version})"
        )

    async def get_for_document(self, document_id: str) -> list[dict]:
        """Return all embeddings for a given parent document."""
        results = await self.collection.get(
            where={"parent_document_id": document_id},
            include=["embeddings", "metadatas"],
        )

        out: list[dict] = []
        if results and results.get("ids"):
            for i, chunk_id in enumerate(results["ids"]):
                out.append({
                    "chunk_id": chunk_id,
                    "parent_document_id": document_id,
                    "embedding": results["embeddings"][i] if results.get("embeddings") else None,
                    "model_name": results["metadatas"][i].get("model_name") if results.get("metadatas") else None,
                    "model_version": results["metadatas"][i].get("model_version") if results.get("metadatas") else None,
                })
        return out

    async def delete_by_document(self, document_id: str) -> None:
        """Delete all embeddings for a document from ChromaDB."""
        try:
            await self.collection.delete(
                where={"parent_document_id": document_id},
            )
            logger.debug(f"Deleted embeddings for document {document_id} from ChromaDB")
        except Exception as exc:
            logger.warning(f"ChromaDB delete for document {document_id} failed: {exc}")

    async def count(self) -> int:
        """Return total number of embeddings in the collection."""
        return await self.collection.count()
