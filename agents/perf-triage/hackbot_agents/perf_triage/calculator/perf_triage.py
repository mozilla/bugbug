#!/usr/bin/env python3
"""
Performance Triage Impact Calculator - Command Line Version

This tool calculates the performance impact score for Firefox bugs based on
various factors like platform, browser impact, site impact, configuration, etc.

Web version: https://mozilla.github.io/perf-triage/calculator.html
"""

import json
import sys
import os
import json
import argparse
from urllib.parse import urlencode
from typing import Dict, List, Set, Optional, Tuple


# ============================================================================
# CONFIGURATION: All factors and constants in one place
# ============================================================================

# Platform multipliers (group: affects-os, max multiplier: 4)
PLATFORMS = {
    'windows': {'multiplier': 3, 'group': 'affects-os', 'group_max_multiplier': 4},
    'macos': {'multiplier': 3, 'group': 'affects-os', 'group_max_multiplier': 4},
    'linux': {'multiplier': 1, 'group': 'affects-os', 'group_max_multiplier': 4},
    'android': {'multiplier': 3, 'group': 'affects-os', 'group_max_multiplier': 4},
}

# Browser impact (radio group: affects-browser)
BROWSER_IMPACT = {
    'minor': {'basescore': 0, 'keywords': []},
    'startup': {'basescore': 5, 'keywords': ['perf:startup']},
    'shutdown': {'basescore': 2, 'keywords': []},
    'jank': {'basescore': 5, 'keywords': ['perf:responsiveness']},
    'major': {'basescore': 10, 'keywords': ['perf:responsiveness']},
}

# Site impact (radio group: affects-site)
SITE_IMPACT = {
    'minor': {'basescore': 0, 'keywords': []},
    'noticeable': {'basescore': 2, 'keywords': ['perf:responsiveness']},
    'major': {'basescore': 3, 'keywords': ['perf:responsiveness']},
}

# Configuration scenario (radio group: affects-scenario)
CONFIGURATION = {
    'general': {'multiplier': 1},
    'common': {'multiplier': 0.5},
    'rare': {'multiplier': 0.3},
}

# Page load impact (radio group: pageload-impact)
PAGELOAD_IMPACT = {
    'none': {'basescore': 0, 'keywords': []},
    'some': {'basescore': 5, 'keywords': ['perf:pageload']},
    'severe': {'basescore': 10, 'keywords': ['perf:pageload']},
}

# Websites affected (radio group: pages-affected)
#
# Tier definitions are grounded in real Firefox pageload traffic (trailing
# 7 days) from STMO queries 109742 (desktop) + 130316 (mobile). The ranked
# domain lists live in top_sites.json next to this script and are kept
# SEPARATE per platform — a site's popularity differs on desktop vs Android.
#
#   major  = a top-50 site by pageload share (rank 1-50).
#   common = a top-5000 site (rank 51-5000).
#   rare   = everything else (rank 5001+).
#
# Desktop platforms (windows/macos/linux) are classified against the desktop
# list; android is classified against the mobile list. When a bug touches
# both, the site takes the highest tier it reaches on either relevant list.
#
# Use --classify-domain <domain> [--platforms ...] to look up which tier a
# specific site falls into before choosing --pages-affected.
PAGES_AFFECTED = {
    'rare': {'multiplier': 0.3, 'keywords': []},
    'common': {'multiplier': 1, 'keywords': []},
    'major': {'multiplier': 5, 'keywords': ['top50']},
    'unclear': {'multiplier': 1, 'keywords': []},
}

# Resource impact (radio group: resource-impact)
RESOURCE_IMPACT = {
    'none': {'basescore': 0, 'keywords': []},
    'some': {'basescore': 0.4, 'keywords': ['perf:resource-use']},
    'severe': {'basescore': 2, 'keywords': ['perf:resource-use']},
}

# Reproducibility (radio group: reproducible)
REPRODUCIBLE = {
    'reproduced': {'multiplier': 2, 'keywords': ['reproducible']},
    'not-reproduced': {'multiplier': 0.5, 'keywords': []},
    'not-attempted': {'multiplier': 1, 'keywords': []},
}

