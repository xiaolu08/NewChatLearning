from types import SimpleNamespace

from new_chat_learning.commands.fast_delete import parse_fast_delete


class Event:
    def __init__(self, text, messages=(), raw=None):
        self.text = text
        self.messages = messages
        self.message_obj = SimpleNamespace(raw_message=raw or {})

    def get_message_str(self):
        return self.text

    def get_messages(self):
        return list(self.messages)


def test_parse_quoted_fast_delete():
    request = parse_fast_delete(Event("！delete", [SimpleNamespace(type="Reply", id="88")]))
    assert request is not None
    assert request.quoted_message_id == "88"
    assert request.recent_position is None


def test_parse_recent_position_and_reject_unrelated_text():
    request = parse_fast_delete(Event("!d 3"))
    assert request is not None
    assert request.quoted_message_id is None
    assert request.recent_position == 3
    assert parse_fast_delete(Event("hello !d 3")) is None
    assert parse_fast_delete(Event("!d 0")) is None


def test_parse_quote_id_from_raw_onebot_segments():
    raw = {"message": [{"type": "reply", "data": {"id": 99}}]}
    request = parse_fast_delete(Event("!d", raw=raw))
    assert request is not None
    assert request.quoted_message_id == "99"
