import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Brain } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import AppShell from '../components/AppShell';
import ArchivedChats from '../components/ArchivedChats';
import { useAuth } from '../context/AuthContext';
import useChat from '../hooks/useChat';
import { getDayWish, getGreeting, istDateKey } from '../lib/greeting';
import { displayName, getFirstName } from '../lib/names';
import { validateQuery } from '../lib/validators';

const MAX_HEIGHT = 140;
const WISH_KEY = 'nm_wish_day';

function safeLinkUrl(value) {
  const href = value.trim();
  if (
    !href ||
    [...href].some(
      (character) => character.charCodeAt(0) <= 0x20 || character === '\\',
    )
  ) {
    return null;
  }

  try {
    const url = new URL(href, window.location.origin);
    return ['http:', 'https:', 'mailto:'].includes(url.protocol) ? href : null;
  } catch {
    return null;
  }
}

function renderInlineMarkdown(value, keyPrefix = 'inline') {
  const pattern =
    /(`[^`\n]+`|\[[^\]\n]+\]\([^\s)]+(?:\s+"[^"]*")?\)|\*\*(?:[^*\n]|\*(?!\*)[^*\n]*\*)+\*\*|__(?:[^_\n]|_(?!_)[^_\n]*_)+__|\*(?:[^*\n]|\*\*[^*\n]+\*\*)+\*|_(?:[^_\n]|__[^_\n]+__)+_)/gu;
  const result = [];
  let cursor = 0;
  let match;

  while ((match = pattern.exec(value))) {
    if (match.index > cursor) result.push(value.slice(cursor, match.index));

    const token = match[0];
    const key = `${keyPrefix}-${match.index}`;
    if (token.startsWith('`')) {
      result.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith('[')) {
      const close = token.indexOf('](');
      const label = token.slice(1, close);
      const rawHref = token
        .slice(close + 2)
        .replace(/\s+"[^"]*"\)$/u, ')')
        .slice(0, -1);
      const href = safeLinkUrl(rawHref);
      const content = renderInlineMarkdown(label, key);
      result.push(
        href ? (
          <a key={key} href={href} rel="noreferrer" target="_blank">
            {content}
          </a>
        ) : (
          <span key={key}>{content}</span>
        ),
      );
    } else if (token.startsWith('**') || token.startsWith('__')) {
      result.push(
        <strong key={key}>
          {renderInlineMarkdown(token.slice(2, -2), key)}
        </strong>,
      );
    } else if (token.startsWith('*') || token.startsWith('_')) {
      result.push(
        <em key={key}>{renderInlineMarkdown(token.slice(1, -1), key)}</em>,
      );
    }

    cursor = pattern.lastIndex;
  }

  if (cursor < value.length) result.push(value.slice(cursor));
  return result;
}

function splitTableRow(line) {
  const cells = [];
  let cell = '';
  let escaped = false;

  for (const character of line.trim().replace(/^\||\|$/gu, '')) {
    if (character === '|' && !escaped) {
      cells.push(cell.trim().replace(/\\\|/gu, '|'));
      cell = '';
    } else {
      cell += character;
    }

    escaped = character === '\\' && !escaped;
    if (character !== '\\') escaped = false;
  }
  cells.push(cell.trim().replace(/\\\|/gu, '|'));
  return cells;
}

function isListLine(line) {
  return /^\s{0,3}(?:[-+*]|\d+[.)])\s+\S/u.test(line);
}

