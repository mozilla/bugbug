# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os

import pg8000
import sqlalchemy
from google.cloud.sql.connector import Connector, IPTypes


def init_connection_pool_engine() -> sqlalchemy.engine.base.Engine:
    """Initializes a connection pool for a Cloud SQL instance of Postgres.

    Uses the Cloud SQL Python Connector package.
    """
    connector = Connector()
    credentials = json.loads(os.environ["DATABASE_CREDENTIALS"])
    ip_type = IPTypes.PRIVATE if credentials["private_ip"] else IPTypes.PUBLIC

    def getconn() -> pg8000.dbapi.Connection:
        conn: pg8000.dbapi.Connection = connector.connect(
            credentials["instance_connection_name"],
            "pg8000",
            user=credentials["db_user"],
            password=credentials["db_password"],
            db=credentials["db_name"],
            ip_type=ip_type,
        )
        return conn

    engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=getconn,
        pool_size=5,
        max_overflow=2,
        pool_timeout=30,  # 30 seconds
        pool_recycle=1800,  # 30 minutes
    )
    return engine
