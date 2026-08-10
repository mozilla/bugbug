# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Inference-only performance regression predictor."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from bugbug.model import Model

MODEL_NAME = "Performance Regression Predictor"
MODEL_IDENTIFIER = "performanceregressionpredictor"
DEFAULT_MODEL_DIRECTORY = f"{MODEL_IDENTIFIER}model"
POSITIVE_CLASS_ID = 1


def clean_commit_message(
    commit_message: str | None, *, clean_subject_only: bool = True
) -> str:
    """Remove common noisy prefixes from a commit message.

    This intentionally mirrors the preprocessing used to prepare the model's
    training data.
    """
    if commit_message is None:
        return ""

    message = str(commit_message)
    lines = message.splitlines()
    if not lines:
        return ""

    def _clean_subject(subject: str) -> str:
        prefix = r"(?:\[[^\]]+\]|\([^)]+\)|bug\s*#?\s*\d+\b)"
        return re.sub(
            rf"^\s*(?:{prefix}\s*(?:[-–—:.,]\s*)?)+",
            "",
            subject,
            count=1,
            flags=re.IGNORECASE,
        ).strip()

    if clean_subject_only:
        for index, line in enumerate(lines):
            if line.strip():
                lines[index] = _clean_subject(line)
                break
        return "\n".join(lines).strip("\n")

    cleaned_lines = [
        _clean_subject(line) if index == 0 else line for index, line in enumerate(lines)
    ]
    return "\n".join(cleaned_lines).strip("\n")


def combine_commit_messages(commit_messages: Sequence[str]) -> str:
    """Clean and combine commit messages uploaded for one Phabricator diff.

    Phabricator exposes local commit metadata as a list. Most Mozilla diffs have
    one entry, but cleaning each message separately also gives deterministic
    preprocessing for the uncommon multi-commit case.
    """
    return "\n\n".join(
        cleaned_message
        for commit_message in commit_messages
        if (cleaned_message := clean_commit_message(commit_message).strip())
    )


def diff_to_structured_text(diff_string: str) -> str:
    """Convert a Git or Mercurial diff to the model's structured format."""
    lines = diff_string.strip().splitlines()
    output: list[str] = []

    current_file: str | None = None
    current_block_type: str | None = None
    current_block_lines: list[str] = []

    pending_binary_status: str | None = None
    rename_from: str | None = None
    rename_to: str | None = None
    pending_rename = False

    def flush_block() -> None:
        nonlocal current_block_type, current_block_lines
        if current_block_type and current_block_lines:
            output.append(f"  <{current_block_type.upper()}>")
            output.extend(f"      {line}" for line in current_block_lines)
            output.append(f"  </{current_block_type.upper()}>")
        current_block_type = None
        current_block_lines = []

    def flush_file() -> None:
        nonlocal current_file, pending_binary_status
        nonlocal rename_from, rename_to, pending_rename

        if current_file:
            flush_block()
            if pending_rename and rename_from and rename_to:
                output.append(f"  File renamed from {rename_from}.")
            elif pending_binary_status:
                output.append(f"  Binary file {pending_binary_status}.")
            output.append("</FILE>")

        current_file = None
        pending_binary_status = None
        rename_from = None
        rename_to = None
        pending_rename = False

    for line in lines:
        if line.startswith("diff -r"):
            flush_file()
            parts = line.split()
            if len(parts) >= 4:
                current_file = parts[-1]
                output.extend(("<FILE>", f"  {current_file}"))
            continue

        if line.startswith("diff --git"):
            flush_file()
            match = re.match(r"diff --git a/(.+?) b/(.+)", line)
            if match:
                current_file = match.group(2)
                output.extend(("<FILE>", f"  {current_file}"))
        elif line.startswith("rename from "):
            rename_from = line[len("rename from ") :].strip()
            pending_rename = True
        elif line.startswith("rename to "):
            rename_to = line[len("rename to ") :].strip()
            if not current_file:
                current_file = rename_to
                output.extend(("<FILE>", f"  {current_file}"))
        elif line.startswith("--- "):
            pass
        elif line.startswith("+++ "):
            pass
        elif line.startswith("Binary files "):
            flush_block()
            pending_binary_status = "changed"
            flush_file()
        elif line.startswith("@@"):
            flush_block()
        elif line.startswith("-"):
            if current_block_type != "REMOVED":
                flush_block()
                current_block_type = "REMOVED"
            current_block_lines.append(line[1:].rstrip())
        elif line.startswith("+"):
            if current_block_type != "ADDED":
                flush_block()
                current_block_type = "ADDED"
            current_block_lines.append(line[1:].rstrip())
        else:
            flush_block()

    flush_file()
    return "\n".join(output)


