# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import json
import os
import tomllib

DEPLOY_METADATA_PATH = os.path.join(os.path.dirname(__file__), "deploy_metadata.toml")


def get_prod_version(metadata_path: str = DEPLOY_METADATA_PATH) -> str:
    with open(metadata_path, "rb") as f:
        metadata = tomllib.load(f)

    return metadata["current_prod_version"].removeprefix("v")


def resolve_tags(image_tag: str | None, prod_version: str) -> tuple[str, list[str]]:
    """Return the Docker Hub tag to pull and the release tags to push it as.

    Without an image tag (prod version bump), the prod image is promoted.
    With an image tag (pipeline run), the image is released to stage, and also
    to prod if it is the current prod version (retraining of a promoted version).
    """
    prod_tag = f"v{prod_version}"

    if image_tag is None:
        return prod_tag, [f"{prod_tag}-prod"]

    image_tag = f"v{image_tag.removeprefix('v')}"
    release_tags = [image_tag]
    if image_tag == prod_tag:
        release_tags.append(f"{prod_tag}-prod")

    return image_tag, release_tags


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve the release tags to push, in GITHUB_OUTPUT format."
    )
    parser.add_argument(
        "--image-tag",
        help=(
            "Docker Hub tag dispatched by the data pipeline; released to stage, and to "
            "prod too if it matches current_prod_version. If omitted (prod version "
            "bump), only current_prod_version is released to prod."
        ),
    )
    parser.add_argument("--metadata", default=DEPLOY_METADATA_PATH)
    args = parser.parse_args()

    source_tag, release_tags = resolve_tags(
        args.image_tag, get_prod_version(args.metadata)
    )

    print(f"source_tag={source_tag}")
    print(f"release_tags={json.dumps(release_tags)}")


if __name__ == "__main__":
    main()
