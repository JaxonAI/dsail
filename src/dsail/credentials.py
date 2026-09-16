"""Where the credential a call carries comes from.

Resolution order, first hit wins:

1. the ``credential`` argument to :class:`dsail.Client`
2. ``DSAIL_CREDENTIAL`` in the environment
3. the file ``$DSAIL_CONFIG_DIR/credential`` (default
   ``~/.config/dsail/credential``), one token on one line

Nothing here mints a credential. The evaluation grade — auto-issued on first
contact with no human gate — is issued by the service, and when it lands the
client stores what it was handed through :func:`store`. Until then a call with
no credential is a call with no ``x-jaxon-credential`` header, which the
service answers with its local stub.
"""

import os
import stat

ENV_CREDENTIAL = "DSAIL_CREDENTIAL"
ENV_CONFIG_DIR = "DSAIL_CONFIG_DIR"
HEADER = "x-jaxon-credential"
_FILENAME = "credential"


def config_dir():
    explicit = os.environ.get(ENV_CONFIG_DIR)
    if explicit:
        return os.path.expanduser(explicit)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "dsail")


def credential_path():
    return os.path.join(config_dir(), _FILENAME)


def resolve(explicit=None):
    """The credential to send, or ``None`` for an unauthenticated call."""
    if explicit:
        return explicit.strip()
    from_env = os.environ.get(ENV_CREDENTIAL)
    if from_env and from_env.strip():
        return from_env.strip()
    path = credential_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError:
        return None
    return token or None


def store(token):
    """Persist a credential for later calls, readable by this user only."""
    directory = config_dir()
    os.makedirs(directory, exist_ok=True)
    path = credential_path()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(token.strip() + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def forget():
    """Remove the stored credential. Returns whether one was there."""
    try:
        os.remove(credential_path())
    except FileNotFoundError:
        return False
    return True
