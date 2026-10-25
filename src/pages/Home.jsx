import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Brain } from 'lucide-react';
import AppShell from '../components/AppShell';
import ArchivedChats from '../components/ArchivedChats';
import { useAuth } from '../context/AuthContext';
import useChat from '../hooks/useChat';
import { getDayWish, getGreeting, istDateKey } from '../lib/greeting';
import { displayName, getFirstName } from '../lib/names';
import { validateQuery } from '../lib/validators';
import { useSearchParams } from 'react-router-dom';

const MAX_HEIGHT = 140;
const WISH_KEY = 'nm_wish_day';

export default function Home() {
  const { user } = useAuth();
  const chat = useChat();
  const [params, setParams] = useSearchParams();
  const [initialId] = useState(() => params.get('c'));
  const openConversation = chat.open;
  const { conversationId } = chat;

  // Opening /home?c=<id> loads that conversation once.
  useEffect(() => {
    if (initialId) openConversation(initialId);
  }, [initialId, openConversation]);

  // Keep the address bar pointing at the open conversation.
  useEffect(() => {
    if (conversationId === null || conversationId === undefined) return;
    setParams({ c: String(conversationId) }, { replace: true });
  }, [conversationId, setParams]);

  const [showArchived, setShowArchived] = useState(false);
  const [question, setQuestion] = useState('');
  const [questionError, setQuestionError] = useState('');
  const [think, setThink] = useState(false);
  const endRef = useRef(null);
  const inputRef = useRef(null);

  // Greeting is fixed when the page opens (IST). The day wish ("Happy
  // Thursday!") appears only on the first visit of each day.
  const [greeting] = useState(() => {
    const today = istDateKey();
    let first = true;
    try {
      first = localStorage.getItem(WISH_KEY) !== today;
    } catch {
      first = true;
    }
    return {
      first,
      today,
      name: getFirstName(displayName(user)),
      hello: getGreeting(),
      wish: getDayWish(),
    };
  });
  const [initialDailyState, setInitialDailyState] = useState(
    () => greeting.first,
  );

  useEffect(() => {
    try {
      localStorage.setItem(WISH_KEY, greeting.today);
    } catch {
      /* storage unavailable: the wish just shows again next time */
    }
  }, [greeting.today]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chat.messages]);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return; // the composer is hidden while Archived chats is open
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [question]);

  const submit = (e) => {
    e.preventDefault();
    const text = question.trim();
    if (!text || chat.busy) return;
    const validationError = validateQuery(text);
    if (validationError) {
      setQuestionError(validationError);
      return;
    }
    setQuestionError('');
    setInitialDailyState(false);
    setQuestion('');
    chat.send(text, { think });
  };

  const startNewResearch = () => {
    setShowArchived(false);
    setInitialDailyState(false);
    setParams({}, { replace: true });
    chat.reset();
  };

  const archiveConversation = async (id) => {
    const wasOpen = String(id) === String(chat.conversationId);
    await chat.archive(id);
    if (wasOpen) startNewResearch();
  };

  const openArchivedView = () => {
    setShowArchived(true);
    chat.loadArchived();
  };

  const openConversationFromSidebar = (id) => {
    setShowArchived(false);
    chat.open(id);
  };

  return (
    <AppShell
      className="home-shell"
      crumb={
        showArchived
          ? 'Archived chats'
          : chat.messages.length
            ? 'Conversation'
            : 'Welcome'
      }
      conversations={chat.conversations}
      activeId={chat.conversationId}
      onNew={startNewResearch}
      onSelect={openConversationFromSidebar}
      onArchive={archiveConversation}
      onShowArchived={openArchivedView}
      showingArchived={showArchived}
    >
      {showArchived ? (
        <ArchivedChats
          chats={chat.archived}
          status={chat.archivedStatus}
          error={chat.archivedError}
          onRetry={chat.loadArchived}
          onRestore={chat.restore}
          onBack={() => setShowArchived(false)}
        />
      ) : (
        <div
          className={`chat-area ${chat.messages.length ? '' : 'empty'} ${initialDailyState && !chat.messages.length ? 'initial-daily' : ''}`}
        >
          <div className="chat" aria-live="polite">
            {!chat.messages.length && (
              <section className="hero">
                {greeting.first && <p className="wish">{greeting.wish}</p>}
                <h1>
                  <b>{greeting.hello},</b> <span>{greeting.name}.</span>
                </h1>
                <p className="muted">
                  A considered space for your Supreme Court research.
                </p>
              </section>
            )}

            {chat.messages.map((m, i) => (
              <div key={i} className={`msg ${m.role} ${m.error ? 'err' : ''}`}>
                <p>{m.text}</p>
                {m.sources?.map((s, j) => (
                  <span key={`${s.case}-${j}`} className="src">
                    {s.case}
                    {s.year ? ` (${s.year})` : ''}, p. {s.page}
                  </span>
                ))}
              </div>
            ))}
            {chat.busy && (
              <div className="msg assistant muted">
                {think ? 'Thinking it through…' : 'Reading judgments…'}
              </div>
            )}
            <div ref={endRef} />
          </div>

          <form className="card composer" onSubmit={submit}>
            <textarea
              ref={inputRef}
              rows={1}
              value={question}
              onChange={(e) => {
                setQuestion(e.target.value);
                setQuestionError('');
              }}
              placeholder="Ask about a ruling, citation, or doctrine"
              aria-label="Your research question"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) submit(e);
              }}
            />
            <button
              type="button"
              className={`btn btn-secondary think ${think ? 'on' : ''}`}
              aria-pressed={think}
              title="Think: take longer for a deeper answer"
              onClick={() => setThink(!think)}
            >
              <Brain size={15} />
              Think
            </button>
            <button
              type="submit"
              className="askbtn"
              aria-label="Ask"
              disabled={chat.busy || !question.trim()}
            >
              <ArrowRight size={17} />
              <span className="lbl">Ask</span>
            </button>
          </form>
          {questionError && (
            <p className="field-error" role="alert">
              {questionError}
            </p>
          )}
        </div>
      )}
    </AppShell>
  );
}
