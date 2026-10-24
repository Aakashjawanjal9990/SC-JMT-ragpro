import { useCallback, useEffect, useState } from 'react';
import { api } from '../lib/api';

/** Chat state for the Home page: messages, sidebar history, sending. */
export default function useChat() {
  const [messages, setMessages] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [busy, setBusy] = useState(false);

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
        setConversationId(res.conversationId);
        setMessages((prev) => [
          ...prev,
          { role: 'assistant', text: res.answer, sources: res.sources },
        ]);
        setConversations((prev) =>
          prev.some((c) => c.id === res.conversationId)
            ? prev
            : [
                { id: res.conversationId, title: question.slice(0, 48) },
                ...prev,
              ],
        );
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

  return { messages, conversations, conversationId, busy, send, reset, open };
}
