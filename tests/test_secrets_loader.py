"""CHORE-secrets-ssm-migration: `_load_secrets` is SSM-only and fails loudly.

These pin the one behaviour the migration's final leg changed. Before it, an SSM
failure logged "falling back to Secrets Manager", found no ARN, and returned
`{}`; the caller then continued with whatever defaults it had. Neither of these
properties had a test on this service — the whole suite was green either way,
which is exactly why the change needed its own.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import _load_secrets

_PARAM = "/myblog/music"


def test_returns_parsed_json_from_ssm():
    payload = {"DATABASE_URL": "postgresql://host/db"}
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": json.dumps(payload)}}
    with patch("boto3.client", return_value=ssm) as mk:
        assert _load_secrets(_PARAM) == payload
    assert mk.call_args.args[0] == "ssm"
    assert ssm.get_parameter.call_args.kwargs == {"Name": _PARAM, "WithDecryption": True}


def test_never_constructs_a_secretsmanager_client():
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": "{}"}}
    seen: list[str] = []

    def client(name, **kw):
        seen.append(name)
        return ssm

    with patch("boto3.client", side_effect=client):
        _load_secrets(_PARAM)
    assert seen == ["ssm"]


def test_ssm_error_raises_instead_of_returning_empty(caplog):
    ssm = MagicMock()
    ssm.get_parameter.side_effect = Exception("AccessDenied")
    with patch("boto3.client", return_value=ssm):
        with pytest.raises(Exception, match="AccessDenied"):
            _load_secrets(_PARAM)
    assert _PARAM in caplog.text, "the failure log must name the parameter"


def test_unparseable_parameter_value_raises_and_is_logged(caplog):
    """A value that is not JSON is a load failure too.

    `/myblog/*` has been written with unquoted JSON before. If `json.loads` sat
    outside the try, this surfaced as a bare JSONDecodeError naming nothing.
    """
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": "not json"}}
    with patch("boto3.client", return_value=ssm):
        with pytest.raises(json.JSONDecodeError):
            _load_secrets(_PARAM)
    assert _PARAM in caplog.text


def test_client_construction_failure_raises_and_is_logged(caplog):
    with patch("boto3.client", side_effect=Exception("NoRegionError")):
        with pytest.raises(Exception, match="NoRegionError"):
            _load_secrets(_PARAM)
    assert _PARAM in caplog.text


# --- required-key contract ---------------------------------------------------
#
# `_load_secrets` raising covers "the parameter could not be read". These cover
# the condition it explicitly did NOT: the parameter was read, parsed as valid
# JSON, and a key this service cannot serve without is absent — or is present as
# something unusable.
#
# The loader this replaced checked the RESOLVED settings (`if not
# s.DATABASE_URL`) after copying anything truthy across. Reproduced against that
# code, not inferred: `{"DATABASE_URL": "   "}` booted with a whitespace DSN and
# `{"SPOTIFY_CLIENT_ID": 12345}` booted with an `int` in a `str` field, because
# this model does not set `validate_assignment`. The required SET is unchanged
# by this sweep; only how presence is judged.

_DB = "postgresql+psycopg://u:pw@db.example/music"
_CID = "spotify-client-id"
_CSECRET = "spotify-client-secret"
_FULL = {
    "DATABASE_URL": _DB,
    "SPOTIFY_CLIENT_ID": _CID,
    "SPOTIFY_CLIENT_SECRET": _CSECRET,
}


def _get_settings(secrets, *, param: str = _PARAM, env: dict | None = None):
    """get_settings() with the SSM load stubbed and the singleton cache cleared.

    `SECRETS_PARAM` is set through the environment because that is how a Lambda
    supplies it (infra/lambda.tf). The three required names are blanked so a
    developer's exported shell value cannot stand in for a key the payload is
    missing — the very substitution `test_env_var_cannot_mask_...` pins.
    """
    import app.core.config as cfg

    cfg.get_settings.cache_clear()
    base = {
        "SECRETS_PARAM": param,
        "DATABASE_URL": "",
        "SPOTIFY_CLIENT_ID": "",
        "SPOTIFY_CLIENT_SECRET": "",
    }
    try:
        with (
            patch("app.core.config._load_secrets", return_value=secrets) as loader,
            patch.dict("os.environ", {**base, **(env or {})}, clear=False),
        ):
            return cfg.get_settings(), loader
    finally:
        cfg.get_settings.cache_clear()


def test_all_required_keys_present_boots_and_applies_them():
    s, loader = _get_settings(_FULL)
    assert s.DATABASE_URL == _DB
    assert s.SPOTIFY_CLIENT_ID == _CID
    assert s.SPOTIFY_CLIENT_SECRET == _CSECRET
    loader.assert_called_once_with(_PARAM)


def test_missing_database_url_fails_closed_at_boot():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        _get_settings({"SPOTIFY_CLIENT_ID": _CID, "SPOTIFY_CLIENT_SECRET": _CSECRET})


def test_missing_spotify_secret_fails_closed_at_boot():
    with pytest.raises(ValueError, match="SPOTIFY_CLIENT_SECRET"):
        _get_settings({"DATABASE_URL": _DB, "SPOTIFY_CLIENT_ID": _CID})


def test_env_var_cannot_mask_a_key_missing_from_the_parameter():
    """A stale Lambda env var must not satisfy the contract.

    Presence is judged on what SSM supplied. Judged on the resolved Settings
    instead — the formulation this replaced — a `SPOTIFY_CLIENT_SECRET` left
    behind in the function's environment marks the parameter healthy while the
    two values silently diverge. Dormant today (`musicApi` carries no such
    variable), which is exactly why only a test keeps it that way.
    """
    with pytest.raises(ValueError, match="SPOTIFY_CLIENT_SECRET"):
        _get_settings(
            {"DATABASE_URL": _DB, "SPOTIFY_CLIENT_ID": _CID},
            env={"SPOTIFY_CLIENT_SECRET": "from-the-env"},
        )


@pytest.mark.parametrize("bad", ["", "   ", "\t\n", None, 123, 0, [], {}, True])
def test_unusable_required_value_counts_as_missing(bad):
    """`null`, empty, whitespace-only and non-strings are all "absent".

    The parameter is hand-edited JSON; each of these has a plausible way of
    being written, and none of them is a secret this service can use. `"   "`
    and `123` are the two the previous truthiness check let through.
    """
    with pytest.raises(ValueError, match="SPOTIFY_CLIENT_ID"):
        _get_settings({**_FULL, "SPOTIFY_CLIENT_ID": bad})


def test_payload_that_is_valid_json_but_not_an_object_fails():
    """`json.loads` succeeds on `"abc"` and `[1,2]`; `.get` would then explode.

    The failure must name the parameter, not surface as an AttributeError from
    inside the loader.
    """
    for payload in ("just-a-string", [1, 2], 7, None):
        with pytest.raises(ValueError, match=_PARAM):
            _get_settings(payload)


def test_required_values_are_assigned_verbatim_and_never_stripped():
    """Whitespace decides presence but must NOT be trimmed off the value.

    "Normalize the config values" is a natural tidy-up and it would break the
    Spotify token mint: the client secret is sent byte-for-byte and a trimmed
    one 400s the exchange. The DSN is asserted alongside it because a driver URL
    is no safer to silently rewrite. Without this, `secrets[k].strip()` passes
    every other test in this file.
    """
    padded = {
        "DATABASE_URL": " postgresql+psycopg://u:pw@h/music ",
        "SPOTIFY_CLIENT_ID": "  cid-with-padding  ",
        "SPOTIFY_CLIENT_SECRET": "\tcsecret-with-padding\t",
    }
    s, _ = _get_settings(padded)
    assert s.DATABASE_URL == padded["DATABASE_URL"]
    assert s.SPOTIFY_CLIENT_ID == padded["SPOTIFY_CLIENT_ID"]
    assert s.SPOTIFY_CLIENT_SECRET == padded["SPOTIFY_CLIENT_SECRET"]


def test_no_ssm_call_and_no_validation_when_secrets_param_is_unset():
    """Local dev and the unit suite: empty SECRETS_PARAM, no contract at all.

    Read narrowly: this pins LOCAL ergonomics (no AWS call from a laptop or a CI
    runner), NOT a guarantee that a deployed environment may run without
    `SECRETS_PARAM`. It may not, and that hole is still open — an empty
    `SECRETS_PARAM` on the real Lambda skips this whole contract. Closing it
    means asserting ENV-vs-SECRETS_PARAM consistency, which is
    SEC-system-hardening open question 3 and is deliberately not decided here.
    """
    import app.core.config as cfg

    cfg.get_settings.cache_clear()
    try:
        with (
            patch("app.core.config._load_secrets") as loader,
            patch.dict("os.environ", {"SECRETS_PARAM": ""}, clear=False),
        ):
            cfg.get_settings()
        loader.assert_not_called()
    finally:
        cfg.get_settings.cache_clear()


def test_secret_values_never_reach_the_exception_or_the_logs(caplog):
    """The error names the parameter and the KEYS. Never a value.

    A config error is read off CloudWatch by whoever is paged; a value pasted
    into it is a secret published to a log group with a different audience than
    the parameter it came from.
    """
    leaked = "SUPERSECRET-DB-PASSWORD"
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ValueError) as exc:
            _get_settings({"DATABASE_URL": f"postgresql://u:{leaked}@h/music"})
    assert leaked not in str(exc.value)
    assert leaked not in caplog.text
    assert "SPOTIFY_CLIENT_ID" in str(exc.value)
    assert _PARAM in str(exc.value)


def test_successful_boot_does_not_log_the_secret_values(caplog):
    with caplog.at_level(logging.DEBUG):
        _get_settings(_FULL)
    assert _CSECRET not in caplog.text
    assert "pw@db.example" not in caplog.text
