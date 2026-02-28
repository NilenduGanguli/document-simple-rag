import { useState } from 'react';
import type { RetrievalRequest, RetrievalConfig } from '../../types';
import LoadingSpinner from '../common/LoadingSpinner';

interface Props {
  onSubmit: (request: RetrievalRequest) => void;
  isLoading: boolean;
}

// Must match MAX_QUERY_TOKENS on the backend. Rough browser estimate: 1 token ≈ 4 characters.
const MAX_TOKENS = 100;
const CHARS_PER_TOKEN = 4;
const MAX_QUERY_CHARS = MAX_TOKENS * CHARS_PER_TOKEN; // 400

export default function QueryForm({ onSubmit, isLoading }: Props) {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<'k_chunks' | 'n_documents'>('k_chunks');
  const [k, setK] = useState(10);
  const [n, setN] = useState(5);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [config, setConfig] = useState<RetrievalConfig>({
    dense_candidates: 100,
    sparse_candidates: 100,
    mmr_lambda: 0.7,
    enable_reranking: true,
    enable_ner: false,
    k_rrf_dense: 60,
    k_rrf_sparse: 60,
  });

  const estTokens = Math.ceil(query.length / CHARS_PER_TOKEN);
  const overLimit = query.length > MAX_QUERY_CHARS;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || overLimit) return;
    onSubmit({
      query: query.trim(),
      mode,
      k: mode === 'k_chunks' ? k : undefined,
      n: mode === 'n_documents' ? n : undefined,
      config,
    });
  };

  return (
    <form onSubmit={handleSubmit} className="rounded-lg border border-gray-200 bg-white p-6">
      <div className="flex gap-3">
        <div className="flex-1">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Enter your search query… (max ~100 tokens)"
            className={`w-full rounded-md border px-4 py-2 text-sm focus:outline-none focus:ring-1 ${
              overLimit
                ? 'border-red-400 focus:border-red-500 focus:ring-red-500'
                : 'border-gray-300 focus:border-blue-500 focus:ring-blue-500'
            }`}
          />
          <div className="mt-1 flex items-center justify-between">
            <span className={`text-xs ${overLimit ? 'text-red-600 font-medium' : 'text-gray-400'}`}>
              ~{estTokens} / {MAX_TOKENS} tokens
              {overLimit && ' — query too long, please shorten it'}
            </span>
            <span className="text-xs text-gray-400">
              {query.length} / {MAX_QUERY_CHARS} chars
            </span>
          </div>
        </div>
        <button
          type="submit"
          disabled={isLoading || !query.trim() || overLimit}
          className="flex items-center gap-2 self-start rounded-md bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {isLoading && <LoadingSpinner size="sm" />}
          Search
        </button>
      </div>

      <div className="mt-4 flex items-center gap-4">
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-gray-600">Mode:</label>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as 'k_chunks' | 'n_documents')}
            className="rounded border border-gray-300 px-2 py-1 text-xs focus:border-blue-500 focus:outline-none"
          >
            <option value="k_chunks">Top-K Chunks</option>
            <option value="n_documents">Top-N Documents</option>
          </select>
        </div>

        {mode === 'k_chunks' ? (
          <div className="flex items-center gap-2">
            <label className="text-xs font-medium text-gray-600">K:</label>
            <input
              type="number"
              value={k}
              onChange={(e) => setK(Number(e.target.value))}
              min={1}
              max={100}
              className="w-16 rounded border border-gray-300 px-2 py-1 text-xs focus:border-blue-500 focus:outline-none"
            />
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <label className="text-xs font-medium text-gray-600">N:</label>
            <input
              type="number"
              value={n}
              onChange={(e) => setN(Number(e.target.value))}
              min={1}
              max={50}
              className="w-16 rounded border border-gray-300 px-2 py-1 text-xs focus:border-blue-500 focus:outline-none"
            />
          </div>
        )}

        <button
          type="button"
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="ml-auto text-xs text-gray-500 hover:text-gray-700"
        >
          {showAdvanced ? 'Hide' : 'Show'} advanced
        </button>
      </div>

      {showAdvanced && (
        <div className="mt-4 grid grid-cols-3 gap-4 rounded-md bg-gray-50 p-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">Dense candidates</label>
            <input
              type="number"
              value={config.dense_candidates}
              onChange={(e) => setConfig({ ...config, dense_candidates: Number(e.target.value) })}
              min={10}
              max={500}
              className="w-full rounded border border-gray-300 px-2 py-1 text-xs"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">Sparse candidates</label>
            <input
              type="number"
              value={config.sparse_candidates}
              onChange={(e) => setConfig({ ...config, sparse_candidates: Number(e.target.value) })}
              min={10}
              max={500}
              className="w-full rounded border border-gray-300 px-2 py-1 text-xs"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">MMR Lambda</label>
            <input
              type="number"
              value={config.mmr_lambda}
              onChange={(e) => setConfig({ ...config, mmr_lambda: Number(e.target.value) })}
              min={0}
              max={1}
              step={0.1}
              className="w-full rounded border border-gray-300 px-2 py-1 text-xs"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">
              RRF k — dense
              <span className="ml-1 font-normal text-gray-400">(↓ = more semantic weight)</span>
            </label>
            <input
              type="number"
              value={config.k_rrf_dense}
              onChange={(e) => setConfig({ ...config, k_rrf_dense: Number(e.target.value) })}
              min={1}
              max={1000}
              className="w-full rounded border border-gray-300 px-2 py-1 text-xs"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">
              RRF k — sparse
              <span className="ml-1 font-normal text-gray-400">(↓ = more keyword weight)</span>
            </label>
            <input
              type="number"
              value={config.k_rrf_sparse}
              onChange={(e) => setConfig({ ...config, k_rrf_sparse: Number(e.target.value) })}
              min={1}
              max={1000}
              className="w-full rounded border border-gray-300 px-2 py-1 text-xs"
            />
          </div>
          <div className="flex flex-col gap-3 pt-1">
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={config.enable_reranking}
                onChange={(e) => setConfig({ ...config, enable_reranking: e.target.checked })}
                className="rounded border-gray-300"
                id="reranking"
              />
              <label htmlFor="reranking" className="text-xs text-gray-600">Reranking</label>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={config.enable_ner}
                onChange={(e) => setConfig({ ...config, enable_ner: e.target.checked })}
                className="rounded border-gray-300"
                id="ner"
              />
              <label htmlFor="ner" className="text-xs text-gray-600">NER preprocessing</label>
            </div>
          </div>
        </div>
      )}
    </form>
  );
}
