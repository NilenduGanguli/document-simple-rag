import type { DocumentSummary } from '../../types';
import StatusBadge from '../common/StatusBadge';

interface Props {
  document: DocumentSummary;
  selected: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

export default function DocumentRow({ document: doc, selected, onSelect, onDelete }: Props) {
  const sizeStr = doc.file_size_bytes
    ? `${(doc.file_size_bytes / (1024 * 1024)).toFixed(1)} MB`
    : '-';

  const dateStr = doc.created_at
    ? new Date(doc.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : '-';

  return (
    <tr
      onClick={() => onSelect(doc.document_id)}
      className={`cursor-pointer border-b border-gray-100 text-sm transition-colors ${
        selected ? 'bg-blue-50' : 'hover:bg-gray-50'
      }`}
    >
      <td className="px-4 py-3 font-medium text-gray-900 max-w-[200px] truncate" title={doc.filename}>
        {doc.filename}
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={doc.status} />
      </td>
      <td className="px-4 py-3 text-gray-500">{doc.page_count ?? '-'}</td>
      <td className="px-4 py-3 text-gray-500">{doc.chunk_count}</td>
      <td className="px-4 py-3 text-gray-500">{sizeStr}</td>
      <td className="px-4 py-3 text-gray-500">{dateStr}</td>
      <td className="px-4 py-3">
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(doc.document_id); }}
          className="text-gray-400 hover:text-red-600"
          title="Delete document"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
        </button>
      </td>
    </tr>
  );
}