function isBlockStart(lines, index) {
  const line = lines[index];
  return (
    /^\s*```/u.test(line) ||
    /^ {0,3}#{1,6}\s+/u.test(line) ||
    isListLine(line) ||
    /^\s*>/u.test(line) ||
    (index + 1 < lines.length &&
      line.includes('|') &&
      /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/u.test(
        lines[index + 1],
      ))
  );
}

function renderMarkdownBlocks(lines, keyPrefix = 'block') {
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    if (!lines[index].trim()) {
      index += 1;
      continue;
    }

    const fence = lines[index].match(/^\s*```([\w-]*)\s*$/u);
    if (fence) {
      const code = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/u.test(lines[index])) {
        code.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(
        <pre key={`${keyPrefix}-code-${index}`}>
          <code className={fence[1] ? `language-${fence[1]}` : undefined}>
            {code.join('\n')}
          </code>
        </pre>,
      );
      continue;
    }

    const heading = lines[index].match(/^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/u);
    if (heading) {
      const level = heading[1].length;
      const Heading = `h${level}`;
      blocks.push(
        <Heading key={`${keyPrefix}-heading-${index}`}>
          {renderInlineMarkdown(heading[2], `${keyPrefix}-heading-${index}`)}
        </Heading>,
      );
      index += 1;
      continue;
    }

    if (
      index + 1 < lines.length &&
      lines[index].includes('|') &&
      /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/u.test(
        lines[index + 1],
      )
    ) {
      const headers = splitTableRow(lines[index]);
      const alignments = splitTableRow(lines[index + 1]).map((cell) =>
        cell.startsWith(':') && cell.endsWith(':')
          ? 'center'
          : cell.endsWith(':')
            ? 'right'
            : cell.startsWith(':')
              ? 'left'
              : undefined,
      );
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].includes('|')) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      blocks.push(
        <table key={`${keyPrefix}-table-${index}`}>
          <thead>
            <tr>
              {headers.map((cell, cellIndex) => (
                <th
                  key={cellIndex}
                  style={{ textAlign: alignments[cellIndex] }}
                >
                  {renderInlineMarkdown(
                    cell,
                    `${keyPrefix}-th-${index}-${cellIndex}`,
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {headers.map((_, cellIndex) => (
                  <td
                    key={cellIndex}
                    style={{ textAlign: alignments[cellIndex] }}
                  >
                    {renderInlineMarkdown(
                      row[cellIndex] || '',
                      `${keyPrefix}-td-${rowIndex}-${cellIndex}`,
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      );
      continue;
    }

    if (isListLine(lines[index])) {
      const first = lines[index].match(/^(\s*)([-+*]|\d+[.)])\s+(.*)$/u);
      const baseIndent = first[1].length;
      const ordered = /^\d+[.)]$/u.test(first[2]);
      const List = ordered ? 'ol' : 'ul';
      const items = [];

      while (index < lines.length) {
        const item = lines[index].match(/^(\s*)([-+*]|\d+[.)])\s+(.*)$/u);
        if (
          !item ||
          item[1].length !== baseIndent ||
          /^\d+[.)]$/u.test(item[2]) !== ordered
        ) {
          break;
        }

        const itemContent = [item[3]];
        index += 1;
        const nestedLines = [];
        while (index < lines.length) {
          const next = lines[index];
          const nextItem = next.match(/^(\s*)([-+*]|\d+[.)])\s+(.*)$/u);
          if (nextItem && nextItem[1].length <= baseIndent) break;
          if (!next.trim()) {
            if (
              index + 1 < lines.length &&
              /^\s{2,}\S/u.test(lines[index + 1])
            ) {
              nestedLines.push('');
              index += 1;
              continue;
            }
            break;
          }
          if (!/^\s{2,}/u.test(next)) break;
          nestedLines.push(next.slice(Math.min(baseIndent + 2, next.length)));
          index += 1;
        }
        items.push({ content: itemContent.join(' '), nestedLines });
      }

      blocks.push(
        <List key={`${keyPrefix}-list-${index}`}>
          {items.map(({ content, nestedLines }, itemIndex) => (
            <li key={itemIndex}>
              {renderInlineMarkdown(
                content,
                `${keyPrefix}-list-${index}-${itemIndex}`,
              )}
              {nestedLines.length > 0 &&
                renderMarkdownBlocks(
                  nestedLines,
                  `${keyPrefix}-nested-${index}-${itemIndex}`,
                )}
            </li>
          ))}
        </List>,
      );
      continue;
    }

    if (/^\s*>/u.test(lines[index])) {
      const quoteLines = [];
      while (index < lines.length && /^\s*>/u.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^\s*>\s?/u, ''));
        index += 1;
      }
      blocks.push(
        <blockquote key={`${keyPrefix}-quote-${index}`}>
          {renderMarkdownBlocks(quoteLines, `${keyPrefix}-quote-${index}`)}
        </blockquote>,
      );
      continue;
    }

    const paragraph = [];
    while (
      index < lines.length &&
      lines[index].trim() &&
      !isBlockStart(lines, index)
    ) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    if (!paragraph.length) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push(
      <p key={`${keyPrefix}-paragraph-${index}`}>
        {renderInlineMarkdown(
          paragraph.join(' '),
          `${keyPrefix}-paragraph-${index}`,
        )}
      </p>,
    );
  }

  return blocks;
}

