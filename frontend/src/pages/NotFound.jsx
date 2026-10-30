import { Link } from 'react-router-dom';
import Logo from '../components/Logo';
import { PrimaryButton } from '../components/Buttons';

export default function NotFound() {
  return (
    <div className="center col">
      <Logo />
      <h1>Page not found</h1>
      <p className="muted">The page you are looking for does not exist.</p>
      <PrimaryButton as={Link} to="/">
        Go to home
      </PrimaryButton>
    </div>
  );
}
