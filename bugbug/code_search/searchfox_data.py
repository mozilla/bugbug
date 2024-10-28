# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import collections
import glob
import json
import os
import re
import sys

from bugbug.code_search import searchfox_download
from bugbug.code_search.function_search import (
    Function,
    FunctionSearch,
    register_function_search,
)


def find_symbol_definition_for_line(path, line, searchfox_path):
    searchfox_origfile_path = os.path.join(searchfox_path, path)
    assert os.path.exists(
        searchfox_origfile_path
    ), f"{searchfox_origfile_path} doesn't exist"

    ret_obj = None
    with open(searchfox_origfile_path, "r") as fd:
        for line_content in fd:
            obj = json.loads(line_content)
            lineno = int(obj["loc"].split(":")[0])

            if line >= lineno and "syntax" in obj:
                syntax = obj["syntax"].split(",")
                if "def" in syntax and "function" in syntax:
                    ret_obj = {}
                    ret_obj["file"] = path
                    ret_obj["target_line"] = int(obj["loc"].split(":")[0])

                    if "nestingRange" in obj:
                        ret_obj["target_end_line"] = int(
                            obj["nestingRange"].split("-")[-1].split(":")[0]
                        )
                    else:
                        ret_obj["target_end_line"] = None

    return ret_obj


def find_symbol_definition(
    searchfox_path,
    target_symbols=None,
    target_sym_is_pretty=False,
    headers_first=False,
    target_sym_type_restriction=None,
):
    searchfox_files = []
    searchfox_header_files = glob.glob(
        os.path.join(searchfox_path, "**/*.h"), recursive=True
    )
    searchfox_cpp_files = glob.glob(
        os.path.join(searchfox_path, "**/*.c*"), recursive=True
    )
    searchfox_mm_files = glob.glob(
        os.path.join(searchfox_path, "**/*.mm"), recursive=True
    )

    if headers_first:
        searchfox_files.extend(searchfox_header_files)
        searchfox_files.extend(searchfox_cpp_files)
        searchfox_files.extend(searchfox_mm_files)
    else:
        searchfox_files.extend(searchfox_cpp_files)
        searchfox_files.extend(searchfox_mm_files)
        searchfox_files.extend(searchfox_header_files)

    ret = collections.defaultdict(list)
    target_symbols_left = target_symbols

    for searchfox_file in searchfox_files:
        if not target_symbols_left:
            break

        with open(searchfox_file, "r") as fd:
            data = fd.read()

            has_interesting_symbol = False
            for target_sym in target_symbols_left:
                if target_sym in data:
                    has_interesting_symbol = True
                    break

            if not has_interesting_symbol:
                continue

            data = data.splitlines()
            for line in data:
                sym_found = None
                for target_sym in target_symbols_left:
                    if target_sym in line:
                        obj = json.loads(line)
                        if "syntax" in obj:
                            syntax = obj["syntax"].split(",")
                            if "def" in syntax and (
                                target_sym_type_restriction is None
                                or target_sym_type_restriction in syntax
                            ):
                                if (
                                    not target_sym_is_pretty
                                    or target_sym in obj["pretty"]
                                ):
                                    sym_found = target_sym
                                    ret_obj = {}
                                    ret_obj["name"] = obj["pretty"]
                                    ret_obj["file"] = searchfox_file
                                    ret_obj["target_line"] = int(
                                        obj["loc"].split(":")[0]
                                    )

                                    if "nestingRange" in obj:
                                        ret_obj["target_end_line"] = int(
                                            obj["nestingRange"]
                                            .split("-")[-1]
                                            .split(":")[0]
                                        )
                                    else:
                                        ret_obj["target_end_line"] = None

                                    ret[target_sym].append(ret_obj)

                                    if not target_sym_is_pretty:
                                        break

                if not target_sym_is_pretty and sym_found is not None:
                    target_symbols_left.remove(sym_found)

                if not target_symbols_left:
                    break
    return ret


def default_read_mc_path(path):
    with open(path, "r") as fd:
        for line in fd:
            yield line