# Additional factors (checkboxes)
ADDITIONAL_FACTORS = {
    'animation': {'basescore': 1, 'keywords': ['perf:animation']},
    'multiple-sites': {'multiplier': 2, 'keywords': []},
    'multiple-reporters': {'multiplier': 1.5, 'keywords': []},
    'chrome': {'multiplier': 0.3, 'keywords': []},
}

# Impact thresholds
IMPACT_THRESHOLDS = {
    'none': (0, 0),
    'low': (0, 10),
    'medium': (10, 40),
    'high': (40, float('inf')),
}

# Labels for output
LABELS = {
    'platform-windows': 'Windows',
    'platform-macos': 'macOS',
    'platform-linux': 'Linux',
    'platform-android': 'Android',
    'browser-minor': 'Minor',
    'browser-startup': 'Causes noticeable startup delay',
    'browser-shutdown': 'Causes noticeable shutdown delay',
    'browser-jank': 'Causes noticeable jank',
    'browser-major': 'Renders browser effectively unusable',
    'site-minor': 'Minor',
    'site-noticeable': 'Causes noticeable jank',
    'site-major': 'Renders site effectively unusable',
    'scenario-general': 'General',
    'scenario-common': 'Specific but common',
    'scenario-rare': 'Rare',
    'pageload-none': 'None',
    'pageload-some': 'Some',
    'pageload-severe': 'Severe',
    'pages-rare': 'Rare',
    'pages-common': 'Common',
    'pages-major': 'Major',
    'pages-unclear': 'Unclear',
    'resource-none': 'None',
    'resource-some': 'Some',
    'resource-severe': 'Severe',
    'reproducible-reproduced': 'Reproduced',
    'reproducible-not-reproduced': 'Could not reproduce',
    'reproducible-not-attempted': 'Not attempted',
    'animation': 'Affects animation smoothness',
    'multiple-sites': 'Bug affects multiple sites',
    'multiple-reporters': 'Multiple reporters',
    'chrome': 'Reproduces in Chrome',
}

SECTION_HEADERS = {
    'platforms': 'Platforms:',
    'browser': 'Impact on browser:',
    'site': 'Impact on site:',
    'configuration': 'Configuration:',
    'pageload': 'Page load impact:',
    'pages': 'Websites affected:',
    'resource': 'Resource impact:',
    'reproducible': 'Reproducibility:',
}


# ============================================================================
# Site tier classification (data-driven, from top_sites.json)
# ============================================================================

# Which top_sites.json list applies to each platform.
PLATFORM_TO_LIST = {
    'windows': 'desktop',
    'macos': 'desktop',
    'linux': 'desktop',
    'android': 'mobile',
}

MAJOR_MAX_RANK = 50
COMMON_MAX_RANK = 5000

TOP_SITES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'top_sites.json')


def _normalize_domain(domain: str) -> str:
    """Reduce a URL or host to the base domain used in top_sites.json."""
    d = domain.strip().lower()
    if '://' in d:
        d = d.split('://', 1)[1]
    d = d.split('/', 1)[0]      # strip path
    d = d.split(':', 1)[0]      # strip port
    if d.startswith('www.'):
        d = d[4:]
    return d


