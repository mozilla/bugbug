import re


# If the bug contains these keywords, it's very likely a feature.
def feature_check_keywords(bug):
    keywords = [
        'feature', 'polish',
    ]
    return sum(keyword in bug['keywords'] for keyword in keywords)


def feature_check_title(bug):
    keywords = [
        'implement', 'refactor', 'meta', 'tracker', 'dexpcom',
        'indent', 'ui review', 'support', '[ux]',
    ]
    if any(keyword in bug['summary'].lower() for keyword in keywords):
        return sum(keyword in bug['summary'].lower() for keyword in keywords)

    keyword_couples = [
        ('add', 'test')
    ]

    return sum(couple[0] in bug['summary'].lower() and couple[1] in bug['summary'].lower() for couple in keyword_couples)


def feature_check_first_comment(bug):
    keywords = [
        'refactor',
    ]
    return sum(keyword in bug['comments'][0]['text'].lower() for keyword in keywords)


# If the Severity (Importance) field's value is "enhancement", it's likely not a bug.
def check_severity_enhancement(bug):
    return bug['severity'] == 'enhancement'


def feature_check_whiteboard(bug):
    return '[ux]' in bug['whiteboard'].lower()


def check_attachments(bug):
    return (len(bug['attachments']) == 0 or sum(1 for a in bug['attachments'] if a['is_patch']) == 0 or sum(1 for a in bug['attachments'] if a['content_type'] == 'text/x-review-board-request') == 0) and sum(1 for c in bug['comments'] if '://hg.mozilla.org/' in c['text']) == 0


feature_rules = [
    feature_check_keywords,
    feature_check_title,
    feature_check_first_comment,
    check_severity_enhancement,
    feature_check_whiteboard,
    check_attachments,
]


# If the bug has a crash signature, it is definitely a bug.
def has_crash_signature(bug):
    return 'cf_crash_signature' in bug and bug['cf_crash_signature'] != ''


# If the bug has steps to reproduce, it is very likely a bug.
def has_str(bug):
    return 'cf_has_str' in bug and bug['cf_has_str'] == 'yes'


# If the bug has a regression range, it is definitely a bug.
def has_regression_range(bug):
    return 'cf_has_regression_range' in bug and bug['cf_has_regression_range'] == 'yes'


# If the bug has a URL, it's very likely a bug that the reporter experienced
# on the given URL.
# If the bug contains the strings `github` and `w3c`, it's likely a feature implementing a w3c spec.
def has_url(bug):
    return bug['url'] != '' and ('github' not in bug['url'] or 'w3c' not in bug['url'])


# If the bug contains these keywords, it's definitely a bug.
def bug_check_keywords(bug):
    keywords = [
        'crash', 'regression', 'regressionwindow-wanted', 'jsbugmon',
        'hang', 'topcrash', 'assertion', 'coverity', 'infra-failure',
        'intermittent-failure', 'reproducible', 'stack-wanted',
        'steps-wanted', 'testcase-wanted', 'testcase', 'crashreportid',
        'talos-regression',
    ]
    if any(keyword in bug['keywords'] for keyword in keywords):
        return sum(keyword in bug['keywords'] for keyword in keywords)

    sub_keywords = [
        'sec-', 'csectype-',
    ]

    return sum(sub_keyword in keyword for sub_keyword in sub_keywords for keyword in bug['keywords'])


# If the bug title contains these substrings, it's definitely a bug.
def bug_check_title(bug):
    keywords = [
        'fail', 'npe', 'except', 'broken', 'crash', 'bug', 'differential testing', 'error',
        'addresssanitizer', 'hang ', ' hang', 'jsbugmon', 'leak', 'permaorange', 'random orange',
        'intermittent', 'regression', 'test fix', 'heap overflow', 'uaf', 'use-after-free',
        'asan', 'address sanitizer', 'rooting hazard', 'race condition', 'xss', '[static analysis]',
        'warning c',
    ]
    return sum(keyword in bug['summary'].lower() for keyword in keywords)


# If the first comment in the bug contains these substrings, it's likely a bug.
def check_first_comment(bug):
    keywords = [
        'steps to reproduce', 'crash', 'assertion', 'failure', 'leak', 'stack trace', 'regression',
        'test fix', ' hang', 'hang ', 'heap overflow', 'str:', 'use-after-free', 'asan',
        'address sanitizer', 'permafail', 'intermittent', 'race condition', 'unexpected fail',
        'unexpected-fail', 'unexpected pass', 'unexpected-pass', 'repro steps:', 'to reproduce:',
    ]

    casesensitive_keywords = [
        'FAIL', 'UAF',
    ]

    return sum(keyword in bug['comments'][0]['text'].lower() for keyword in keywords) +\
           sum(keyword in bug['comments'][0]['text'] for keyword in casesensitive_keywords)


# If any of the comments in the bug contains these substirngs, it's likely a bug.
def check_comments(bug):
    keywords = [
        'mozregression', 'safemode', 'safe mode',
        # mozregression messages.
        'Looks like the following bug has the changes which introduced the regression', 'First bad revision',
    ]
    return sum(keyword in comment['text'].lower() for comment in bug['comments'] for keyword in keywords)


# If the Severity (Importance) field's value is "major", it's likely a bug
def bug_check_severity(bug):
    return bug['severity'] in ['major', 'critical']


def bug_check_whiteboard(bug):
    return 'memshrink' in bug['whiteboard'].lower()


def is_coverity_issue(bug):
    return re.search('[CID ?[0-9]+]', bug['summary']) is not None or re.search('[CID ?[0-9]+]', bug['whiteboard']) is not None


bug_rules = [
    has_crash_signature,
    has_str,
    has_regression_range,
    has_url,
    bug_check_keywords,
    bug_check_title,
    check_first_comment,
    check_comments,
    bug_check_severity,
    bug_check_whiteboard,
    is_coverity_issue,
]


def is_bug(bug):
    return sum(rule(bug) for rule in bug_rules) > sum(rule(bug) for rule in feature_rules)
