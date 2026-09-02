# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Inference-only perf regression predictor."""

from __future__ import annotations

import json
import re
from email import policy
from email.parser import Parser
from pathlib import Path
from typing import Any

import numpy as np

from bugbug.model import Model

MODEL_NAME = "Perf Regression Predictor"
MODEL_IDENTIFIER = "perfregressionpredictor"
DEFAULT_MODEL_DIRECTORY = f"{MODEL_IDENTIFIER}model"
POSITIVE_CLASS_ID = 1


class CommitMessageCleaner:
    """Remove common noisy prefixes from a commit message.

    This intentionally mirrors the preprocessing used to prepare the model's
    training data.
    """

    def __init__(self, clean_subject_only: bool = True) -> None:
        self.clean_subject_only = clean_subject_only
        prefix = r"(?:\[[^\]]+\]|\([^)]+\)|bug\s*#?\s*\d+\b)"
        self.prefix_pattern = re.compile(
            rf"^\s*(?:{prefix}\s*(?:[-–—:.,]\s*)?)+",
            re.IGNORECASE,
        )

    def _clean_subject(self, subject: str) -> str:
        return self.prefix_pattern.sub("", subject, count=1).strip()

    def __call__(self, commit_message: str | None) -> str:
        if commit_message is None:
            return ""

        lines = str(commit_message).splitlines()
        if not lines:
            return ""

        if self.clean_subject_only:
            for index, line in enumerate(lines):
                if line.strip():
                    lines[index] = self._clean_subject(line)
                    break
            return "\n".join(lines).strip("\n")

        cleaned_lines = [
            self._clean_subject(line) if index == 0 else line
            for index, line in enumerate(lines)
        ]
        return "\n".join(cleaned_lines).strip("\n")


class PatchCommitMessageExtractor:
    """Extract a message from Git format-patch or Mercurial export content.

    A Mercurial ``hg export`` (or Git ``format-patch``) bundles the commit
    message together with the diff, so we need to peel the message off before
    feeding the diff to the structuring logic.
    """

    def __init__(self) -> None:
        self.subject_pattern = re.compile(r"^Subject:", re.MULTILINE)
        self.body_end_pattern = re.compile(r"^---\s*$|^diff --git ", re.MULTILINE)

    def __call__(self, patch: str) -> str | None:
        if patch.startswith("# HG changeset patch"):
            message_lines: list[str] = []
            metadata_finished = False
            for line in patch.splitlines()[1:]:
                if not metadata_finished and (line.startswith("#") or not line.strip()):
                    continue
                metadata_finished = True
                if line.startswith(("diff -r ", "diff --git ")):
                    break
                message_lines.append(line)
            message = "\n".join(message_lines).strip()
            return message or None

        if self.subject_pattern.search(patch):
            email_message = Parser(policy=policy.default).parsestr(patch)
            subject = str(email_message.get("Subject", "")).strip()
            body = email_message.get_payload()
            if not isinstance(body, str):
                body = ""
            body = self.body_end_pattern.split(body, maxsplit=1)[0].strip()
            message = "\n\n".join(part for part in (subject, body) if part)
            return message or None

        return None