def load_top_sites() -> Optional[dict]:
    """Load the ranked top-sites data, or None if unavailable."""
    try:
        with open(TOP_SITES_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _rank_to_tier(rank: Optional[int]) -> str:
    if rank is None:
        return 'rare'
    if rank <= MAJOR_MAX_RANK:
        return 'major'
    if rank <= COMMON_MAX_RANK:
        return 'common'
    return 'rare'


def classify_domain(domain: str, platforms: List[str], data: Optional[dict] = None) -> Tuple[str, Dict[str, Optional[int]]]:
    """
    Classify a domain into a pages-affected tier based on real pageload rank.

    Looks the domain up against each platform-relevant list (desktop for
    windows/macos/linux, mobile for android). The returned tier is the
    highest tier the site reaches on any relevant list. If no platforms are
    given, both lists are consulted.

    Returns (tier, {list_name: rank or None}).
    """
    if data is None:
        data = load_top_sites()
    norm = _normalize_domain(domain)

    lists = {PLATFORM_TO_LIST[p] for p in platforms if p in PLATFORM_TO_LIST}
    if not lists:
        lists = {'desktop', 'mobile'}

    ranks: Dict[str, Optional[int]] = {}
    tier_order = {'rare': 0, 'common': 1, 'major': 2}
    best = 'rare'
    for name in sorted(lists):
        rank = None
        if data and name in data:
            table = data[name].get('rank', {})
            # Lists are keyed by base domain; walk up from the full host
            # (en.m.wikipedia.org -> m.wikipedia.org -> wikipedia.org).
            labels = norm.split('.')
            for i in range(len(labels) - 1):
                rank = table.get('.'.join(labels[i:]))
                if rank is not None:
                    break
            if isinstance(rank, str):
                rank = int(rank)
        ranks[name] = rank
        tier = _rank_to_tier(rank)
        if tier_order[tier] > tier_order[best]:
            best = tier
    return best, ranks


# ============================================================================
# Calculator Logic
# ============================================================================

class Group:
    """Represents a scoring group with optional max basescore and multiplier."""

    def __init__(self, max_basescore: Optional[float] = None, max_multiplier: Optional[float] = None):
        self.basescore = 0
        self.max_basescore = max_basescore
        self.multiplier = 1
        self.max_multiplier = max_multiplier

    def get_basescore(self) -> float:
        if self.max_basescore is not None and self.basescore > self.max_basescore:
            return self.max_basescore
        return self.basescore

    def get_multiplier(self) -> float:
        if self.max_multiplier is not None and self.multiplier > self.max_multiplier:
            return self.max_multiplier
        return self.multiplier


def calculate_impact(
    platforms: List[str],
    browser_impact: str,
    site_impact: str,
    configuration: str,
    pageload_impact: str,
    pages_affected: str,
    resource_impact: str,
    additional_factors: List[str],
    reproducible: str = 'not-attempted'
) -> Tuple[float, str, Set[str], Dict[str, List[str]]]:
    """
    Calculate the performance impact score based on selected factors.

    Returns:
        Tuple of (score, impact_level, keywords, selections)
    """

    # Initialize groups
    groups: Dict[str, Group] = {}

    # Track selections for output
    selections = {
        'platforms': [],
        'browser': [],
        'site': [],
        'configuration': [],
        'pageload': [],
        'pages': [],
        'resource': [],
        'reproducible': [],
        'additional': [],
    }

    # Initialize global scores
    global_basescore = 0
    global_multiplier = 1
    keywords: Set[str] = set()

    # Process platforms (grouped with max multiplier of 4)
    if platforms:
        group_name = 'affects-os'
        if group_name not in groups:
            groups[group_name] = Group(max_multiplier=4)

        for platform in platforms:
            if platform in PLATFORMS:
                platform_data = PLATFORMS[platform]
                groups[group_name].multiplier *= platform_data['multiplier']
                selections['platforms'].append(f'platform-{platform}')

    # Process browser impact
    if browser_impact in BROWSER_IMPACT:
        data = BROWSER_IMPACT[browser_impact]
        global_basescore += data['basescore']
        keywords.update(data['keywords'])
        selections['browser'].append(f'browser-{browser_impact}')

    # Process site impact
    if site_impact in SITE_IMPACT:
        data = SITE_IMPACT[site_impact]
        global_basescore += data['basescore']
        keywords.update(data['keywords'])
        selections['site'].append(f'site-{site_impact}')

    # Process configuration
    if configuration in CONFIGURATION:
        data = CONFIGURATION[configuration]
        global_multiplier *= data['multiplier']
        selections['configuration'].append(f'scenario-{configuration}')

    # Process pageload impact
    if pageload_impact in PAGELOAD_IMPACT:
        data = PAGELOAD_IMPACT[pageload_impact]
        global_basescore += data['basescore']
        keywords.update(data['keywords'])
        selections['pageload'].append(f'pageload-{pageload_impact}')

    # Process pages affected
    if pages_affected in PAGES_AFFECTED:
        data = PAGES_AFFECTED[pages_affected]
        global_multiplier *= data['multiplier']
        keywords.update(data['keywords'])
        selections['pages'].append(f'pages-{pages_affected}')

    # Process resource impact
    if resource_impact in RESOURCE_IMPACT:
        data = RESOURCE_IMPACT[resource_impact]
        global_basescore += data['basescore']
        keywords.update(data['keywords'])
        selections['resource'].append(f'resource-{resource_impact}')

    # Process reproducibility
    if reproducible in REPRODUCIBLE:
        data = REPRODUCIBLE[reproducible]
        global_multiplier *= data['multiplier']
        keywords.update(data['keywords'])
        selections['reproducible'].append(f'reproducible-{reproducible}')

    # Process additional factors
    for factor in additional_factors:
        if factor in ADDITIONAL_FACTORS:
            data = ADDITIONAL_FACTORS[factor]
            if 'basescore' in data:
                global_basescore += data['basescore']
            if 'multiplier' in data:
                global_multiplier *= data['multiplier']
            keywords.update(data['keywords'])
            selections['additional'].append(factor)

    # Apply group caps and add to global scores
    for group in groups.values():
        global_basescore += group.get_basescore()
        global_multiplier *= group.get_multiplier()

    # Calculate final score
    score = global_basescore * global_multiplier

    # Determine impact level
    impact_level = 'none'
    if score == 0:
        impact_level = 'none'
    elif score < 10:
        impact_level = 'low'
    elif score < 40:
        impact_level = 'medium'
    else:
        impact_level = 'high'

    return score, impact_level, keywords, selections


def generate_url(selections: Dict[str, List[str]]) -> str:
    """Generate a URL to the web calculator with the current selections."""
    params = {}

    # Map internal selections to form field names and values
    # Platforms use name=value format where name is "platform-{os}" and value is "true"
    for item in selections['platforms']:
        params[item] = 'true'

    # Radio groups use name=value format where value is the selection
    # Browser impact: name="affects-browser" value="startup|shutdown|jank|major|minor"
    if selections['browser']:
        browser_item = selections['browser'][0]  # Only one radio selection
        browser_value = browser_item.replace('browser-', '')
        params['affects-browser'] = browser_value

    # Site impact: name="affects-site" value="minor|noticeable|major"
    if selections['site']:
        site_item = selections['site'][0]
        site_value = site_item.replace('site-', '')
        params['affects-site'] = site_value

    # Configuration: name="affects-scenario" value="general|common|rare"
    if selections['configuration']:
        config_item = selections['configuration'][0]
        config_value = config_item.replace('scenario-', '')
        params['affects-scenario'] = config_value

    # Page load impact: name="pageload-impact" value="none|some|severe"
    if selections['pageload']:
        pageload_item = selections['pageload'][0]
        pageload_value = pageload_item.replace('pageload-', '')
        params['pageload-impact'] = pageload_value

    # Pages affected: name="pages-affected" value="rare|common|major|unclear"
    if selections['pages']:
        pages_item = selections['pages'][0]
        pages_value = pages_item.replace('pages-', '')
        params['pages-affected'] = pages_value

    # Resource impact: name="resource-impact" value="none|some|severe"
    if selections['resource']:
        resource_item = selections['resource'][0]
        resource_value = resource_item.replace('resource-', '')
        params['resource-impact'] = resource_value

    # Reproducibility: name="reproducible"
    # value="reproduced|not-reproduced|not-attempted"
    if selections['reproducible']:
        reproducible_item = selections['reproducible'][0]
        params['reproducible'] = reproducible_item.replace('reproducible-', '')

    # Additional factors use name=value format where name is the factor and value is "true"
    for item in selections['additional']:
        params[item] = 'true'

    base_url = 'https://mozilla.github.io/perf-triage/calculator.html'
    if params:
        return f"{base_url}?{urlencode(params)}"
    return base_url


def format_bugzilla_comment(
    score: float,
    impact_level: str,
    keywords: Set[str],
    selections: Dict[str, List[str]],
    command_line: str
) -> str:
    """Format the output as a Bugzilla comment."""

    url = generate_url(selections)

    lines = [
        f"The [Performance Impact Calculator]({url}) has determined this bug's performance impact to be **{impact_level}**. If you'd like to request re-triage, you can reset the Performance Impact flag to \"?\" or needinfo the triage sheriff.",
        "",
    ]

    # Add selections that have an impact on the score
    if selections['platforms']:
        platform_labels = [LABELS[p] for p in selections['platforms']]
        if len(platform_labels) > 1:
            items = ' '.join([f'[x] {label}' for label in platform_labels])
            lines.append(f"{SECTION_HEADERS['platforms']} {items}")
        else:
            lines.append(f"{SECTION_HEADERS['platforms']} {platform_labels[0]}")

    if selections['browser']:
        for sel in selections['browser']:
            if sel != 'browser-minor':  # Skip minor as it has no impact
                lines.append(f"{SECTION_HEADERS['browser']} {LABELS[sel]}")

    if selections['site']:
        for sel in selections['site']:
            if sel != 'site-minor':  # Skip minor as it has no impact
                lines.append(f"{SECTION_HEADERS['site']} {LABELS[sel]}")

    if selections['configuration']:
        for sel in selections['configuration']:
            if sel != 'scenario-general':  # Skip general as it has multiplier of 1
                lines.append(f"{SECTION_HEADERS['configuration']} {LABELS[sel]}")

    if selections['pageload']:
        for sel in selections['pageload']:
            if sel != 'pageload-none':  # Skip none as it has no impact
                lines.append(f"{SECTION_HEADERS['pageload']} {LABELS[sel]}")

    if selections['pages']:
        for sel in selections['pages']:
            # Skip common and unclear as they have a multiplier of 1
            if sel not in ('pages-common', 'pages-unclear'):
                lines.append(f"{SECTION_HEADERS['pages']} {LABELS[sel]}")

    if selections['resource']:
        for sel in selections['resource']:
            if sel != 'resource-none':  # Skip none as it has no impact
                lines.append(f"{SECTION_HEADERS['resource']} {LABELS[sel]}")

    if selections['reproducible']:
        for sel in selections['reproducible']:
            # Skip not-attempted as it has a multiplier of 1
            if sel != 'reproducible-not-attempted':
                lines.append(f"{SECTION_HEADERS['reproducible']} {LABELS[sel]}")

    if selections['additional']:
        for factor in selections['additional']:
            lines.append(f"[x] {LABELS[factor]}")

    lines.extend([
        "",
        f"*Generated by command line: `{command_line}`*",
    ])

    return '\n'.join(lines)


# ============================================================================
# Command Line Interface
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Calculate performance impact score for Firefox bugs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Minimal usage with defaults (minor browser, minor site, general config, etc.)
  %(prog)s --platforms windows

  # Startup delay on major site
  %(prog)s --platforms windows macos --browser-impact startup \\
    --pageload-impact some --pages-affected major --reproducible reproduced

  # Maximum impact scenario
  %(prog)s --platforms windows --browser-impact major --site-impact major \\
    --pageload-impact severe --pages-affected major --resource-impact severe \\
    --animation --reproducible reproduced --multiple-sites
        """
    )

    # Platform options
    parser.add_argument(
        '--platforms',
        nargs='+',
        choices=['windows', 'macos', 'linux', 'android'],
        default=[],
        help='Affected platforms (can specify multiple)'
    )

    # Browser impact (default: minor)
    parser.add_argument(
        '--browser-impact',
        default='minor',
        choices=['minor', 'startup', 'shutdown', 'jank', 'major'],
        help='Impact on browser (default: minor)'
    )

    # Site impact (default: minor)
    parser.add_argument(
        '--site-impact',
        default='minor',
        choices=['minor', 'noticeable', 'major'],
        help='Impact on site (default: minor)'
    )

    # Configuration (default: general)
    parser.add_argument(
        '--configuration',
        default='general',
        choices=['general', 'common', 'rare'],
        help='Configuration scenario (default: general)'
    )

    # Page load impact (default: none)
    parser.add_argument(
        '--pageload-impact',
        default='none',
        choices=['none', 'some', 'severe'],
        help='Page load impact (default: none)'
    )

    # Pages affected (default: common)
    parser.add_argument(
        '--pages-affected',
        default='common',
        choices=['rare', 'common', 'major', 'unclear'],
        help=(
            'Websites affected (default: common). "unclear" is for when the '
            'bug does not say; it scores the same as "common"'
        )
    )

    # Resource impact (default: none)
    parser.add_argument(
        '--resource-impact',
        default='none',
        choices=['none', 'some', 'severe'],
        help='Resource impact (CPU, memory, battery) (default: none)'
    )

    # Additional factors
    parser.add_argument(
        '--animation',
        action='store_true',
        help='Affects animation smoothness'
    )

    parser.add_argument(
        '--reproducible',
        default='not-attempted',
        choices=['reproduced', 'not-reproduced', 'not-attempted'],
        help=(
            'Whether the bug was reproduced locally (default: not-attempted). '
            '"reproduced" doubles the score, "not-reproduced" halves it, '
            '"not-attempted" leaves it unchanged'
        )
    )

    parser.add_argument(
        '--multiple-sites',
        action='store_true',
        help='Bug affects multiple sites'
    )

    parser.add_argument(
        '--multiple-reporters',
        action='store_true',
        help='Multiple reporters'
    )

    parser.add_argument(
        '--chrome',
        action='store_true',
        help='Reproduces in Chrome'
    )

    # Output options
    parser.add_argument(
        '--show-score',
        action='store_true',
        help='Show the numeric score in addition to the Bugzilla comment'
    )

    parser.add_argument(
        '--show-keywords',
        action='store_true',
        help='Show keywords that should be added to the bug'
    )

    parser.add_argument(
        '--show-url',
        action='store_true',
        help='Show only the URL to the web calculator'
    )

    parser.add_argument(
        '--json',
        action='store_true',
        dest='as_json',
        help=(
            'Emit the result as a single JSON object instead of a Bugzilla '
            'comment. For programmatic callers: the comment form is Markdown '
            'meant for a human to paste, and parsing it back out is brittle.'
        )
    )

    parser.add_argument(
        '--classify-domain',
        metavar='DOMAIN',
        help='Look up which pages-affected tier (major/common/rare) a site '
             'falls into, based on real pageload rank. Respects --platforms '
             '(desktop list for windows/macos/linux, mobile list for android). '
             'Prints the tier and exits.'
    )

    args = parser.parse_args()

    # Domain classification mode: resolve a site to a pages-affected tier.
    if args.classify_domain:
        data = load_top_sites()
        if data is None:
            print(f"error: could not load {TOP_SITES_PATH}", file=sys.stderr)
            sys.exit(1)
        tier, ranks = classify_domain(args.classify_domain, args.platforms, data)
        norm = _normalize_domain(args.classify_domain)
        rank_desc = ', '.join(
            f"{name}=#{rank}" if rank else f"{name}=unranked"
            for name, rank in ranks.items()
        )
        print(f"{norm}: {tier}  ({rank_desc})")
        print(f"Use: --pages-affected {tier}")
        return

    # Collect additional factors
    additional_factors = []
    if args.animation:
        additional_factors.append('animation')
    if args.multiple_sites:
        additional_factors.append('multiple-sites')
    if args.multiple_reporters:
        additional_factors.append('multiple-reporters')
    if args.chrome:
        additional_factors.append('chrome')

    # Calculate impact
    score, impact_level, keywords, selections = calculate_impact(
        platforms=args.platforms,
        browser_impact=args.browser_impact,
        site_impact=args.site_impact,
        configuration=args.configuration,
        pageload_impact=args.pageload_impact,
        pages_affected=args.pages_affected,
        resource_impact=args.resource_impact,
        additional_factors=additional_factors,
        reproducible=args.reproducible
    )

    # Reconstruct command line for documentation
    command_line = ' '.join(sys.argv)

    # Generate URL
    url = generate_url(selections)

    # Output based on flags
    if args.as_json:
        # Everything a programmatic caller needs, with the same values the
        # comment form reports. `comment` is included so a caller can post the
        # human-readable text without re-deriving it, and `url` so the reader
        # can check the inputs in the web calculator.
        print(json.dumps({
            'impact': impact_level,
            'score': round(score, 2),
            'keywords': sorted(keywords),
            'url': url,
            'comment': format_bugzilla_comment(
                score, impact_level, keywords, selections, command_line
            ),
            'selections': selections,
        }, indent=2))
    elif args.show_url:
        print(url)
    else:
        # Generate Bugzilla comment
        comment = format_bugzilla_comment(score, impact_level, keywords, selections, command_line)
        print(comment)

        if args.show_score:
            print(f"\nScore: {score:.2f}")

        if args.show_keywords:
            if keywords:
                print(f"Keywords: {', '.join(sorted(keywords))}")
            else:
                print("Keywords: (none)")


if __name__ == '__main__':
    main()
