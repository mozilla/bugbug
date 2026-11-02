import numpy as np

from scripts import comment_classifier


def test_classify_comments_uses_bug_comment_tuple(monkeypatch, capsys):
    bug = {"id": 10}
    comment = {"id": 123, "bug_id": 10, "count": 1}
    classified_items = []

    class FakeModel:
        calculate_importance = False

        def classify(self, item, probabilities, importances):
            classified_items.append(item)
            return np.array([[0.1, 0.9]])

    class FakeModelClass:
        @staticmethod
        def load(model_file_name):
            return FakeModel()

    monkeypatch.setattr(comment_classifier.os.path, "exists", lambda path: True)
    monkeypatch.setattr(
        comment_classifier, "get_model_class", lambda model_name: FakeModelClass
    )
    monkeypatch.setattr(
        comment_classifier.bugzilla,
        "get_comments",
        lambda comment_ids: {123: (bug, comment)},
    )
    monkeypatch.setattr("builtins.input", lambda: "")

    comment_classifier.classify_comments("spamcomment", [123, 999])

    assert classified_items == [(bug, comment)]
    assert "show_bug.cgi?id=10#c1" in capsys.readouterr().out
