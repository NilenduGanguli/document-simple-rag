import React, { useState } from 'react';
import type { ChunkResult } from '../../types';

interface Props {
  chunk: ChunkResult;
  query: string;
  /** Parent document filename — shown in k_chunks mode where docs are not grouped */
  filename?: string;
  /** Navigate to Explorer and focus this chunk */
  onViewDocument: (docId: string, chunkId: string) => void;
  /** Open the PDF preview for the parent document */
  onViewPDF?: (docId: string) => void;
}

// Mirrors the backend _ENGLISH_STOPWORDS set. Standalone stopword matches are
// not highlighted; stopwords within a matched phrase are highlighted together
// with the surrounding content words.
const HIGHLIGHT_STOPWORDS = new Set([
  'a', 'about', 'above', 'after', 'again', 'against', 'all', 'am', 'an',
  'and', 'any', 'are', "aren't", 'as', 'at', 'be', 'because', 'been',
  'before', 'being', 'below', 'between', 'both', 'but', 'by', "can't",
  'cannot', 'could', "couldn't", 'did', "didn't", 'do', 'does', "doesn't",
  'doing', "don't", 'down', 'during', 'each', 'few', 'for', 'from',
  'further', 'get', 'got', 'had', "hadn't", 'has', "hasn't", 'have',
  "haven't", 'having', 'he', "he'd", "he'll", "he's", 'her', 'here',
  "here's", 'hers', 'herself', 'him', 'himself', 'his', 'how', "how's",
  'i', "i'd", "i'll", "i'm", "i've", 'if', 'in', 'into', 'is', "isn't",
  'it', "it's", 'its', 'itself', "let's", 'me', 'more', 'most', "mustn't",
  'my', 'myself', 'no', 'nor', 'not', 'of', 'off', 'on', 'once', 'only',
  'or', 'other', 'ought', 'our', 'ours', 'ourselves', 'out', 'over', 'own',
  'same', "shan't", 'she', "she'd", "she'll", "she's", 'should',
  "shouldn't", 'so', 'some', 'such', 'than', 'that', "that's", 'the',
  'their', 'theirs', 'them', 'themselves', 'then', 'there', "there's",
  'these', 'they', "they'd", "they'll", "they're", "they've", 'this',
  'those', 'through', 'to', 'too', 'under', 'until', 'up', 'very', 'was',
  "wasn't", 'we', "we'd", "we'll", "we're", "we've", 'were', "weren't",
  'what', "what's", 'when', "when's", 'where', "where's", 'which', 'while',
  'who', "who's", 'whom', 'why', "why's", 'with', "won't", 'would',
  "wouldn't", 'you', "you'd", "you'll", "you're", "you've", 'your',
  'yours', 'yourself', 'yourselves',
]);

