import { useState } from 'react';
import type { DocumentSummary, ReprocessParams } from '../../types';

const OCR_LANGUAGE_OPTIONS = [
  { code: 'eng', label: 'English' },
  { code: 'fra', label: 'French' },
  { code: 'deu', label: 'German' },
  { code: 'spa', label: 'Spanish' },
  { code: 'chi_sim', label: 'Chinese (Simplified)' },
];

interface Props {
  document: DocumentSummary;
  onConfirm: (params: ReprocessParams) => Promise<void>;
  onCancel: () => void;
}

export default function ReprocessModal({ document, onConfirm, onCancel }: Props) {
  const [chunkMaxTokens, setChunkMaxTokens] = useState(400);
  const [chunkOverlapTokens, setChunkOverlapTokens] = useState(50);
  const [chunkingStrategy, setChunkingStrategy] = useState('recursive');
  const [forceOcr, setForceOcr] = useState(false);
  const [ocrLanguages, setOcrLanguages] = useState<string[]>(['eng']);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggleOcrLanguage(code: string) {
    setOcrLanguages((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code],
    );
  }

  async function handleConfirm() {
    if (ocrLanguages.length === 0) {
      setError('Select at least one OCR language.');
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await onConfirm({
        chunk_max_tokens: chunkMaxTokens,
        chunk_overlap_tokens: chunkOverlapTokens,
        chunking_strategy: chunkingStrategy,
        force_ocr: forceOcr,
        ocr_languages: ocrLanguages,
      });
    } catch (e: any) {
      setError(e.message || 'Reprocess request failed.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
        <h2 className="mb-1 text-lg font-semibold text-gray-900">Reprocess Document</h2>
        <p className="mb-4 text-sm text-gray-500 truncate">{document.filename}</p>

        <div className="mb-4 rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">
          This will <strong>delete all existing chunks</strong> and re-ingest the original PDF
          from S3 using the parameters below.
        </div>

        <div className="space-y-4">
          {/* Chunk Max Tokens */}
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Chunk Max Tokens
              <span className="ml-1 font-normal text-gray-400">(50–1000)</span>
            </label>
            <input
              type="number"
              min={50}
              max={1000}
              value={chunkMaxTokens}
              onChange={(e) => setChunkMaxTokens(Number(e.target.value))}
              className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Chunk Overlap Tokens */}
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Chunk Overlap Tokens
              <span className="ml-1 font-normal text-gray-400">(0–200)</span>
            </label>
            <input
              type="number"
              min={0}
              max={200}
              value={chunkOverlapTokens}
              onChange={(e) => setChunkOverlapTokens(Number(e.target.value))}
              className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Chunking Strategy */}
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Chunking Strategy
            </label>
            <select
              value={chunkingStrategy}
              onChange={(e) => setChunkingStrategy(e.target.value)}
              className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="recursive">Recursive Character Split</option>
            </select>
          </div>

          {/* Force OCR */}
          <div className="flex items-center gap-3">
            <input
              id="force-ocr"
              type="checkbox"
              checked={forceOcr}
              onChange={(e) => setForceOcr(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-blue-600"
            />
            <label htmlFor="force-ocr" className="text-sm text-gray-700">
              Force OCR on all pages
              <span className="ml-1 text-xs text-gray-400">
                (skip text density check, rasterize everything)
              </span>
            </label>
          </div>

          {/* OCR Languages */}
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-2">
              OCR Languages
            </label>
            <div className="flex flex-wrap gap-2">
              {OCR_LANGUAGE_OPTIONS.map(({ code, label }) => (
                <button
                  key={code}
                  type="button"
                  onClick={() => toggleOcrLanguage(code)}
                  className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                    ocrLanguages.includes(code)
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {error && (
          <p className="mt-3 text-xs text-red-600">{error}</p>
        )}

        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onCancel}
            disabled={submitting}
            className="rounded-md px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            disabled={submitting}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {submitting ? 'Reprocessing…' : 'Reprocess'}
          </button>
        </div>
      </div>
    </div>
  );
}