def build_model_input(commit_message: str | None, raw_diff: str) -> str:
    """Build the exact text representation consumed during training."""
    cleaned_message = clean_commit_message(commit_message)
    structured_diff = diff_to_structured_text(raw_diff)
    return "\n".join(
        (
            "<COMMIT_MESSAGE>",
            cleaned_message,
            "</COMMIT_MESSAGE>",
            structured_diff,
        )
    )


class PerformanceRegressionPredictorModel(Model):
    """Hugging Face sequence classifier used only for inference."""

    training_supported = False

    def __init__(self, tokenizer: Any = None, transformer_model: Any = None) -> None:
        super().__init__()
        self.tokenizer = tokenizer
        self.transformer_model = transformer_model
        self.calculate_importance = False
        self.model_directory: str | None = None
        self.model_metadata: dict[str, Any] = {}

    @classmethod
    def load(cls, model_directory: str) -> "PerformanceRegressionPredictorModel":
        """Load a local Hugging Face checkpoint directory."""
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            model_directory,
            local_files_only=True,
        )
        transformer_model = AutoModelForSequenceClassification.from_pretrained(
            model_directory,
            local_files_only=True,
        )
        # The service runs inference on CPU. Converting here also makes
        # checkpoints saved in bfloat16 usable on CPUs without bfloat16
        # acceleration.
        transformer_model.float().to("cpu")
        transformer_model.eval()

        model = cls(tokenizer=tokenizer, transformer_model=transformer_model)
        model.model_directory = model_directory

        metadata_path = Path(model_directory) / "bugbug_model.json"
        if metadata_path.exists():
            with metadata_path.open(encoding="utf-8") as metadata_file:
                model.model_metadata = json.load(metadata_file)

        model._validate_checkpoint()
        return model

    def _validate_checkpoint(self) -> None:
        if self.tokenizer is None or self.transformer_model is None:
            raise ValueError("The tokenizer and transformer model must both be loaded")

        config = self.transformer_model.config
        if int(config.num_labels) != 2:
            raise ValueError(
                "Performance Regression Predictor requires exactly two labels"
            )

        id2label = {
            int(label_id): label
            for label_id, label in getattr(config, "id2label", {}).items()
        }
        if id2label and id2label.get(POSITIVE_CLASS_ID) not in (
            "POSITIVE",
            "1",
            1,
        ):
            raise ValueError(
                "Checkpoint label 1 must be the positive performance-regression class"
            )

        required_tokens = {
            "<COMMIT_MESSAGE>",
            "</COMMIT_MESSAGE>",
            "<FILE>",
            "</FILE>",
            "<ADDED>",
            "</ADDED>",
            "<REMOVED>",
            "</REMOVED>",
        }
        tokenizer_tokens = set(self.tokenizer.get_added_vocab())
        missing_tokens = required_tokens - tokenizer_tokens
        if missing_tokens:
            raise ValueError(
                "Checkpoint tokenizer is missing structural tokens: "
                f"{sorted(missing_tokens)}"
            )

    @property
    def max_length(self) -> int:
        tokenizer_limit = int(self.tokenizer.model_max_length)
        model_limit = int(self.transformer_model.config.max_position_embeddings)
        return min(tokenizer_limit, model_limit)

    def classify(
        self,
        items,
        probabilities=False,
        importances=False,
        importance_cutoff=0.15,
        background_dataset=None,
    ):
        """Classify commit-message/diff dictionaries."""
        del importance_cutoff, background_dataset
        if importances:
            raise ValueError("Transformer feature importances are not supported")

        if not isinstance(items, list):
            items = [items]
        if not items:
            return np.empty((0, 2)) if probabilities else np.empty((0,), dtype=int)

        prompts = [
            build_model_input(item.get("commit_message"), item["diff"])
            for item in items
        ]
        encoded = self.tokenizer(
            prompts,
            truncation=True,
            max_length=self.max_length,
            padding=True,
            return_tensors="pt",
        )

        import torch

        with torch.inference_mode():
            logits = self.transformer_model(**encoded).logits.float()
            class_probabilities = torch.softmax(logits, dim=-1).cpu().numpy()

        if probabilities:
            return class_probabilities
        return class_probabilities.argmax(axis=-1)

    def get_extra_data(self) -> dict[str, Any]:
        return {
            "model_name": MODEL_NAME,
            "model_version": self.model_metadata.get("model_version"),
            "max_length": self.max_length,
            "calibrated": False,
        }
