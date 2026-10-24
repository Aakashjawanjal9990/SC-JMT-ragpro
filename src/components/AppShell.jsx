import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Check,
  LogOut,
  Menu,
  MessageSquare,
  Plus,
  Search,
  Share2,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
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
  children,
}) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [copied, setCopied] = useState(false);

  const shown = conversations.filter((c) =>
    (c.title || '').toLowerCase().includes(query.toLowerCase()),
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
        <h4>YOUR RESEARCH</h4>
        {shown.length === 0 && (
          <p className="sidehint">
            {conversations.length
              ? 'No matching research.'
              : 'Start a conversation and revisit your research here.'}
          </p>
        )}
        {shown.map((c) => (
          <button
            key={c.id}
            className={`sidelink ${String(c.id) === String(activeId) ? 'active' : ''}`}
            onClick={() => {
              setOpen(false);
              onSelect?.(c.id);
            }}
          >
            <MessageSquare size={14} />
            <span className="trunc">{c.title || 'Untitled research'}</span>
          </button>
        ))}
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
