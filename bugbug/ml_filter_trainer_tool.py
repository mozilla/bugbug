from abc import ABC, abstractmethod

import numpy as np

from sklearn.metrics import recall_score


class Trainer(ABC):
    def __init__(self, min_recall: float = 0.9, thr_metric: str = "acceptance_rate"):
        self.min_recall = min_recall
        self.thr_metric = thr_metric

    @abstractmethod
    def train_test_split(self, data, test_size=0.5, random_split=True): ...

    def fit(self, model):
        model.fit(self.train_inputs, self.train_labels)
        return model.predict(self.val_inputs)

    def train(self, model):
        probs = self.fit(model)
        thresholds_results = {}
        for thr in np.arange(0, 1.01, 0.01):
            preds = np.where(probs >= thr, 0, 1)
            recalls = recall_score(self.val_labels, preds, average=None)
            acceptance_rate = sum(
                [1 for pred, label in zip(preds, self.val_labels) if pred and label]
            ) / sum(preds)
            thresholds_results[thr] = {
                "recall_accept": recalls[1],
                "gmean": np.sqrt(recalls[0] * recalls[1]),
                "acceptance_rate": acceptance_rate,
            }
        # Select threshold based on minimum accept recall and max acceptance_rate/gmean
        thresholds_results = {
            thr: metrics
            for thr, metrics in thresholds_results.items()
            if metrics["recall_accept"] >= self.min_recall
        }
        thresholds_results = sorted(
            thresholds_results.items(),
            key=lambda x: x[1][f"{self.thr_metric}"],
            reverse=True,
        )
        return thresholds_results[0][0]
