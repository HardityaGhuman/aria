"""classify_query routes a caller's own-HR-data question to the new `hr` label,
while a generic policy lookup stays `policy`. The parse is tested against a mocked
model response so no live LLM is needed."""
import backend.core.llm as llm


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


def _fake_invoke(label):
    def _inv(*a, **k):
        return _Resp(label)
    return _inv


def test_hr_label_returned(monkeypatch):
    monkeypatch.setattr(llm, "_invoke", _fake_invoke("hr"))
    assert llm.classify_query("how many leaves do I have left?", []) == "hr"


def test_policy_label_unchanged(monkeypatch):
    monkeypatch.setattr(llm, "_invoke", _fake_invoke("policy"))
    assert llm.classify_query("what is the PTO policy?", []) == "policy"


def test_meta_and_chitchat_unaffected(monkeypatch):
    monkeypatch.setattr(llm, "_invoke", _fake_invoke("meta"))
    assert llm.classify_query("what did I ask?", []) == "meta"
    monkeypatch.setattr(llm, "_invoke", _fake_invoke("chitchat"))
    assert llm.classify_query("hi", []) == "chitchat"


def test_out_of_scope_unaffected(monkeypatch):
    monkeypatch.setattr(llm, "_invoke", _fake_invoke("out_of_scope"))
    assert llm.classify_query("write me an email", []) == "out_of_scope"


def test_unknown_defaults_to_policy(monkeypatch):
    monkeypatch.setattr(llm, "_invoke", _fake_invoke("something-weird"))
    assert llm.classify_query("x", []) == "policy"


def test_calendar_label_returned(monkeypatch):
    # §4.2 — who-is-out queries get their own `calendar` lane, split off from `hr`.
    monkeypatch.setattr(llm, "_invoke", _fake_invoke("calendar"))
    assert llm.classify_query("who is out this week?", []) == "calendar"


def test_calendar_does_not_shadow_hr(monkeypatch):
    # "calendar" contains no other label substring, so hr/meta/etc parse unchanged.
    monkeypatch.setattr(llm, "_invoke", _fake_invoke("hr"))
    assert llm.classify_query("how many leaves do I have left?", []) == "hr"
