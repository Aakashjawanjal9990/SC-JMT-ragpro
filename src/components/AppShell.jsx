import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Check,
  LogOut,
  Menu,
  MessageSquare,
  Pin,
  PinOff,
  Plus,
  Search,
  Share2,
  Trash2,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import useSidebarPrefs from '../hooks/useSidebarPrefs';
import { displayName } from '../lib/names';
import scales from '../assets/scales.png';
import Logo from './Logo';

export default function AppShell({
  className = '',
  crumb,
  conversations = [],
  activeId = null,
  onNew,
  onSelect,
  onDelete,
  children,
}) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const prefs = useSidebarPrefs(user.email);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [copied, setCopied] = useState(false);

  const matches = conversations.filter((c) =>
    (c.title || '').toLowerCase().includes(query.toLowerCase()),
  );
  const pinnedList = matches.filter((c) => prefs.pinned.includes(String(c.id)));
  const recentList = matches.filter(
    (c) => !prefs.pinned.includes(String(c.id)),
  );

  const share = async () => {
    try {
      if (navigator.share) {
        await navigator.share({ title: 'Nyaya-Mitra', url: location.href });
      } else {
        await navigator.clipboard.writeText(location.href);
        setCopied(true);
        setTimeout(() => setCopied(false), 1800);
      }
    } catch {
      /* user dismissed the share sheet */
    }
  };

  const signOut = () => {
    logout();
    navigate('/', { replace: true });
  };

  const deleteChat = async (c) => {
    const title = c.title || 'Untitled research';
    if (
      !window.confirm(
        `Delete "${title}" permanently?\n\nThis cannot be undone.`,
      )
    )
      return;
    try {
      await onDelete?.(c.id);
    } catch (err) {
      window.alert(err.message);
    }
  };

  const renderRow = (c) => {
    const isPinned = prefs.pinned.includes(String(c.id));
    const isActive = String(c.id) === String(activeId);
    return (
      <div key={c.id} className={`sidebar-row ${isActive ? 'active' : ''}`}>
        <button
          className="sidelink"
          onClick={() => {
            setOpen(false);
            onSelect?.(c.id);
          }}
        >
          <MessageSquare size={14} />
          <span className="trunc">{c.title || 'Untitled research'}</span>
        </button>
        <button
          className="row-action"
          aria-label={isPinned ? 'Unpin research' : 'Pin research'}
          title={isPinned ? 'Unpin' : 'Pin to top'}
          onClick={() => prefs.togglePin(c.id)}
        >
          {isPinned ? <PinOff size={13} /> : <Pin size={13} />}
        </button>
        <button
          className="row-action"
          aria-label="Delete research"
          title="Delete"
          onClick={() => deleteChat(c)}
        >
          <Trash2 size={13} />
        </button>
      </div>
    );
  };

  return (
    <div className={`shell ${className}`.trim()}>
      <div className="shell-brand">
        <button
          className="icon-btn menu"
          aria-label="Open menu"
          onClick={() => setOpen(true)}
        >
          <Menu size={20} />
        </button>
        <Logo to="/home" />
      </div>

      <header className="shell-top">
        <span className="crumb">
          Supreme Court research / <b>{crumb}</b>
        </span>
        <div className="row">
          <button className="ghost" onClick={share}>
            {copied ? <Check size={15} /> : <Share2 size={15} />}
            {copied ? 'Link copied' : 'Share'}
          </button>
          <div className="userchip" title={user.email}>
            <span className="avatar">{displayName(user)[0].toUpperCase()}</span>
            <span className="uname">{displayName(user)}</span>
          </div>
          <button className="ghost" onClick={signOut}>
            <LogOut size={15} />
            Logout
          </button>
        </div>
      </header>

      {open && <div className="scrim" onClick={() => setOpen(false)} />}
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <button
          className="newbtn"
          onClick={() => {
            setOpen(false);
            onNew?.();
          }}
        >
          <Plus size={16} /> New Research
        </button>
        {searching ? (
          <input
            className="sidesearch-input"
            autoFocus
            placeholder="Search research"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onBlur={() => !query && setSearching(false)}
          />
        ) : (
          <button className="sidelink" onClick={() => setSearching(true)}>
            <Search size={14} /> Search research
          </button>
        )}

        {pinnedList.length > 0 && (
          <>
            <h4>PINNED</h4>
            {pinnedList.map(renderRow)}
          </>
        )}

        <h4>YOUR RESEARCH</h4>
        {matches.length === 0 && (
          <p className="sidehint">
            {conversations.length
              ? 'No matching research.'
              : 'Start a conversation and revisit your research here.'}
          </p>
        )}
        {recentList.map(renderRow)}
      </aside>

      <main className="shell-main">
        <img className="watermark" src={scales} alt="" />
        <div className="shell-content">{children}</div>
        <p className="foot">
          Research assistance, not legal advice. Always check the original
          judgment.
        </p>
      </main>
    </div>
  );
}
