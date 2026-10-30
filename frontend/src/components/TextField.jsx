import { useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';

/** Labelled input with inline error, optional hint and show/hide password. */
export default function TextField({
  label,
  error,
  hint,
  type = 'text',
  id,
  ...rest
}) {
  const [shown, setShown] = useState(false);
  const isPassword = type === 'password';
  const fieldId = id || `f-${label.toLowerCase().replace(/\W+/g, '-')}`;
  return (
    <div className="field">
      <label htmlFor={fieldId}>{label}</label>
      <span className="inputwrap">
        <input
          id={fieldId}
          type={isPassword && shown ? 'text' : type}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${fieldId}-msg` : undefined}
          {...rest}
        />
        {isPassword && (
          <button
            type="button"
            className="eye"
            aria-label={shown ? 'Hide password' : 'Show password'}
            onClick={() => setShown(!shown)}
          >
            {shown ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        )}
      </span>
      {error ? (
        <span id={`${fieldId}-msg`} className="field-error" role="alert">
          {error}
        </span>
      ) : (
        hint && <span className="field-hint">{hint}</span>
      )}
    </div>
  );
}
