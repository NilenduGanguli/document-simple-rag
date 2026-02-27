import type { DocumentSummary } from '../../types';
import DocumentRow from './DocumentRow';
import LoadingSpinner from '../common/LoadingSpinner';

interface Props {
  documents: DocumentSummary[];
  isLoading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

export default function DocumentList({ documents, isLoading, selectedId, onSelect, onDelete }: Props) {
  if (isLoading && documents.length === 0) {
    return (
      <div className="flex items-center justify-center py-12">
        <LoadingSpinner />
      </div>
    );
  }

  if (documents.length === 0) {
    return (
      <div className="py-12 text-center text-sm text-gray-400">
        No documents yet. Upload a PDF to get started.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
      <table className="w-full text-left">
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50 text-xs font-medium uppercase tracking-wider text-gray-500">
            <th className="px-4 py-3">Filename</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Pages</th>
            <th className="px-4 py-3">Chunks</th>
            <th className="px-4 py-3">Size</th>
            <th className="px-4 py-3">Created</th>
            <th className="px-4 py-3 w-10"></th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => (
            <DocumentRow
              key={doc.document_id}
              document={doc}
              selected={doc.document_id === selectedId}
              onSelect={onSelect}
              onDelete={onDelete}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
