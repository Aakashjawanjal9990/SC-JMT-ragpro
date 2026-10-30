import { LogOut } from 'lucide-react';
import Logo from '../../components/Logo';
import AdminData from './AdminData';

export default function AdminConsole({ onSignOut }) {
  return (
    <div className="console">
      <header className="console-top">
        <div className="row">
          <Logo to="/admin" />
          <span className="tagpill">Admin console</span>
        </div>
        <div className="row">
          <button className="ghost" onClick={onSignOut}>
            <LogOut size={15} /> Sign out
          </button>
        </div>
      </header>

      <main className="console-main">
        <AdminData />
      </main>
    </div>
  );
}