function MarkdownMessage({ source }) {
  const lines = String(source ?? '')
    .replace(/\r\n?/gu, '\n')
    .split('\n');
  return (
    <div className="assistant-markdown">
      {renderMarkdownBlocks(lines, 'message')}
    </div>
  );
}

function formatBackendContent(value) {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    return value
      .map((item) => (typeof item === 'string' ? item : JSON.stringify(item)))
      .filter(Boolean)
      .join('\n');
  }
  if (value && typeof value === 'object') {
    return JSON.stringify(value);
  }
  return value === null ? '' : String(value);
}

export function AssistantAnswer({ message }) {
  const limitations = formatBackendContent(message.limitations);

  return (
    <>
      <MarkdownMessage source={message.text} />
      {message.limitations !== null &&
        message.limitations !== undefined &&
        limitations && (
          <div className="assistant-limitations muted small">
            <strong>Limitations</strong>
            <MarkdownMessage source={limitations} />
          </div>
        )}
    </>
  );
}

export default function Home() {
  const { user } = useAuth();
  const chat = useChat();
  const [params, setParams] = useSearchParams();
  const routeConversationId = params.get('c');
  const openConversation = chat.open;
  const resetConversation = chat.reset;
  const { conversationId } = chat;
  const routeChangeRef = useRef(true);
  const chatStateRef = useRef(null);

  useEffect(() => {
    chatStateRef.current = {
      conversationId,
      openConversation,
      resetConversation,
    };
  });

  useEffect(() => {
    routeChangeRef.current = true;
    const current = chatStateRef.current;

    if (routeConversationId) {
      if (String(current.conversationId) !== routeConversationId) {
        current.openConversation(routeConversationId);
      }
    } else if (
      current.conversationId !== null &&
      current.conversationId !== undefined
    ) {
      current.resetConversation();
    }
  }, [routeConversationId]); // React to browser URL navigation, not chat state changes.

  useEffect(() => {
    if (routeChangeRef.current) {
      routeChangeRef.current = false;
      return;
    }
    if (conversationId === null || conversationId === undefined) return;
    setParams({ c: String(conversationId) }, { replace: true });
  }, [conversationId, setParams]);

  const [showArchived, setShowArchived] = useState(false);
  const [question, setQuestion] = useState('');
  const [questionError, setQuestionError] = useState('');
  const [think, setThink] = useState(false);
  const endRef = useRef(null);
  const inputRef = useRef(null);

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
      // Storage unavailable: the daily wish may show again next visit.
    }
  }, [greeting.today]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chat.messages]);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;

    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [question]);

  const submit = (e) => {
    e.preventDefault();

    const text = question.trim();

    if (!text || chat.busy || chat.conversationLoading) return;

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
      conversationsStatus={chat.conversationsStatus}
      conversationsError={chat.conversationsError}
      onRetryConversations={chat.loadConversations}
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
            {chat.conversationLoading && (
              <p className="muted" aria-live="polite">
                Loading conversation…
              </p>
            )}

            {chat.conversationError && (
              <div className="error" role="alert">
                <p>{chat.conversationError}</p>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => chat.open(chat.conversationId)}
                >
                  Try again
                </button>
              </div>
            )}

            {!chat.messages.length &&
              !chat.conversationLoading &&
              !chat.conversationError && (
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
                {m.role === 'assistant' && !m.error ? (
                  <AssistantAnswer message={m} />
                ) : (
                  <p>{m.text}</p>
                )}
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
              disabled={
                chat.busy || chat.conversationLoading || !question.trim()
              }
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