export default function ChunkCard({ chunk, query, filename, onViewDocument, onViewPDF }: Props) {
  const [expanded, setExpanded] = useState(false);

  // Phrase-aware highlighting:
  // 1. Multi-word phrase matches from the query highlight the whole phrase
  //    (stopwords within the phrase are highlighted together with content words).
  // 2. Individual non-stopword content words are highlighted on their own.
  // 3. Standalone stopwords are never highlighted.
  const highlightText = (text: string) => {
    if (!query.trim()) return text;

    const queryWords = query.trim().toLowerCase().split(/\s+/);
    const lowerText = text.toLowerCase();
    const ranges: { start: number; end: number }[] = [];

    // Step 1: multi-word phrase matches (try longest phrases first)
    for (let len = queryWords.length; len >= 2; len--) {
      for (let i = 0; i <= queryWords.length - len; i++) {
        const phrase = queryWords.slice(i, i + len).join(' ');
        let pos = 0;
        while ((pos = lowerText.indexOf(phrase, pos)) !== -1) {
          const end = pos + phrase.length;
          if (!ranges.some(r => r.start < end && r.end > pos)) {
            ranges.push({ start: pos, end });
          }
          pos++;
        }
      }
    }

    // Step 2: single content words (non-stopword, length > 2) not already covered
    const contentWords = queryWords.filter(w => w.length > 2 && !HIGHLIGHT_STOPWORDS.has(w));
    if (contentWords.length > 0) {
      const escaped = contentWords.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
      const wordRegex = new RegExp(`(${escaped.join('|')})`, 'gi');
      let match: RegExpExecArray | null;
      while ((match = wordRegex.exec(text)) !== null) {
        const start = match.index;
        const end = start + match[0].length;
        if (!ranges.some(r => r.start < end && r.end > start)) {
          ranges.push({ start, end });
        }
      }
    }

    if (ranges.length === 0) return text;

    // Step 3: sort and merge overlapping ranges
    ranges.sort((a, b) => a.start - b.start);
    const merged: { start: number; end: number }[] = [];
    for (const r of ranges) {
      const last = merged[merged.length - 1];
      if (last && r.start <= last.end) {
        last.end = Math.max(last.end, r.end);
      } else {
        merged.push({ ...r });
      }
    }

    // Step 4: render segments
    const parts: React.ReactNode[] = [];
    let cursor = 0;
    merged.forEach(({ start, end }, i) => {
      if (cursor < start) parts.push(<span key={`t${i}`}>{text.slice(cursor, start)}</span>);
      parts.push(
        <mark key={`m${i}`} className="bg-yellow-200 text-yellow-900 rounded px-0.5">
          {text.slice(start, end)}
        </mark>
      );
      cursor = end;
    });
    if (cursor < text.length) parts.push(<span key="tail">{text.slice(cursor)}</span>);
    return parts;
  };

  const displayText = expanded ? chunk.chunk_text : chunk.chunk_text.slice(0, 300);

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 transition-shadow hover:shadow-md">
      {/* Parent document name (k_chunks mode) */}
      {filename && (
        <div className="mb-2 flex items-center gap-1.5 truncate text-xs">
          <svg className="h-3.5 w-3.5 flex-shrink-0 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <span className="truncate font-medium text-gray-600" title={filename}>{filename}</span>
        </div>
      )}

      {/* Header with scores */}
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-gray-600">
          #{chunk.chunk_index}
        </span>
        {chunk.page_number != null && (
          <span className="text-gray-400">pg. {chunk.page_number}</span>
        )}
        <span className="text-gray-400">|</span>
        <span className="text-gray-500" title="Cosine similarity">
          cos: <span className="font-medium text-blue-600">{chunk.cosine_score.toFixed(3)}</span>
        </span>
        <span className="text-gray-500" title="BM25 score">
          bm25: <span className="font-medium text-indigo-600">{chunk.bm25_score.toFixed(2)}</span>
        </span>
        <span className="text-gray-500" title="RRF fused score">
          rrf: <span className="font-medium text-purple-600">{chunk.rrf_score.toFixed(4)}</span>
        </span>
        {chunk.rerank_score != null && (
          <span className="text-gray-500" title="Cross-encoder rerank score">
            rerank: <span className="font-medium text-emerald-600">{chunk.rerank_score.toFixed(4)}</span>
          </span>
        )}
        <span className={`rounded px-1.5 py-0.5 text-[10px] ${
          chunk.source_type === 'ocr' ? 'bg-orange-100 text-orange-700' : 'bg-gray-100 text-gray-600'
        }`}>
          {chunk.source_type}
        </span>
      </div>

      {/* Chunk text */}
      <div className="text-sm leading-relaxed text-gray-800">
        {highlightText(displayText)}
        {!expanded && chunk.chunk_text.length > 300 && (
          <span className="text-gray-400">...</span>
        )}
      </div>

      {/* Actions */}
      <div className="mt-3 flex items-center gap-3">
        {chunk.chunk_text.length > 300 && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-blue-600 hover:text-blue-700"
          >
            {expanded ? 'Show less' : 'Show more'}
          </button>
        )}
        {onViewPDF && (
          <button
            onClick={() => onViewPDF(chunk.parent_document_id)}
            className="text-xs text-gray-500 hover:text-gray-700"
          >
            View parent document
          </button>
        )}
        <button
          onClick={() => onViewDocument(chunk.parent_document_id, chunk.chunk_id)}
          className="text-xs text-gray-500 hover:text-gray-700"
        >
          View in Explorer ↗
        </button>
        <span className="ml-auto text-[10px] font-mono text-gray-300" title="Chunk ID">
          {chunk.chunk_id.slice(0, 8)}...
        </span>
      </div>
    </div>
  );
}
