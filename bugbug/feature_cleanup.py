# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re


def url(text):
    text = re.sub(
        r"http[s]?://(hg.mozilla|searchfox|dxr.mozilla)\S+",
        "__CODE_REFERENCE_URL__",
        text,
    )
    return re.sub(r"http\S+", "__URL__", text)


def fileref(text):
    return re.sub(
        r"\w+\.py\b|\w+\.json\b|\w+\.js\b|\w+\.jsm\b|\w+\.html\b|\w+\.css\b|\w+\.c\b|\w+\.cpp\b|\w+\.h\b",
        "__FILE_REFERENCE__",
        text,
    )


def responses(text):
    return re.sub(">[^\n]+", " ", text)


def hex(text):
    return re.sub(r"\b0[xX][0-9a-fA-F]+\b", "__HEX_NUMBER__", text)


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


def dll(text):
    regex = fr"\b(?!{FIREFOX_DLLS_MATCH})\w+(\.dll|\.so|\.dylib)\b"
    return re.sub(regex, "__DLL_NAME__", text)


def synonyms(text):
    synonyms = [
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
    ]

    for synonym_group, synonym_list in synonyms:
        text = re.sub(
            "|".join(fr"\b{synonym}\b" for synonym in synonym_list),
            synonym_group,
            text,
            flags=re.IGNORECASE,
        )

    return text


def crash(text):
    return re.sub(
        r"bp-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{6}[0-9]{6}\b",
        "__CRASH_STATS_LINK__",
        text,
    )
