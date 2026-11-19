from __future__ import annotations

from code_reviewer.review.secrets import PLACEHOLDER, redact


def test_redacts_known_tokens_and_private_keys():
    src = (
        'OPENAI = "sk-proj-abcdefghijklmnopqrstuvwxyz1234"\n'
        "GH = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'\n"
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAK\n-----END RSA PRIVATE KEY-----\n"
    )
    redacted, count = redact(src)
    assert count >= 3
    assert "sk-proj-" not in redacted
    assert "ghp_" not in redacted
    assert "MIIEowIBAAK" not in redacted
    assert PLACEHOLDER in redacted


def test_redacts_quoted_password_but_preserves_code_expressions():
    hardcoded = 'def connect(password="admin123"):\n    return password\n'
    text, count = redact(hardcoded)
    assert count == 1
    assert "admin123" not in text
    assert f'password="{PLACEHOLDER}"' in text

    # Регрессионный тест: обычные выражения не должны заменяться.
    expr = (
        "password = user.password\n"
        "token = self.fetch_token()\n"
        "secret = os.environ.get('MY_VAR')\n"
    )
    untouched, expr_count = redact(expr)
    assert untouched == expr
    assert expr_count == 0


def test_does_not_mangle_long_identifiers_or_sha256_hashes():
    code = (
        "class InternationalizationConfigurationProviderFactory:\n"
        '    EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"\n'
    )
    text, count = redact(code)
    assert text == code
    assert count == 0


def test_redacts_high_entropy_quoted_secret():
    secret_line = 'WEBHOOK = "k9$Lm2!pQ8xR4vZ7wN3bT6yJ1cF5hG0dA"'
    text, count = redact(secret_line)
    assert count == 1
    assert "k9$Lm2" not in text
    assert PLACEHOLDER in text
