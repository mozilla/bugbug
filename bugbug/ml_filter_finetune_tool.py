from abc import ABC, abstractmethod
from pathlib import Path

import torch
from datasets import Dataset
from torch.nn.functional import softmax
from transformers import (
    AutoTokenizer,
    ModernBertForSequenceClassification,
    Trainer,
    TrainingArguments,
    set_seed,
)


class FineTuneMLClassifer(ABC):
    def __init__(self, model_path, seed=42):
        self.model = ModernBertForSequenceClassification.from_pretrained(
            model_path, device_map=self.device, attn_implementation="sdpa"
        )
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_path, device_map=self.device
        )
        self.seed = seed
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    def _tokenize(self, batch):
        return self.tokenizer(
            batch["comment"],
            padding=True,
            truncation=True,
            return_tensors="pt",
        )

    def fit(self, inputs, labels):
        set_seed(self.seed)

        train_dataset = Dataset.from_dict(
            {
                "comment": inputs,
                "label": labels,
            }
        )

        train_dataset = train_dataset.map(
            self._tokenize, batched=True, remove_columns=["comment"]
        )

        training_args = TrainingArguments(
            # Required parameter:
            output_dir=None,
            # Optional training parameters:
            num_train_epochs=30,
            per_device_train_batch_size=128,
            warmup_steps=500,
            learning_rate=5e-5,
            optim="adamw_torch",
            # lr_scheduler_type="constant",
            # warmup_ratio=0.1,
            bf16=True,
            eval_steps=0,
            save_strategy="no",
            save_steps=100,
            save_total_limit=2,
            logging_steps=10,
            logging_strategy="epoch",
            report_to="none",
            seed=self.seed,
            use_cpu=True if self.device == "cpu" else False,
        )
        trainer = Trainer(
            model=self.model,
            args=training_args,
            tokenizer=self.tokenizer,
            train_dataset=train_dataset,
            eval_dataset=None,
        )

        trainer.train()
        self.model.save_pretrained(save_directory=self.tmpdir)
        self.tokenizer.save_pretrained(save_directory=self.tmpdir)

    def predict(self, inputs):
        self.model.to(self.device).eval()

        input = self.tokenizer(
            inputs, padding=True, truncation=True, return_tensors="pt"
        ).to(self.device)

        with torch.no_grad():
            logits = self.model(**input).logits
            probs = softmax(logits, dim=1)[:, 0]
            probs = probs.detach().cpu().numpy()
        return probs

    @abstractmethod
    def save(self, tmpdir: Path): ...
