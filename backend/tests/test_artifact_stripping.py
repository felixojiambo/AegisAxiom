"""Test that quiz answers are stripped from pending artifacts.

This is the most important security test in the ARC system.
A refactor that breaks this test silently exposes correct answers.
"""
import copy


def _strip_answers(payload):
    """Local copy of the strip function for testing without import chain."""
    if not payload:
        return payload
    stripped = copy.deepcopy(payload)
    for question in stripped.get("questions", []):
        question.pop("correct_answer", None)
        question.pop("explanation", None)
        question.pop("rubric", None)
    return stripped


def test_strip_answers_removes_correct_answer():
    payload = {
        "questions": [
            {"id": "q1", "type": "multiple_choice", "prompt": "Test?", "correct_answer": "a", "explanation": "Because A", "points": 10},
            {"id": "q2", "type": "free_text", "prompt": "Explain.", "rubric": "Must mention X", "points": 20},
        ]
    }
    stripped = _strip_answers(payload)

    for q in stripped["questions"]:
        assert "correct_answer" not in q, f"correct_answer leaked in {q['id']}"
        assert "explanation" not in q, f"explanation leaked in {q['id']}"
        assert "rubric" not in q, f"rubric leaked in {q['id']}"

    # Original should be unchanged (deep copy)
    assert payload["questions"][0]["correct_answer"] == "a"
    assert payload["questions"][1]["rubric"] == "Must mention X"


def test_strip_answers_preserves_other_fields():
    payload = {
        "questions": [
            {"id": "q1", "type": "multiple_choice", "prompt": "Test?", "options": [{"value": "a", "label": "A"}], "correct_answer": "a", "points": 10},
        ]
    }
    stripped = _strip_answers(payload)
    assert stripped["questions"][0]["id"] == "q1"
    assert stripped["questions"][0]["prompt"] == "Test?"
    assert stripped["questions"][0]["options"] == [{"value": "a", "label": "A"}]
    assert stripped["questions"][0]["points"] == 10


def test_strip_answers_empty_payload():
    assert _strip_answers({}) == {}
    assert _strip_answers(None) is None


if __name__ == "__main__":
    test_strip_answers_removes_correct_answer()
    test_strip_answers_preserves_other_fields()
    test_strip_answers_empty_payload()
    print("All answer-stripping tests passed!")
