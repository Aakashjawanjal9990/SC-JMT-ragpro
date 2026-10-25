import { useCallback, useEffect, useState } from 'react';
import { api } from '../lib/api';

/** Chat state for the Home page: messages, sidebar history, sending. */
export default function useChat() {
  const [messages, setMessages] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [busy, setBusy] = useState(false);
  // Archived chats are fetched only when the Archived view is opened.
  const [archived, setArchived] = useState([]);
  // Status: idle, loading, ready or error.
  const [archivedStatus, setArchivedStatus] = useState('idle');
  const [archivedError, setArchivedError] = useState('');

  useEffect(() => {
    api
      .conversations()
      .then(setConversations)
      .catch(() => setConversations([]));
  }, []);

  const send = useCallback(
    async (question, { think = false } = {}) => {
      setBusy(true);
      setMessages((prev) => [...prev, { role: 'user', text: question }]);
      try {
        const res = await api.query(question, conversationId, think);
        if (res.conversationId !== null) {
          setConversationId(res.conversationId);
          setConversations((prev) =>
            prev.some((c) => c.id === res.conversationId)
              ? prev
              : [
                  { id: res.conversationId, title: question.slice(0, 48) },
                  ...prev,
                ],
          );
        }
        setMessages((prev) => [
          ...prev,
          { role: 'assistant', text: res.answer, sources: res.sources },
        ]);
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          { role: 'assistant', text: err.message, error: true },
        ]);
      } finally {
        setBusy(false);
      }
    },
    [conversationId],
  );

  const reset = useCallback(() => {
    setMessages([]);
    setConversationId(null);
  }, []);

  const open = useCallback(async (id) => {
    setConversationId(id);
    try {
      setMessages(await api.conversation(id));
    } catch (err) {
      setMessages([{ role: 'assistant', text: err.message, error: true }]);
    }
  }, []);

  // Hides the chat from the sidebar. The backend keeps its messages.
  const archive = useCallback(
    async (id) => {
      await api.archiveConversation(id);
      const chat = conversations.find((c) => String(c.id) === String(id));
      setConversations((prev) =>
        prev.filter((c) => String(c.id) !== String(id)),
      );
      if (chat) {
        setArchived((prev) =>
          prev.some((c) => String(c.id) === String(id))
            ? prev
            : [chat, ...prev],
        );
      }
    },
    [conversations],
  );

  const loadArchived = useCallback(async () => {
    setArchivedStatus('loading');
    setArchivedError('');
    try {
      setArchived(await api.archivedConversations());
      setArchivedStatus('ready');
    } catch (err) {
      setArchivedError(err.message);
      setArchivedStatus('error');
    }
  }, []);

  // Puts an archived chat back in the sidebar.
  const restore = useCallback(
    async (id) => {
      await api.restoreConversation(id);
      const chat = archived.find((c) => String(c.id) === String(id));
      setArchived((prev) => prev.filter((c) => String(c.id) !== String(id)));
      if (chat) {
        setConversations((prev) =>
          prev.some((c) => String(c.id) === String(id))
            ? prev
            : [chat, ...prev],
        );
      }
    },
    [archived],
  );

  return {
    messages,
    conversations,
    conversationId,
    busy,
    send,
    reset,
    open,
    archive,
    archived,
    archivedStatus,
    archivedError,
    loadArchived,
    restore,
  };
}
