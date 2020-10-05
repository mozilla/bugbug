# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from libmozdata.phabricator import PhabricatorAPI

DEPLOYMENT_URL = None
API_KEY = None

PROJECTS = {
    "PHID-PROJ-h7y4cs7m2o67iczw62pp": "testing-approved",
    "PHID-PROJ-e4fcjngxcws3egiecv3r": "testing-exception-elsewhere",
    "PHID-PROJ-iciyosoekrczpf2a4emw": "testing-exception-other",
    "PHID-PROJ-zjipshabawolpkllehvg": "testing-exception-ui",
    "PHID-PROJ-cspmf33ku3kjaqtuvs7g": "testing-exception-unchanged",
}


def set_api_key(url, api_key):
    global DEPLOYMENT_URL, API_KEY
    DEPLOYMENT_URL = url
    API_KEY = api_key


def get(rev_id):
    assert DEPLOYMENT_URL is not None
    assert API_KEY is not None

    phabricator_api = PhabricatorAPI(API_KEY, DEPLOYMENT_URL)
    return phabricator_api.load_revision(rev_id=rev_id, attachments={"projects": True})
