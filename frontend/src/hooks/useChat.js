import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';

/** Chat state for the Home page: messages, sidebar history, sending. */
export default function useChat() {
  const [messages, setMessages] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [busy, setBusy] = useState(false);

  const [conversationsStatus, setConversationsStatus] = useState('loading');
  const [conversationsError, setConversationsError] = useState('');

  const [conversationLoading, setConversationLoading] = useState(false);
  const [conversationError, setConversationError] = useState('');

  const requestGeneration = useRef(0);

  // Archived chats are fetched only when the Archived view is opened.
  const [archived, setArchived] = useState([]);
  // Status: idle, loading, ready or error.
  const [archivedStatus, setArchivedStatus] = useState('idle');
  const [archivedError, setArchivedError] = useState('');

  const loadConversations = useCallback(async () => {
    setConversationsStatus('loading');
    setConversationsError('');

    try {
      const data = await api.conversations();
      setConversations(data);
      setConversationsStatus('ready');
    } catch (err) {
      setConversationsError(err.message);
      setConversationsStatus('error');
    }
  }, []);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  const send = useCallback(
    async (question, { think = false } = {}) => {
      const generation = requestGeneration.current;

      setBusy(true);
      setConversationError('');
      setMessages((prev) => [...prev, { role: 'user', text: question }]);

      try {
        const res = await api.query(question, conversationId, think);

        if (generation !== requestGeneration.current) return;

        if (res.conversationId !== null && res.conversationId !== undefined) {
          setConversationId(res.conversationId);

          setConversations((prev) =>
            prev.some((c) => String(c.id) === String(res.conversationId))
              ? prev
              : [
                  {
                    id: res.conversationId,
                    title: question.slice(0, 48),
                  },
                  ...prev,
                ],
          );

          setConversationsStatus('ready');
          setConversationsError('');
        }

        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            text: res.answer,
            sources: res.sources,
          },
        ]);
      } catch (err) {
        if (generation !== requestGeneration.current) return;

        setMessages((prev) => [
          ...prev,
          { role: 'assistant', text: err.message, error: true },
        ]);
      } finally {
        if (generation === requestGeneration.current) {
          setBusy(false);
        }
      }
    },
    [conversationId],
  );

  const reset = useCallback(() => {
    requestGeneration.current += 1;
    setBusy(false);
    setConversationLoading(false);
    setConversationError('');
    setMessages([]);
    setConversationId(null);
  }, []);

  const open = useCallback(async (id) => {
    const generation = ++requestGeneration.current;

    setConversationId(id);
    setMessages([]);
    setBusy(false);
    setConversationLoading(true);
    setConversationError('');

    try {
      const data = await api.conversation(id);

      if (generation === requestGeneration.current) {
        setMessages(data);
      }
    } catch (err) {
      if (generation === requestGeneration.current) {
        setConversationError(err.message);
      }
    } finally {
      if (generation === requestGeneration.current) {
        setConversationLoading(false);
      }
    }
  }, []);

  const loadArchived = useCallback(async () => {
    setArchivedStatus('loading');
    setArchivedError('');

    try {
      const data = await api.conversations({ archived: true });
      setArchived(data);
      setArchivedStatus('ready');
    } catch (err) {
      setArchivedError(err.message);
      setArchivedStatus('error');
    }
  }, []);

  const archive = useCallback(async (id) => {
    await api.archiveConversation(id);

    setConversations((prev) => prev.filter((c) => String(c.id) !== String(id)));

    setArchivedStatus('idle');
  }, []);

  const restore = useCallback(async (id) => {
    await api.restoreConversation(id);

    setArchived((prev) => prev.filter((c) => String(c.id) !== String(id)));

    setConversationsStatus('loading');
    setArchivedStatus('idle');
  }, []);

  return {
    messages,
    conversations,
    conversationsStatus,
    conversationsError,
    loadConversations,
    conversationId,
    conversationLoading,
    conversationError,
    busy,
    send,
    reset,
    open,
    archived,
    archivedStatus,
    archivedError,
    loadArchived,
    archive,
    restore,
  };
}
