from misty_engine import keychain


def test_set_get_and_delete_all():
    for n in keychain.NAMES:
        keychain.set_secret(n, "v-" + n)
    assert keychain.get_secret("openrouter") == "v-openrouter"
    assert keychain.delete_all() == 3
    assert keychain.get_secret("openrouter") is None
    assert keychain.delete_all() == 0
