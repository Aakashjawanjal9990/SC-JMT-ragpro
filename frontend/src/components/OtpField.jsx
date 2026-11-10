import TextField from './TextField';

/** Six-digit code input. Verification itself happens on the backend. */
export default function OtpField({
  value,
  onChange,
  error,
  label = 'Verification code',
}) {
  return (
    <TextField
      label={label}
      inputMode="numeric"
      autoComplete="one-time-code"
      className="otp"
      placeholder="••••••"
      value={value}
      onChange={(e) => onChange(e.target.value.replace(/\D/g, '').slice(0, 6))}
      error={error}
    />
  );
}
