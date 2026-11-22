from py_vapid import Vapid

from app.tools.vapid import generate


def test_generated_keys_load_in_pywebpush() -> None:
    pub, priv = generate()
    v = Vapid.from_string(priv)  # the form pywebpush uses for VAPID_PRIVATE_KEY
    assert v.public_key is not None
    assert len(pub) == 87  # 65-byte uncompressed point, base64url without padding
