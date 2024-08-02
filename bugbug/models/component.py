# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from collections import Counter
from datetime import datetime, timezone

import dateutil.parser
import xgboost
from dateutil.relativedelta import relativedelta
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.bugzilla import get_product_component_count
from bugbug.model import BugModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ComponentModel(BugModel):
    PRODUCTS = {
        "Core",
        "External Software Affecting Firefox",
        "DevTools",
        "Fenix",
        "Firefox",
        "Toolkit",
        "WebExtensions",
        "Firefox Build System",
    }

    PRODUCT_COMPONENTS = {
        "Cloud Services": {"Server: Firefox Accounts"},
    }

    CONFLATED_COMPONENTS = [
        "Core::Audio/Video",
        "Core::DOM",
        "Core::Graphics",
        "Core::IPC",
        "Core::JavaScript",
        "Core::Layout",
        "Core::Networking",
        "Core::Print",
        "Core::WebRTC",
        "Toolkit::Password Manager",
        "DevTools",
        "External Software Affecting Firefox",
        "WebExtensions",
        "Firefox Build System",
        "Fenix",
    ]

    CONFLATED_COMPONENTS_MAPPING = {
        "Core::DOM": "Core::DOM: Core & HTML",
        "Core::JavaScript": "Core::JavaScript Engine",
        "Core::Print": "Core::Printing: Output",
        "DevTools": "DevTools::General",
        "External Software Affecting Firefox": "External Software Affecting Firefox::Other",
        "WebExtensions": "WebExtensions::Untriaged",
        "Firefox Build System": "Firefox Build System::General",
        "Fenix": "Fenix::General",
    }

    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        self.cross_validation_enabled = False
        self.calculate_importance = False

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.Severity(),
            bug_features.Keywords(),
            bug_features.HasCrashSignature(),
            bug_features.HasURL(),
            bug_features.HasW3CURL(),
            bug_features.HasGithubURL(),
            bug_features.Whiteboard(),
            bug_features.Patches(),
            bug_features.Landings(),
        ]

        cleanup_functions = [
            feature_cleanup.fileref(),
            feature_cleanup.url(),
            feature_cleanup.synonyms(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "bug_extractor",
                    bug_features.BugExtractor(
                        feature_extractors, cleanup_functions, rollback=True
                    ),
                ),
            ]
        )

        self.clf = Pipeline(
            [
                (
                    "union",
                    ColumnTransformer(
                        [
                            ("data", DictVectorizer(), "data"),
                            ("title", self.text_vectorizer(min_df=0.0001), "title"),
                            (
                                "comments",
                                self.text_vectorizer(min_df=0.0001),
                                "comments",
                            ),
                        ]
                    ),
                ),
                (
                    "estimator",
                    xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count()),
                ),
            ]
        )

        self.CONFLATED_COMPONENTS_INVERSE_MAPPING = {
            v: k for k, v in self.CONFLATED_COMPONENTS_MAPPING.items()
        }

    def filter_component(self, product, component):
        if product == "Fenix" or product == "GeckoView":
            return "Fenix"

        full_comp = f"{product}::{component}"

        if full_comp in self.CONFLATED_COMPONENTS_INVERSE_MAPPING:
            return self.CONFLATED_COMPONENTS_INVERSE_MAPPING[full_comp]

        if (product, component) in self.meaningful_product_components:
            return full_comp

        for conflated_component in self.CONFLATED_COMPONENTS:
            if full_comp.startswith(conflated_component):
                return conflated_component

        return None

    def get_labels(self):
        product_components = {}
        for bug_data in bugzilla.get_bugs():
            if dateutil.parser.parse(bug_data["creation_time"]) < datetime.now(
                timezone.utc
            ) - relativedelta(years=2):
                continue

            product_components[bug_data["id"]] = (
                bug_data["product"],
                bug_data["component"],
            )

        self.meaningful_product_components = self.get_meaningful_product_components(
            (
                (product, component)
                for product, component in product_components.values()
                if self.is_meaningful(product, component)
            )
        )

        classes = {}
        for bug_id, (product, component) in product_components.items():
            component = self.filter_component(product, component)

            if component:
                classes[bug_id] = component

        component_counts = Counter(classes.values()).most_common()
        top_components = set(component for component, count in component_counts)

        logger.info("%d components", len(top_components))
        for component, count in component_counts:
            logger.info("%s: %d", component, count)

        # Assert there is at least one bug for each conflated component.
        for conflated_component in self.CONFLATED_COMPONENTS:
            assert any(
                conflated_component == component
                for component, count in component_counts
            ), f"There should be at least one bug matching {conflated_component}*"

        # Assert there is at least one bug for each component the conflated components are mapped to.
        for conflated_component_mapping in self.CONFLATED_COMPONENTS_MAPPING.values():
            assert any(
                conflated_component_mapping == f"{product}::{component}"
                for product, component in product_components.values()
            ), f"There should be at least one bug in {conflated_component_mapping}"

        # Assert all conflated components are either in conflated_components_mapping or exist as components.
        for conflated_component in self.CONFLATED_COMPONENTS:
            assert conflated_component in self.CONFLATED_COMPONENTS_MAPPING or any(
                conflated_component == f"{product}::{component}"
                for product, component in product_components.values()
            ), f"It should be possible to map {conflated_component}"

        classes = {
            bug_id: component
            for bug_id, component in classes.items()
            if component in top_components
        }

        return classes, set(classes.values())

    def is_meaningful(self, product, component):
        return (
            product in self.PRODUCTS
            and component not in ["General", "Untriaged", "Foxfooding"]
        ) or (
            product in self.PRODUCT_COMPONENTS
            and component in self.PRODUCT_COMPONENTS[product]
        )

    def get_meaningful_product_components(self, full_comp_tuples, threshold_ratio=100):
        """Filter out components which does not have more than 1% of the most common component.

        Returns:
            a set of tuples which have at least 1% of the most common tuple
        """
        product_component_counts = Counter(full_comp_tuples).most_common()

        max_count = product_component_counts[0][1]
        threshold = max_count / threshold_ratio

        active_product_components = bugzilla.get_active_product_components(
            list(self.PRODUCTS) + list(self.PRODUCT_COMPONENTS)
        )

        return set(
            product_component
            for product_component, count in product_component_counts
            if count > threshold and product_component in active_product_components
        )

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()

    def check(self):
        success = super().check()

        # Get the number of bugs per full component to fasten up the check
        bugs_number = get_product_component_count()

        # Check number 1, check that the most meaningful product components
        # still have at least a bug in this component. If the check is failing
        # that could mean that:
        # - A component has been renamed / removed
        # - A component is not used anymore by developers

        for product, component in self.meaningful_product_components:
            full_comp = f"{product}::{component}"

            if full_comp not in bugs_number.keys():
                logger.warning(
                    "Component %r of product %r doesn't exists, failure",
                    component,
                    product,
                )
                success = False

            elif bugs_number[full_comp] <= 0:
                logger.warning(
                    "Component %r of product %r have 0 bugs or less in it, failure",
                    component,
                    product,
                )
                success = False

        # Check number 2, check that conflated components in
        # CONFLATED_COMPONENTS match at least one component which has more
        # than 0 bugs

        for conflated_component in self.CONFLATED_COMPONENTS:
            matching_components = [
                full_comp
                for full_comp in bugs_number
                if full_comp.startswith(conflated_component)
            ]

            if not matching_components:
                logger.warning("%s doesn't match any component", conflated_component)
                success = False
                continue

            matching_components_values = [
                bugs_number[full_comp]
                for full_comp in matching_components
                if bugs_number[full_comp] > 0
            ]

            if not matching_components_values:
                logger.warning(
                    "%s should match at least one component with more than 0 bugs",
                    conflated_component,
                )
                success = False

        # Check number 3, check that values of CONFLATED_COMPONENTS_MAPPING
        # still exist as components and have more than 0 bugs

        for full_comp in self.CONFLATED_COMPONENTS_MAPPING.values():
            if full_comp not in bugs_number:
                logger.warning(
                    "%s from conflated component mapping doesn't exists, failure",
                    full_comp,
                )
                success = False
            elif bugs_number[full_comp] <= 0:
                logger.warning(
                    "%s from conflated component mapping have less than 1 bug, failure",
                    full_comp,
                )
                success = False

        # Check number 4, conflated components in CONFLATED_COMPONENTS either
        # exist as components or are in CONFLATED_COMPONENTS_MAPPING

        for conflated_component in self.CONFLATED_COMPONENTS:
            in_mapping = conflated_component in self.CONFLATED_COMPONENTS_MAPPING

            matching_components = [
                full_comp
                for full_comp in bugs_number
                if full_comp.startswith(conflated_component)
            ]

            if not (matching_components or in_mapping):
                logger.warning("It should be possible to map %s", conflated_component)
                success = False
                continue

        # Check number 5, there is no component with many bugs that is not in
        # meaningful_product_components

        # Recompute the meaningful components

        def generate_meaningful_tuples():
            for full_comp, count in bugs_number.items():
                product, component = full_comp.split("::", 1)

                if not self.is_meaningful(product, component):
                    continue

                if count > 0:
                    for i in range(count):
                        yield (product, component)

        meaningful_product_components = self.get_meaningful_product_components(
            generate_meaningful_tuples(), threshold_ratio=10
        )

        if not meaningful_product_components.issubset(
            self.meaningful_product_components
        ):
            logger.warning("Meaningful product components mismatch")

            new_meaningful_product_components = (
                meaningful_product_components.difference(
                    self.meaningful_product_components
                )
            )
            logger.info(
                "New meaningful product components %r",
                new_meaningful_product_components,
            )

            success = False

        return success

    def get_extra_data(self):
        return {"conflated_components_mapping": self.CONFLATED_COMPONENTS_MAPPING}
