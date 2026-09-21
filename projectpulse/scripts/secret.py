"""Print the two secrets a deployed instance needs to manage its own keys.

    python -m scripts.secret

`PULSE_SECRET_KEY` encrypts the API keys in `llm_credentials`;
`PULSE_ADMIN_TOKEN` is what authorises changing them from a browser. Neither is
stored by this script - it prints them once and forgets, which is why it also
prints the `fly secrets set` line rather than leaving you to retype them.

Losing `PULSE_SECRET_KEY` does not lose the app, only the stored keys: they
become undecryptable ciphertext and have to be pasted in again. Rotating it
deliberately is `app/llm/keys.py`'s `rotate()`.
"""

from __future__ import annotations

import secrets as _secrets
import sys


def main(argv: list[str] | None = None) -> int:
    from app.llm.keys import generate_secret

    secret_key = generate_secret()
    admin_token = _secrets.token_urlsafe(32)

    print("# Encrypts the API keys stored in the database.")
    print(f"PULSE_SECRET_KEY={secret_key}")
    print()
    print("# Authorises changing settings and keys from a deployed browser.")
    print("# Leave it unset to keep writes loopback-only, as before.")
    print(f"PULSE_ADMIN_TOKEN={admin_token}")
    print()
    print("# On Fly:")
    print(
        f'fly secrets set PULSE_SECRET_KEY="{secret_key}" '
        f'PULSE_ADMIN_TOKEN="{admin_token}"'
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