def extract_source(target_sym_file, target_sym_line, target_sym_end_line, read_mc_path):
    current_lineno = 0
    target_source = None
    end_template = "%s}"
    end = None
    for line in read_mc_path(target_sym_file):
        line = line.rstrip()
        current_lineno += 1
        if current_lineno == target_sym_line:
            target_source = []

            if target_sym_end_line is None:
                # If we don't have an end-line, use "heuristics" (favorite cover term for dirty hacks)
                print("INFO: Using heuristics to detect end of function definition")
                if ";" in line and "}" not in line:
                    print(
                        "WARNING: Likely virtual function: '%s'" % line, file=sys.stderr
                    )
                    return None
                end = end_template % (
                    " " * (len(line) - len(line.lstrip()))
                )  # TODO: Dirty hack #2, use indent to match end of function

                if "}" in line:
                    # This is a one liner function
                    target_source = [line]
                    break

        if target_source is not None:
            target_source.append(line)

            if (
                target_sym_end_line is not None
                and current_lineno == target_sym_end_line
            ):
                break

            if line == end:
                break

    return target_source


def extract_function_approx(
    name, funcobj, mc_path, searchfox_path, read_mc_path=default_read_mc_path
):
    searchfox_origfile_path = os.path.join(
        searchfox_path, funcobj["file"].replace(mc_path, "")
    )
    assert os.path.exists(searchfox_origfile_path)

    # This is a hack to rewrite some interface definitions to their actual
    # implementation. Searchfox itself doesn't offer this functionality.
    # For example, nsIWidget is implemented either in nsBaseWidget or
    # per operating system, e.g. in widget/gtk/nsWindow. We can approximate
    # this my looking for the function in that order and match it by the
    # `pretty` field in searchfox data.
    interface_rewrites = {
        "nsIWidget": ["nsWindow", "nsChildView", "nsBaseWidget", "nsIWidget"],
        "nsIUserIdleService": ["nsUserIdleService", "nsIUserIdleService"],
        "nsIWebAuthnSignResult": ["WebAuthnSignResult"],
        "nsIWebAuthnRegisterResult": ["WebAuthnRegisterResult"],
        "nsIWebAuthnService": ["WinWebAuthnService", "WebAuthnService"],
    }

    # GPT sometimes tries to get the class this belongs to, but it isn't always right
    call_name = name.split("::")[-1]

    # Step 1: Prepare what we're looking for and where. In particular, we want
    # to match a pattern on the pretty print name to rule out false positives
    # due to substring matches.
    source = funcobj["source"]
    line_start = funcobj["start"]
    line_stop = funcobj["start"] + len(source)
    pattern = "([^a-zA-Z0-9_]|^)%s" % call_name

    # {"loc":"03731:41-72","source":1,"syntax":"use,function","type":"already_AddRefed<DataSourceSurface> (const IntSize &, SurfaceFormat, const uint8_t *, int32_t)","pretty":"function mozilla::gfx::CreateDataSourceSurfaceFromData","sym":"_ZN7mozilla3gfx31CreateDataSourceSurfaceFromDataERKNS0_12IntSizeTypedINS0_12UnknownUnitsEEENS0_13SurfaceFormatEPKhi"}

    # Step 2: Find symbol use in searchfox data
    target_sym = None
    target_sym_is_pretty = False
    target_sym_interface = None
    with open(searchfox_origfile_path, "r") as fd:
        for line in fd:
            obj = json.loads(line)
            lineno = int(obj["loc"].split(":")[0])
            if lineno >= line_start and lineno < line_stop and "syntax" in obj:
                if "use" in obj["syntax"].split(","):
                    if re.search(pattern, obj["pretty"]):
                        for interface in interface_rewrites:
                            if ("%s::" % interface) in obj["pretty"]:
                                for item in obj["pretty"].split(" "):
                                    if ("%s::" % interface) in item:
                                        target_sym = item
                                if not target_sym:
                                    print(
                                        "ERROR: Failed to extract pretty name for interface rewriting: %s"
                                        % obj["pretty"]
                                    )
                                    return None
                                print(
                                    "Using pretty name for interface rewriting: %s"
                                    % target_sym
                                )
                                target_sym_is_pretty = True
                                target_sym_interface = interface
                                break
                        if target_sym is not None:
                            break
                        target_sym = obj["sym"]
                        break

    # searchfox/363bddf92f7a2d58a5b87cac7b19a4c74c7544e5_linux64/gfx/2d/DataSurfaceHelpers.cpp:68:{"loc":"00037:36-67","source":1,"nestingRange":"39:25-56:0","syntax":"def,function","type":"already_AddRefed<DataSourceSurface> (const IntSize &, SurfaceFormat, const uint8_t *, int32_t)","pretty":"function mozilla::gfx::CreateDataSourceSurfaceFromData","sym":"_ZN7mozilla3gfx31CreateDataSourceSurfaceFromDataERKNS0_12IntSizeTypedINS0_12UnknownUnitsEEENS0_13SurfaceFormatEPKhi"}

    if target_sym is None:
        return None

    # Step 3: Locate symbol definition

    target_syms = [target_sym]
    if target_sym_is_pretty:
        target_syms.clear()
        for specialization in interface_rewrites[target_sym_interface]:
            target_syms.append(
                target_sym.replace("%s::" % interface, "%s::" % specialization)
            )

    target_sym_pretty_name = None
    target_sym_file = None
    target_sym_line = None
    target_sym_end_line = None
    target_searchfox_file = None
    for target_sym in target_syms:
        result = find_symbol_definition(
            searchfox_path,
            [target_sym],
            target_sym_is_pretty,
            headers_first=False,
            target_sym_type_restriction="function",
        )
        if result:
            assert len(result[target_sym]) == 1
            target_sym_pretty_name = result[target_sym][0]["name"]
            target_searchfox_file = result[target_sym][0]["file"]
            target_sym_file = result[target_sym][0]["file"].replace(
                searchfox_path, mc_path
            )
            target_sym_line = result[target_sym][0]["target_line"]
            target_sym_end_line = result[target_sym][0]["target_end_line"]
            break

    if target_sym_file is None:
        print(
            "WARNING: Couldn't find a function definition for symbol '%s'" % target_sym,
            file=sys.stderr,
        )
        return None

    if "__GENERATED__" in target_sym_file:
        print(
            "WARNING: Symbol '%s' is in generated source, ignoring!" % target_sym,
            file=sys.stderr,
        )
        return None

    # Step 4: Extract source
    target_source = extract_source(
        target_sym_file, target_sym_line, target_sym_end_line, read_mc_path
    )

    ret_context = {}
    ret_context["name"] = target_sym_pretty_name
    ret_context["start"] = target_sym_line
    ret_context["file"] = target_sym_file
    ret_context["source"] = target_source

    annotations = []
    if target_sym_end_line is not None:
        # Step 6: Search for any class members involved in annotated code (optional)
        field_syms = set()
        with open(target_searchfox_file, "r") as fd:
            data = fd.read()
            data = data.splitlines()
            for line in data:
                obj = json.loads(line)
                current_lineno = int(obj["loc"].split(":")[0])
                if (
                    current_lineno >= target_sym_line
                    and current_lineno <= target_sym_end_line
                ):
                    if "syntax" in obj:
                        syntax = obj["syntax"].split(",")
                        if "use" in syntax and "field" in syntax:
                            field_syms.add(obj["sym"])

        # Step 5: Locate and annotate member definitions as comments (optional)
        result = find_symbol_definition(
            searchfox_path,
            list(field_syms),
            target_sym_is_pretty=False,
            headers_first=True,
            target_sym_type_restriction="field",
        )
        for sym in result:
            assert len(result[sym]) == 1
            target_file = result[sym][0]["file"].replace(searchfox_path, mc_path)
            target_line = result[sym][0]["target_line"]
            lines = [line for line in read_mc_path(target_file)]
            annotations.append(lines[target_line - 1].strip())

    ret_context["annotations"] = annotations

    return ret_context


