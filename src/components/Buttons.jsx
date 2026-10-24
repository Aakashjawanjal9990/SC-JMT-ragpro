import { ArrowRight, Loader2, UserPlus } from 'lucide-react';

/** Primary action: black background, white text, trailing arrow. */
export function PrimaryButton({
  as: Tag = 'button',
  arrow = true,
  loading = false,
  children,
  ...rest
}) {
  return (
    <Tag className="btn btn-primary" {...rest}>
      {loading && <Loader2 size={15} className="spin" />}
      {children}
      {arrow && !loading && <ArrowRight size={15} />}
    </Tag>
  );
}

/** Secondary action: white background, peach outline, optional user icon. */
export function SecondaryButton({
  as: Tag = 'button',
  userIcon = false,
  children,
  ...rest
}) {
  return (
    <Tag className="btn btn-secondary" {...rest}>
      {userIcon && <UserPlus size={15} />}
      {children}
    </Tag>
  );
}
