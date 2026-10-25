import { useState } from 'react';
import { ArchiveRestore, ArrowLeft } from 'lucide-react';

/** Archived chats list. Restoring puts a chat back in the sidebar. */
export default function ArchivedChats({
  chats,
  status,
  error,
  onRetry,
  onRestore,
  onBack,
}) {
  const [busyId, setBusyId] = useState(null);
  const [actionError, setActionError] = useState('');

  const restore = async (id) => {
    setBusyId(id);
    setActionError('');
    try {
      await onRestore(id);
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="archived">
      <div className="archived-head">
        <h1>Archived chats</h1>
        <button className="ghost" onClick={onBack}>
          <ArrowLeft size={15} />
          Back to research
        </button>
      </div>
      <p className="muted">
        Archived chats are hidden from your sidebar. Their messages are kept,
        and you can restore them at any time.
      </p>

      {status === 'loading' && <p className="muted">Loading…</p>}

      {status === 'error' && (
        <div className="archived-empty">
          <p className="field-error" role="alert">
            {error}
          </p>
          <button className="btn btn-secondary" onClick={onRetry}>
            Try again
          </button>
        </div>
      )}

      {status === 'ready' && chats.length === 0 && (
        <p className="muted">No archived chats.</p>
      )}

      {status === 'ready' && chats.length > 0 && (
        <ul className="archived-list">
          {chats.map((c) => (
            <li key={c.id} className="card archived-item">
              <span className="trunc">{c.title || 'Untitled research'}</span>
              <button
                className="btn btn-secondary"
                disabled={busyId === c.id}
                onClick={() => restore(c.id)}
              >
                <ArchiveRestore size={15} />
                {busyId === c.id ? 'Restoring…' : 'Unarchive'}
              </button>
            </li>
          ))}
        </ul>
      )}

      {actionError && (
        <p className="field-error" role="alert">
          {actionError}
        </p>
      )}
    </section>
  );
}
