# -*- coding: utf-8 -*-
import logging

from libmozevent import taskcluster_config
from libmozevent.bus import MessageBus
from libmozevent.monitoring import Monitoring
from libmozevent.phabricator import (
    PhabricatorActions,
    PhabricatorBuild,
    PhabricatorBuildState,
)
from libmozevent.utils import run_tasks
from libmozevent.web import WebServer

from bugbug_events import MONITORING_PERIOD, QUEUE_MONITORING, QUEUE_WEB_BUILDS

logger = logging.getLogger(__name__)


class BugBug(PhabricatorActions):
    """
    Trigger a bugbug classification task on new Phabricator builds
    """

    def __init__(self, risk_analysis_reviewers=[], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hooks = taskcluster_config.get_service("hooks")
        self.risk_analysis_reviewers = risk_analysis_reviewers

    def register(self, bus):
        self.bus = bus

    async def run(self):
        """
        BugBug workflow to load all necessary information from Phabricator builds
        received from the webserver
        """
        while True:

            # Receive build from webserver
            build = await self.bus.receive(QUEUE_WEB_BUILDS)
            assert build is not None, "Invalid payload"
            assert isinstance(build, PhabricatorBuild)

            # Update its state
            self.update_state(build)

            if build.state == PhabricatorBuildState.Public:
                # When the build is public, load needed details
                try:
                    self.load_reviewers(build)
                    logger.info("Loaded reviewers", build=str(build))
                except Exception as e:
                    logger.warning(
                        "Failed to load build details", build=str(build), error=str(e)
                    )
                    continue

                # Start risk analysis
                await self.start_risk_analysis(build)

            elif build.state == PhabricatorBuildState.Queued:
                # Requeue when nothing changed for now
                await self.bus.send(QUEUE_WEB_BUILDS, build)

    async def start_risk_analysis(self, build):
        """
        Run risk analysis by triggering a Taskcluster hook
        """
        assert isinstance(build, PhabricatorBuild)
        assert build.state == PhabricatorBuildState.Public
        try:
            if self.should_run_risk_analysis(build):
                task = self.hooks.triggerHook(
                    "project-relman",
                    "bugbug-classify-patch",
                    {"DIFF_ID": build.diff_id},
                )
                task_id = task["status"]["taskId"]
                logger.info("Triggered a new risk analysis task", id=task_id)

                # Send task to monitoring
                await self.bus.send(
                    QUEUE_MONITORING,
                    ("project-relman", "bugbug-classify-patch", task_id),
                )
        except Exception as e:
            logger.error("Failed to trigger risk analysis task", error=str(e))

    def should_run_risk_analysis(self, build):
        """
        Check if we should trigger a risk analysis for this revision:
        * when the revision is being reviewed by one of some specific reviewers
        """
        usernames = set(
            [reviewer["fields"]["username"] for reviewer in build.reviewers]
        )
        return len(usernames.intersection(self.risk_analysis_reviewers)) > 0


class Events(object):
    """
    Listen to HTTP notifications from phabricator and trigger new try jobs
    """

    def __init__(self, cache_root):
        # Create message bus shared amongst processes
        self.bus = MessageBus()

        self.workflow = BugBug(
            api_key=taskcluster_config.secrets["PHABRICATOR"]["api_key"],
            url=taskcluster_config.secrets["PHABRICATOR"]["url"],
            risk_analysis_reviewers=taskcluster_config.secrets.get(
                "risk_analysis_reviewers", []
            ),
        )
        self.workflow.register(self.bus)

        # Create web server
        self.webserver = WebServer(QUEUE_WEB_BUILDS)
        self.webserver.register(self.bus)

        # Setup monitoring for newly created tasks
        self.monitoring = Monitoring(
            QUEUE_MONITORING, taskcluster_config.secrets["admins"], MONITORING_PERIOD
        )
        self.monitoring.register(self.bus)

    def run(self):
        consumers = [
            # BugBug main workflow
            self.workflow.run(),
            # Add monitoring task
            self.monitoring.run(),
        ]

        # Start the web server in its own process
        self.webserver.start()

        # Run all tasks concurrently
        run_tasks(consumers)

        # Stop the webserver when other async processes are stopped
        self.webserver.stop()
