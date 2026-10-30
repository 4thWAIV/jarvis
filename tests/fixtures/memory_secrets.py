"""The one Keychain stand-in for tests: a `team_client` SecretStore in
memory, so no test reads or writes the user's Keychain."""


class MemorySecrets:
    def __init__(self):
        self.items: dict[str, str] = {}

    def get(self, account):
        return self.items.get(account)

    def set(self, account, secret):
        self.items[account] = secret

    def delete(self, account):
        self.items.pop(account, None)
