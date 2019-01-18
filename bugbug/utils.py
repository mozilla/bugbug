# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np


def numpy_to_dict(array):
    return {name: array[name].squeeze(axis=1) for name in array.dtype.names}