CPP_EXTENSIONS = [
    ".c",
    ".cpp",
    ".cc",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".mm",
    ".m",
]


class FunctionSearchSearchfoxData(FunctionSearch):
    def get_function_by_line(
        self, commit_hash: str, path: str, line: int
    ) -> list[Function]:
        try:
            searchfox_path = searchfox_download.fetch(commit_hash)
        except searchfox_download.SearchfoxDataNotAvailable:
            return []

        if not any(path.endswith(ext) for ext in CPP_EXTENSIONS):
            return []

        result = []

        definitions = [find_symbol_definition_for_line(path, line, searchfox_path)]

        for definition in definitions:
            definition_path = definition["file"].replace(searchfox_path, "")
            source = extract_source(
                definition_path,
                definition["target_line"],
                definition["target_end_line"],
                read_mc_path=lambda path: io.StringIO(
                    get_file(
                        commit_hash or "tip",
                        path,
                    )
                ),
            )
            result.append(
                Function(
                    definition["name"],
                    definition["target_line"],
                    definition_path,
                    "\n".join(source),
                )
            )

        return result

    def get_function_by_name(
        self, commit_hash: str, path: str, function_name: str
    ) -> list[Function]:
        try:
            searchfox_path = searchfox_download.fetch(commit_hash)
        except searchfox_download.SearchfoxDataNotAvailable:
            return []

        if not any(path.endswith(ext) for ext in CPP_EXTENSIONS):
            return []

        result: list[Function] = []

        # TODO: Try looking for a function call within the "before patch" first (it'll be more precise, as we can identify the exact call and so full symbol name)
        # caller_obj = {
        #     "file": path,
        #     "start": XXX,
        #     "source": XXX,  # the content doesn't matter, extract_function_approx only uses it for the number of lines
        # }
        # out = searchfox_search.extract_function_approx(
        #     function_name,
        #     caller_obj,
        #     "",
        #     searchfox_path
        # )
        # if out is not None:
        #     result.append(out)

        # If it wasn't found, try with entire file next
        if not result:
            if commit_hash is None:
                mc_file = get_file("tip", path)
            else:
                mc_file = get_file(commit_hash, path)

            caller_obj = {
                "file": path,
                "start": 0,
                "source": mc_file,  # the content doesn't matter, extract_function_approx only uses it for the number of lines
            }

            out = extract_function_approx(
                function_name,
                caller_obj,
                "",
                searchfox_path,
                read_mc_path=lambda path: io.StringIO(
                    get_file(
                        commit_hash or "tip",
                        path,
                    )
                ),
            )
            if out is not None:
                result.append(out)

        # If it wasn't found, try with string matching
        if not result:
            definitions = find_symbol_definition(
                searchfox_path,
                [function_name],
                target_sym_is_pretty=True,
                headers_first=False,
                target_sym_type_restriction="function",
            )[function_name]

            for definition in definitions:
                definition_path = definition["file"].replace(searchfox_path, "")
                source = extract_source(
                    definition_path,
                    definition["target_line"],
                    definition["target_end_line"],
                    read_mc_path=lambda path: io.StringIO(
                        get_file(
                            commit_hash or "tip",
                            path,
                        )
                    ),
                )
                result.append(
                    Function(
                        definition["name"],
                        definition["target_line"],
                        definition_path,
                        source,
                    )
                )

        return result


