// Client-side password checks, shared by the forgot-password reset step, the
// first-login (forced) password change and the sign-up page.
//
// These mirror the parts of the backend's AUTH_PASSWORD_VALIDATORS
// (backend/config/settings.py) that can be checked in the browser, i.e. the
// PasswordPolicyValidator (backend/apps/accounts/validators.py):
//   at least 8 characters, not entirely numeric, at least one uppercase letter,
//   at least one special (non-alphanumeric) character
// The other two (CommonPasswordValidator, UserAttributeSimilarityValidator) need
// the server's word list and the user record, so the backend's 400 message is
// shown when they fail.

export const PASSWORD_MIN_LENGTH = 8;

export interface PasswordRule {
  id: string;
  label: string;
  test: (password: string) => boolean;
}

export const PASSWORD_RULES: PasswordRule[] = [
  {
    id: "length",
    label: `Use ${PASSWORD_MIN_LENGTH} or more characters`,
    test: (password) => password.length >= PASSWORD_MIN_LENGTH,
  },
  {
    id: "not-numeric",
    label: "Not made up of numbers only",
    test: (password) => password.length > 0 && !/^\d+$/.test(password),
  },
  {
    id: "uppercase",
    label: "Include at least one uppercase letter",
    test: (password) => /\p{Lu}/u.test(password),
  },
  {
    // Backend rule is "any character that is not a letter or digit" (Python's
    // str.isalnum), so spaces and non-ASCII symbols count too.
    id: "special",
    label: "Include at least one special character (e.g. ! @ # $ %)",
    test: (password) => /[^\p{L}\p{N}]/u.test(password),
  },
];

export function checkPasswordRules(password: string): { results: { rule: PasswordRule; met: boolean }[]; allMet: boolean } {
  const results = PASSWORD_RULES.map((rule) => ({ rule, met: rule.test(password) }));
  return { results, allMet: results.every((r) => r.met) };
}
