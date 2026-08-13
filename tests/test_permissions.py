from new_chat_learning.commands.permissions import is_group_admin, is_plugin_admin


class Event:
    def __init__(self, sender_id: str, admin: bool = False, group_id: str = "10001"):
        self.sender_id = sender_id
        self.admin = admin
        self.group_id = group_id

    def is_admin(self):
        return self.admin

    def get_sender_id(self):
        return self.sender_id

    def get_group_id(self):
        return self.group_id


def test_astrbot_admin_is_allowed():
    assert is_plugin_admin(Event("1", admin=True), {}) is True


def test_configured_plugin_admin_is_allowed():
    config = {"permissions": {"plugin_admin_ids": ["42"]}}
    assert is_plugin_admin(Event("42"), config) is True


def test_regular_member_is_silently_rejected():
    assert is_plugin_admin(Event("7"), {}) is False


def test_group_sub_admin_is_scoped_to_configured_group():
    config = {
        "permissions": {
            "group_sub_admins": [{"group_id": "10001", "admin_ids": ["7"]}]
        }
    }
    assert is_group_admin(Event("7", group_id="10001"), config) is True
    assert is_group_admin(Event("7", group_id="10002"), config) is False