register_function_search("searchfox_data", FunctionSearchSearchfoxData)


if __name__ == "__main__":
    import typing

    from bugbug import utils

    def get_file(commit_hash, path):
        r = utils.get_session("hgmo").get(
            f"https://hg.mozilla.org/mozilla-unified/raw-file/{commit_hash}/{path}",
            headers={
                "User-Agent": utils.get_user_agent(),
            },
        )
        r.raise_for_status()
        return r.text

    caller_function_obj: dict[str, typing.Any] = {}
    caller_function_obj["start"] = 516
    caller_function_obj["file"] = "gfx/layers/NativeLayerWayland.cpp"
    caller_function_obj["source"] = [
        "Maybe<GLuint> NativeLayerWayland::NextSurfaceAsFramebuffer(",
        "const IntRect& aDisplayRect, const IntRegion& aUpdateRegion,",
        "bool aNeedsDepth) {",
        "MutexAutoLock lock(mMutex);",
        "",
        "mDisplayRect = IntRect(aDisplayRect);",
        "mDirtyRegion = IntRegion(aUpdateRegion);",
        "",
        "MOZ_ASSERT(!mInProgressBuffer);",
        "if (mFrontBuffer && !mFrontBuffer->IsAttached()) {",
        "// the Wayland compositor released the buffer early, we can reuse it",
        "mInProgressBuffer = std::move(mFrontBuffer);",
        "mFrontBuffer = nullptr;",
        "} else {",
        "mInProgressBuffer = mSurfacePoolHandle->ObtainBufferFromPool(mSize);",
        "}",
        "",
        "if (!mInProgressBuffer) {",
        'gfxCriticalError() << "Failed to obtain buffer";',
        "wr::RenderThread::Get()->HandleWebRenderError(",
        "wr::WebRenderError::NEW_SURFACE);",
        "return Nothing();",
        "}",
        "",
        "// get the framebuffer before handling partial damage so we don't accidentally",
        "// create one without depth buffer",
        "Maybe<GLuint> fbo = mSurfacePoolHandle->GetFramebufferForBuffer(",
        "mInProgressBuffer, aNeedsDepth);",
        'MOZ_RELEASE_ASSERT(fbo, " failed.");',
        "",
        "if (mFrontBuffer) {",
        "HandlePartialUpdate(lock);",
        "mSurfacePoolHandle->ReturnBufferToPool(mFrontBuffer);",
        "mFrontBuffer = nullptr;",
        "}",
        "",
        "return fbo;",
        "}",
    ]

    mc_path = sys.argv[1]  # e.g. "/home/marco/FD/mozilla-unified/"
    searchfox_path = sys.argv[
        2
    ]  # e.g. "searchfox_data/1d2ccbe0bb6db4d9628b16914b33c5dc5e9406dc_linux64"

    import io

    # print("Full thing")
    print(
        extract_function_approx(
            "GetFramebufferForBuffer", caller_function_obj, mc_path, searchfox_path
        )
    )
    print(
        extract_function_approx(
            "GetFramebufferForBuffer",
            caller_function_obj,
            "",
            searchfox_path,
            read_mc_path=lambda path: io.StringIO(get_file("tip", path)),
        )
    )
    print("\n\nfind_symbol_definition1")
    print(
        find_symbol_definition(
            searchfox_path,
            [
                "_ZN7mozilla6layers24SurfacePoolHandleWayland23GetFramebufferForBufferERK6RefPtrINS_6widget13WaylandBufferEEb"
            ],
            target_sym_is_pretty=False,
            headers_first=False,
            target_sym_type_restriction="function",
        )
    )
    print("\n\nfind_symbol_definition2")
    definitions = find_symbol_definition(
        searchfox_path,
        ["GetFramebufferForBuffer"],
        target_sym_is_pretty=True,
        headers_first=False,
        target_sym_type_restriction="function",
    )
    print(definitions)
    result = []
    for definition in definitions["GetFramebufferForBuffer"]:
        definition_path = definition["file"].replace(searchfox_path, "")
        source = extract_source(
            definition_path,
            definition["target_line"],
            definition["target_end_line"],
            read_mc_path=lambda path: io.StringIO(get_file("tip", path)),
        )
        result.append(
            {
                "start": definition["target_line"],
                "file": definition_path,
                "source": source,
                "annotations": [],
            }
        )

    print(result)

    # print(find_symbol_definition(searchfox_path, [ "getStrings" ], target_sym_is_pretty=False, headers_first=False, target_sym_type_restriction="function", match_full_symbol=False))

    print(
        find_symbol_definition_for_line(
            "gfx/layers/NativeLayerWayland.cpp", 528, searchfox_path
        )
    )
