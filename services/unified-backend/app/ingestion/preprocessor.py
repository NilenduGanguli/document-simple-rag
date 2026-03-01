import re
import unicodedata
import logging

logger = logging.getLogger(__name__)


class TextPreprocessor:
    """
    Text cleaning and normalization pipeline.
    CPU-only, no ML dependencies.
    """

    def clean(self, text: str) -> str:
        if not text:
            return ""

        # Unicode normalization
        text = unicodedata.normalize('NFKC', text)

        # Remove null bytes and control characters (except newlines/tabs)
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

        # Normalize whitespace: multiple spaces to single
        text = re.sub(r'[ \t]+', ' ', text)

        # Normalize line endings
        text = re.sub(r'\r\n|\r', '\n', text)

        # Collapse excessive blank lines (more than 2 consecutive)
        text = re.sub(r'\n{3,}', '\n\n', text)

        # Strip leading/trailing whitespace per line
        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(lines)

        # Final strip
        text = text.strip()

        return text
