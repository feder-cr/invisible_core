"""B291: a page that rejects with a value that is not an Error is an error.

Juggler answers `{exceptionDetails}` only for an exception. For a promise
rejected with an Event (a script or stylesheet whose `onerror` rejected) or
a plain object, it fills `text` and `stack` from `e.message` and `e.stack`,
both undefined, so the field arrives empty. An empty dict is falsy, and until
41.39.0 the rejection was read as a successful result of None: pyppeteer's
`addScriptTag(url=...)` "loaded" a 404, and `page.evaluate` of a function that
rejected with `{a: 1}` answered None in every client.
"""
from __future__ import annotations

import pytest

from invisible_core.juggler.injected import EvaluationError, InjectedScript

pytestmark = pytest.mark.unit


def _result(response):
    return InjectedScript._result(InjectedScript.__new__(InjectedScript), response)


def test_an_empty_exception_is_an_exception():
    with pytest.raises(EvaluationError, match="not an Error"):
        _result({"exceptionDetails": {}})


def test_an_exception_keeps_its_text_and_stack():
    with pytest.raises(EvaluationError, match="boom\n@eval"):
        _result({"exceptionDetails": {"text": "boom", "stack": "@eval code:1:1"}})


def test_a_thrown_value_is_reported_by_its_value():
    with pytest.raises(EvaluationError, match="^0$"):
        _result({"exceptionDetails": {"value": "0"}})


def test_a_result_is_a_result():
    assert _result({"result": {"type": "number", "value": 1}}) == {"type": "number", "value": 1}
    assert _result({}) == {}
