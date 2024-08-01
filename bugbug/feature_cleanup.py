# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re


class url(object):
    def __init__(self):
        self.reference_url = re.compile(
            r"http[s]?://(hg.mozilla|searchfox|dxr.mozilla)\S+"
        )
        self.url = re.compile(r"http\S+")

    def __call__(self, text):
        return self.url.sub(
            "__URL__", self.reference_url.sub("__CODE_REFERENCE_URL__", text)
        )


class fileref(object):
    def __init__(self):
        self.pattern = re.compile(r"\w+\.(py|json|js|jsm|mjs|jsx|html|css|c|cpp|h)\b")

    def __call__(self, text):
        return self.pattern.sub("__FILE_REFERENCE__", text)


class responses(object):
    def __init__(self):
        self.pattern = re.compile(">[^\n]+")

    def __call__(self, text):
        return self.pattern.sub(" ", text)


class hex(object):
    def __init__(self):
        self.pattern = re.compile(r"\b0[xX][0-9a-fA-F]+\b")

    def __call__(self, text):
        return self.pattern.sub("__HEX_NUMBER__", text)


class dll(object):
    def __init__(self):
        FIREFOX_DLLS_MATCH = "|".join(
            [
                "libmozwayland.so",
                "libssl3.so",
                "libnssdbm3.so",
                "liblgpllibs.so",
                "libmozavutil.so",
                "libxul.so",
                "libmozgtk.so",
                "libnssckbi.so",
                "libclearkey.dylib",
                "libmozsqlite3.so",
                "libplc4.so",
                "libsmime3.so",
                "libclearkey.so",
                "libnssutil3.so",
                "libnss3.so",
                "libplds4.so",
                "libfreeblpriv3.so",
                "libsoftokn3.so",
                "libmozgtk.so",
                "libmozavcodec.so",
                "libnspr4.so",
                "IA2Marshal.dll",
                "lgpllibs.dll",
                "libEGL.dll",
                "libGLESv2.dll",
                "libmozsandbox.so",
                "AccessibleHandler.dll",
                "AccessibleMarshal.dll",
                "api-ms-win-core-console-l1-1-0.dll",
                "api-ms-win-core-datetime-l1-1-0.dll",
                "api-ms-win-core-debug-l1-1-0.dll",
                "api-ms-win-core-errorhandling-l1-1-0.dll",
                "api-ms-win-core-file-l1-1-0.dll",
                "api-ms-win-core-file-l1-2-0.dll",
                "api-ms-win-core-file-l2-1-0.dll",
                "api-ms-win-core-handle-l1-1-0.dll",
                "api-ms-win-core-heap-l1-1-0.dll",
                "api-ms-win-core-interlocked-l1-1-0.dll",
                "api-ms-win-core-libraryloader-l1-1-0.dll",
                "api-ms-win-core-localization-l1-2-0.dll",
                "api-ms-win-core-memory-l1-1-0.dll",
                "api-ms-win-core-namedpipe-l1-1-0.dll",
                "api-ms-win-core-processenvironment-l1-1-0.dll",
                "api-ms-win-core-processthreads-l1-1-0.dll",
                "api-ms-win-core-processthreads-l1-1-1.dll",
                "api-ms-win-core-profile-l1-1-0.dll",
                "api-ms-win-core-rtlsupport-l1-1-0.dll",
                "api-ms-win-core-string-l1-1-0.dll",
                "api-ms-win-core-synch-l1-1-0.dll",
                "api-ms-win-core-synch-l1-2-0.dll",
                "api-ms-win-core-sysinfo-l1-1-0.dll",
                "api-ms-win-core-timezone-l1-1-0.dll",
                "api-ms-win-core-util-l1-1-0.dll",
                "api-ms-win-crt-conio-l1-1-0.dll",
                "api-ms-win-crt-convert-l1-1-0.dll",
                "api-ms-win-crt-environment-l1-1-0.dll",
                "api-ms-win-crt-filesystem-l1-1-0.dll",
                "api-ms-win-crt-heap-l1-1-0.dll",
                "api-ms-win-crt-locale-l1-1-0.dll",
                "api-ms-win-crt-math-l1-1-0.dll",
                "api-ms-win-crt-multibyte-l1-1-0.dll",
                "api-ms-win-crt-private-l1-1-0.dll",
                "api-ms-win-crt-process-l1-1-0.dll",
                "api-ms-win-crt-runtime-l1-1-0.dll",
                "api-ms-win-crt-stdio-l1-1-0.dll",
                "api-ms-win-crt-string-l1-1-0.dll",
                "api-ms-win-crt-time-l1-1-0.dll",
                "api-ms-win-crt-utility-l1-1-0.dll",
                "d3dcompiler_47.dll",
                "freebl3.dll",
                "mozavcodec.dll",
                "mozavutil.dll",
                "mozglue.dll",
                "msvcp140.dll",
                "nss3.dll",
                "nssckbi.dll",
                "nssdbm3.dll",
                "qipcap64.dll",
                "softokn3.dll",
                "ucrtbase.dll",
                "vcruntime140.dll",
                "xul.dll",
                "clearkey.dll",
                "libfreebl3.dylib",
                "liblgpllibs.dylib",
                "libmozavcodec.dylib",
                "libmozavutil.dylib",
                "libmozglue.dylib",
                "libnss3.dylib",
                "libnssckbi.dylib",
                "libnssdbm3.dylib",
                "libplugin_child_interpose.dylib",
                "libsoftokn3.dylib",
            ]
        ).replace(".", r"\.")
        self.pattern = re.compile(
            rf"\b(?!{FIREFOX_DLLS_MATCH})\w+(\.dll|\.so|\.dylib)\b"
        )

    def __call__(self, text):
        return self.pattern.sub("__DLL_NAME__", text)


