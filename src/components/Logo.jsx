import { Link } from 'react-router-dom';
import mark from '../assets/logo.svg';

export default function Logo({ to = '/' }) {
  return (
    <Link to={to} className="logo" aria-label="Nyaya-Mitra home">
      <img src={mark} alt="" width="26" height="36" />
      <span>
        <b>Nyaya-Mitra</b>
        <small>Your Rights. Your Voice. Your Mitra.</small>
      </span>
    </Link>
  );
}