class DiffStructurer:
    """Convert a Git or Mercurial diff to the model's structured format.

    The input may be a bare diff or a full ``hg export`` / ``git format-patch``
    payload that still carries the commit-message header; any preamble before
    the first ``diff`` header is ignored.
    """

    def __init__(self) -> None:
        self.git_header_pattern = re.compile(r"diff --git a/(.+?) b/(.+)")
        self._reset()

    def _reset(self) -> None:
        self.output: list[str] = []
        self.current_file: str | None = None
        self.current_block_type: str | None = None
        self.current_block_lines: list[str] = []
        self.pending_binary_status: str | None = None
        self.rename_from: str | None = None
        self.rename_to: str | None = None
        self.pending_rename = False

    def _start_file(self, file_name: str) -> None:
        self.current_file = file_name
        self.output.extend(("<FILE>", f"  {file_name}"))

    def _flush_block(self) -> None:
        if self.current_block_type and self.current_block_lines:
            self.output.append(f"  <{self.current_block_type.upper()}>")
            self.output.extend(f"      {line}" for line in self.current_block_lines)
            self.output.append(f"  </{self.current_block_type.upper()}>")
        self.current_block_type = None
        self.current_block_lines = []

    def _flush_file(self) -> None:
        if self.current_file:
            self._flush_block()
            if self.pending_rename and self.rename_from and self.rename_to:
                self.output.append(f"  File renamed from {self.rename_from}.")
            elif self.pending_binary_status:
                self.output.append(f"  Binary file {self.pending_binary_status}.")
            self.output.append("</FILE>")

        self.current_file = None
        self.pending_binary_status = None
        self.rename_from = None
        self.rename_to = None
        self.pending_rename = False

    def __call__(self, diff_string: str) -> str:
        self._reset()
        started = False

        for line in diff_string.strip().splitlines():
            if not started:
                if line.startswith(("diff -r", "diff --git")):
                    started = True
                else:
                    continue

            if line.startswith("diff -r"):
                self._flush_file()
                parts = line.split()
                if len(parts) >= 4:
                    self._start_file(parts[-1])
                continue

            if line.startswith("diff --git"):
                self._flush_file()
                match = self.git_header_pattern.match(line)
                if match:
                    self._start_file(match.group(2))
            elif line.startswith("rename from "):
                self.rename_from = line[len("rename from ") :].strip()
                self.pending_rename = True
            elif line.startswith("rename to "):
                self.rename_to = line[len("rename to ") :].strip()
                if not self.current_file:
                    self._start_file(self.rename_to)
            elif line.startswith("--- "):
                pass
            elif line.startswith("+++ "):
                pass
            elif line.startswith("Binary files "):
                self._flush_block()
                self.pending_binary_status = "changed"
                self._flush_file()
            elif line.startswith("@@"):
                self._flush_block()
            elif line.startswith("-"):
                if self.current_block_type != "REMOVED":
                    self._flush_block()
                    self.current_block_type = "REMOVED"
                self.current_block_lines.append(line[1:].rstrip())
            elif line.startswith("+"):
                if self.current_block_type != "ADDED":
                    self._flush_block()
                    self.current_block_type = "ADDED"
                self.current_block_lines.append(line[1:].rstrip())
            else:
                self._flush_block()

        self._flush_file()
        return "\n".join(self.output)


class PerfRegressionPredictorModel(Model):
    """Hugging Face sequence classifier used only for inference."""

    training_supported = False

    # Trained outside bugbug, so it is not in the Taskcluster index.
    # When retraining, upload to a new versioned path instead of overwriting.
    artifact_url = (
        "https://storage.googleapis.com/models-dump-public/"
        "perf-regression-predictor-v1.tar.zst"
    )

    def __init__(self, tokenizer: Any = None, transformer_model: Any = None) -> None:
        super().__init__()
        self.tokenizer = tokenizer
        self.transformer_model = transformer_model
        self.calculate_importance = False
        self.model_directory: str | None = None
        self.model_metadata: dict[str, Any] = {}
        self.commit_message_cleaner = CommitMessageCleaner()
        self.diff_structurer = DiffStructurer()

    def build_model_input(self, commit_message: str | None, raw_diff: str) -> str:
        """Build the exact text representation consumed during training."""
        return "\n".join(
            (
                "<COMMIT_MESSAGE>",
                self.commit_message_cleaner(commit_message),
                "</COMMIT_MESSAGE>",
                self.diff_structurer(raw_diff),
            )
        )

    @classmethod
    def load(cls, model_directory: str) -> "PerfRegressionPredictorModel":
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
            raise ValueError("Perf Regression Predictor requires exactly two labels")

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
            self.build_model_input(item.get("commit_message"), item["diff"])
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
