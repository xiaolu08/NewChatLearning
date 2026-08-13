from new_chat_learning.commands.permissions import is_plugin_admin


class Event:
    def __init__(self, sender_id: str, admin: bool = False):
        self.sender_id = sender_id
        self.admin = admin

    def is_admin(self):
        return self.admin

    def get_sender_id(self):
        return self.sender_id


def test_astrbot_admin_is_allowed():
    assert is_plugin_admin(Event("1", admin=True), {}) is True


def test_configured_plugin_admin_is_allowed():
    config = {"permissions": {"plugin_admin_ids": ["42"]}}
    assert is_plugin_admin(Event("42"), config) is True


def test_regular_member_is_silently_rejected():
    assert is_plugin_admin(Event("7"), {}) is False
