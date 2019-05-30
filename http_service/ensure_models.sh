#!/bin/bash
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Download models and check that models can be correctly be loaded. Can be
# disabled by passing CHECK_MODELS=0 as an environment variable

set -eox pipefail

CURRENT_DIR=$(dirname "$0")

if [ "$CHECK_MODELS" == "0" ]; then
    echo "Skipping downloading and checking models!"
    exit 0;
fi

python "$CURRENT_DIR/download_models.py"

python "$CURRENT_DIR/check_models.py"