class synonyms(object):
    def __init__(self):
        synonyms = (
            ("safemode", ["safemode", "safe mode"]),
            ("str", ["str", "steps to reproduce", "repro steps"]),
            ("uaf", ["uaf", "use after free", "use-after-free"]),
            ("asan", ["asan", "address sanitizer", "addresssanitizer"]),
            (
                "permafailure",
                [
                    "permafailure",
                    "permafailing",
                    "permafail",
                    "perma failure",
                    "perma failing",
                    "perma fail",
                    "perma-failure",
                    "perma-failing",
                    "perma-fail",
                ],
            ),
            ("spec", ["spec", "specification"]),
        )
        self.synonyms_dict = {
            synonym: synonym_group
            for synonym_group, synonym_list in synonyms
            for synonym in synonym_list
        }
        self.pattern = re.compile(
            r"|".join(rf"\b{synonym}\b" for synonym in self.synonyms_dict.keys()),
            flags=re.IGNORECASE,
        )

    def _replace(self, match):
        return self.synonyms_dict[match.group(0).lower()]

    def __call__(self, text):
        return self.pattern.sub(self._replace, text)


class crash(object):
    def __init__(self):
        self.pattern = re.compile(
            r"bp-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{6}[0-9]{6}\b"
        )

    def __call__(self, text):
        return self.pattern.sub("__CRASH_STATS_LINK__", text)


class CleanCompatibilityReportDescription(object):
    def __init__(self):
        self.sub_patterns = {
            "details": re.compile(r"<details>.*?</details>", re.DOTALL),
            "footer": re.compile(
                r"_From \[webcompat\.com\]\(https://webcompat\.com/\) with ❤️_"
            ),
            "link": re.compile(
                r"\[View console log messages\]\(https://webcompat\.com/console_logs/.*?\)"
            ),
            "screenshot": re.compile(r"\[\!\[Screenshot Description\]\(.*?\)\]\(.*?\)"),
            "screenshot_md": re.compile(
                r'\*\*Screenshot\*\*\s*\r?\n\<img width="[\d]+" alt="[^"]*" src="https?://[^"]+"[^>]*>'
            ),
            "watchers": re.compile(r"\*\*Watchers:\*\*(?:\r?\n@[\w-]+)+"),
        }
        self.extract_patterns = {
            "description": re.compile(r"\*\*Description\*\*: (.*?)\n", re.DOTALL),
            "problem_type": re.compile(r"\*\*Problem type\*\*: (.*?)\n", re.DOTALL),
            "steps": re.compile(r"\*\*Steps to Reproduce\*\*:?(.*)", re.DOTALL),
        }

        self.default_problems = {
            "Desktop site instead of mobile site",
            "Browser unsupported",
            "Page not loading correctly",
            "Missing items",
            "Buttons or links not working",
            "Unable to type",
            "Unable to login",
            "Problems with Captcha",
            "Images not loaded",
            "Items are overlapped",
            "Items are misaligned",
            "Items not fully visible",
            "There is no video",
            "There is no audio",
            "Media controls are broken or missing",
            "The video or audio does not play",
        }

    def _extract_and_strip(self, pattern, text):
        match = pattern.search(text)
        return match.group(1).strip() if match else ""

    def __call__(self, text):
        for pattern in self.sub_patterns.values():
            text = pattern.sub("", text)

        problem_type = self._extract_and_strip(
            self.extract_patterns["problem_type"], text
        )
        description = self._extract_and_strip(
            self.extract_patterns["description"], text
        )
        steps = self._extract_and_strip(self.extract_patterns["steps"], text)

        if problem_type == "Something else" or description not in self.default_problems:
            return f"{description}\n {steps}" if steps else description
        else:
            return steps
